"""In-memory structural precheck for a multi-voltage quotient."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Literal

import networkx as nx
import pandas as pd
import pypsa

from psdcalc.reduction.core.ids import IdentitySidecar
from psdcalc.reduction.exceptions import BackendCapabilityError, MappingError
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.protection.engine import ProtectionSet


@dataclass(frozen=True)
class PreliminaryStructuralEvidence:
    """Builder-stage checks without any final quotient status."""

    stage: Literal["builder"]
    finalized: Literal[False]
    checks: Mapping[str, bool]
    details: Mapping[str, str]


@dataclass(frozen=True)
class AggregationInvariantSpec:
    """One explicit static or per-snapshot aggregation invariant."""

    component: str
    attribute: str
    strategy: Literal["sum", "preserve_exact"]
    dynamic: bool = False
    scope: Literal["target", "source_connected_component", "global"] = "target"
    snapshots: tuple[object, ...] | None = None
    atol: float = 1e-9


_DEFAULT_INVARIANTS = (
    AggregationInvariantSpec("Load", "p_set", "sum"),
    AggregationInvariantSpec("Load", "q_set", "sum"),
    AggregationInvariantSpec("Load", "p_set", "sum", dynamic=True),
    AggregationInvariantSpec("Load", "q_set", "sum", dynamic=True),
)


def _normal(value: object) -> object:
    """Normalize scalar PyPSA values for a stable semantic comparison."""
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, pd.Timedelta)):
        return value.isoformat()
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            return str(number)
        return number
    return str(value)


def _transformer_semantics(network: pypsa.Network, name: str,
                           sidecar: IdentitySidecar,
                           busmap: Mapping[object, object] | None
                           ) -> str:
    """Hash all Transformer fields with ordered target StableId terminals."""
    row = network.transformers.loc[name]
    static = {str(key): _normal(value) for key, value in row.items()
              if str(key) not in {"bus0", "bus1", "sub_network"}}
    terminals = []
    for column in ("bus0", "bus1"):
        source_id = sidecar.source_id("Bus", str(row[column]))
        terminals.append((busmap[source_id] if busmap is not None else
                          source_id).encode())
    dynamic = {}
    for attribute, frame in sorted(network.components["Transformer"].dynamic.items()):
        if not frame.empty and name in frame.columns:
            dynamic[attribute] = [_normal(value) for value in
                                  frame[name].reindex(network.snapshots)]
    type_name = str(row.get("type", "") or "")
    type_row = None
    if type_name:
        if type_name not in network.transformer_types.index:
            raise MappingError(f"Missing TransformerType reference: {type_name}")
        type_row = {str(key): _normal(value)
                    for key, value in network.transformer_types.loc[type_name].items()}
    payload = {"static": static, "terminals": terminals,
               "dynamic": dynamic, "type": type_name, "type_row": type_row,
               "snapshots": [str(value) for value in network.snapshots]}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _branch_graph(network: pypsa.Network,
                  sidecar: IdentitySidecar) -> nx.Graph:
    """Create a stable-ID connected-component graph from supported branches."""
    graph = nx.Graph()
    graph.add_nodes_from(sidecar.source_id("Bus", str(name))
                         for name in network.buses.index)
    for frame in (network.lines, network.transformers, network.links):
        for _, row in frame.iterrows():
            graph.add_edge(sidecar.source_id("Bus", str(row.bus0)),
                           sidecar.source_id("Bus", str(row.bus1)))
    return graph


def _cycle_rank(network: pypsa.Network) -> int:
    """Count independent Line cycles, including parallel-edge cycles."""
    graph = nx.MultiGraph()
    graph.add_nodes_from(str(name) for name in network.buses.index)
    for _, row in network.lines.iterrows():
        graph.add_edge(str(row.bus0), str(row.bus1))
    return graph.number_of_edges() - graph.number_of_nodes() + nx.number_connected_components(graph)


def _cycle_lines(network: pypsa.Network) -> set[str]:
    """Find Line identities whose removal leaves a same-layer alternate path."""
    graph = nx.MultiGraph()
    graph.add_nodes_from(str(name) for name in network.buses.index)
    for name, row in network.lines.iterrows():
        graph.add_edge(str(row.bus0), str(row.bus1), key=str(name))
    result: set[str] = set()
    for name, row in network.lines.iterrows():
        a, b = str(row.bus0), str(row.bus1)
        graph.remove_edge(a, b, key=str(name))
        if nx.has_path(graph, a, b):
            result.add(str(name))
        graph.add_edge(a, b, key=str(name))
    return result


class MultiVoltageStructuralValidator:
    """Check a detached quotient before any export/reload claim."""

    def validate_supported_dynamic(
        self, network: pypsa.Network,
        invariants: tuple[AggregationInvariantSpec, ...] = (),
    ) -> None:
        """Reject unsupported populated input time series before construction."""
        self._reject_unknown_dynamic(network, invariants or _DEFAULT_INVARIANTS)

    def precheck(self, parent_network: pypsa.Network,
                 quotient_network: pypsa.Network,
                 parent_sidecar: IdentitySidecar,
                 target_sidecar: IdentitySidecar,
                 mappings: MappingBundle,
                 invariants: tuple[AggregationInvariantSpec, ...],
                 protection: ProtectionSet) -> PreliminaryStructuralEvidence:
        """Return only in-memory evidence or raise a typed error."""
        specs = invariants or _DEFAULT_INVARIANTS
        self._reject_unknown_dynamic(parent_network, specs)
        checks: dict[str, bool] = {}
        details: dict[str, str] = {}
        source_components = {
            "Bus": parent_network.buses,
            "Line": parent_network.lines,
            "Transformer": parent_network.transformers,
            "Link": parent_network.links,
            "Generator": parent_network.generators,
            "Load": parent_network.loads,
            "StorageUnit": parent_network.storage_units,
            "Store": parent_network.stores,
            "ShuntImpedance": parent_network.shunt_impedances,
        }
        row_groups = {
            "Bus": mappings.bus_map,
            "Line": mappings.line_map,
            "Transformer": mappings.transformer_map,
            "Link": tuple(row for row in mappings.branch_map
                          if row.source_id.component == "Link"),
            "Generator": tuple(row for row in mappings.device_map
                               if row.source_id.component == "Generator"),
            "Load": tuple(row for row in mappings.device_map
                          if row.source_id.component == "Load"),
            "StorageUnit": tuple(row for row in mappings.device_map
                                 if row.source_id.component == "StorageUnit"),
            "Store": tuple(row for row in mappings.device_map
                           if row.source_id.component == "Store"),
            "ShuntImpedance": tuple(row for row in mappings.device_map
                                    if row.source_id.component == "ShuntImpedance"),
        }
        checks["mapping_coverage"] = all(
            len(rows) == len(frame) and
            {row.source_id for row in rows} ==
            {parent_sidecar.source_id(component, str(name))
             for name in frame.index}
            for component, frame in source_components.items()
            for rows in (row_groups[component],)
        )
        target_ok = True
        for rows in row_groups.values():
            for row in rows:
                if row.target_id is None:
                    target_ok &= (
                        row.source_id.component in {"Line", "Link"}
                        and row.mapping_type == "absorbed_internal"
                    )
                    continue
                try:
                    backend = target_sidecar.backend_name(row.target_id)
                    target_ok &= backend in quotient_network.components[
                        row.source_id.component].static.index
                except (KeyError, MappingError, AttributeError):
                    target_ok = False
        checks["target_existence"] = target_ok
        checks["snapshots"] = parent_network.snapshots.equals(
            quotient_network.snapshots
        ) and parent_network.snapshot_weightings.equals(
            quotient_network.snapshot_weightings
        )
        checks["investment_periods"] = (
            parent_network.investment_periods.equals(
                quotient_network.investment_periods
            ) and parent_network.investment_period_weightings.equals(
                quotient_network.investment_period_weightings
            )
        )
        checks["metadata_tables"] = all(
            parent_network.components[name].static.equals(
                quotient_network.components[name].static
            ) for name in ("Carrier", "LineType", "TransformerType")
        )
        busmap = {row.source_id: row.target_id for row in mappings.bus_map}
        transformer_ok = set(parent_network.transformers.index) == set(
            quotient_network.transformers.index
        )
        if transformer_ok:
            for name in parent_network.transformers.index:
                if (_transformer_semantics(parent_network, str(name),
                                           parent_sidecar, busmap) !=
                        _transformer_semantics(quotient_network, str(name),
                                               target_sidecar, None)):
                    transformer_ok = False
                    break
        checks["transformer_semantics"] = transformer_ok
        checks["connected_components"] = self._components_preserved(
            parent_network, quotient_network, parent_sidecar,
            target_sidecar, busmap,
        )
        checks["ring_topology"] = _cycle_rank(parent_network) == _cycle_rank(
            quotient_network
        )
        target_cycle_lines = _cycle_lines(quotient_network)
        line_rows = {row.source_id: row for row in mappings.line_map}
        corridor_ok = True
        for name in _cycle_lines(parent_network):
            source_id = parent_sidecar.source_id("Line", name)
            row = line_rows.get(source_id)
            if row is None or row.target_id is None:
                corridor_ok = False
                break
            if target_sidecar.backend_name(row.target_id) not in target_cycle_lines:
                corridor_ok = False
                break
        checks["cycle_corridors"] = corridor_ok
        checks["aggregation_invariants"] = self._invariants_hold(
            parent_network, quotient_network, parent_sidecar,
            target_sidecar, mappings, specs,
        )
        singleton = protection.singleton_buses | protection.terminal_buses
        checks["protected_singletons"] = all(
            sum(row.target_id == busmap[source] for row in mappings.bus_map) == 1
            for source in singleton
        )
        for name, passed in checks.items():
            if not passed:
                details[name] = f"In-memory {name} check failed"
        return PreliminaryStructuralEvidence("builder", False, checks, details)

    @staticmethod
    def _reject_unknown_dynamic(network: pypsa.Network,
                                specs: tuple[AggregationInvariantSpec, ...]) -> None:
        """Fail populated unrecognized or unspecified dynamic input tables."""
        spec_keys = {(spec.component, spec.attribute) for spec in specs
                     if spec.dynamic}
        for component in network.components:
            for attribute, frame in component.dynamic.items():
                if frame.empty or not len(frame.columns):
                    continue
                defaults = component.defaults
                if (attribute not in defaults.index or
                        not bool(defaults.loc[attribute, "varying"]) or
                        not str(defaults.loc[attribute, "status"]).startswith("Input")):
                    raise BackendCapabilityError(
                        f"Unknown or unsupported dynamic attribute: "
                        f"{component.name}.{attribute}"
                    )
                if component.name != "Transformer" and (
                        component.name, attribute) not in spec_keys:
                    raise BackendCapabilityError(
                        f"Dynamic aggregation strategy is missing: "
                        f"{component.name}.{attribute}"
                    )

    @staticmethod
    def _components_preserved(parent: pypsa.Network, child: pypsa.Network,
                              parent_sidecar: IdentitySidecar,
                              target_sidecar: IdentitySidecar,
                              busmap: Mapping[object, object]) -> bool:
        """Keep every source connected component separate after quotient."""
        source_graph = _branch_graph(parent, parent_sidecar)
        target_graph = _branch_graph(child, target_sidecar)
        target_components = {bus: index for index, component in enumerate(
            nx.connected_components(target_graph)) for bus in component}
        used: set[int] = set()
        for component in nx.connected_components(source_graph):
            mapped = {target_components.get(busmap[source]) for source in component}
            if None in mapped or len(mapped) != 1:
                return False
            index = next(iter(mapped))
            if index in used:
                return False
            used.add(index)
        return True

    @staticmethod
    def _invariants_hold(parent: pypsa.Network, child: pypsa.Network,
                         parent_sidecar: IdentitySidecar,
                         target_sidecar: IdentitySidecar,
                         mappings: MappingBundle,
                         specs: tuple[AggregationInvariantSpec, ...]) -> bool:
        """Compare configured attributes at the declared scope and snapshots."""
        component_of_bus: dict[object, int] = {}
        graph = _branch_graph(parent, parent_sidecar)
        for index, component in enumerate(sorted(
                nx.connected_components(graph), key=lambda nodes: min(nodes))):
            component_of_bus.update({bus: index for bus in component})
        for spec in specs:
            if spec.component not in {"Load", "Generator", "StorageUnit",
                                      "Store", "ShuntImpedance"}:
                raise BackendCapabilityError(
                    f"Unsupported invariant component: {spec.component}"
                )
            source = parent.components[spec.component]
            target = child.components[spec.component]
            relevant = [row for row in mappings.device_map
                        if row.source_id.component == spec.component]
            if spec.dynamic:
                source_frame = source.dynamic.get(spec.attribute)
                target_frame = target.dynamic.get(spec.attribute)
                if source_frame is None or source_frame.empty:
                    continue
                if target_frame is None:
                    return False
            else:
                if spec.attribute not in source.static.columns:
                    continue
            if spec.atol < 0 or not math.isfinite(spec.atol):
                raise BackendCapabilityError("Invariant tolerance must be finite and nonnegative")
            snapshots = (parent.snapshots if spec.snapshots is None else
                         pd.Index(spec.snapshots))
            if spec.dynamic and (not snapshots.isin(parent.snapshots).all() or
                                 not snapshots.isin(child.snapshots).all()):
                return False
            groups: dict[object, list[object]] = {}
            for row in relevant:
                if row.target_id is None:
                    return False
                if spec.scope == "target":
                    group_key: object = row.target_id
                elif spec.scope == "global":
                    group_key = "global"
                else:
                    source_name = parent_sidecar.backend_name(row.source_id)
                    if "bus" not in source.static.columns:
                        raise BackendCapabilityError(
                            f"Component has no Bus scope: {spec.component}"
                        )
                    bus_name = str(source.static.loc[source_name, "bus"])
                    bus_id = parent_sidecar.source_id("Bus", bus_name)
                    group_key = component_of_bus[bus_id]
                groups.setdefault(group_key, []).append(row)
            for rows in groups.values():
                names = [parent_sidecar.backend_name(row.source_id) for row in rows]
                target_ids = sorted({row.target_id for row in rows})
                target_names = [target_sidecar.backend_name(target_id)
                                for target_id in target_ids]
                if spec.strategy == "preserve_exact" and (
                        len(names) != 1 or len(target_names) != 1):
                    return False
                if spec.dynamic:
                    dynamic_names = [name for name in names
                                     if name in source_frame.columns]
                    if not dynamic_names:
                        continue
                    if any(name not in target_frame.columns for name in target_names):
                        return False
                    source_values = source_frame.reindex(snapshots)[dynamic_names]
                    expected = (source_values.sum(axis=1) if spec.strategy == "sum"
                                else source_values.iloc[:, 0])
                    static_names = set(names) - set(dynamic_names)
                    if static_names:
                        if spec.attribute not in source.static.columns:
                            return False
                        baseline = pd.to_numeric(
                            source.static.loc[sorted(static_names), spec.attribute],
                            errors="coerce",
                        ).fillna(0.0).sum()
                        expected = expected + baseline
                    actual_values = target_frame.reindex(snapshots)[target_names]
                    actual = (actual_values.sum(axis=1) if spec.strategy == "sum"
                              else actual_values.iloc[:, 0])
                    if not expected.sub(actual).abs().le(spec.atol).all():
                        return False
                else:
                    if any(name not in target.static.index for name in target_names):
                        return False
                    source_values = pd.to_numeric(
                        source.static.loc[names, spec.attribute], errors="coerce"
                    ).fillna(0.0)
                    expected = (source_values.sum() if spec.strategy == "sum"
                                else source_values.iloc[0])
                    if spec.attribute not in target.static.columns:
                        return False
                    target_values = pd.to_numeric(
                        target.static.loc[target_names, spec.attribute],
                        errors="coerce",
                    ).fillna(0.0)
                    actual = (target_values.sum() if spec.strategy == "sum"
                              else target_values.iloc[0])
                    if abs(float(expected) - float(actual)) > spec.atol:
                        return False
        return True
