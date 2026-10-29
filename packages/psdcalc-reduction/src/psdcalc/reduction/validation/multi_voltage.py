"""In-memory structural precheck for a multi-voltage quotient."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from numbers import Real
from typing import Literal

import networkx as nx
import numpy as np
import pandas as pd
import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import (
    BackendCapabilityError, ConfigSemanticError, MappingError,
)
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.planners.multi_voltage_busmap import MultiVoltageBusmapValidator
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.active import branch_is_active
from psdcalc.reduction.topology.cycle_corridors import (
    CycleCorridorAnalysis, analyze_cycle_corridors,
)


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

    def __post_init__(self) -> None:
        """Reject unsupported strategies and malformed comparison scopes."""
        if (not isinstance(self.component, str) or not self.component or
                not isinstance(self.attribute, str) or not self.attribute):
            raise ConfigSemanticError("Invariant component and attribute are required")
        if (not isinstance(self.strategy, str) or
                self.strategy not in {"sum", "preserve_exact"}):
            raise ConfigSemanticError("Unsupported invariant strategy")
        if (not isinstance(self.scope, str) or
                self.scope not in {"target", "source_connected_component", "global"}):
            raise ConfigSemanticError("Unsupported invariant scope")
        if not isinstance(self.dynamic, bool):
            raise ConfigSemanticError("Invariant dynamic flag must be boolean")
        if (isinstance(self.atol, (bool, np.bool_)) or
                not isinstance(self.atol, Real) or
                not math.isfinite(float(self.atol)) or self.atol < 0):
            raise ConfigSemanticError("Invariant atol must be finite and nonnegative")
        if self.snapshots is not None and not isinstance(self.snapshots, tuple):
            raise ConfigSemanticError("Invariant snapshots must be a tuple")


_DEFAULT_INVARIANTS = (
    AggregationInvariantSpec("Load", "p_set", "sum"),
    AggregationInvariantSpec("Load", "q_set", "sum"),
    AggregationInvariantSpec("Load", "p_set", "sum", dynamic=True),
    AggregationInvariantSpec("Load", "q_set", "sum", dynamic=True),
)


def _finite_number(value: object, attribute: str) -> float:
    """Return one finite numeric scalar without coercing missing or text data."""
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise BackendCapabilityError(
            f"Invariant {attribute} requires finite numeric values"
        )
    number = float(value)
    if not math.isfinite(number):
        raise BackendCapabilityError(
            f"Invariant {attribute} requires finite numeric values"
        )
    return number


def _sum_numbers(values: object, attribute: str) -> float:
    """Sum declared additive values without Pandas numeric coercion."""
    return math.fsum(_finite_number(value, attribute) for value in values)


def _exact_scalar(value: object, attribute: str) -> tuple[str, object]:
    """Canonicalize a comparable exact value, including enum members."""
    if isinstance(value, Enum):
        return _exact_scalar(value.value, attribute)
    if isinstance(value, (bool, np.bool_)):
        return "bool", bool(value)
    if isinstance(value, str):
        return "string", value
    if isinstance(value, Real):
        return "number", _finite_number(value, attribute)
    raise BackendCapabilityError(
        f"Invariant {attribute} has an unsupported or incomparable value"
    )


def _exact_equal(left: object, right: object, attribute: str,
                 atol: float) -> bool:
    """Compare two supported scalars without changing their logical type."""
    left_type, left_value = _exact_scalar(left, attribute)
    right_type, right_value = _exact_scalar(right, attribute)
    if left_type != right_type:
        return False
    if left_type == "number":
        return abs(float(left_value) - float(right_value)) <= atol
    return left_value == right_value


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
            if not branch_is_active(row):
                continue
            graph.add_edge(sidecar.source_id("Bus", str(row.bus0)),
                           sidecar.source_id("Bus", str(row.bus1)))
    return graph


def _simple_ring_preserved(
    source: CycleCorridorAnalysis,
    target: CycleCorridorAnalysis,
    source_sidecar: IdentitySidecar,
    target_sidecar: IdentitySidecar,
    busmap: Mapping[object, object],
) -> bool:
    """Compare the mapped simple corridor graph and each source ring rank."""
    try:
        translated = {
            name: target_sidecar.backend_name(
                busmap[source_sidecar.source_id("Bus", name)]
            ) for name in source.simple_graph.nodes
        }
    except (KeyError, MappingError):
        return False
    mapped = nx.Graph()
    mapped.add_nodes_from(translated.values())
    mapped.add_edges_from(
        (translated[a], translated[b])
        for a, b in source.simple_graph.edges
        if translated[a] != translated[b]
    )
    if (set(mapped.nodes) != set(target.simple_graph.nodes) or
            {frozenset(edge) for edge in mapped.edges} !=
            {frozenset(edge) for edge in target.simple_graph.edges}):
        return False
    mapped_rank = (mapped.number_of_edges() - mapped.number_of_nodes() +
                   nx.number_connected_components(mapped))
    if source.simple_cycle_rank != mapped_rank:
        return False
    for nodes in nx.connected_components(source.simple_graph):
        target_nodes = {translated[name] for name in nodes}
        original = source.simple_graph.subgraph(nodes)
        projected = mapped.subgraph(target_nodes)
        original_rank = original.number_of_edges() - original.number_of_nodes() + 1
        projected_rank = (projected.number_of_edges() - projected.number_of_nodes()
                          + nx.number_connected_components(projected))
        if original_rank != projected_rank:
            return False
    return True


class MultiVoltageStructuralValidator:
    """Check a detached quotient before any export/reload claim."""

    def validate_supported_dynamic(
        self, network: pypsa.Network,
        invariants: tuple[AggregationInvariantSpec, ...] = (),
    ) -> None:
        """Reject unsupported populated input time series before construction."""
        if invariants:
            self._validate_explicit_specs(network, invariants)
        self._reject_unknown_dynamic(network, invariants or _DEFAULT_INVARIANTS)

    def precheck(self, parent_network: pypsa.Network,
                 quotient_network: pypsa.Network,
                 parent_sidecar: IdentitySidecar,
                 target_sidecar: IdentitySidecar,
                 mappings: MappingBundle,
                 invariants: tuple[AggregationInvariantSpec, ...],
                 protection: ProtectionSet,
                 *,
                 voltage_layer_map: Mapping[StableId, tuple[float, float]],
                 ) -> PreliminaryStructuralEvidence:
        """Return only in-memory evidence or raise a typed error."""
        specs = invariants or _DEFAULT_INVARIANTS
        if invariants:
            self._validate_explicit_specs(parent_network, invariants)
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
        checks["voltage_layers"] = self._voltage_layers_hold(
            parent_network, quotient_network, parent_sidecar,
            target_sidecar, mappings, voltage_layer_map,
        )
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
        transformer_rows = mappings.transformer_map
        expected_transformers = {
            parent_sidecar.source_id("Transformer", str(name))
            for name in parent_network.transformers.index
        }
        checks["transformer_mapping_identity"] = (
            len(transformer_rows) == len(expected_transformers)
            and {row.source_id for row in transformer_rows} == expected_transformers
            and all(row.target_id == row.source_id and
                    row.mapping_type == "preserved" for row in transformer_rows)
        )
        expected_controls = {
            item.source_id for item in PypsaAdapter().inventory(
                parent_network, parent_sidecar
            ).items if item.controllable
        }
        controls = mappings.control_map
        checks["control_mapping_coverage"] = (
            len(controls) == len(expected_controls)
            and {row.source_id for row in controls} == expected_controls
        )
        device_owners = {row.source_id: row for row in mappings.device_map}
        checks["control_owner_consistency"] = all(
            row.source_id in device_owners
            and row.target_id == device_owners[row.source_id].target_id
            and row.mapping_type == device_owners[row.source_id].mapping_type
            for row in controls
        )
        control_targets_exist = True
        for row in controls:
            try:
                if (row.target_id is None or
                        row.target_id.component != row.source_id.component):
                    control_targets_exist = False
                    continue
                backend = target_sidecar.backend_name(row.target_id)
                if backend not in quotient_network.components[
                        row.source_id.component].static.index:
                    control_targets_exist = False
            except (KeyError, MappingError, AttributeError):
                control_targets_exist = False
        checks["control_target_existence"] = control_targets_exist
        checks["connected_components"] = self._components_preserved(
            parent_network, quotient_network, parent_sidecar,
            target_sidecar, busmap,
        )
        source_cycles = analyze_cycle_corridors(parent_network)
        target_cycles = analyze_cycle_corridors(quotient_network)
        checks["simple_ring_topology"] = _simple_ring_preserved(
            source_cycles, target_cycles, parent_sidecar, target_sidecar, busmap,
        )
        details["multigraph_cycle_rank_delta"] = str(
            target_cycles.multigraph_cycle_rank -
            source_cycles.multigraph_cycle_rank
        )
        line_rows = {row.source_id: row for row in mappings.line_map}
        corridor_ok = True
        for name in source_cycles.cycle_lines:
            source_id = parent_sidecar.source_id("Line", name)
            row = line_rows.get(source_id)
            if row is None or row.target_id is None:
                corridor_ok = False
                break
            target_name = target_sidecar.backend_name(row.target_id)
            target_pair = target_cycles.line_corridors.get(target_name)
            a, b = source_cycles.line_corridors[name]
            mapped_pair = tuple(sorted((
                target_sidecar.backend_name(busmap[parent_sidecar.source_id("Bus", a)]),
                target_sidecar.backend_name(busmap[parent_sidecar.source_id("Bus", b)]),
            )))
            if (target_pair != mapped_pair or
                    target_pair not in target_cycles.non_bridge_corridors):
                corridor_ok = False
                break
        checks["cycle_corridors"] = corridor_ok
        checks["aggregation_invariants"] = self._invariants_hold(
            parent_network, quotient_network, parent_sidecar,
            target_sidecar, mappings, specs,
        )
        singleton = MultiVoltageBusmapValidator.mandatory_singletons(
            parent_network, parent_sidecar, protection,
        )
        checks["protected_singletons"] = all(
            source in busmap and busmap[source] is not None and
            sum(row.target_id == busmap[source] for row in mappings.bus_map) == 1
            for source in singleton
        )
        for name, passed in checks.items():
            if not passed:
                details[name] = f"In-memory {name} check failed"
        return PreliminaryStructuralEvidence("builder", False, checks, details)

    @staticmethod
    def _voltage_layers_hold(
        parent: pypsa.Network,
        child: pypsa.Network,
        parent_sidecar: IdentitySidecar,
        target_sidecar: IdentitySidecar,
        mappings: MappingBundle,
        voltage_layer_map: Mapping[StableId, tuple[float, float]],
    ) -> bool:
        """Verify canonical Bus voltages and legal branch interfaces."""
        bus_rows = mappings.bus_map
        if {row.source_id for row in bus_rows} != set(voltage_layer_map):
            return False
        layers_by_target: dict[StableId, set[float]] = {}
        for row in bus_rows:
            if row.target_id is None:
                return False
            try:
                raw, canonical = voltage_layer_map[row.source_id]
                raw = float(raw)
                canonical = float(canonical)
                parent_voltage = float(parent.buses.loc[
                    parent_sidecar.backend_name(row.source_id), "v_nom"
                ])
            except (TypeError, ValueError, KeyError, MappingError):
                return False
            if (not math.isfinite(raw) or raw <= 0 or
                    not math.isfinite(canonical) or canonical <= 0 or
                    parent_voltage != raw):
                return False
            layers_by_target.setdefault(row.target_id, set()).add(canonical)
        if any(len(values) != 1 for values in layers_by_target.values()):
            return False
        names_by_layer: dict[str, float] = {}
        for target_id, values in layers_by_target.items():
            try:
                backend = target_sidecar.backend_name(target_id)
            except MappingError:
                return False
            if backend not in child.buses.index:
                return False
            canonical = next(iter(values))
            try:
                target_voltage = float(child.buses.loc[backend, "v_nom"])
            except (TypeError, ValueError):
                return False
            if target_voltage != canonical:
                return False
            names_by_layer[backend] = canonical
        if set(names_by_layer) != {str(name) for name in child.buses.index}:
            return False
        for _, row in child.lines.iterrows():
            if names_by_layer.get(str(row.bus0)) != names_by_layer.get(str(row.bus1)):
                return False
        for _, row in child.transformers.iterrows():
            a = names_by_layer.get(str(row.bus0))
            b = names_by_layer.get(str(row.bus1))
            if a is None or b is None or a == b:
                return False
        for _, row in child.links.iterrows():
            a = names_by_layer.get(str(row.bus0))
            b = names_by_layer.get(str(row.bus1))
            if a is None or b is None or a != b:
                return False
            for column in child.links.columns:
                if (str(column).startswith("bus") and
                        str(column)[3:].isdigit() and int(str(column)[3:]) >= 2):
                    value = row[column]
                    if pd.notna(value) and str(value) != "":
                        return False
        return True

    @staticmethod
    def _validate_explicit_specs(
        network: pypsa.Network,
        specs: tuple[AggregationInvariantSpec, ...],
    ) -> None:
        """Fail misspelled invariant references before any quotient work."""
        supported = {"Load", "Generator", "StorageUnit", "Store",
                     "ShuntImpedance"}
        component_names = {component.name for component in network.components}
        for spec in specs:
            if spec.component not in component_names:
                raise ConfigSemanticError(
                    f"Invariant component does not exist: {spec.component}"
                )
            if spec.component not in supported:
                raise BackendCapabilityError(
                    f"Unsupported invariant component: {spec.component}"
                )
            component = network.components[spec.component]
            if spec.dynamic:
                if (spec.attribute not in component.defaults.index or
                        not bool(component.defaults.loc[spec.attribute, "varying"])):
                    raise ConfigSemanticError(
                        f"Invariant dynamic attribute does not exist: "
                        f"{spec.component}.{spec.attribute}"
                    )
            elif spec.attribute not in component.static.columns:
                raise ConfigSemanticError(
                    f"Invariant static attribute is unavailable: "
                    f"{spec.component}.{spec.attribute}"
                )

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
        # PyPSA's public dense accessor may rewrite MultiIndex display metadata.
        # Network.copy() shares that index, so install independent snapshots on
        # detached views before asking the accessor for static fallback values.
        if any(spec.dynamic for spec in specs):
            source_view = parent.copy()
            target_view = child.copy()
            source_view.set_snapshots(parent.snapshots.copy(deep=True))
            target_view.set_snapshots(child.snapshots.copy(deep=True))
        else:
            source_view = parent
            target_view = child
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
            if not spec.dynamic:
                if spec.attribute not in source.static.columns:
                    continue
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
                    if (spec.attribute not in source.static.columns or
                            spec.attribute not in target.static.columns or
                            any(name not in target.static.index for name in target_names)):
                        return False
                    try:
                        source_values = source_view.get_switchable_as_dense(
                            spec.component, spec.attribute,
                            snapshots=snapshots, inds=pd.Index(names),
                        ).reindex(index=snapshots, columns=names)
                        actual_values = target_view.get_switchable_as_dense(
                            spec.component, spec.attribute,
                            snapshots=snapshots, inds=pd.Index(target_names),
                        ).reindex(index=snapshots, columns=target_names)
                    except (KeyError, ValueError, AttributeError):
                        return False
                    source_values = source_values.fillna(
                        source.static.loc[names, spec.attribute]
                    )
                    actual_values = actual_values.fillna(
                        target.static.loc[target_names, spec.attribute]
                    )
                    if spec.strategy == "sum":
                        for snapshot in snapshots:
                            expected = _sum_numbers(
                                source_values.loc[snapshot].tolist(), spec.attribute,
                            )
                            actual = _sum_numbers(
                                actual_values.loc[snapshot].tolist(), spec.attribute,
                            )
                            if abs(expected - actual) > spec.atol:
                                return False
                    else:
                        for snapshot in snapshots:
                            if not _exact_equal(
                                source_values.loc[snapshot, names[0]],
                                actual_values.loc[snapshot, target_names[0]],
                                spec.attribute, spec.atol,
                            ):
                                return False
                else:
                    if any(name not in target.static.index for name in target_names):
                        return False
                    if spec.attribute not in target.static.columns:
                        return False
                    source_values = source.static.loc[names, spec.attribute]
                    target_values = target.static.loc[target_names, spec.attribute]
                    if spec.strategy == "sum":
                        expected = _sum_numbers(source_values, spec.attribute)
                        actual = _sum_numbers(target_values, spec.attribute)
                        if abs(expected - actual) > spec.atol:
                            return False
                    elif not _exact_equal(
                        source_values.iloc[0], target_values.iloc[0],
                        spec.attribute, spec.atol,
                    ):
                        return False
        return True
