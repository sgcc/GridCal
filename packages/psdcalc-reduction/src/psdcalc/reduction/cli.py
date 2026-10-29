"""First-batch inspection, custom planning, build, and structural reporting."""

import argparse
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from importlib.metadata import version
import json
import math
from pathlib import Path
import re
import sys

import pypsa
import yaml

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.loader import load_config
from psdcalc.reduction.config.models import ReductionConfig
from psdcalc.reduction.config.schema_export import export_schema
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.core.records import ReductionRecord
from psdcalc.reduction.core.signatures import hash_file, hash_path
from psdcalc.reduction.exceptions import (
    PartitionError, ReductionError, UnsupportedFeatureError,
)
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.compose import compose_maps
from psdcalc.reduction.materializers.quotient import QuotientMaterializer
from psdcalc.reduction.planners.custom_busmap import CustomBusmapPlanner
from psdcalc.reduction.planners.energy import EnergyPartitionPlanner
from psdcalc.reduction.planners.layer_energy import VoltageLayerEnergyPlanner
from psdcalc.reduction.pipelines.multi_voltage import MultiVoltageQuotientPipeline
from psdcalc.reduction.protection.engine import ProtectionEngine, ProtectionSet
from psdcalc.reduction.topology.simplifier import (
    TopologySimplificationResult, VoltageAwareTopologySimplifier,
)
from psdcalc.reduction.validation.structural import StructuralValidator


def _input_path(config: ReductionConfig, config_path: Path) -> Path:
    """Resolve an input path relative to its configuration file."""
    path = Path(config.input.path)
    return path if path.is_absolute() else config_path.parent / path


def _context(config: ReductionConfig, config_path: Path) -> tuple[object, object, object]:
    """Load a source network, sidecar, and protection set."""
    adapter = PypsaAdapter()
    network = adapter.load(_input_path(config, config_path), config.input.format)
    sidecar = adapter.identity_sidecar(network, config.input.source_namespace)
    adapter.validate_model(network, sidecar)
    protection = ProtectionEngine().compile(
        adapter.inventory(network, sidecar), config.protection.rules,
        profile=config.task.profile, base_dir=config_path.parent,
    )
    return network, sidecar, protection


def _plan(config: ReductionConfig, config_path: Path,
          network: object, sidecar: object, protection: ProtectionSet,
          *, ring_edges: frozenset[str] = frozenset()) -> object:
    """Plan a source-ID custom partition using its declared connectivity policy."""
    if config.partition is None:
        raise UnsupportedFeatureError("Partition is unavailable for this pipeline")
    if config.partition.planner != "custom_busmap":
        target = config.partition.target
        count = (target.n_clusters if target.type == "count" else
                 math.ceil(len(network.buses) * (target.ratio or 0.0)))
        if count is None:
            raise UnsupportedFeatureError("Energy planner requires a count or ratio target")
        result = EnergyPartitionPlanner().plan(
            network, sidecar, protection, planner=config.partition.planner,
            n_clusters=count, seed=config.run.seed,
            builders=config.features.builders, options=config.partition.options,
            connectivity_policy=config.partition.connectivity_policy,
            ring_edges=tuple(sorted(ring_edges)),
        )
        return _enforce_cluster_sizes(result, config)
    path = Path(config.partition.custom_busmap.path or "")
    if not path.is_absolute():
        path = config_path.parent / path
    result = CustomBusmapPlanner().plan(
        network, sidecar, path, protection,
        connectivity_policy=config.partition.connectivity_policy,
    )
    return _enforce_cluster_sizes(result, config)


def _enforce_cluster_sizes(plan: object, config: ReductionConfig) -> object:
    """Reject planner output outside declared cluster-size bounds."""
    if config.partition is None:
        raise PartitionError("Cluster size requires a partition configuration")
    sizes = Counter(plan.stable_busmap.values())
    minimum = config.partition.min_cluster_size
    maximum = config.partition.max_cluster_size
    if any(size < minimum or maximum is not None and size > maximum
           for size in sizes.values()):
        raise PartitionError("Partition violates min_cluster_size or max_cluster_size")
    return plan


def _protect_ring(protection: ProtectionSet, sidecar: object,
                  network: object, cycle_edges: frozenset[str]) -> ProtectionSet:
    """Keep every retained cycle edge and its terminal buses distinct."""
    ring_buses = set()
    ring_lines = set()
    for name in cycle_edges:
        row = network.lines.loc[name]
        ring_lines.add(sidecar.source_id("Line", name))
        ring_buses.add(sidecar.source_id("Bus", str(row.bus0)))
        ring_buses.add(sidecar.source_id("Bus", str(row.bus1)))
    return replace(
        protection,
        singleton_buses=frozenset(set(protection.singleton_buses) | ring_buses),
        preserved_components=frozenset(set(protection.preserved_components) | ring_lines),
    )


