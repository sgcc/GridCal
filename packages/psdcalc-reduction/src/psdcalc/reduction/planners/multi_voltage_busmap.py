"""Validation and target allocation for stable-ID voltage-layer partitions."""

from collections.abc import Mapping
from dataclasses import dataclass

import networkx as nx
import pandas as pd
import pypsa

from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import PartitionError
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.active import branch_is_active
from psdcalc.reduction.topology.cycle_corridors import analyze_cycle_corridors
from psdcalc.reduction.topology.voltage_layers import VoltageLayer


@dataclass(frozen=True)
class ValidatedLayerBusmaps:
    """One accepted plan with per-layer (planned, post-repair actual) counts."""

    global_busmap: Mapping[StableId, StableId]
    backend_by_layer: Mapping[str, Mapping[str, str]]
    transformer_terminals: frozenset[StableId]
    requested_cluster_count: int
    planned_cluster_count: int
    actual_cluster_count: int
    per_layer_counts: Mapping[str, tuple[int, int]]


class MultiVoltageBusmapValidator:
    """Validate layer ownership, protected singleton buses, and connectivity."""

    @staticmethod
    def _bus_target(target: StableId) -> StableId:
        """Represent a planned cluster as an actual target Bus identity."""
        if target.component not in {"Bus", "BusCluster"}:
            raise PartitionError("Busmap target must identify a Bus or BusCluster")
        return StableId(target.namespace, "Bus", target.source_key)

    @staticmethod
    def mandatory_singletons(network: pypsa.Network,
                             sidecar: IdentitySidecar,
                             protection: ProtectionSet) -> frozenset[StableId]:
        """Collect interfaces and devices whose Bus must remain a singleton."""
        buses = set(protection.singleton_buses | protection.terminal_buses)
        for _, row in network.transformers.iterrows():
            buses.add(sidecar.source_id("Bus", str(row.bus0)))
            buses.add(sidecar.source_id("Bus", str(row.bus1)))
        for _, row in network.generators.iterrows():
            if str(row.get("control", "")) in {"PV", "Slack"}:
                buses.add(sidecar.source_id("Bus", str(row.bus)))
        for _, row in network.shunt_impedances.iterrows():
            buses.add(sidecar.source_id("Bus", str(row.bus)))
        analysis = analyze_cycle_corridors(network)
        for name in analysis.cycle_lines:
            a, b = analysis.line_corridors[name]
            buses.add(sidecar.source_id("Bus", a))
            buses.add(sidecar.source_id("Bus", b))
        return frozenset(buses)

    @staticmethod
    def _domain(network: pypsa.Network, bus_name: str) -> tuple[str, str]:
        """Return declared subnetwork and region labels for a Bus."""
        row = network.buses.loc[bus_name]
        raw_subnet = row.get("sub_network", "")
        raw_region = row.get("country", "")
        subnet = "" if pd.isna(raw_subnet) else str(raw_subnet)
        region = "" if pd.isna(raw_region) else str(raw_region)
        return subnet, region

    @classmethod
    def _graph(cls, network: pypsa.Network,
               sidecar: IdentitySidecar,
               bus_ids: tuple[StableId, ...]) -> nx.Graph:
        """Build a same-domain Line graph with stable Bus IDs."""
        graph = nx.Graph()
        allowed = set(bus_ids)
        graph.add_nodes_from(bus_ids)
        for _, row in network.lines.iterrows():
            if not branch_is_active(row):
                continue
            a = sidecar.source_id("Bus", str(row.bus0))
            b = sidecar.source_id("Bus", str(row.bus1))
            if (a in allowed and b in allowed and
                    cls._domain(network, str(row.bus0)) ==
                    cls._domain(network, str(row.bus1))):
                graph.add_edge(a, b)
        return graph

    def allocate_targets(self, network: pypsa.Network,
                         sidecar: IdentitySidecar,
                         layers: tuple[VoltageLayer, ...],
                         protection: ProtectionSet,
                         global_target: int,
                         per_layer: Mapping[str, int] | None = None
                         ) -> dict[str, int]:
        """Allocate requested counts or raise a partition error."""
        if (not isinstance(global_target, int) or isinstance(global_target, bool)
                or global_target < 1):
            raise PartitionError("Global cluster target must be a positive integer")
        keys = {layer.key for layer in layers}
        overrides = dict(per_layer or {})
        if set(overrides) - keys:
            raise PartitionError("per_layer contains an unknown voltage layer")
        mandatory = self.mandatory_singletons(network, sidecar, protection)
        floors: dict[str, int] = {}
        capacity: dict[str, int] = {}
        for layer in layers:
            graph = self._graph(network, sidecar, layer.bus_ids)
            fixed = mandatory.intersection(layer.bus_ids)
            free = graph.subgraph(set(layer.bus_ids) - fixed)
            floors[layer.key] = len(fixed) + nx.number_connected_components(free)
            capacity[layer.key] = len(layer.bus_ids)
        if global_target < sum(floors.values()):
            raise PartitionError("Global target is below the minimum cluster count")
        if global_target > sum(capacity.values()):
            raise PartitionError("Global target exceeds the source bus count")
        for key, target in overrides.items():
            if (not isinstance(target, int) or isinstance(target, bool) or
                    target < floors[key] or target > capacity[key]):
                raise PartitionError(f"per_layer target is outside the feasible range: {key}")
        if set(overrides) == keys and sum(overrides.values()) != global_target:
            raise PartitionError("Fully specified per_layer targets must sum to global target")
        remaining_keys = sorted(keys - set(overrides))
        allocated = {**overrides, **{key: floors[key] for key in remaining_keys}}
        remaining = global_target - sum(allocated.values())
        if remaining < 0:
            raise PartitionError("per_layer targets exceed global target")
        headroom = {key: capacity[key] - floors[key] for key in remaining_keys}
        total_headroom = sum(headroom.values())
        if remaining > total_headroom:
            raise PartitionError("Unassigned layers have insufficient cluster capacity")
        if remaining:
            quotas = {key: remaining * headroom[key] / total_headroom
                      for key in remaining_keys}
            whole = {key: int(quotas[key]) for key in remaining_keys}
            for key, value in whole.items():
                allocated[key] += value
            residue = remaining - sum(whole.values())
            ranked = sorted(remaining_keys,
                            key=lambda key: (-(quotas[key] - whole[key]), key))
            for key in ranked[:residue]:
                allocated[key] += 1
        return dict(sorted(allocated.items()))

    def validate(self, network: pypsa.Network,
                 sidecar: IdentitySidecar,
                 partition: PartitionPlan,
                 layers: tuple[VoltageLayer, ...],
                 protection: ProtectionSet) -> ValidatedLayerBusmaps:
        """Return a checked global and per-layer busmap."""
        expected = {sidecar.source_id("Bus", str(name))
                    for name in network.buses.index}
        if set(partition.stable_busmap) != expected:
            raise PartitionError("Stable busmap must cover every source Bus exactly once")
        if set(partition.backend_busmap) != {str(name) for name in network.buses.index}:
            raise PartitionError("Backend busmap must cover every source Bus exactly once")
        if (not isinstance(partition.requested_cluster_count, int) or
                isinstance(partition.requested_cluster_count, bool) or
                partition.requested_cluster_count < 1):
            raise PartitionError("requested_cluster_count must be a positive integer")
        if (not isinstance(partition.actual_cluster_count, int) or
                isinstance(partition.actual_cluster_count, bool) or
                partition.actual_cluster_count < 1):
            raise PartitionError("actual_cluster_count must be a positive integer")
        planned_count = len({self._bus_target(target) for target in
                             partition.stable_busmap.values()})
        if partition.actual_cluster_count != planned_count:
            raise PartitionError(
                "PartitionPlan.actual_cluster_count differs from unique targets"
            )
        if partition.connectivity_policy not in {"reject", "split_disconnected"}:
            raise PartitionError("Unsupported connectivity policy")
        if planned_count < partition.requested_cluster_count:
            raise PartitionError("planned cluster count is below requested_cluster_count")
        if (partition.connectivity_policy == "reject" and
                planned_count != partition.requested_cluster_count):
            raise PartitionError("reject policy requires planned count to equal requested_cluster_count")
        layer_by_bus = {source: layer.key for layer in layers
                        for source in layer.bus_ids}
        if set(layer_by_bus) != expected:
            raise PartitionError("Voltage layers do not cover every source Bus")
        members: dict[StableId, list[StableId]] = {}
        backend_by_target: dict[StableId, str] = {}
        target_by_backend: dict[str, StableId] = {}
        for source in sorted(expected):
            target = self._bus_target(partition.stable_busmap[source])
            name = sidecar.backend_name(source)
            backend = partition.backend_busmap[name]
            if not backend:
                raise PartitionError("Empty backend cluster label")
            if target in backend_by_target and backend_by_target[target] != backend:
                raise PartitionError("One stable target has multiple backend names")
            if backend in target_by_backend and target_by_backend[backend] != target:
                raise PartitionError("Backend cluster name collides across stable targets")
            backend_by_target[target] = backend
            target_by_backend[backend] = target
            members.setdefault(target, []).append(source)
        for target, sources in members.items():
            if len({layer_by_bus[source] for source in sources}) != 1:
                raise PartitionError(f"cross-voltage target is forbidden: {target.encode()}")
            domains = {self._domain(network, sidecar.backend_name(source))
                       for source in sources}
            if len(domains) != 1:
                raise PartitionError("A cluster cannot cross subnetwork or region")
        mandatory = self.mandatory_singletons(network, sidecar, protection)
        for source in mandatory:
            if len(members[self._bus_target(partition.stable_busmap[source])]) != 1:
                raise PartitionError(f"Protected singleton Bus was merged: {source.encode()}")
        for pair in protection.cannot_link:
            present = [source for source in pair if source in expected]
            if len(present) > 1 and len({self._bus_target(partition.stable_busmap[source])
                                         for source in present}) != len(present):
                raise PartitionError("cannot_link pair was merged")
        repaired = {source: self._bus_target(target)
                    for source, target in partition.stable_busmap.items()}
        backend_names = dict(partition.backend_busmap)
        before_counts: dict[str, int] = {}
        for layer in layers:
            before_counts[layer.key] = len({self._bus_target(partition.stable_busmap[source])
                                            for source in layer.bus_ids})
            graph = self._graph(network, sidecar, layer.bus_ids)
            for target in sorted({self._bus_target(partition.stable_busmap[source])
                                  for source in layer.bus_ids}):
                sources = set(members[target])
                components = sorted(
                    (tuple(sorted(component)) for component in
                     nx.connected_components(graph.subgraph(sources))),
                    key=lambda component: component[0],
                )
                if len(components) < 2:
                    continue
                if partition.connectivity_policy == "reject":
                    raise PartitionError("Cluster is disconnected")
                for index, component in enumerate(components[1:], start=1):
                    split = StableId(target.namespace, target.component,
                                     f"{target.source_key}:split:{index}")
                    if split in members or split in repaired.values():
                        raise PartitionError("Deterministic split target collides")
                    for source in component:
                        repaired[source] = split
                        backend_names[sidecar.backend_name(source)] = split.encode()
        projections = {
            layer.key: {sidecar.backend_name(source):
                        backend_names[sidecar.backend_name(source)]
                        for source in layer.bus_ids}
            for layer in layers
        }
        counts = {layer.key: (before_counts[layer.key],
                              len({repaired[source] for source in layer.bus_ids}))
                  for layer in layers}
        return ValidatedLayerBusmaps(
            repaired, projections, mandatory,
            partition.requested_cluster_count,
            planned_count,
            len(set(repaired.values())), dict(sorted(counts.items())),
        )
