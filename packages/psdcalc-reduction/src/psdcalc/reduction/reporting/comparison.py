"""Anonymous five-stage candidate and accepted structural comparison contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

import pypsa

from psdcalc.reduction.metrics.stage_inventory import (
    CompressionStage, StageInventory, pypsa_stage_inventory,
)
from psdcalc.reduction.mapping.bundle import MappingBundle

if TYPE_CHECKING:
    from psdcalc.reduction.pipelines.multi_voltage import MultiVoltagePipelineResult


_VALID = "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"
_INVALID = "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID"
_PHYSICAL = "PHYSICAL_FIDELITY_NOT_YET_EVALUATED"
_PUBLIC_ID = re.compile(r"[A-Za-z0-9_.-]+\Z")


@dataclass(frozen=True)
class VoltageLayerComparison:
    """Anonymous Bus counts and structural anchors for one canonical layer."""

    voltage_kv: float
    source_bus_count: int | None
    projected_bus_count: int
    simplified_bus_count: int
    candidate_bus_count: int
    final_bus_count: int
    mandatory_singleton_count: int
    ring_endpoint_count: int
    transformer_endpoint_count: int
    shunt_endpoint_count: int
    control_endpoint_count: int
    parallel_singleton_endpoint_count: int
    requested_cluster_count: int | None
    planned_cluster_count: int | None
    actual_cluster_count: int | None
    minimum_required_bus_count: int
    candidate_compression_fraction: float
    accepted_compression_fraction: float | None


@dataclass(frozen=True)
class DeviceTransitionSummary:
    """Mapping-derived counts for one component class."""

    component: str
    source_count: int
    target_count: int
    preserved_count: int
    aggregated_count: int
    absorbed_internal_count: int
    moved_count: int
    mapping_coverage_fraction: float


_MAPPED_COMPONENTS = (
    "Bus", "Line", "Transformer", "Link", "Generator", "Load",
    "ShuntImpedance", "StorageUnit", "Store",
)


def summarize_device_transitions(
    mappings: MappingBundle, source_counts: Mapping[str, int],
    target_counts: Mapping[str, int],
) -> tuple[DeviceTransitionSummary, ...]:
    """Count each source object once from authoritative composed map rows."""
    rows = mappings.bus_map + mappings.branch_map + mappings.device_map
    result: list[DeviceTransitionSummary] = []
    for component in _MAPPED_COMPONENTS:
        expected = source_counts.get(component, 0)
        target = target_counts.get(component, 0)
        if expected < 0 or target < 0:
            raise ValueError("Negative component inventory")
        members = [row for row in rows if row.source_id.component == component]
        if (len(members) > expected or
                len({row.source_id for row in members}) != len(members)):
            raise ValueError("Duplicate or excess source mapping rows")
        result.append(DeviceTransitionSummary(
            component, expected, target,
            sum(row.mapping_type == "preserved" for row in members),
            sum(row.mapping_type in {"aggregated", "clustered"} for row in members),
            sum(row.mapping_type == "absorbed_internal" for row in members),
            sum(row.mapping_type == "moved" for row in members),
            len(members) / expected if expected else 1.0,
        ))
    return tuple(result)


@dataclass(frozen=True)
class EnergyAggregateSummary:
    """Inventory or aggregation statistic, never a power-flow result."""

    stage: CompressionStage
    scope: str
    voltage_kv: float | None
    source_component: int | None
    region: str | None
    load_count: int
    load_p_set: float | None
    load_q_set: float | None
    generator_count: int
    generator_p_nom: float | None
    generator_p_set: float | None
    shunt_count: int
    shunt_g: float | None
    shunt_b: float | None
    weighted_load_p: float | None = None
    weighted_load_q: float | None = None
    weighted_generation_p: float | None = None
    statistic_kind: str = "AGGREGATION/INVENTORY STATISTIC NOT AC POWER-FLOW FIDELITY"


@dataclass(frozen=True)
class CompressionComparisonReport:
    """One canonical report object used by all renderers and artifacts."""

    model_id: str
    run_id: str
    config_sha256: str
    code_sha: str
    tree_sha: str
    parallel_corridor_policy: str
    final_structural_status: str
    physical_fidelity_status: str
    stages: Mapping[str, StageInventory]
    voltage_layers: tuple[VoltageLayerComparison, ...] = ()
    device_transitions: tuple[DeviceTransitionSummary, ...] = ()
    energy_aggregates: tuple[EnergyAggregateSummary, ...] = ()
    topology: Mapping[str, object] = field(default_factory=dict)
    parallel_corridors: Mapping[str, int] = field(default_factory=dict)
    mappings: Mapping[str, object] = field(default_factory=dict)
    runtime: Mapping[str, float | int | str] = field(default_factory=dict)
    failed_checks: tuple[str, ...] = ()
    candidate_bus_count: int | None = None
    candidate_line_count: int | None = None
    candidate_bus_compression_fraction: float | None = None
    candidate_line_compression_fraction: float | None = None
    accepted_final_bus_count: int | None = None
    accepted_final_line_count: int | None = None
    accepted_bus_compression_fraction: float | None = None
    accepted_line_compression_fraction: float | None = None

    @classmethod
    def create(
        cls, *, model_id: str, run_id: str, config_sha256: str,
        code_sha: str, tree_sha: str, parallel_corridor_policy: str,
        final_structural_status: str, physical_fidelity_status: str,
        stages: Mapping[str, StageInventory],
        failed_checks: tuple[str, ...],
        voltage_layers: tuple[VoltageLayerComparison, ...] = (),
        device_transitions: tuple[DeviceTransitionSummary, ...] = (),
        energy_aggregates: tuple[EnergyAggregateSummary, ...] = (),
        topology: Mapping[str, object] | None = None,
        parallel_corridors: Mapping[str, int] | None = None,
        mappings: Mapping[str, object] | None = None,
        runtime: Mapping[str, float | int | str] | None = None,
    ) -> "CompressionComparisonReport":
        """Derive candidate and accepted counts exclusively from stage and Gate evidence."""
        if not _PUBLIC_ID.fullmatch(model_id) or not _PUBLIC_ID.fullmatch(run_id):
            raise ValueError("Invalid public identifier")
        if (not re.fullmatch(r"[0-9a-f]{64}", config_sha256) or
                not re.fullmatch(r"[0-9a-f]{40}", code_sha) or
                not re.fullmatch(r"[0-9a-f]{40}", tree_sha)):
            raise ValueError("Invalid public evidence SHA")
        expected = tuple(stage.value for stage in CompressionStage)
        if tuple(stages) != expected or any(
                stages[key].stage.value != key for key in expected):
            raise ValueError("Report requires five ordered stages")
        if final_structural_status not in {_VALID, _INVALID}:
            raise ValueError("Unsupported final structural status")
        if physical_fidelity_status != _PHYSICAL:
            raise ValueError("Unsupported physical fidelity status")
        if final_structural_status == _VALID and failed_checks:
            raise ValueError("Valid report cannot contain failed checks")
        source, candidate, final = (stages[expected[index]]
                                     for index in (0, 3, 4))
        if (source.bus_count is None or source.line_count is None or
                candidate.bus_count is None or candidate.line_count is None or
                final.bus_count is None or final.line_count is None or
                source.bus_count <= 0 or source.line_count <= 0):
            raise ValueError("Missing source or candidate comparison counts")
        valid = final_structural_status == _VALID
        if valid and (candidate.bus_count != final.bus_count or
                      candidate.line_count != final.line_count):
            raise ValueError("Accepted network differs from candidate counts")
        if voltage_layers:
            stage_counts = (
                ("source_bus_count", source.bus_count),
                ("projected_bus_count", stages[expected[1]].bus_count),
                ("simplified_bus_count", stages[expected[2]].bus_count),
                ("candidate_bus_count", candidate.bus_count),
                ("final_bus_count", final.bus_count),
            )
            if any(sum(getattr(layer, name) or 0 for layer in voltage_layers)
                   != count for name, count in stage_counts):
                raise ValueError("Voltage layer sums differ from global stages")
            if any((layer.accepted_compression_fraction is None) == valid
                   for layer in voltage_layers):
                raise ValueError("Voltage layer accepted status differs from final Gate")
        return cls(
            model_id, run_id, config_sha256, code_sha, tree_sha,
            parallel_corridor_policy, final_structural_status,
            physical_fidelity_status, stages, voltage_layers,
            device_transitions, energy_aggregates, topology or {},
            parallel_corridors or {}, mappings or {}, runtime or {},
            tuple(sorted(failed_checks)),
            candidate.bus_count, candidate.line_count,
            (source.bus_count - candidate.bus_count) / source.bus_count,
            (source.line_count - candidate.line_count) / source.line_count,
            final.bus_count if valid else None,
            final.line_count if valid else None,
            (source.bus_count - final.bus_count) / source.bus_count if valid else None,
            (source.line_count - final.line_count) / source.line_count if valid else None,
        )

    def to_dict(self) -> dict[str, object]:
        """Return serializable aggregate evidence without raw source rows."""
        result = asdict(self)
        result["stages"] = {key: asdict(value) for key, value in self.stages.items()}
        return result

    def to_json(self) -> str:
        """Render deterministic public JSON from this report object."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"