def _save_topology(store: ArtifactStore,
                   result: TopologySimplificationResult) -> None:
    """Persist one topology stage's source-ID maps and gate evidence."""
    adapter = PypsaAdapter()
    topology_maps = store.path("maps/topology")
    for name, path in result.mappings.save_parquet(topology_maps).items():
        store.record_artifact(f"topology_{name}", str(path.relative_to(store.run_dir)))
    evidence = {
        "voltage_layer_map": result.voltage_layer_map,
        "topology_roles": result.topology_roles,
        "anchors": [source.encode() for source in result.anchors],
        "absorbed_branches": [source.encode() for source in result.absorbed_branches],
        "moved_devices": [source.encode() for source in result.moved_devices],
    }
    for name, value in evidence.items():
        store.write_json(f"validation/{name}.json", value)
        store.record_artifact(name, f"validation/{name}.json")
    store.write_json("validation/topology_structural.json", result.structural.to_dict())
    store.record_artifact("topology_structural", "validation/topology_structural.json")
    for record in result.records:
        relative = f"records/{record.operation_id}.json"
        store.write_json(relative, record.model_dump(mode="json"))
        store.record_artifact(record.operation_id, relative)
    target = store.path("levels/L1.nc")
    adapter.export(result.network, target, "netcdf")
    store.record_artifact("simplified_network", "levels/L1.nc")
    store.set_metadata(output_hash=hash_file(target))


def _build_topology(store: ArtifactStore, config: ReductionConfig,
                    network: object, sidecar: object, protection: object) -> None:
    """Build and persist a standalone selective topology simplification."""
    result = VoltageAwareTopologySimplifier().simplify(
        network, sidecar, protection, mode="keep_transformer_interface"
    )
    _save_topology(store, result)
    store.write_json("validation/structural.json", result.structural.to_dict())
    store.record_artifact("structural", "validation/structural.json")
    store.set_structural_result(
        result.structural.status == "STRUCTURALLY_VALID",
        "STRUCTURALLY_SIMPLIFIED" if result.structural.status == "STRUCTURALLY_VALID"
        else None,
    )


