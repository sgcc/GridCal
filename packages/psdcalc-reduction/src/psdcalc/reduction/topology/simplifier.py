"""Selective same-voltage simplification with transformer interfaces retained."""

from dataclasses import dataclass
from typing import Literal

import networkx as nx
import pandas as pd
import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.core.records import ReductionRecord
from psdcalc.reduction.exceptions import BackendCapabilityError
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.validation.structural import StructuralResult, StructuralValidator


def _line_graph(network: pypsa.Network, voltage: float) -> nx.Graph:
    """Return active same-voltage AC line topology for one layer."""
    graph = nx.Graph()
    graph.add_nodes_from(str(name) for name, row in network.buses.iterrows()
                         if float(row.v_nom) == voltage)
    for name, row in network.lines.iterrows():
        if bool(row.get("active", True)) and row.bus0 in graph and row.bus1 in graph:
            graph.add_edge(str(row.bus0), str(row.bus1), name=str(name))
    return graph


def _cycle_lines(network: pypsa.Network, graph: nx.Graph) -> set[str]:
    """Mark every line whose removal leaves its terminals connected."""
    result: set[str] = set()
    for name, row in network.lines.iterrows():
        a, b = str(row.bus0), str(row.bus1)
        if a not in graph or b not in graph or not bool(row.get("active", True)):
            continue
        trial = graph.copy()
        trial.remove_edge(a, b)
        parallel = sum(
            1 for other_name, other in network.lines.iterrows()
            if other_name != name and bool(other.get("active", True)) and
            {str(other.bus0), str(other.bus1)} == {a, b}
        )
        if parallel or nx.has_path(trial, a, b):
            result.add(str(name))
    return result


def _simple_line(network: pypsa.Network, name: str) -> bool:
    """Accept only static, active, plain series-impedance line models."""
    row = network.lines.loc[name]
    if not bool(row.get("active", True)) or str(row.get("type", "")) not in {"", "nan"}:
        return False
    if any(abs(float(row.get(field, 0.0) or 0.0)) > 1e-12
           for field in ("b", "g")):
        return False
    for frame in network.components["Line"].dynamic.values():
        if isinstance(frame, pd.DataFrame) and name in frame.columns and not frame[name].isna().all():
            return False
    return True


def _record(operation_id: str, before: str, after: str,
            removed: list[str], created: list[str], parameters: dict[str, object]) -> ReductionRecord:
    """Create one deterministic simplification operation record."""
    return ReductionRecord(
        operation_id=operation_id, operation_type="simplification",
        parent_grid_hash=before, child_grid_hash=after,
        input_signature=before, output_signature=after,
        removed_objects=tuple(removed), created_objects=tuple(created),
        parameters=parameters,
    )


@dataclass(frozen=True)
class TopologySimplificationResult:
    """Detached network, source-ID maps, roles, and structural evidence."""

    network: pypsa.Network
    mappings: MappingBundle
    voltage_layer_map: tuple[dict[str, object], ...]
    topology_roles: tuple[dict[str, object], ...]
    anchors: tuple[StableId, ...]
    cycle_edges: frozenset[str]
    absorbed_branches: tuple[StableId, ...]
    moved_devices: tuple[StableId, ...]
    records: tuple[ReductionRecord, ...]
    structural: StructuralResult
    status: Literal["STRUCTURALLY_SIMPLIFIED", "STRUCTURALLY_INVALID"]


