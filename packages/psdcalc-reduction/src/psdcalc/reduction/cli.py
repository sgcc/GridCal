"""First-batch inspection, custom planning, build, and structural reporting."""

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.loader import load_config
from psdcalc.reduction.config.models import ReductionConfig
from psdcalc.reduction.config.schema_export import export_schema
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.core.records import ReductionRecord
from psdcalc.reduction.core.signatures import hash_file
from psdcalc.reduction.exceptions import ReductionError, UnsupportedFeatureError
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.materializers.quotient import QuotientMaterializer
from psdcalc.reduction.planners.custom_busmap import CustomBusmapPlanner
from psdcalc.reduction.protection.engine import ProtectionEngine
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
          network: object, sidecar: object, protection: object) -> object:
    """Plan a source-ID custom partition using its declared connectivity policy."""
    if config.partition.planner != "custom_busmap":
        raise UnsupportedFeatureError(
            f"Planner {config.partition.planner} awaits the energy-planner stage"
        )
    path = Path(config.partition.custom_busmap.path or "")
    if not path.is_absolute():
        path = config_path.parent / path
    return CustomBusmapPlanner().plan(
        network, sidecar, path, protection,
        connectivity_policy=config.partition.connectivity_policy,
    )


def _build(config: ReductionConfig, config_path: Path) -> Path:
    """Build one quotient and persist maps, record, and structural evidence."""
    output_root = Path(config.run.output_dir)
    if not output_root.is_absolute():
        output_root = config_path.parent / output_root
    store = ArtifactStore(output_root, config.run.id)
    with store.capture("build"):
        network, sidecar, protection = _context(config, config_path)
        adapter = PypsaAdapter()
        source_signature = adapter.topology_signature(network, sidecar)
        input_path = _input_path(config, config_path)
        input_hash = hash_file(input_path) if input_path.is_file() else source_signature
        store.write_json("input/source_ids.json", sidecar.to_rows())
        store.record_artifact("source_ids", "input/source_ids.json")
        plan = _plan(config, config_path, network, sidecar, protection)
        store.write_json("input/plan.json", {
            "requested_cluster_count": plan.requested_cluster_count,
            "actual_cluster_count": plan.actual_cluster_count,
            "busmap": {source.encode(): target.encode()
                       for source, target in plan.stable_busmap.items()},
        })
        store.record_artifact("plan", "input/plan.json")
        quotient = QuotientMaterializer().materialize(
            network, sidecar, plan, config.aggregation, protection
        )
        mapping = MappingBundle.from_quotient(
            network, sidecar, plan, quotient, operation_id="cluster-1"
        )
        for name, path in mapping.save_parquet(store.path("maps")).items():
            store.record_artifact(name, str(path.relative_to(store.run_dir)))
        target = store.path("levels/L1.nc")
        adapter.export(quotient.network, target, "netcdf")
        store.record_artifact("quotient", "levels/L1.nc")
        evidence = StructuralValidator().validate(
            network, quotient.network, sidecar, mapping, protection,
            original_topology_signature=source_signature,
        )
        store.write_json("validation/structural.json", evidence.to_dict())
        store.record_artifact("structural", "validation/structural.json")
        record = ReductionRecord(
            operation_id="cluster-1", operation_type="aggregation",
            parent_grid_hash=input_hash, child_grid_hash=hash_file(target),
            input_signature=source_signature,
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
            manifest = Path(args.run_dir) / "run_manifest.json"
            print(json.dumps(json.loads(manifest.read_text(encoding="utf-8")),
                             ensure_ascii=False, indent=2))
        else:
            config_path = Path(args.config).resolve()
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
                plan = _plan(config, config_path, network, sidecar, protection)
                print(json.dumps({"requested_cluster_count": plan.requested_cluster_count,
                                  "actual_cluster_count": plan.actual_cluster_count},
                                 sort_keys=True))
            elif args.command == "build":
                run_dir = _build(config, config_path)
                print(run_dir)
                status = json.loads((run_dir / "run_manifest.json").read_text())["status"]
                return 0 if status == "STRUCTURALLY_VALID" else 1
    except (ReductionError, OSError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
