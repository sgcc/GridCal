"""Near-linear active-Line cycle and corridor analysis shared by protection and Gates."""

from collections import Counter
from dataclasses import dataclass

import networkx as nx
import pypsa

from psdcalc.reduction.topology.active import branch_is_active


Corridor = tuple[str, str]


@dataclass(frozen=True)
class CycleCorridorAnalysis:
    """Simple Line graph, cycle identities, and diagnostic cycle ranks."""

    simple_graph: nx.Graph
    line_corridors: dict[str, Corridor]
    cycle_lines: frozenset[str]
    non_bridge_corridors: frozenset[Corridor]
    simple_cycle_rank: int
    multigraph_cycle_rank: int


def analyze_cycle_corridors(
    network: pypsa.Network,
    *,
    allowed_buses: set[str] | None = None,
) -> CycleCorridorAnalysis:
    """Analyze active Lines using one simple-graph bridge pass.

    Parallel multiplicity makes each corresponding source Line a cycle edge,
    while the simple corridor itself can still be a bridge. This distinction
    lets the quotient preserve physical ring corridors after parallel merging.
    """
    nodes = ({str(name) for name in network.buses.index} if allowed_buses is None
             else set(allowed_buses))
    graph = nx.Graph()
    graph.add_nodes_from(nodes)
    line_corridors: dict[str, Corridor] = {}
    multiplicity: Counter[Corridor] = Counter()
    for name, row in network.lines.iterrows():
        if not branch_is_active(row):
            continue
        a, b = str(row.bus0), str(row.bus1)
        if a not in nodes or b not in nodes:
            continue
        corridor = tuple(sorted((a, b)))
        line_corridors[str(name)] = corridor
        multiplicity[corridor] += 1
        graph.add_edge(a, b)
    bridges = {tuple(sorted(edge)) for edge in nx.bridges(graph)}
    # A simple bridge with two surviving parallel Lines is a non-bridge
    # corridor in the physical multigraph. If PyPSA merges those Lines into
    # one, this condition becomes false and the Gate rejects the collapse.
    non_bridge = frozenset(
        corridor for corridor, count in multiplicity.items()
        if count > 1 or corridor not in bridges
    )
    cycles = frozenset(
        name for name, corridor in line_corridors.items()
        if corridor in non_bridge
    )
    components = nx.number_connected_components(graph)
    simple_rank = graph.number_of_edges() - graph.number_of_nodes() + components
    multi_rank = len(line_corridors) - graph.number_of_nodes() + components
    return CycleCorridorAnalysis(
        graph, line_corridors, cycles, non_bridge, simple_rank, multi_rank,
    )
