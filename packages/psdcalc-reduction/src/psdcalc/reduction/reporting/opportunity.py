"""Deterministic structural compression opportunity statistics."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field

import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import MappingError
from psdcalc.reduction.planners.multi_voltage_busmap import MultiVoltageBusmapValidator
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.cycle_corridors import analyze_cycle_corridors
from psdcalc.reduction.topology.simplifier import TopologySimplificationResult
from psdcalc.reduction.topology.parallel_corridors import ProtectedParallelPlan
from psdcalc.reduction.topology.parallel_provenance import ParallelSourceLedger
from psdcalc.reduction.topology.voltage_layers import VoltageLayerClassifier


@dataclass(frozen=True)
class CompressionOpportunityReport:
    """Counts and fractions; theoretical maximum is only a structural bound."""

    ring_endpoint_count: int
    transformer_endpoint_count: int
    shunt_endpoint_count: int
    control_endpoint_count: int
    anchor_overlaps: dict[str, int]
    radial_tree_opportunities: int
    series_node_opportunities: int
    rejection_reason_counts: dict[str, int]
    theoretical_maximum_bus_compression_fraction: float
    realized_bus_compression_fraction: float
    source_bus_count: int
    simplified_bus_count: int
    final_bus_count: int
    minimum_required_bus_count: int
    per_layer: dict[str, dict[str, int | None]]
    topology_only_realized_compression_fraction: float
    total_realized_compression_fraction: float
    parallel_corridors_by_multiplicity: dict[str, int] = field(default_factory=dict)
    parallel_cycle_type_counts: dict[str, int] = field(default_factory=dict)
    protected_active_member_count: int = 0
    protected_inactive_member_count: int = 0
    parallel_singleton_endpoint_count: int = 0
    l_plus_associated_line_count: int = 0
    l_plus_associated_shunt_count: int = 0
    ordinary_aggregated_parallel_corridor_count: int = 0
    protected_member_retention_bus_cost: int = 0
    protected_member_retention_line_cost: int = 0
    accepted_structural_compression_fraction: float | None = None
    parallel_corridor_policy: str = "aggregate_corridor"
    parallel_corridor_count: int = 0
    double_circuit_corridor_count: int = 0
    three_or_more_corridor_count: int = 0
    parallel_only_cycle_count: int = 0
    parallel_multiplicity_on_simple_cycle_count: int = 0
    protected_parallel_corridor_count: int = 0
    protected_total_member_count: int = 0
    detached_member_count: int = 0
    reconnected_member_count: int = 0
    preserved_mapping_count: int = 0
    line_semantic_hash_pass_count: int = 0
    line_semantic_hash_failure_count: int = 0
    l_plus_association_count: int = 0
    l_plus_shunt_preserved_count: int = 0
    l_plus_association_pass_count: int = 0
    l_plus_association_failure_count: int = 0


def build_compression_opportunity_report(
    original_network: pypsa.Network, source_sidecar: IdentitySidecar,
    topology_result: TopologySimplificationResult, final_network: pypsa.Network,
    mappings: MappingBundle, protection: ProtectionSet,
    *, layer_counts: Mapping[str, tuple[int, int, int]] | None = None,
    voltage_aliases: Mapping[float, float] | None = None,
    parallel_plan: ProtectedParallelPlan | None = None,
    source_ledger: ParallelSourceLedger | None = None,
    quotient_mappings: MappingBundle | None = None,
    final_structural_status: str | None = None,
    baseline_protection: ProtectionSet | None = None,
    parallel_corridor_policy: str = "aggregate_corridor",
    structural_checks: Mapping[str, bool] | None = None,
) -> CompressionOpportunityReport:
    """Compute a conservative structural floor and observed topology operations.

    The bound uses the planner's active-Line connectivity floor on the
    simplified network and does not
    assert physical fidelity. Opportunity counts describe topology-stage
    candidate groups and series nodes, including rejected radial candidates.
    """
    analysis = analyze_cycle_corridors(original_network)
    ring = {bus for name in analysis.cycle_lines for bus in analysis.line_corridors[name]}
    transformers = {str(row[field]) for _, row in original_network.transformers.iterrows()
                    for field in ("bus0", "bus1")}
    shunts = {str(row.bus) for _, row in original_network.shunt_impedances.iterrows()}
    inventory = PypsaAdapter().inventory(original_network, source_sidecar)
    controls = {source_sidecar.backend_name(bus) for item in inventory.items
                if item.controllable for bus in item.terminals}
    protected = {source_sidecar.backend_name(bus) for bus in
                 protection.terminal_buses | protection.singleton_buses}
    protected.update(source_sidecar.backend_name(bus) for bus in protection.preserved_components
                     if bus.component == "Bus")
    protected.update(source_sidecar.backend_name(bus) for pair in protection.cannot_link for bus in pair)
    anchor_sets = {
        "ring": ring, "transformer": transformers, "shunt": shunts,
        "control": controls, "protected": protected,
    }
    anchor_overlaps = {
        f"{left}_{right}": len(anchor_sets[left] & anchor_sets[right])
        for index, left in enumerate(anchor_sets)
        for right in list(anchor_sets)[index + 1:]
    }
    layers = VoltageLayerClassifier().classify(
        original_network, source_sidecar, voltage_aliases)
    simplified_sidecar = PypsaAdapter().identity_sidecar(
        topology_result.network, source_sidecar.source_id(
            "Bus", str(original_network.buses.index[0])).namespace)
    simplified_layers = VoltageLayerClassifier().classify(
        topology_result.network, simplified_sidecar, voltage_aliases)
    floors = MultiVoltageBusmapValidator().minimum_required_bus_counts(
        topology_result.network, simplified_sidecar, simplified_layers, protection)
    floor = sum(floors.values())
    baseline_floor = floor
    if baseline_protection is not None:
        baseline_floor = sum(
            MultiVoltageBusmapValidator().minimum_required_bus_counts(
                topology_result.network, simplified_sidecar,
                simplified_layers, baseline_protection).values())
    n = len(original_network.buses)
    reasons = Counter(str(role["reason"]) for role in topology_result.topology_roles if "reason" in role)
    radial = sum(str(record.operation_id).startswith("radial-") for record in topology_result.records)
    radial += sum(role.get("role") in {"retained_radial_candidate", "not_single_interface_radial_tree"}
                  for role in topology_result.topology_roles)
    series = sum(sum(StableId.decode(source).component == 'Bus' for source in record.removed_objects)
                 for record in topology_result.records if str(record.operation_id).startswith("series-"))
    expected = {source_sidecar.source_id("Bus", str(bus)) for bus in original_network.buses.index}
    rows = mappings.bus_map
    if (len(rows) != len(expected) or {row.source_id for row in rows} != expected
            or any(row.target_id is None or row.target_id.component != "Bus" for row in rows)):
        raise MappingError("Composed bus map must cover each source Bus exactly once")
    target_count = len({row.target_id for row in rows})
    if target_count != len(final_network.buses):
        raise MappingError("Composed bus map target count differs from final network Bus count")
    topology_rows = topology_result.mappings.bus_map
    if (len(topology_rows) != len(expected) or
            {row.source_id for row in topology_rows} != expected or
            any(row.target_id is None or row.target_id.component != "Bus"
                for row in topology_rows)):
        raise MappingError("Topology bus map must cover each source Bus exactly once")
    simplified_count = len({row.target_id for row in topology_rows})
    if simplified_count != len(topology_result.network.buses):
        raise MappingError("Topology bus map target count differs from simplified Bus count")
    topology_by_source = {row.source_id: row.target_id for row in topology_rows}
    final_by_source = {row.source_id: row.target_id for row in rows}
    if layer_counts is not None and set(layer_counts) != {layer.key for layer in layers}:
        raise MappingError("Layer counts must cover the source voltage layers")
    per_layer: dict[str, dict[str, int | None]] = {}
    for layer in layers:
        requested, planned, actual = (
            layer_counts[layer.key] if layer_counts is not None else (None, None, None)
        )
        per_layer[layer.key] = {
            "source_bus_count": len(layer.bus_ids),
            "simplified_bus_count": len({topology_by_source[bus]
                                         for bus in layer.bus_ids}),
            "requested_cluster_count": requested,
            "planned_cluster_count": planned,
            "actual_cluster_count": actual,
            "final_bus_count": len({final_by_source[bus] for bus in layer.bus_ids}),
        }
    if sum(row["final_bus_count"] for row in per_layer.values()) != target_count:
        raise MappingError("Final Bus targets cross voltage layers")
    parallel_counts = Counter(str(len(item.members))
                              for item in (parallel_plan.corridors
                                           if parallel_plan is not None else ()))
    cycle_counts = Counter(item.cycle_type for item in (
        parallel_plan.corridors if parallel_plan is not None else ())
        if item.cycle_type is not None)
    strict_parallel = parallel_corridor_policy == "preserve_protected_members"
    ordinary_aggregated = 0
    if parallel_plan is not None and quotient_mappings is not None:
        line_targets = {row.source_id: row.target_id
                        for row in quotient_mappings.line_map}
        ordinary_aggregated = sum(
            len({line_targets.get(source) for source in corridor.members})
            < len(corridor.members)
            for corridor in parallel_plan.corridors
            if not strict_parallel or not corridor.protected)
    protected_active = 0
    protected_inactive = 0
    preserved_rows = 0
    reconnected = 0
    if parallel_plan is not None and strict_parallel:
        for line_id in parallel_plan.protected_lines:
            row = topology_result.network.lines.loc[
                simplified_sidecar.backend_name(line_id)]
            if bool(row.get("active", True)):
                protected_active += 1
            else:
                protected_inactive += 1
        if quotient_mappings is not None:
            preserved_rows = sum(
                row.source_id in parallel_plan.protected_lines
                and row.source_id == row.target_id
                and row.mapping_type == "preserved"
                for row in quotient_mappings.line_map)
            reconnected = len({
                row.target_id for row in quotient_mappings.line_map
                if row.source_id in parallel_plan.protected_lines
                and row.target_id is not None})
    protected_count = (len(parallel_plan.protected_lines)
                       if parallel_plan is not None and strict_parallel else 0)
    lplus_count = len(source_ledger.l_plus) if source_ledger is not None else 0
    semantic_ok = bool((structural_checks or {}).get(
        "protected_parallel_line_semantics", False))
    lplus_ok = bool((structural_checks or {}).get(
        "l_plus_association_coverage", False) and
        (structural_checks or {}).get("l_plus_terminal_owner_consistency", False)
        and (structural_checks or {}).get("l_plus_semantic_round_trip", False))
    return CompressionOpportunityReport(
        ring_endpoint_count=len(ring), transformer_endpoint_count=len(transformers),
        shunt_endpoint_count=len(shunts), control_endpoint_count=len(controls),
        anchor_overlaps=anchor_overlaps,
        radial_tree_opportunities=radial, series_node_opportunities=series,
        rejection_reason_counts=dict(sorted(reasons.items())),
        theoretical_maximum_bus_compression_fraction=(n - floor) / n if n else 0.0,
        realized_bus_compression_fraction=(n - target_count) / n if n else 0.0,
        source_bus_count=n, simplified_bus_count=simplified_count,
        final_bus_count=target_count, minimum_required_bus_count=floor,
        per_layer=per_layer,
        topology_only_realized_compression_fraction=(n - simplified_count) / n if n else 0.0,
        total_realized_compression_fraction=(n - target_count) / n if n else 0.0,
        parallel_corridors_by_multiplicity=dict(sorted(parallel_counts.items())),
        parallel_cycle_type_counts=dict(sorted(cycle_counts.items())),
        protected_active_member_count=protected_active,
        protected_inactive_member_count=protected_inactive,
        parallel_singleton_endpoint_count=(
            len(parallel_plan.mandatory_singletons)
            if parallel_plan is not None and strict_parallel else 0),
        l_plus_associated_line_count=(
            len({item.base_line_id for item in source_ledger.l_plus})
            if source_ledger is not None else 0),
        l_plus_associated_shunt_count=(
            len(source_ledger.l_plus) if source_ledger is not None else 0),
        ordinary_aggregated_parallel_corridor_count=ordinary_aggregated,
        protected_member_retention_bus_cost=(
            max(0, floor - baseline_floor) if strict_parallel else 0),
        protected_member_retention_line_cost=(sum(
            len(item.members) - 1 for item in parallel_plan.corridors
            if item.protected) if parallel_plan is not None and strict_parallel
            else 0),
        accepted_structural_compression_fraction=(
            (n - target_count) / n if n else 0.0)
            if final_structural_status ==
            "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID" else None,
        parallel_corridor_policy=parallel_corridor_policy,
        parallel_corridor_count=(len(parallel_plan.corridors)
                                 if parallel_plan is not None else 0),
        double_circuit_corridor_count=sum(
            len(item.members) == 2 for item in
            (parallel_plan.corridors if parallel_plan is not None else ())),
        three_or_more_corridor_count=sum(
            len(item.members) >= 3 for item in
            (parallel_plan.corridors if parallel_plan is not None else ())),
        parallel_only_cycle_count=cycle_counts.get("parallel_only", 0),
        parallel_multiplicity_on_simple_cycle_count=cycle_counts.get("both", 0),
        protected_parallel_corridor_count=sum(
            item.protected for item in
            (parallel_plan.corridors if parallel_plan is not None and
             strict_parallel else ())),
        protected_total_member_count=protected_count,
        detached_member_count=protected_count,
        reconnected_member_count=reconnected,
        preserved_mapping_count=preserved_rows,
        line_semantic_hash_pass_count=(protected_count if semantic_ok else 0),
        line_semantic_hash_failure_count=(0 if semantic_ok else protected_count),
        l_plus_association_count=lplus_count,
        l_plus_shunt_preserved_count=(sum(
            row.source_id in {item.shunt_id for item in source_ledger.l_plus}
            and row.source_id == row.target_id and
            row.mapping_type == "preserved"
            for row in quotient_mappings.device_map)
            if source_ledger is not None and quotient_mappings is not None else 0),
        l_plus_association_pass_count=(lplus_count if lplus_ok else 0),
        l_plus_association_failure_count=(0 if lplus_ok else lplus_count),
    )
