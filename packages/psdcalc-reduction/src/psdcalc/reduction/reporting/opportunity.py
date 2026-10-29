"""Deterministic structural compression opportunity statistics."""

from collections import Counter
from dataclasses import dataclass

import networkx as nx
import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import MappingError
from psdcalc.reduction.planners.multi_voltage_busmap import MultiVoltageBusmapValidator
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.cycle_corridors import analyze_cycle_corridors
from psdcalc.reduction.topology.simplifier import TopologySimplificationResult


@dataclass(frozen=True)
class CompressionOpportunityReport:
    """Counts and fractions; theoretical maximum is only a structural bound."""

    ring_endpoint_count: int
    transformer_endpoint_count: int
    control_endpoint_count: int
    radial_tree_opportunities: int
    series_node_opportunities: int
    rejection_reason_counts: dict[str, int]
    theoretical_maximum_bus_compression_fraction: float
    realized_bus_compression_fraction: float


def build_compression_opportunity_report(
    original_network: pypsa.Network, source_sidecar: IdentitySidecar,
    topology_result: TopologySimplificationResult, final_network: pypsa.Network,
    mappings: MappingBundle, protection: ProtectionSet,
) -> CompressionOpportunityReport:
    """Compute a conservative structural floor and observed topology operations.

    The bound ignores placement constraints beyond mandatory singleton buses and
    does not assert physical fidelity. Opportunity counts describe topology-stage
    candidate groups and series nodes, including rejected radial candidates.
    """
    analysis = analyze_cycle_corridors(original_network)
    ring = {bus for name in analysis.cycle_lines for bus in analysis.line_corridors[name]}
    transformers = {str(row[field]) for _, row in original_network.transformers.iterrows()
                    for field in ("bus0", "bus1")}
    inventory = PypsaAdapter().inventory(original_network, source_sidecar)
    controls = {source_sidecar.backend_name(bus) for item in inventory.items
                if item.controllable for bus in item.terminals}
    protected = {source_sidecar.backend_name(bus) for bus in
                 protection.terminal_buses | protection.singleton_buses}
    protected.update(source_sidecar.backend_name(bus) for bus in protection.preserved_components
                     if bus.component == "Bus")
    protected.update(source_sidecar.backend_name(bus) for pair in protection.cannot_link for bus in pair)
    mandatory = {source_sidecar.backend_name(bus) for bus in
                 MultiVoltageBusmapValidator.mandatory_singletons(
                     original_network, source_sidecar, protection)}
    singletons = ring | transformers | controls | protected | mandatory
    graph = analysis.simple_graph
    domains: dict[tuple[float, str, str, int], set[str]] = {}
    components = sorted((tuple(sorted(component)) for component in
                         nx.connected_components(graph)))
    for component_index, component in enumerate(components):
        for bus in component:
            row = original_network.buses.loc[bus]
            subnet, region = MultiVoltageBusmapValidator._domain(original_network, bus)
            key = (float(row.v_nom), subnet, region, component_index)
            domains.setdefault(key, set()).add(bus)
    floor = 0
    for nodes in domains.values():
        fixed = nodes & singletons
        floor += len(fixed) + nx.number_connected_components(graph.subgraph(nodes - fixed))
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
    return CompressionOpportunityReport(
        len(ring), len(transformers), len(controls), radial, series,
        dict(sorted(reasons.items())), (n - floor) / n if n else 0.0,
        (n - target_count) / n if n else 0.0,
    )