class VoltageAwareTopologySimplifier:
    """Collapse safe feeder trees and zero-injection series chains per voltage."""

    def simplify(
        self,
        original: pypsa.Network,
        sidecar: IdentitySidecar,
        protection: ProtectionSet,
        *,
        mode: Literal["keep_transformer_interface", "lift_to_target_voltage"] =
        "keep_transformer_interface",
    ) -> TopologySimplificationResult:
        """Return structural simplification or raise on unsupported voltage lift."""
        if mode != "keep_transformer_interface":
            raise BackendCapabilityError(
                "lift_to_target_voltage requires a planning approximation implementation"
            )
        adapter = PypsaAdapter()
        adapter.validate_model(original, sidecar)
        source_signature = adapter.topology_signature(original, sidecar)
        original_snapshot = adapter.clone(original)
        for name, row in original.lines.iterrows():
            if float(original.buses.loc[row.bus0, "v_nom"]) != float(
                original.buses.loc[row.bus1, "v_nom"]
            ):
                raise BackendCapabilityError(
                    f"Cross-voltage Line {name} requires an explicit interface model"
                )
        working = adapter.clone(original)
        bus_target = {str(name): str(name) for name in original.buses.index}
        branch_target: dict[tuple[str, str], str | None] = {
            (component, str(name)): str(name)
            for component in ("Line", "Link", "Transformer")
            for name in original.components[component].static.index
        }
        moved: set[StableId] = set()
        records: list[ReductionRecord] = []
        all_cycles: set[str] = set()
        roles: list[dict[str, object]] = []
        anchors: set[str] = set()
        inventory = adapter.inventory(original, sidecar)
        attached: dict[str, list[object]] = {str(name): [] for name in original.buses.index}
        for item in inventory.items:
            if item.component in {"Bus", "Line", "Link", "Transformer"}:
                continue
            for terminal in item.terminals:
                attached[sidecar.backend_name(terminal)].append(item)
                if item.controllable or item.component == "ShuntImpedance":
                    anchors.add(sidecar.backend_name(terminal))
        for component in ("Transformer", "Link"):
            for _, row in original.components[component].static.iterrows():
                anchors.update((str(row.bus0), str(row.bus1)))
        anchors.update(sidecar.backend_name(source) for source in
                       protection.terminal_buses | protection.singleton_buses)
        for source in protection.preserved_components:
            if source.component == "Bus":
                anchors.add(sidecar.backend_name(source))
        for pair in protection.cannot_link:
            anchors.update(sidecar.backend_name(source) for source in pair)

        for voltage in sorted(float(value) for value in original.buses.v_nom.unique()):
            graph = _line_graph(original, voltage)
            cycle_lines = _cycle_lines(original, graph)
            all_cycles.update(cycle_lines)
            core = set(nx.k_core(graph, k=2).nodes) if graph.number_of_edges() else set()
            articulation = set(nx.articulation_points(graph)) if graph.number_of_edges() else set()
            cycle_nodes = {str(original.lines.loc[name, terminal])
                           for name in cycle_lines for terminal in ("bus0", "bus1")}
            layer_anchors = anchors | cycle_nodes
            for component in nx.connected_components(graph):
                if not component & layer_anchors:
                    layer_anchors.add(sorted(component)[0])
            anchors.update(layer_anchors & set(graph.nodes))
            for name in sorted(graph.nodes):
                role = ("ring_core" if name in cycle_nodes else
                        "transformer_or_control_anchor" if name in anchors else
                        "radial")
                roles.append({"source_id": sidecar.source_id("Bus", name).encode(),
                              "voltage_kv": voltage, "role": role,
                              "in_2_core": name in core,
                              "articulation": name in articulation,
                              "anchor": name in anchors})
            for name in sorted(original.lines.index):
                row = original.lines.loc[name]
                if row.bus0 not in graph or row.bus1 not in graph:
                    continue
                roles.append({"source_id": sidecar.source_id("Line", str(name)).encode(),
                              "voltage_kv": voltage,
                              "role": "cycle_edge" if name in cycle_lines else "bridge",
                              "bridge": name not in cycle_lines,
                              "anchor": False})
            nonanchors = set(graph.nodes) - layer_anchors
            for component in sorted(nx.connected_components(graph.subgraph(nonanchors)),
                                    key=lambda nodes: sorted(nodes)):
                members = set(component)
                boundary = {neighbor for node in members for neighbor in graph.neighbors(node)
                            if neighbor in layer_anchors}
                if len(boundary) != 1:
                    continue
                anchor = next(iter(boundary))
                names = sorted(str(data["name"]) for a, b, data in graph.edges(data=True)
                               if (a in members or b in members) and
                               (a in members | {anchor} and b in members | {anchor}))
                if not self._safe_tree(original, members, anchor, names, attached,
                                       sidecar, protection, cycle_lines):
                    continue
                before = adapter.topology_signature(
                    working, adapter.identity_sidecar(working, sidecar.source_id("Bus", anchor).namespace)
                )
                removed: list[str] = []
                for name in names:
                    working.remove("Line", name)
                    branch_target[("Line", name)] = None
                    removed.append(sidecar.source_id("Line", name).encode())
                for name in sorted(members):
                    for item in attached[name]:
                        working.components[item.component].static.at[
                            sidecar.backend_name(item.source_id), "bus"
                        ] = anchor
                        moved.add(item.source_id)
                    working.remove("Bus", name)
                    bus_target[name] = anchor
                    removed.append(sidecar.source_id("Bus", name).encode())
                after = adapter.topology_signature(
                    working, adapter.identity_sidecar(working, sidecar.source_id("Bus", anchor).namespace)
                )
                records.append(_record(f"radial-{len(records)+1}", before, after,
                                       removed, [], {"anchor": sidecar.source_id("Bus", anchor).encode(),
                                                     "voltage_kv": voltage}))

        self._collapse_series(original, working, sidecar, protection, attached,
                              bus_target, branch_target, all_cycles, anchors, records)
        mapping = self._maps(original, working, sidecar, bus_target, branch_target,
                             moved, inventory)
        base = StructuralValidator().validate(
            original, working, sidecar, mapping, protection,
            original_topology_signature=source_signature,
            original_snapshot=original_snapshot,
        )
        checks = dict(base.checks)
        details = dict(base.details)
        checks["cycle_skeleton"] = all(
            name in working.lines.index and
            working.lines.loc[name, ["bus0", "bus1"]].equals(
                original.lines.loc[name, ["bus0", "bus1"]]
            ) for name in all_cycles
        )
        checks["transformer_identity"] = original.transformers.equals(working.transformers)
        checks["control_terminals"] = all(
            original.components[item.component].static.loc[
                sidecar.backend_name(item.source_id), "bus"] ==
            working.components[item.component].static.loc[
                sidecar.backend_name(item.source_id), "bus"]
            for item in inventory.items if item.controllable and
            "bus" in original.components[item.component].static.columns
        )
        status = "STRUCTURALLY_VALID" if all(checks.values()) else "STRUCTURALLY_INVALID"
        structural = StructuralResult(status, checks, details)
        voltage_map = tuple({
            "source_id": sidecar.source_id("Bus", name).encode(),
            "source_voltage_kv": float(row.v_nom),
            "target_id": sidecar.source_id("Bus", bus_target[name]).encode(),
            "target_voltage_kv": float(working.buses.loc[bus_target[name], "v_nom"]),
        } for name, row in original.buses.iterrows())
        absorbed = tuple(sidecar.source_id(component, name)
                         for (component, name), target in sorted(branch_target.items())
                         if target is None)
        return TopologySimplificationResult(
            working, mapping, voltage_map, tuple(roles),
            tuple(sorted(sidecar.source_id("Bus", name) for name in anchors)),
            frozenset(all_cycles), absorbed, tuple(sorted(moved)), tuple(records),
            structural,
            "STRUCTURALLY_SIMPLIFIED" if status == "STRUCTURALLY_VALID"
            else "STRUCTURALLY_INVALID",
        )

    @staticmethod
    def _safe_tree(original: pypsa.Network, members: set[str], anchor: str,
                   lines: list[str], attached: dict[str, list[object]],
                   sidecar: IdentitySidecar, protection: ProtectionSet,
                   cycle_lines: set[str]) -> bool:
        """Reject protected, multi-region, controlled, or complex feeder trees."""
        if any(name in cycle_lines or not _simple_line(original, name) or
               sidecar.source_id("Line", name) in protection.preserved_components
               for name in lines):
            return False
        countries = {str(original.buses.loc[name].get("country", ""))
                     for name in members | {anchor}}
        if len(countries) > 1:
            return False
        for name in members:
            if sidecar.source_id("Bus", name) in protection.preserved_components:
                return False
            if any(item.controllable or item.source_id in protection.preserved_components
                   or item.component == "ShuntImpedance" for item in attached[name]):
                return False
        return True

    @staticmethod
    def _collapse_series(
        original: pypsa.Network, working: pypsa.Network,
        sidecar: IdentitySidecar, protection: ProtectionSet,
        attached: dict[str, list[object]], bus_target: dict[str, str],
        branch_target: dict[tuple[str, str], str | None], cycle_lines: set[str],
        anchors: set[str], records: list[ReductionRecord],
    ) -> None:
        """Replace plain, zero-injection degree-two corridors between anchors."""
        adapter = PypsaAdapter()
        for voltage in sorted(float(value) for value in working.buses.v_nom.unique()):
            graph = _line_graph(working, voltage)
            eligible = {
                name for name in graph.nodes
                if name not in anchors and graph.degree(name) == 2 and
                not attached[name] and
                sidecar.source_id("Bus", name) not in protection.preserved_components
            }
            for component in sorted(nx.connected_components(graph.subgraph(eligible)),
                                    key=lambda nodes: sorted(nodes)):
                members = set(component)
                boundary = sorted({neighbor for node in members
                                   for neighbor in graph.neighbors(node)
                                   if neighbor not in members})
                if len(boundary) != 2 or len(members) + 1 != sum(
                    1 for a, b in graph.edges if a in members or b in members
                ):
                    continue
                a, b = boundary
                subgraph = graph.subgraph(members | {a, b})
                path = nx.shortest_path(subgraph, a, b)
                names = [str(graph.edges[u, v]["name"])
                         for u, v in zip(path, path[1:])]
                if any(name in cycle_lines or not _simple_line(working, name) or
                       sidecar.source_id("Line", name) in protection.preserved_components
                       for name in names):
                    continue
                countries = {str(working.buses.loc[name].get("country", ""))
                             for name in members | {a, b}}
                if len(countries) > 1:
                    continue
                before = adapter.topology_signature(
                    working, adapter.identity_sidecar(working, sidecar.source_id("Bus", a).namespace)
                )
                line_rows = working.lines.loc[names]
                new_name = f"series-{len(records)+1}"
                while new_name in working.lines.index:
                    new_name += "-x"
                r = float(line_rows.r.sum())
                x = float(line_rows.x.sum())
                s_nom = float(line_rows.s_nom.min())
                length = float(line_rows.length.sum())
                for name in names:
                    working.remove("Line", name)
                    branch_target[("Line", name)] = None
                for name in path[1:-1]:
                    working.remove("Bus", name)
                    bus_target[name] = a
                working.add("Line", new_name, bus0=a, bus1=b, r=r, x=x,
                            s_nom=s_nom, length=length)
                branch_target[("Line", names[-1])] = new_name
                after = adapter.topology_signature(
                    working, adapter.identity_sidecar(working, sidecar.source_id("Bus", a).namespace)
                )
                records.append(_record(f"series-{len(records)+1}", before, after,
                                       [sidecar.source_id("Line", name).encode()
                                        for name in names] +
                                       [sidecar.source_id("Bus", name).encode()
                                        for name in path[1:-1]],
                                       [StableId(sidecar.source_id("Bus", a).namespace,
                                                 "Line", new_name).encode()],
                                       {"anchors": [sidecar.source_id("Bus", a).encode(),
                                                    sidecar.source_id("Bus", b).encode()],
                                        "voltage_kv": voltage}))

    @staticmethod
    def _maps(original: pypsa.Network, working: pypsa.Network,
              sidecar: IdentitySidecar, bus_target: dict[str, str],
              branch_target: dict[tuple[str, str], str | None],
              moved: set[StableId], inventory: object) -> MappingBundle:
        """Build complete source-ID maps for the final selective simplification."""
        bus_rows = tuple(MapEntry(
            sidecar.source_id("Bus", name), sidecar.source_id("Bus", target),
            "preserved" if name == target else "clustered", "topology"
        ) for name, target in sorted(bus_target.items()))
        branch_rows = []
        for (component, name), target in sorted(branch_target.items()):
            source = sidecar.source_id(component, name)
            if target is None:
                branch_rows.append(MapEntry(source, None, "absorbed_internal",
                                            "topology", reason="collapsed topology"))
            else:
                target_id = (source if target == name else
                             StableId(source.namespace, component, target))
                branch_rows.append(MapEntry(source, target_id,
                                            "preserved" if target == name else "aggregated",
                                            "topology"))
        device_rows = tuple(MapEntry(
            item.source_id, item.source_id,
            "moved" if item.source_id in moved else "preserved", "topology"
        ) for item in inventory.items
            if item.component not in {"Bus", "Line", "Link", "Transformer"})
        control_rows = tuple(row for row in device_rows
                             if next(item for item in inventory.items
                                     if item.source_id == row.source_id).controllable)
        return MappingBundle(bus_rows, tuple(branch_rows), device_rows, control_rows)
