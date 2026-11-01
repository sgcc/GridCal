"""Persist, reload, and finally validate the multi-voltage structural chain."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import tempfile
from time import perf_counter
from collections.abc import Mapping

import pyarrow as pa
import pyarrow.parquet as pq
import pypsa
import yaml

from psdcalc.reduction import __version__
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import ReductionConfig
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.core.ids import IdentitySidecar
from psdcalc.reduction.core.manifests import RunManifest
from psdcalc.reduction.core.records import ReductionRecord
from psdcalc.reduction.core.signatures import hash_file, hash_path
from psdcalc.reduction.exceptions import (
    BackendCapabilityError, ConfigSemanticError, MappingError, SignatureError,
)
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.metrics.stage_inventory import (
    CompressionStage, StageInventory, pypsa_stage_inventory,
)
from psdcalc.reduction.metrics.energy import collect_energy_aggregates
from psdcalc.reduction.reporting.comparison import (
    EnergyAggregateSummary, VoltagePolicyReportSummary,
)
from psdcalc.reduction.mapping.compose import compose_maps
from psdcalc.reduction.materializers.multi_voltage import MultiVoltageQuotientBuilder
from psdcalc.reduction.planners.layer_energy import VoltageLayerEnergyPlanner
from psdcalc.reduction.policies.voltage_cutoff import (
    ResolvedVoltagePolicy, VoltageCutoffPolicy, voltage_policy_evidence,
    voltage_policy_protection,
)
from psdcalc.reduction.planners.multi_voltage_busmap import MultiVoltageBusmapValidator
from psdcalc.reduction.protection.engine import ProtectionEngine
from psdcalc.reduction.reporting.opportunity import (
    CompressionOpportunityReport, build_compression_opportunity_report,
)
from psdcalc.reduction.readiness.ac_model import (
    AcModelReadinessResult,
    AcModelReadinessValidator,
)
from psdcalc.reduction.topology.simplifier import VoltageAwareTopologySimplifier
from psdcalc.reduction.topology.parallel_corridors import ParallelCorridorAnalyzer
from psdcalc.reduction.topology.parallel_provenance import (
    ParallelSourceLedger, SourceProfileContract,
)
from psdcalc.reduction.topology.parallel_projection import (
    project_parallel_source_ledger, reject_derived_protected_lines,
)
from psdcalc.reduction.topology.voltage_layers import VoltageLayerClassifier
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.validation.multi_voltage import AggregationInvariantSpec
from psdcalc.reduction.validation.multi_voltage_final import (
    MultiVoltageFinalValidator, MultiVoltageStructuralResult,
    network_persistence_difference, network_semantic_signature,
)


@dataclass(frozen=True)
class MultiVoltagePipelineResult:
    """Persisted final structural result and its authoritative artifacts."""

    final_network_path: Path
    target_sidecar_path: Path
    composed_mappings: MappingBundle
    structural: MultiVoltageStructuralResult
    manifest: RunManifest
    opportunity_report: CompressionOpportunityReport
    records: tuple[ReductionRecord, ...]
    stage_inventories: Mapping[str, StageInventory] | None = None
    phase_runtime: Mapping[str, float] | None = None
    stage_energy: Mapping[str, tuple[EnergyAggregateSummary, ...]] | None = None
    voltage_policy: VoltagePolicyReportSummary | None = None
    voltage_policy_layer_costs: Mapping[str, Mapping[str, int]] | None = None
    protection_costs: Mapping[str, int] | None = None
    ac_readiness: AcModelReadinessResult | None = None


def _write_sidecar(store: ArtifactStore, relative: str,
                   sidecar: IdentitySidecar, artifact: str) -> Path:
    """Persist and register one authoritative identity table."""
    path = store.write_json(relative, sidecar.to_rows())
    store.record_artifact(artifact, relative)
    return path


def _save_maps(store: ArtifactStore, mappings: MappingBundle,
               directory: str, prefix: str = "") -> tuple[str, ...]:
    """Persist complete maps and branch type projections."""
    output = store.path(directory)
    names = mappings.save_parquet(output)
    for name, rows in (("line_map", mappings.line_map),
                       ("transformer_map", mappings.transformer_map)):
        if rows:
            path = output / f"{name}.parquet"
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=output, prefix=f".{name}.", suffix=".parquet",
                    delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                pq.write_table(pa.Table.from_pylist(
                    [row.to_row() for row in rows]), temporary)
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            names[name] = path
    for name, path in names.items():
        store.record_artifact(prefix + name, str(path.relative_to(store.run_dir)))
    return tuple(str(path.relative_to(store.run_dir)) for path in names.values())


def _policy_layer_costs(
    network: pypsa.Network,
    sidecar: IdentitySidecar,
    resolved: ResolvedVoltagePolicy,
    aliases: Mapping[str, float],
) -> dict[str, dict[str, int]]:
    """Count cutoff memberships by canonical layer from source-side policy IDs."""
    layers = VoltageLayerClassifier().classify(
        network, sidecar, aliases=aliases)
    bus_layer: dict[object, str] = {}
    result: dict[str, dict[str, int]] = {}
    for layer in layers:
        layer_buses = set(layer.bus_ids)
        bus_layer.update((bus_id, layer.key) for bus_id in layer.bus_ids)
        retained_buses = len(layer_buses & resolved.retained_bus_ids)
        result[layer.key] = {
            "cutoff_retained_bus_count": retained_buses,
            "cutoff_retained_line_count": 0,
            "cutoff_mandatory_singleton_count": retained_buses,
            "cutoff_identity_preserved_line_count": 0,
        }
    for name, row in network.lines.iterrows():
        line_id = sidecar.source_id("Line", str(name))
        if line_id not in resolved.retained_line_ids:
            continue
        bus0 = sidecar.source_id("Bus", str(row.bus0))
        bus1 = sidecar.source_id("Bus", str(row.bus1))
        layer0 = bus_layer.get(bus0)
        if layer0 is not None and layer0 == bus_layer.get(bus1):
            result[layer0]["cutoff_retained_line_count"] += 1
            result[layer0]["cutoff_identity_preserved_line_count"] += 1
    return result


class MultiVoltageQuotientPipeline:
    """Close topology, energy planning, quotient, persistence, and final Gate."""

    def __init__(self, adapter: PypsaAdapter | None = None,
                 protection_base_dir: Path | None = None,
                 source_ledger: ParallelSourceLedger | None = None) -> None:
        """Accept an adapter, protection base directory, and external ledger."""
        self._adapter = adapter or PypsaAdapter()
        self._protection_base_dir = protection_base_dir
        self._source_ledger = source_ledger

    def run(
        self, *, original_network: pypsa.Network,
        source_sidecar: IdentitySidecar, config: ReductionConfig,
        store: ArtifactStore,
    ) -> MultiVoltagePipelineResult:
        """Run the structural pipeline, recording failures and re-raising them."""
        with store.capture("multi_voltage_pipeline"):
            resolved_input = str(Path(config.input.path).resolve())
            config = config.model_copy(update={
                "input": config.input.model_copy(update={"path": resolved_input}),
            })
            resolved_yaml = yaml.safe_dump(
                config.model_dump(mode="json"),
                allow_unicode=True, sort_keys=True,
            )
            resolved_path = store.write_text(
                "resolved_config.yaml", resolved_yaml)
            store.record_artifact("resolved_config", "resolved_config.yaml")
            config_digest = hash_file(resolved_path)
            store.write_text("resolved_config.sha256", config_digest + "\n")
            store.record_artifact("resolved_config_sha256",
                                  "resolved_config.sha256")
            store.set_metadata(config_hash=config_digest)
            if config.pipeline.materialization_family != "multi_voltage_quotient":
                raise ConfigSemanticError("Pipeline requires multi_voltage_quotient family")
            if config.partition is None or config.partition.target.n_clusters is None:
                raise ConfigSemanticError("Multi-voltage pipeline requires a count target")
            source_path = Path(config.input.path)
            input_hash = hash_path(source_path)
            source_semantics = network_semantic_signature(original_network)
            adapter = self._adapter
            adapter.validate_model(original_network, source_sidecar)
            source_reload = adapter.load(source_path, config.input.format)
            provenance_difference = network_persistence_difference(
                original_network, source_reload)
            if provenance_difference is not None:
                raise SignatureError(
                    "Configured input differs from original_network: "
                    + provenance_difference)
            protection = ProtectionEngine().compile(
                adapter.inventory(original_network, source_sidecar),
                config.protection.rules, profile=config.task.profile,
                base_dir=self._protection_base_dir or source_path.parent,
            )
            voltage_policy = VoltageCutoffPolicy(
                config.voltage_policy.enabled,
                config.voltage_policy.mode,
                config.voltage_policy.cutoff_kv,
            ).resolve(original_network, source_sidecar)
            voltage_policy_request = VoltageCutoffPolicy(
                config.voltage_policy.enabled,
                config.voltage_policy.mode,
                config.voltage_policy.cutoff_kv,
            )
            cutoff_protection = voltage_policy_protection(voltage_policy)
            protection = protection.union(cutoff_protection)
            baseline_protection = protection
            policy = config.aggregation.parallel_corridors.policy
            source_ledger = self._source_ledger or ParallelSourceLedger(
                SourceProfileContract("programmatic_pypsa", "unavailable"))
            if policy == "preserve_protected_members":
                source_ledger.validate(
                    original_network, source_sidecar,
                    voltage_aliases=config.voltage_layers.aliases)
                initial_parallel = ParallelCorridorAnalyzer().analyze(
                    original_network, source_sidecar, protection,
                    voltage_aliases=config.voltage_layers.aliases,
                    lplus_base_lines=frozenset(
                        item.base_line_id for item in source_ledger.l_plus))
                protection = protection.union(ProtectionSet(
                    traced=frozenset(),
                    preserved_components=frozenset(
                        item.shunt_id for item in source_ledger.l_plus) |
                        initial_parallel.protected_lines | frozenset(
                            item.base_line_id for item in source_ledger.l_plus),
                    terminal_buses=frozenset(),
                    singleton_buses=initial_parallel.mandatory_singletons,
                    cannot_link=frozenset(),
                ))
            store.set_metadata(
                input_hash=input_hash,
                topology_signature=adapter.topology_signature(
                    original_network, source_sidecar),
                seed=config.run.seed,
                parallel_corridor_policy=policy,
                software_versions={"python": sys.version.split()[0],
                                   "pypsa": pypsa.__version__,
                                   "psdcalc-reduction": __version__},
            )
            _write_sidecar(store, "input/source_ids.json", source_sidecar, "source_ids")
            store.path("voltage_policy").mkdir(parents=True, exist_ok=True)
            store.write_json(
                "voltage_policy/resolved_policy.json",
                voltage_policy_evidence(voltage_policy_request, voltage_policy),
            )
            store.record_artifact(
                "voltage_policy_evidence",
                "voltage_policy/resolved_policy.json",
            )
            if policy == "preserve_protected_members":
                source_dir = store.path("input/source_provenance")
                for path in source_ledger.save_parquet(source_dir):
                    store.record_artifact("l0_" + path.stem,
                        str(path.relative_to(store.run_dir)))
            if config.output.save_original_snapshot:
                adapter.export(adapter.clone(original_network),
                               store.path("input/original.nc"), "netcdf")
                store.record_artifact("original_snapshot", "input/original.nc")

            topology_start = perf_counter()
            topology = VoltageAwareTopologySimplifier().simplify(
                original_network, source_sidecar, protection,
                mode="keep_transformer_interface",
            )
            topology_seconds = perf_counter() - topology_start
            stage_inventories: dict[str, StageInventory] = {
                CompressionStage.TOPOLOGY_SIMPLIFIED.value:
                    pypsa_stage_inventory(
                        topology.network, CompressionStage.TOPOLOGY_SIMPLIFIED),
            }
            stage_energy: dict[str, tuple[EnergyAggregateSummary, ...]] = {
                CompressionStage.TOPOLOGY_SIMPLIFIED.value:
                    collect_energy_aggregates(
                        topology.network, CompressionStage.TOPOLOGY_SIMPLIFIED),
            }
            adapter.export(topology.network, store.path("levels/L1.nc"), "netcdf")
            store.record_artifact("simplified_network", "levels/L1.nc")
            _save_maps(store, topology.mappings, "maps/topology", "topology_")
            store.write_json("validation/topology_structural.json",
                             topology.structural.to_dict())
            store.record_artifact("topology_structural", "validation/topology_structural.json")
            for name, value in (
                ("voltage_layer_map", topology.voltage_layer_map),
                ("topology_roles", topology.topology_roles),
                ("anchors", [sid.encode() for sid in topology.anchors]),
                ("absorbed_branches", [sid.encode() for sid in topology.absorbed_branches]),
                ("moved_devices", [sid.encode() for sid in topology.moved_devices]),
            ):
                store.write_json(f"validation/{name}.json", value)
                store.record_artifact(name, f"validation/{name}.json")
            if topology.structural.status != "STRUCTURALLY_VALID":
                raise SignatureError("Topology simplification failed its structural Gate")
            parent_sidecar = adapter.identity_sidecar(
                topology.network, config.input.source_namespace)
            _write_sidecar(store, "input/simplified_ids.json", parent_sidecar,
                           "simplified_ids")
            loaded_parent = IdentitySidecar.from_rows(json.loads(
                store.path("input/simplified_ids.json").read_text(encoding="utf-8")))
            if loaded_parent.to_rows() != parent_sidecar.to_rows():
                raise SignatureError("Simplified identity sidecar failed readback")
            if policy == "preserve_protected_members":
                projected = None
                if source_ledger.profile.name in {"BPA10056814", "BPA10056819"}:
                    projected = project_parallel_source_ledger(
                        source_ledger, topology.mappings, original_network,
                        source_sidecar, topology.network, loaded_parent,
                        protected_lines=initial_parallel.protected_lines)
                    l1_dir = store.path("input/l1_provenance")
                    for path in projected.ledger.save_parquet(l1_dir):
                        store.record_artifact("l1_" + path.stem,
                            str(path.relative_to(store.run_dir)))
                    lineage_path = l1_dir / "topology_lineage.parquet"
                    pq.write_table(pa.Table.from_pylist(
                        [row.to_row() for row in projected.lineage]), lineage_path)
                    if tuple(MapEntry.from_row(row) for row in
                             pq.read_table(lineage_path).to_pylist()) != projected.lineage:
                        raise MappingError("L1 topology lineage changed on Parquet readback")
                    store.record_artifact("l1_topology_lineage",
                        "input/l1_provenance/topology_lineage.parquet")
                    store.write_json("input/l1_provenance/projection_summary.json",
                                     projected.summary)
                    store.record_artifact("l1_projection_summary",
                        "input/l1_provenance/projection_summary.json")
                    source_ledger = projected.ledger
                else:
                    source_ledger.validate(topology.network, loaded_parent,
                                           voltage_aliases=config.voltage_layers.aliases)
                parent_parallel = ParallelCorridorAnalyzer().analyze(
                    topology.network, loaded_parent, protection,
                    voltage_aliases=config.voltage_layers.aliases,
                    lplus_base_lines=frozenset(
                        item.base_line_id for item in source_ledger.l_plus))
                if projected is not None:
                    reject_derived_protected_lines(
                        projected, parent_parallel.protected_lines)
                protection = ProtectionSet(
                    protection.traced, protection.preserved_components,
                    protection.terminal_buses,
                    protection.singleton_buses | parent_parallel.mandatory_singletons,
                    protection.cannot_link,
                )

            target = config.partition.target
            planning_start = perf_counter()
            plan = VoltageLayerEnergyPlanner().plan(
                topology.network, loaded_parent, protection,
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
            planning_seconds = perf_counter() - planning_start
            store.write_json("input/plan.json", {
                "requested_cluster_count": plan.requested_cluster_count,
                "planned_cluster_count": len(set(plan.stable_busmap.values())),
                "actual_cluster_count": plan.actual_cluster_count,
                "busmap": {sid.encode(): target_sid.encode()
                           for sid, target_sid in plan.stable_busmap.items()},
            })
            store.record_artifact("plan", "input/plan.json")
            invariants = tuple(AggregationInvariantSpec(**item.model_dump())
                               for item in config.aggregation.invariants)
            builder_start = perf_counter()
            built = MultiVoltageQuotientBuilder().build(
                topology.network, loaded_parent, plan, config.aggregation,
                protection, config.voltage_layers.aliases, invariants,
                source_ledger=source_ledger,
            )
            builder_seconds = perf_counter() - builder_start
            stage_inventories[CompressionStage.QUOTIENT_CANDIDATE.value] = (
                pypsa_stage_inventory(
                    built.network, CompressionStage.QUOTIENT_CANDIDATE))
            stage_energy[CompressionStage.QUOTIENT_CANDIDATE.value] = (
                collect_energy_aggregates(
                    built.network, CompressionStage.QUOTIENT_CANDIDATE))
            persistence_start = perf_counter()
            persisted_ledger = None
            if policy == "preserve_protected_members":
                ledger_dir = store.path("input/parallel_provenance")
                for path in source_ledger.save_parquet(ledger_dir):
                    store.record_artifact(
                        path.stem, str(path.relative_to(store.run_dir)))
                persisted_ledger = ParallelSourceLedger.load_parquet(ledger_dir)
            store.write_json("validation/preliminary.json", {
                "stage": built.preliminary.stage,
                "finalized": built.preliminary.finalized,
                "parallel_corridor_policy": built.preliminary.parallel_corridor_policy,
                "checks": dict(built.preliminary.checks),
                "details": dict(built.preliminary.details),
            })
            store.record_artifact("preliminary", "validation/preliminary.json")
            store.write_json("validation/quotient_voltage_layers.json", {
                sid.encode(): {"source_v_nom": raw, "canonical_v_nom": canonical}
                for sid, (raw, canonical) in built.voltage_layer_map.items()
            })
            store.record_artifact("quotient_voltage_layers",
                                  "validation/quotient_voltage_layers.json")
            quotient_files = _save_maps(
                store, built.mappings, "maps/quotient", "quotient_")
            persisted_quotient = MappingBundle.load_parquet(
                store.path("maps/quotient"),
                expected_files=(Path(path).stem for path in quotient_files),
            )
            quotient_maps_match = (
                persisted_quotient.canonical_rows() ==
                built.mappings.canonical_rows())
            if not quotient_maps_match and policy != "preserve_protected_members":
                raise MappingError("Quotient mapping changed during Parquet readback")
            composed = MappingBundle(
                compose_maps(topology.mappings.bus_map, built.mappings.bus_map),
                compose_maps(topology.mappings.branch_map, built.mappings.branch_map),
                compose_maps(topology.mappings.device_map, built.mappings.device_map),
                compose_maps(topology.mappings.control_map, built.mappings.control_map),
            )
            map_files = _save_maps(store, composed, "maps")
            persisted_composed = MappingBundle.load_parquet(
                store.path("maps"),
                expected_files=(Path(path).stem for path in map_files),
            )
            composed_maps_match = (
                persisted_composed.canonical_rows() == composed.canonical_rows())
            if not composed_maps_match and policy != "preserve_protected_members":
                raise MappingError("Composed mapping changed during Parquet readback")
            final_path = store.path("levels/L2.nc")
            adapter.export(built.network, final_path, "netcdf")
            store.record_artifact("quotient", "levels/L2.nc")
            store.set_metadata(output_hash=hash_file(final_path))
            final_sidecar_path = _write_sidecar(
                store, "input/target_ids.json", built.target_sidecar, "target_ids")
            reloaded = adapter.load(final_path, "netcdf")
            target_sidecar = IdentitySidecar.from_rows(json.loads(
                final_sidecar_path.read_text(encoding="utf-8")))
            sidecar_match = (
                target_sidecar.to_rows() == built.target_sidecar.to_rows())
            if not sidecar_match and policy != "preserve_protected_members":
                raise SignatureError("Target identity sidecar changed during readback")
            if hash_path(source_path) != input_hash:
                raise SignatureError("Original input SHA changed during pipeline")
            policy_request_from_disk: VoltageCutoffPolicy | None = None
            policy_evidence_from_disk: Mapping[str, object] | None = None
            policy_source_from_disk: pypsa.Network | None = None
            policy_sidecar_from_disk: IdentitySidecar | None = None
            policy_round_trip_ready = False
            try:
                resolved_config_path = store.path("resolved_config.yaml")
                resolved_config_sha_path = store.path("resolved_config.sha256")
                resolved_config_sha = resolved_config_sha_path.read_text(
                    encoding="utf-8").strip()
                disk_config = yaml.safe_load(
                    resolved_config_path.read_text(encoding="utf-8"))
                disk_voltage_policy = disk_config["voltage_policy"]
                policy_request_from_disk = VoltageCutoffPolicy(
                    enabled=disk_voltage_policy["enabled"],
                    mode=disk_voltage_policy["mode"],
                    cutoff_kv=disk_voltage_policy["cutoff_kv"],
                )
                source_sidecar_from_disk = IdentitySidecar.from_rows(json.loads(
                    store.path("input/source_ids.json").read_text(
                        encoding="utf-8")))
                policy_source_from_disk = adapter.load(
                    source_path, config.input.format)
                policy_sidecar_from_disk = source_sidecar_from_disk
                policy_evidence_from_disk = json.loads(
                    store.path("voltage_policy/resolved_policy.json").read_text(
                        encoding="utf-8"))
                policy_round_trip_ready = (
                    hash_file(resolved_config_path) == resolved_config_sha
                    == store.manifest.config_hash
                    and source_sidecar_from_disk.to_rows() == source_sidecar.to_rows()
                    and network_persistence_difference(
                        original_network, policy_source_from_disk) is None
                    and composed_maps_match and sidecar_match
                    and final_path.is_file()
                    and policy_evidence_from_disk is not None
                )
            except (OSError, ValueError, TypeError, KeyError,
                    ConfigSemanticError, BackendCapabilityError):
                policy_round_trip_ready = False
            structural = MultiVoltageFinalValidator().validate(
                topology.network, reloaded, loaded_parent, target_sidecar,
                persisted_quotient, invariants, protection,
                voltage_layer_map=built.voltage_layer_map,
                original_network=original_network,
                original_sidecar=source_sidecar,
                composed_mappings=persisted_composed,
                original_input_signature=source_semantics,
                built_network=built.network,
                parallel_plan=built.parallel_plan,
                source_ledger=built.source_ledger,
                persisted_ledger=persisted_ledger,
                parallel_mapping_round_trip=(quotient_maps_match and
                                             composed_maps_match),
                parallel_sidecar_round_trip=sidecar_match,
                parallel_corridor_policy=policy,
                voltage_policy_request=policy_request_from_disk,
                voltage_policy_persisted=policy_evidence_from_disk,
                voltage_policy_source_network=policy_source_from_disk,
                voltage_policy_source_sidecar=policy_sidecar_from_disk,
                voltage_policy_round_trip_ready=policy_round_trip_ready,
            )
            persist_reload_gate_seconds = perf_counter() - persistence_start
            try:
                stage_inventories[CompressionStage.FINAL_RELOADED.value] = (
                    pypsa_stage_inventory(
                        reloaded, CompressionStage.FINAL_RELOADED))
                stage_energy[CompressionStage.FINAL_RELOADED.value] = (
                    collect_energy_aggregates(
                        reloaded, CompressionStage.FINAL_RELOADED))
            except BackendCapabilityError:
                if structural.status == "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID":
                    raise
                stage_inventories[CompressionStage.FINAL_RELOADED.value] = StageInventory(
                    CompressionStage.FINAL_RELOADED,
                    bus_count=len(reloaded.buses), line_count=len(reloaded.lines),
                    transformer_count=len(reloaded.transformers),
                    generator_count=len(reloaded.generators), load_count=len(reloaded.loads),
                    shunt_count=len(reloaded.shunt_impedances),
                    link_count=len(reloaded.links),
                    storage_unit_count=len(reloaded.storage_units),
                    store_count=len(reloaded.stores))
                stage_energy[CompressionStage.FINAL_RELOADED.value] = ()
            store.write_json("validation/multi_voltage_structural.json", {
                "status": structural.status, "checks": dict(structural.checks),
                "details": dict(structural.details),
                "parallel_corridor_policy": structural.parallel_corridor_policy,
            })
            store.record_artifact("multi_voltage_structural",
                                  "validation/multi_voltage_structural.json")
            ac_readiness = AcModelReadinessValidator().validate(
                original_network, source_sidecar, reloaded, target_sidecar,
                persisted_composed,
            )
            store.write_json(
                "validation/ac_model_readiness.json", ac_readiness.to_dict())
            store.record_artifact(
                "ac_model_readiness", "validation/ac_model_readiness.json")
            layers = VoltageLayerClassifier().classify(
                topology.network, loaded_parent,
                aliases=config.voltage_layers.aliases)
            requested_by_layer = MultiVoltageBusmapValidator().allocate_targets(
                topology.network, loaded_parent, layers, protection,
                target.n_clusters, target.per_layer)
            layer_counts = {
                key: (requested_by_layer[key], *built.layer_cluster_counts[key])
                for key in requested_by_layer
            }
            report_parallel_plan = built.parallel_plan or (
                ParallelCorridorAnalyzer().analyze(
                    topology.network, loaded_parent, protection,
                    voltage_aliases=config.voltage_layers.aliases))
            opportunity = build_compression_opportunity_report(
                original_network, source_sidecar, topology, reloaded,
                persisted_composed, protection,
                layer_counts=layer_counts,
                voltage_aliases=config.voltage_layers.aliases,
                parallel_plan=report_parallel_plan,
                source_ledger=built.source_ledger,
                quotient_mappings=persisted_quotient,
                final_structural_status=structural.status,
                baseline_protection=baseline_protection,
                parallel_corridor_policy=policy,
                structural_checks=structural.checks,
                protected_object_results=structural.protected_object_results,
            )
            store.write_json("validation/opportunity.json", opportunity.__dict__)
            store.record_artifact("opportunity", "validation/opportunity.json")
            final_record = ReductionRecord(
                operation_id="mvq-final", operation_type="aggregation",
                parent_grid_hash=input_hash,
                child_grid_hash=hash_file(final_path),
                input_signature=source_semantics,
                output_signature=network_semantic_signature(reloaded),
                parameters={
                    "requested_cluster_count": built.requested_cluster_count,
                    "planned_cluster_count": built.planned_cluster_count,
                    "actual_cluster_count": built.actual_cluster_count,
                    "final_structural_status": structural.status,
                    "netcdf_round_trip": True,
                    "parallel_corridor_policy": policy,
                },
                removed_objects=tuple(row.source_id.encode()
                                      for row in persisted_composed.branch_map
                                      if row.mapping_type == "absorbed_internal"),
                mapping_files=map_files,
            )
            records = topology.records + built.records + (final_record,)
            for record in records:
                relative = f"records/{record.operation_id}.json"
                store.write_json(relative, record.model_dump(mode="json"))
                store.record_artifact(record.operation_id, relative)
            store.set_multi_voltage_result(
                structural.status == "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID")
            report_policy: VoltagePolicyReportSummary | None = None
            report_resolution: ResolvedVoltagePolicy | None = None
            policy_layer_costs: Mapping[str, Mapping[str, int]] = {}
            if (policy_request_from_disk is not None
                    and policy_source_from_disk is not None
                    and policy_sidecar_from_disk is not None):
                report_resolution = policy_request_from_disk.resolve(
                    policy_source_from_disk, policy_sidecar_from_disk)
                report_policy = VoltagePolicyReportSummary(
                    policy_id=report_resolution.policy_id,
                    enabled=policy_request_from_disk.enabled,
                    mode=policy_request_from_disk.mode,
                    requested_cutoff_kv=report_resolution.requested_cutoff_kv,
                    source_voltage_layers=report_resolution.source_voltage_layers,
                    retained_voltage_layers=report_resolution.retained_voltage_layers,
                    compressible_voltage_layers=(
                        report_resolution.compressible_voltage_layers),
                    retained_bus_count=report_resolution.retained_bus_count,
                    retained_line_count=report_resolution.retained_line_count,
                    resolved_policy_fingerprint=(
                        report_resolution.resolved_policy_fingerprint),
                    cutoff_mandatory_singleton_count=(
                        report_resolution.retained_bus_count),
                    cutoff_identity_preserved_line_count=(
                        report_resolution.retained_line_count),
                    round_trip_validated=structural.checks.get(
                        "voltage_policy_round_trip", False),
                )
                policy_layer_costs = _policy_layer_costs(
                    policy_source_from_disk, policy_sidecar_from_disk,
                    report_resolution, config.voltage_layers.aliases)
            parallel_line_ids = set(report_parallel_plan.protected_lines)
            l_plus_line_ids = {
                item.base_line_id for item in (built.source_ledger.l_plus
                                                if built.source_ledger else ())
            }
            cutoff_line_ids = (
                set(report_resolution.retained_line_ids)
                if report_resolution is not None else set())
            configured_line_ids = {
                item for item in protection.preserved_components
                if item.component == "Line"
            }
            unique_protected_lines = (parallel_line_ids | l_plus_line_ids
                                      | cutoff_line_ids | configured_line_ids)
            unique_protected_buses = (
                set(protection.singleton_buses)
                | set(report_parallel_plan.mandatory_singletons)
                | (set(report_resolution.retained_bus_ids)
                   if report_resolution is not None else set())
            )
            protection_costs = {
                "unique_protected_bus_count": len(unique_protected_buses),
                "unique_protected_line_count": len(unique_protected_lines),
                "cutoff_retained_bus_count": (report_policy.retained_bus_count
                                               if report_policy else 0),
                "cutoff_retained_line_count": (report_policy.retained_line_count
                                                if report_policy else 0),
                "cutoff_mandatory_singleton_membership_count": (
                    report_policy.cutoff_mandatory_singleton_count
                    if report_policy else 0),
                "cutoff_identity_preserved_line_membership_count": (
                    report_policy.cutoff_identity_preserved_line_count
                    if report_policy else 0),
                "parallel_protected_line_membership_count": len(parallel_line_ids),
                "l_plus_base_line_membership_count": len(l_plus_line_ids),
            }
            return MultiVoltagePipelineResult(
                final_path, final_sidecar_path, persisted_composed, structural,
                store.manifest, opportunity, records, stage_inventories,
                {"topology": topology_seconds, "planning": planning_seconds,
                 "builder": builder_seconds,
                 "persist_reload_gate": persist_reload_gate_seconds},
                stage_energy,
                report_policy,
                policy_layer_costs,
                protection_costs,
                ac_readiness,
            )