def _build(config: ReductionConfig, config_path: Path,
           store: ArtifactStore | None = None) -> Path:
    """Build one quotient and persist maps, record, and structural evidence."""
    if store is None:
        output_root = Path(config.run.output_dir)
        if not output_root.is_absolute():
            output_root = config_path.parent / output_root
        store = ArtifactStore(output_root, config.run.id)
    if config.pipeline.materialization_family == "multi_voltage_quotient":
        with store.capture("input"):
            network, sidecar, _ = _context(config, config_path)
        resolved = config.model_copy(update={
            "input": config.input.model_copy(update={
                "path": str(_input_path(config, config_path).resolve()),
            }),
        })
        MultiVoltageQuotientPipeline(
            protection_base_dir=config_path.parent).run(
            original_network=network, source_sidecar=sidecar,
            config=resolved, store=store,
        )
        return store.run_dir
    with store.capture("build"):
        network, sidecar, protection = _context(config, config_path)
        adapter = PypsaAdapter()
        source_signature = adapter.topology_signature(network, sidecar)
        original_snapshot = adapter.clone(network)
        input_path = _input_path(config, config_path)
        input_hash = hash_path(input_path)
        store.set_metadata(
            input_hash=input_hash, topology_signature=source_signature,
            seed=config.run.seed,
            software_versions={"python": sys.version.split()[0],
                               "pypsa": pypsa.__version__,
                               "psdcalc-reduction": version("psdcalc-reduction")},
        )
        store.write_json("input/source_ids.json", sidecar.to_rows())
        store.record_artifact("source_ids", "input/source_ids.json")
        if config.output.save_original_snapshot:
            adapter.export(adapter.clone(network), store.path("input/original.nc"),
                           "netcdf")
            store.record_artifact("original_snapshot", "input/original.nc")
        if config.pipeline.materialization_family == "simplification":
            _build_topology(store, config, network, sidecar, protection)
            return store.run_dir
        enabled_topology = any(op.enabled and op.type == "voltage_aware_topology"
                               for op in config.simplification.operations)
        topology = None
        working = network
        working_sidecar = sidecar
        working_protection = protection
        if enabled_topology:
            topology = VoltageAwareTopologySimplifier().simplify(
                network, sidecar, protection, mode="keep_transformer_interface"
            )
            _save_topology(store, topology)
            if topology.structural.status != "STRUCTURALLY_VALID":
                store.set_structural_result(False)
                return store.run_dir
            working = topology.network
            working_sidecar = adapter.identity_sidecar(
                working, config.input.source_namespace
            )
            working_protection = _protect_ring(
                protection, working_sidecar, working, topology.cycle_edges
            )
        plan = _plan(config, config_path, working, working_sidecar,
                     working_protection,
                     ring_edges=topology.cycle_edges if topology else frozenset())
        store.write_json("input/plan.json", {
            "requested_cluster_count": plan.requested_cluster_count,
            "actual_cluster_count": plan.actual_cluster_count,
            "busmap": {source.encode(): target.encode()
                       for source, target in plan.stable_busmap.items()},
        })
        store.record_artifact("plan", "input/plan.json")
        quotient = QuotientMaterializer().materialize(
            working, working_sidecar, plan, config.aggregation, working_protection
        )
        stage_mapping = MappingBundle.from_quotient(
            working, working_sidecar, plan, quotient, operation_id="cluster-1"
        )
        mapping = (MappingBundle(
            compose_maps(topology.mappings.bus_map, stage_mapping.bus_map),
            compose_maps(topology.mappings.branch_map, stage_mapping.branch_map),
            compose_maps(topology.mappings.device_map, stage_mapping.device_map),
            compose_maps(topology.mappings.control_map, stage_mapping.control_map),
        ) if topology else stage_mapping)
        for name, path in mapping.save_parquet(store.path("maps")).items():
            store.record_artifact(name, str(path.relative_to(store.run_dir)))
        target_relative = "levels/L2.nc" if topology else "levels/L1.nc"
        target = store.path(target_relative)
        adapter.export(quotient.network, target, "netcdf")
        store.record_artifact("quotient", target_relative)
        store.set_metadata(output_hash=hash_file(target))
        evidence = StructuralValidator().validate(
            network, quotient.network, sidecar, mapping, protection,
            original_topology_signature=source_signature,
            original_snapshot=original_snapshot,
        )
        if topology:
            ring_sources = {sidecar.source_id("Line", name)
                            for name in topology.cycle_edges}
            ring_rows = [row for row in mapping.branch_map
                         if row.source_id in ring_sources]
            ring_valid = (len(ring_rows) == len(ring_sources) and
                          all(row.target_id is not None for row in ring_rows) and
                          len({row.target_id for row in ring_rows}) == len(ring_rows))
            checks = {**evidence.checks, "cycle_skeleton": ring_valid}
            evidence = type(evidence)(
                "STRUCTURALLY_VALID" if all(checks.values()) else "STRUCTURALLY_INVALID",
                checks, evidence.details,
            )
        store.write_json("validation/structural.json", evidence.to_dict())
        store.record_artifact("structural", "validation/structural.json")
        record = ReductionRecord(
            operation_id="cluster-1", operation_type="aggregation",
            parent_grid_hash=(hash_file(store.path("levels/L1.nc")) if topology
                              else input_hash), child_grid_hash=hash_file(target),
            input_signature=adapter.topology_signature(working, working_sidecar),
            output_signature=adapter.topology_signature(
                quotient.network,
                adapter.identity_sidecar(quotient.network, config.input.source_namespace),
            ),
            parameters={"requested_cluster_count": plan.requested_cluster_count,
                        "actual_cluster_count": plan.actual_cluster_count,
                        "connectivity_policy": plan.connectivity_policy},
            removed_objects=tuple(row.source_id.encode() for row in mapping.branch_map
                                  if row.mapping_type == "absorbed_internal"),
            mapping_files=tuple(f"maps/{name}.parquet" for name in
                                ("bus_map", "branch_map", "device_map", "control_map")
                                if store.path(f"maps/{name}.parquet").exists()),
        )
        store.write_json("records/cluster-1.json", record.model_dump(mode="json"))
        store.record_artifact("record", "records/cluster-1.json")
        store.set_structural_result(evidence.status == "STRUCTURALLY_VALID")
    return store.run_dir


