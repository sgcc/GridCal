"""Strict custom busmap planner with deterministic connectivity handling."""

from collections import defaultdict
import hashlib
from pathlib import Path
from typing import Literal

import networkx as nx
import pandas as pd
import pypsa

from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import IdentityError, PartitionError
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.protection.engine import ProtectionSet


def _read_map(path: Path) -> dict[StableId, StableId]:
    """Read a CSV or Parquet map using encoded source and target IDs."""
    try:
        if path.suffix.lower() == ".csv":
            frame = pd.read_csv(path, dtype=str)
        elif path.suffix.lower() == ".parquet":
            frame = pd.read_parquet(path)
        else:
            raise PartitionError(f"Unsupported custom busmap format: {path.suffix}")
    except (OSError, ValueError) as exc:
        raise PartitionError(f"Cannot read custom busmap {path}: {exc}") from exc
    if set(frame.columns) != {"source_id", "target_id"}:
        raise PartitionError("Custom busmap requires exactly source_id,target_id columns")
    result: dict[StableId, StableId] = {}
    try:
        for source_text, target_text in frame[["source_id", "target_id"]].itertuples(
            index=False, name=None
        ):
            source = StableId.decode(str(source_text))
            target = StableId.decode(str(target_text))
            if source.component != "Bus" or target.component not in {"Bus", "BusCluster"}:
                raise PartitionError("Custom busmap IDs must be Bus to Bus/BusCluster")
            if source in result:
                raise PartitionError(f"Duplicate source bus in busmap: {source.encode()}")
            result[source] = target
    except IdentityError as exc:
        raise PartitionError(f"Invalid stable ID in custom busmap: {exc}") from exc
    return result


def _line_graph(network: pypsa.Network) -> nx.Graph:
    """Build the active, same-layer AC-line graph for cluster checks."""
    graph = nx.Graph()
    graph.add_nodes_from(str(bus) for bus in network.buses.index)
    for _, row in network.lines.iterrows():
        if bool(row.get("active", True)):
            graph.add_edge(str(row.bus0), str(row.bus1))
    return graph


class CustomBusmapPlanner:
    """Validate a source-ID map and split disconnected clusters if requested."""

    def plan(
        self,
        network: pypsa.Network,
        sidecar: IdentitySidecar,
        path: Path,
        protection: ProtectionSet,
        *,
        connectivity_policy: Literal["reject", "split_disconnected"] = "reject",
    ) -> PartitionPlan:
        """Return a deterministic partition or raise PartitionError."""
        return self.plan_mapping(
            network, sidecar, _read_map(path), protection,
            connectivity_policy=connectivity_policy,
        )

    def plan_mapping(
        self,
        network: pypsa.Network,
        sidecar: IdentitySidecar,
        mapping: dict[StableId, StableId],
        protection: ProtectionSet,
        *,
        connectivity_policy: Literal["reject", "split_disconnected"] = "reject",
    ) -> PartitionPlan:
        """Validate any source-ID mapping with the same hard topology rules."""
        if connectivity_policy not in {"reject", "split_disconnected"}:
            raise PartitionError(f"Unknown connectivity policy: {connectivity_policy}")
        expected = {sidecar.source_id("Bus", str(name)) for name in network.buses.index}
        if set(mapping) != expected:
            missing = len(expected - set(mapping))
            extra = len(set(mapping) - expected)
            raise PartitionError(f"Custom busmap incomplete: missing={missing}, extra={extra}")
        requested = len(set(mapping.values()))
        groups: dict[StableId, list[StableId]] = defaultdict(list)
        for source, target in mapping.items():
            groups[target].append(source)
        graph = _line_graph(network)
        result = dict(mapping)
        for target, members in sorted(groups.items(), key=lambda entry: entry[0]):
            if target.component == "Bus" and target not in members:
                raise PartitionError(f"Representative bus is outside its cluster: {target.encode()}")
            backend_names = [sidecar.backend_name(source) for source in members]
            voltages = {float(network.buses.loc[name, "v_nom"]) for name in backend_names}
            if len(voltages) > 1:
                raise PartitionError(f"Cluster crosses nominal voltage levels: {target.encode()}")
            components = [sorted(component) for component in
                          nx.connected_components(graph.subgraph(backend_names))]
            anchor_name = (sidecar.backend_name(target)
                           if target.component == "Bus" else None)
            components.sort(key=lambda component: (anchor_name not in component,
                                                    component))
            if len(components) > 1:
                if connectivity_policy == "reject":
                    raise PartitionError(f"Cluster is disconnected: {target.encode()}")
                for index, component in enumerate(components[1:], start=1):
                    digest = hashlib.sha256("\0".join(component).encode()).hexdigest()[:12]
                    split_target = StableId(target.namespace, "BusCluster",
                                            f"{target.source_key}~split-{index}-{digest}")
                    for name in component:
                        result[sidecar.source_id("Bus", name)] = split_target
        for source in protection.singleton_buses:
            target = result[source]
            if target != source or sum(value == target for value in result.values()) != 1:
                raise PartitionError(f"Protected singleton bus was merged: {source.encode()}")
        for source in protection.terminal_buses:
            if result[source] != source:
                raise PartitionError(f"Protected terminal bus lost identity: {source.encode()}")
        for pair in protection.cannot_link:
            a, b = tuple(pair)
            if result[a] == result[b]:
                raise PartitionError(f"cannot_link buses share cluster: {a.encode()}, {b.encode()}")
        backend_map = {sidecar.backend_name(source): target.encode()
                       for source, target in sorted(result.items())}
        return PartitionPlan(result, backend_map, requested,
                             len(set(result.values())), connectivity_policy)
