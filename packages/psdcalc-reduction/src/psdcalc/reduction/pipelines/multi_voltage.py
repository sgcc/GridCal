"""Persist, reload, and finally validate the multi-voltage structural chain."""

from dataclasses import dataclass
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import ReductionConfig
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.core.ids import IdentitySidecar
from psdcalc.reduction.core.manifests import RunManifest
from psdcalc.reduction.core.records import ReductionRecord
from psdcalc.reduction.core.signatures import hash_file, hash_path
from psdcalc.reduction.exceptions import ConfigSemanticError, SignatureError
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.compose import compose_maps
from psdcalc.reduction.materializers.multi_voltage import MultiVoltageQuotientBuilder
from psdcalc.reduction.planners.layer_energy import VoltageLayerEnergyPlanner
from psdcalc.reduction.protection.engine import ProtectionEngine
from psdcalc.reduction.reporting.opportunity import (
    CompressionOpportunityReport, build_compression_opportunity_report,
)
from psdcalc.reduction.topology.simplifier import VoltageAwareTopologySimplifier
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


class MultiVoltageQuotientPipeline:
    """Close topology, energy planning, quotient, persistence, and final Gate."""

    def __init__(self, adapter: PypsaAdapter | None = None,
                 protection_base_dir: Path | None = None) -> None:
        """Accept an adapter and base directory for relative protection files."""
        self._adapter = adapter or PypsaAdapter()
        self._protection_base_dir = protection_base_dir

    def run(
        self, *, original_network: pypsa.Network,
        source_sidecar: IdentitySidecar, config: ReductionConfig,
        store: ArtifactStore,
    ) -> MultiVoltagePipelineResult:
        """Run the structural pipeline, recording failures and re-raising them."""
        with store.capture("multi_voltage_pipeline"):
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
            store.set_metadata(
                input_hash=input_hash,
                topology_signature=adapter.topology_signature(
                    original_network, source_sidecar),
                seed=config.run.seed,
                software_versions={"python": sys.version.split()[0],
                                   "pypsa": pypsa.__version__,
                                   "psdcalc-reduction": version("psdcalc-reduction")},
            )
            _write_sidecar(store, "input/source_ids.json", source_sidecar, "source_ids")
            if config.output.save_original_snapshot:
                adapter.export(adapter.clone(original_network),
                               store.path("input/original.nc"), "netcdf")
                store.record_artifact("original_snapshot", "input/original.nc")

            topology = VoltageAwareTopologySimplifier().simplify(
                original_network, source_sidecar, protection,
                mode="keep_transformer_interface",
            )
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

            target = config.partition.target
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
            built = MultiVoltageQuotientBuilder().build(
                topology.network, loaded_parent, plan, config.aggregation,
                protection, config.voltage_layers.aliases, invariants,
            )
            store.write_json("validation/preliminary.json", {
                "stage": built.preliminary.stage,
                "finalized": built.preliminary.finalized,
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
            _save_maps(store, built.mappings, "maps/quotient", "quotient_")
            composed = MappingBundle(
                compose_maps(topology.mappings.bus_map, built.mappings.bus_map),
                compose_maps(topology.mappings.branch_map, built.mappings.branch_map),
                compose_maps(topology.mappings.device_map, built.mappings.device_map),
                compose_maps(topology.mappings.control_map, built.mappings.control_map),
            )
            map_files = _save_maps(store, composed, "maps")
            final_path = store.path("levels/L2.nc")
            adapter.export(built.network, final_path, "netcdf")
            store.record_artifact("quotient", "levels/L2.nc")
            store.set_metadata(output_hash=hash_file(final_path))
            final_sidecar_path = _write_sidecar(
                store, "input/target_ids.json", built.target_sidecar, "target_ids")
            reloaded = adapter.load(final_path, "netcdf")
            target_sidecar = IdentitySidecar.from_rows(json.loads(
                final_sidecar_path.read_text(encoding="utf-8")))
            if hash_path(source_path) != input_hash:
                raise SignatureError("Original input SHA changed during pipeline")
            structural = MultiVoltageFinalValidator().validate(
                topology.network, reloaded, loaded_parent, target_sidecar,
                built.mappings, invariants, protection,
                voltage_layer_map=built.voltage_layer_map,
                original_network=original_network,
                original_sidecar=source_sidecar,
                composed_mappings=composed,
                original_input_signature=source_semantics,
                built_network=built.network,
            )
            store.write_json("validation/multi_voltage_structural.json", {
                "status": structural.status, "checks": dict(structural.checks),
                "details": dict(structural.details),
            })
            store.record_artifact("multi_voltage_structural",
                                  "validation/multi_voltage_structural.json")
            opportunity = build_compression_opportunity_report(
                original_network, source_sidecar, topology, reloaded,
                composed, protection,
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
                },
                removed_objects=tuple(row.source_id.encode()
                                      for row in composed.branch_map
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
            return MultiVoltagePipelineResult(
                final_path, final_sidecar_path, composed, structural,
                store.manifest, opportunity, records,
            )