def _build_from_path(config_path: Path) -> Path:
    """Create failure evidence before validating a potentially invalid config."""
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raw = None
    run = raw.get("run", {}) if isinstance(raw, dict) else {}
    run = run if isinstance(run, dict) else {}
    run_id = run.get("id") if isinstance(run.get("id"), str) else None
    if not run_id or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", run_id) is None:
        run_id = f"config-error-{hash_file(config_path)[:12]}"
    output = run.get("output_dir") if isinstance(run.get("output_dir"), str) else "runs"
    root = Path(output)
    if not root.is_absolute():
        root = config_path.parent / root
    store = ArtifactStore(root, run_id)
    with store.capture("config"):
        config = load_config(config_path)
    return _build(config, config_path, store)


def main(argv: Sequence[str] | None = None) -> int:
    """Execute one CLI command and return its process exit status."""
    parser = argparse.ArgumentParser(prog="psdcalc-reduction")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate-config", "inspect", "plan", "build"):
        commands.add_parser(name).add_argument("config")
    schema = commands.add_parser("dump-schema", help="write JSON Schema")
    schema.add_argument("path")
    report = commands.add_parser("report", help="show a persisted run manifest")
    report.add_argument("run_dir")
    args = parser.parse_args(argv)
    try:
        if args.command == "dump-schema":
            export_schema(args.path)
            print(args.path)
        elif args.command == "report":
            run_dir = Path(args.run_dir)
            manifest = json.loads((run_dir / "run_manifest.json").read_text(
                encoding="utf-8"
            ))
            payload: dict[str, object] = {"manifest": manifest}
            for name in ("structural", "topology_structural",
                         "multi_voltage_structural", "preliminary",
                         "opportunity", "failure"):
                path = run_dir / "validation" / f"{name}.json"
                if path.exists():
                    payload[name] = json.loads(path.read_text(encoding="utf-8"))
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            config_path = Path(args.config).resolve()
            if args.command == "build":
                run_dir = _build_from_path(config_path)
                print(run_dir)
                status = json.loads((run_dir / "run_manifest.json").read_text())["status"]
                return 0 if status in {"STRUCTURALLY_VALID",
                                       "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"} else 1
            config = load_config(config_path)
            if args.command == "validate-config":
                print(f"VALID {config.schema_version}: {config.run.id}")
            elif args.command == "inspect":
                network, sidecar, _ = _context(config, config_path)
                adapter = PypsaAdapter()
                print(json.dumps({
                    "counts": adapter.inventory(network, sidecar).counts(),
                    "topology_signature": adapter.topology_signature(network, sidecar),
                }, sort_keys=True))
            elif args.command == "plan":
                network, sidecar, protection = _context(config, config_path)
                if config.pipeline.materialization_family == "multi_voltage_quotient":
                    topology = VoltageAwareTopologySimplifier().simplify(
                        network, sidecar, protection,
                        mode="keep_transformer_interface",
                    )
                    parent_sidecar = PypsaAdapter().identity_sidecar(
                        topology.network, config.input.source_namespace)
                    target = config.partition.target
                    planned = VoltageLayerEnergyPlanner().plan(
                        topology.network, parent_sidecar, protection,
                        planner=config.partition.planner,
                        n_clusters=target.n_clusters, seed=config.run.seed,
                        builders=config.features.builders,
                        options=config.partition.options,
                        connectivity_policy=config.partition.connectivity_policy,
                        aliases=config.voltage_layers.aliases,
                        per_layer=target.per_layer,
                        min_cluster_size=config.partition.min_cluster_size,
                        max_cluster_size=config.partition.max_cluster_size,
                    )
                    print(json.dumps({
                        "requested_cluster_count": planned.requested_cluster_count,
                        "planned_cluster_count": len(set(planned.stable_busmap.values())),
                        "actual_cluster_count": planned.actual_cluster_count,
                    }, sort_keys=True))
                    return 0
                ring_edges = frozenset()
                if any(op.enabled and op.type == "voltage_aware_topology"
                       for op in config.simplification.operations):
                    topology = VoltageAwareTopologySimplifier().simplify(
                        network, sidecar, protection
                    )
                    network = topology.network
                    sidecar = PypsaAdapter().identity_sidecar(
                        network, config.input.source_namespace
                    )
                    protection = _protect_ring(
                        protection, sidecar, network, topology.cycle_edges
                    )
                    ring_edges = topology.cycle_edges
                plan = _plan(config, config_path, network, sidecar, protection,
                             ring_edges=ring_edges)
                print(json.dumps({"requested_cluster_count": plan.requested_cluster_count,
                                  "actual_cluster_count": plan.actual_cluster_count},
                                 sort_keys=True))
    except (ReductionError, OSError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
