"""Deterministic structural compression opportunity statistics."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import MappingError
from psdcalc.reduction.planners.multi_voltage_busmap import MultiVoltageBusmapValidator
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.cycle_corridors import analyze_cycle_corridors
from psdcalc.reduction.topology.simplifier import TopologySimplificationResult
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


def build_compression_opportunity_report(
    original_network: pypsa.Network, source_sidecar: IdentitySidecar,
    topology_result: TopologySimplificationResult, final_network: pypsa.Network,
    mappings: MappingBundle, protection: ProtectionSet,
    *, layer_counts: Mapping[str, tuple[int, int, int]] | None = None,
    voltage_aliases: Mapping[float, float] | None = None,
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
    )
