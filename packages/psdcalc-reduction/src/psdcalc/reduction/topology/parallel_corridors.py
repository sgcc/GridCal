"""Stable-ID parallel corridor analysis on complete active Line topology."""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

import networkx as nx
import pypsa

from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import BackendCapabilityError
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.active import branch_is_active
from psdcalc.reduction.topology.voltage_layers import VoltageLayerClassifier


@dataclass(frozen=True)
class ParallelCorridor:
    """One unordered same-layer Bus pair with independent Line members."""

    endpoints: tuple[StableId, StableId]
    canonical_voltage: float
    members: frozenset[StableId]
    active_members: frozenset[StableId]
    inactive_members: frozenset[StableId]
    cycle_type: str | None
    protected: bool

    @property
    def parallel_only_cycle(self) -> bool:
        """Return whether multiplicity alone makes this corridor cyclic."""
        return self.cycle_type == "parallel_only"

    @property
    def simple_graph_cycle(self) -> bool:
        """Return whether an alternate simple Line path exists."""
        return self.cycle_type in {"simple_graph_cycle", "both"}


@dataclass(frozen=True)
class ProtectedParallelPlan:
    """Pre-quotient protected Line identities and mandatory Bus singletons."""

    corridors: tuple[ParallelCorridor, ...]
    protected_lines: frozenset[StableId]
    mandatory_singletons: frozenset[StableId]


class ParallelCorridorAnalyzer:
    """Classify Line corridors with one bridge pass per voltage layer."""

    def analyze(
        self, network: pypsa.Network, sidecar: IdentitySidecar,
        protection: ProtectionSet,
        *, voltage_aliases: Mapping[float, float] | None = None,
        lplus_base_lines: frozenset[StableId] = frozenset(),
    ) -> ProtectedParallelPlan:
        """Return deterministic corridor classes without mutating the network."""
        layers = VoltageLayerClassifier().classify(
            network, sidecar, aliases=voltage_aliases)
        by_bus = {sid: layer.v_nom_kv for layer in layers for sid in layer.bus_ids}
        groups: dict[tuple[float, StableId, StableId], list[tuple[StableId, bool]]] = defaultdict(list)
        graphs = {layer.v_nom_kv: nx.Graph() for layer in layers}
        for layer in layers:
            graphs[layer.v_nom_kv].add_nodes_from(layer.bus_ids)
        for name, row in network.lines.sort_index().iterrows():
            a = sidecar.source_id("Bus", str(row.bus0))
            b = sidecar.source_id("Bus", str(row.bus1))
            if by_bus[a] != by_bus[b]:
                raise BackendCapabilityError(f"Cross-layer Line is unsupported: {name}")
            sid = sidecar.source_id("Line", str(name))
            active = bool(branch_is_active(row))
            left, right = sorted((a, b))
            groups[(by_bus[a], left, right)].append((sid, active))
            if active:
                graphs[by_bus[a]].add_edge(left, right)
        bridges = {
            layer: {tuple(sorted(edge)) for edge in nx.bridges(graph)}
            for layer, graph in graphs.items()
        }
        corridors: list[ParallelCorridor] = []
        protected_lines: set[StableId] = set()
        singletons: set[StableId] = set()
        known_lines = {sid for rows in groups.values() for sid, _ in rows}
        if lplus_base_lines - known_lines:
            raise BackendCapabilityError("L+ base Line has no source identity binding")
        for (voltage, left, right), rows in sorted(groups.items()):
            members = frozenset(sid for sid, _ in rows)
            active = frozenset(sid for sid, enabled in rows if enabled)
            inactive = members - active
            simple_cycle = (left, right) not in bridges[voltage] and bool(active)
            parallel_cycle = len(active) >= 2
            cycle_type = (
                "both" if parallel_cycle and simple_cycle else
                "parallel_only" if parallel_cycle else
                "simple_graph_cycle" if simple_cycle else None
            )
            protected = (
                cycle_type == "parallel_only" or
                bool(members & protection.preserved_components) or
                bool(active and inactive) or
                bool(members & lplus_base_lines)
            )
            corridor = ParallelCorridor(
                (left, right), voltage, members, active, inactive,
                cycle_type, protected)
            if len(rows) >= 2:
                corridors.append(corridor)
            if protected:
                protected_lines.update(members)
                singletons.update((left, right))
        return ProtectedParallelPlan(tuple(corridors), frozenset(protected_lines),
                                     frozenset(singletons))