def build_pipeline_comparison(
    *, model_id: str, source_stage: StageInventory,
    projected_network: pypsa.Network, result: MultiVoltagePipelineResult,
    config_sha256: str, code_sha: str, tree_sha: str,
    external_runtime: Mapping[str, float | int | str] | None = None,
) -> CompressionComparisonReport:
    """Combine source, observed Pipeline stages, maps and final Gate evidence."""
    from psdcalc.reduction.metrics.energy import collect_energy_aggregates

    if source_stage.stage != CompressionStage.PSD_BPA_SOURCE:
        raise ValueError("S0 must come from the audited source view")
    if result.stage_inventories is None or result.stage_energy is None:
        raise ValueError("Pipeline did not capture S2–S4 stage evidence")
    stages = {CompressionStage.PSD_BPA_SOURCE.value: source_stage,
              CompressionStage.PYPSA_FULL_PROJECTION.value:
                  pypsa_stage_inventory(
                      projected_network, CompressionStage.PYPSA_FULL_PROJECTION)}
    stages.update(result.stage_inventories)
    status = result.structural.status
    valid = status == _VALID
    opportunity = result.opportunity_report
    layers: list[VoltageLayerComparison] = []
    for key, counts in sorted(opportunity.per_layer.items(),
                              key=lambda pair: float(pair[0])):
        source_count = counts["source_bus_count"]
        candidate_count = counts["actual_cluster_count"]
        if candidate_count is None:
            candidate_count = counts["final_bus_count"]
        if source_count is None or candidate_count is None or source_count <= 0:
            raise ValueError("Voltage layer lacks source/candidate counts")
        final_count = counts["final_bus_count"]
        if final_count is None:
            raise ValueError("Voltage layer lacks final count")
        layers.append(VoltageLayerComparison(
            float(key), source_count, source_count,
            int(counts["simplified_bus_count"]), candidate_count, final_count,
            int(counts["mandatory_singleton_count"]),
            int(counts["ring_endpoint_count"]),
            int(counts["transformer_endpoint_count"]),
            int(counts["shunt_endpoint_count"]),
            int(counts["control_endpoint_count"]),
            int(counts["parallel_singleton_endpoint_count"]),
            counts["requested_cluster_count"], counts["planned_cluster_count"],
            counts["actual_cluster_count"],
            int(counts["minimum_required_bus_count"]),
            (source_count - candidate_count) / source_count,
            ((source_count - final_count) / source_count) if valid else None,
        ))
    source_counts = {
        "Bus": len(projected_network.buses), "Line": len(projected_network.lines),
        "Transformer": len(projected_network.transformers),
        "Link": len(projected_network.links),
        "Generator": len(projected_network.generators),
        "Load": len(projected_network.loads),
        "ShuntImpedance": len(projected_network.shunt_impedances),
        "StorageUnit": len(projected_network.storage_units),
        "Store": len(projected_network.stores),
    }
    final_stage = stages[CompressionStage.FINAL_RELOADED.value]
    target_counts = dict(zip(_MAPPED_COMPONENTS, (
        final_stage.bus_count, final_stage.line_count,
        final_stage.transformer_count, final_stage.link_count,
        final_stage.generator_count, final_stage.load_count,
        final_stage.shunt_count, final_stage.storage_unit_count,
        final_stage.store_count,
    )))
    if any(value is None for value in target_counts.values()):
        raise ValueError("Final stage has incomplete component inventory")
    transitions = summarize_device_transitions(
        result.composed_mappings, source_counts, target_counts)
    energy = (collect_energy_aggregates(
        projected_network, CompressionStage.PYPSA_FULL_PROJECTION) +
        tuple(row for stage in list(CompressionStage)[2:]
              for row in result.stage_energy[stage.value]))
    topology = {f"{stage.value}_{field}": getattr(stages[stage.value], field)
                for stage in list(CompressionStage)[1:]
                for field in ("bridge_count", "articulation_count",
                              "two_core_bus_count", "cycle_edge_count",
                              "simple_cycle_rank", "multigraph_cycle_rank")}
    parallel_fields = (
        "parallel_corridor_count", "double_circuit_corridor_count",
        "three_or_more_corridor_count", "parallel_only_cycle_count",
        "parallel_multiplicity_on_simple_cycle_count",
        "protected_parallel_corridor_count", "protected_active_member_count",
        "protected_inactive_member_count", "protected_total_member_count",
        "ordinary_aggregated_parallel_corridor_count",
        "detached_member_count", "reconnected_member_count",
        "preserved_mapping_count", "line_semantic_hash_pass_count",
        "line_semantic_hash_failure_count", "l_plus_association_count",
        "l_plus_shunt_preserved_count", "l_plus_association_pass_count",
        "l_plus_association_failure_count",
    )
    parallel = {field: getattr(opportunity, field) for field in parallel_fields}
    runtime: dict[str, float | int | str] = {
        key: "NOT_AVAILABLE" for key in (
            "t_card_scan", "t_psdcase_parse", "t_psdcase_projection",
            "t_parallel_analysis", "t_detach", "t_quotient", "t_reconnect",
            "t_report", "t_total", "peak_rss")}
    runtime.update({"t_" + key: value for key, value in
                    (result.phase_runtime or {}).items()})
    runtime.update(external_runtime or {})
    return CompressionComparisonReport.create(
        model_id=model_id, run_id=result.manifest.run_id,
        config_sha256=config_sha256, code_sha=code_sha, tree_sha=tree_sha,
        parallel_corridor_policy=opportunity.parallel_corridor_policy,
        final_structural_status=status, physical_fidelity_status=_PHYSICAL,
        stages=stages, voltage_layers=tuple(layers),
        device_transitions=transitions, energy_aggregates=energy,
        topology=topology, parallel_corridors=parallel,
        mappings={"source_rows": sum(source_counts.values()),
                  "covered_rows": sum(row.source_count * row.mapping_coverage_fraction
                                      for row in transitions)},
        runtime=runtime,
        failed_checks=tuple(name for name, passed in result.structural.checks.items()
                            if not passed),
    )
