"""Anonymous source and PyPSA stage inventories; active Line graph only."""

from collections import Counter
from dataclasses import dataclass
from enum import Enum

import networkx as nx
import pypsa

from psdcalc.reduction.exceptions import BackendCapabilityError
from psdcalc.reduction.topology.active import branch_is_active
from psdcalc.reduction.readiness.bpa_ac import BpaAcInventory
from psdcalc.reduction.readiness.psdcase_ac import (
    PsdCaseAcLike, PsdCaseAcProjection,
)


class CompressionStage(str, Enum):
    """Five ordered source-to-reloaded structural stages."""

    PSD_BPA_SOURCE = "S0_PSD_BPA_SOURCE"
    PYPSA_FULL_PROJECTION = "S1_PYPSA_FULL_PROJECTION"
    TOPOLOGY_SIMPLIFIED = "S2_TOPOLOGY_SIMPLIFIED"
    QUOTIENT_CANDIDATE = "S3_QUOTIENT_CANDIDATE"
    FINAL_RELOADED = "S4_FINAL_RELOADED"


@dataclass(frozen=True)
class StageInventory:
    """Stage counts; source-only and PyPSA-only fields remain distinct."""

    stage: CompressionStage
    bus_count: int | None = None
    line_count: int | None = None
    transformer_count: int | None = None
    source_transformer2w_count: int | None = None
    source_transformer3w_group_count: int | None = None
    source_transformer3w_leg_count: int | None = None
    source_be_count: int | None = None
    source_l_plus_count: int | None = None
    known_dc_card_count: int | None = None
    unsupported_active_card_count: int | None = None
    inactive_unmatched_ref_x_count: int | None = None
    generator_count: int | None = None
    load_count: int | None = None
    shunt_count: int | None = None
    link_count: int | None = None
    storage_unit_count: int | None = None
    store_count: int | None = None
    active_line_count: int | None = None
    inactive_line_count: int | None = None
    active_transformer_count: int | None = None
    inactive_transformer_count: int | None = None
    active_connected_component_count: int | None = None
    largest_component_bus_count: int | None = None
    bridge_count: int | None = None
    articulation_count: int | None = None
    two_core_bus_count: int | None = None
    cycle_edge_count: int | None = None
    simple_cycle_rank: int | None = None
    multigraph_cycle_rank: int | None = None


def source_stage_inventory(case: PsdCaseAcLike, inventory: BpaAcInventory,
                           projection: PsdCaseAcProjection) -> StageInventory:
    """Count audited PSD/BPA source objects without equating groups to T legs."""
    inventory.require_supported_ac()
    groups = len(case.transformers3w)
    two_winding = len(case.transformers2w)
    if groups != len(projection.three_winding_groups):
        raise BackendCapabilityError("Source and projected 3W groups differ")
    if inventory.card_counts.get("T ", 0) != two_winding + 3 * groups:
        raise BackendCapabilityError("Source T-card leg count differs")
    return StageInventory(
        stage=CompressionStage.PSD_BPA_SOURCE,
        bus_count=len(case.ac_buses), line_count=len(case.ac_lines),
        transformer_count=two_winding + groups,
        source_transformer2w_count=two_winding,
        source_transformer3w_group_count=groups,
        source_transformer3w_leg_count=3 * groups,
        source_be_count=projection.preserved_be_count,
        source_l_plus_count=projection.preserved_lplus_count,
        known_dc_card_count=inventory.known_dc_total,
        unsupported_active_card_count=sum(inventory.unsupported_cards.values()),
        inactive_unmatched_ref_x_count=(
            projection.inactive_ref_three_winding_count),
        generator_count=len(case.generators), load_count=len(case.loads),
        shunt_count=len(case.fixed_shunts), link_count=None,
    )


def pypsa_stage_inventory(network: pypsa.Network,
                          stage: CompressionStage) -> StageInventory:
    """Count public PyPSA tables and topology on static active same-layer Lines."""
    if stage == CompressionStage.PSD_BPA_SOURCE:
        raise BackendCapabilityError("S0 requires source-model inventory")
    graph = nx.Graph()
    graph.add_nodes_from(str(name) for name in network.buses.index)
    multiplicity: Counter[tuple[str, str]] = Counter()
    active_lines = 0
    for _, row in network.lines.iterrows():
        if not branch_is_active(row):
            continue
        left, right = str(row.bus0), str(row.bus1)
        if left not in graph or right not in graph:
            raise BackendCapabilityError("Active Line refers to absent Bus")
        if float(network.buses.at[left, "v_nom"]) != float(
                network.buses.at[right, "v_nom"]):
            raise BackendCapabilityError("Active Line crosses voltage layers")
        if left == right:
            raise BackendCapabilityError("Active Line self-loop is unsupported")
        active_lines += 1
        edge = tuple(sorted((left, right)))
        multiplicity[edge] += 1
        graph.add_edge(*edge)
    components = list(nx.connected_components(graph))
    bridges = {tuple(sorted(edge)) for edge in nx.bridges(graph)}
    cycle_edges = sum(count for edge, count in multiplicity.items()
                      if count > 1 or edge not in bridges)
    active_transformers = sum(branch_is_active(row)
                              for _, row in network.transformers.iterrows())
    return StageInventory(
        stage=stage,
        bus_count=len(network.buses), line_count=len(network.lines),
        transformer_count=len(network.transformers),
        generator_count=len(network.generators), load_count=len(network.loads),
        shunt_count=len(network.shunt_impedances), link_count=len(network.links),
        storage_unit_count=len(network.storage_units), store_count=len(network.stores),
        active_line_count=active_lines,
        inactive_line_count=len(network.lines) - active_lines,
        active_transformer_count=active_transformers,
        inactive_transformer_count=len(network.transformers) - active_transformers,
        active_connected_component_count=len(components),
        largest_component_bus_count=max(map(len, components), default=0),
        bridge_count=sum(edge in bridges and count == 1
                         for edge, count in multiplicity.items()),
        articulation_count=sum(1 for _ in nx.articulation_points(graph)),
        two_core_bus_count=len(nx.k_core(graph, k=2)),
        cycle_edge_count=cycle_edges,
        simple_cycle_rank=graph.number_of_edges() - graph.number_of_nodes() + len(components),
        multigraph_cycle_rank=active_lines - graph.number_of_nodes() + len(components),
    )
