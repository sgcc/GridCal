"""Structural gate; this module makes no electrical-equivalence claim."""

from dataclasses import dataclass
from typing import Mapping

import networkx as nx
import pandas as pd
import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.protection.engine import ProtectionSet


_BRANCHES = ("Line", "Link", "Transformer")
_INJECTION_COMPONENTS = ("Load", "Generator", "StorageUnit", "Store")


def _component_frame(network: pypsa.Network, name: str) -> pd.DataFrame:
    """Get a public static component table."""
    return network.components[name].static


def _target_name(row: MapEntry, target_frame: pd.DataFrame) -> str | None:
    """Resolve a generated target identifier against the target table."""
    if row.target_id is None:
        return None
    candidates = (row.target_id.source_key,
                  row.target_id.source_key.split(":", 1)[-1],
                  row.target_id.encode())
    return next((name for name in candidates if name in target_frame.index), None)


def _graph(network: pypsa.Network) -> nx.Graph:
    """Build a structural graph, retaining isolated buses."""
    graph = nx.Graph()
    graph.add_nodes_from(str(name) for name in network.buses.index)
    for name in _BRANCHES:
        for _, row in _component_frame(network, name).iterrows():
            if bool(row.get("active", True)):
                graph.add_edge(str(row.bus0), str(row.bus1))
    return graph


def _injection_totals(network: pypsa.Network) -> dict[str, float]:
    """Sum declared static real/reactive injections by component and field."""
    totals: dict[str, float] = {}
    for component in _INJECTION_COMPONENTS:
        frame = _component_frame(network, component)
        for column in ("p_set", "q_set"):
            if column in frame:
                values = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
                totals[f"{component}.{column}"] = float(values.sum())
    return totals


@dataclass(frozen=True)
class StructuralResult:
    """Per-check evidence and strictly structural gate status."""

    status: str
    checks: Mapping[str, bool]
    details: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        """Serialize this gate result for an artifact."""
        return {"status": self.status, "checks": dict(self.checks),
                "details": dict(self.details)}


class StructuralValidator:
    """Validate map coverage and original-component preservation."""

    def validate(
        self,
        original: pypsa.Network,
        reduced: pypsa.Network,
        sidecar: IdentitySidecar,
        mappings: MappingBundle,
        protection: ProtectionSet,
        *,
        original_topology_signature: str | None = None,
    ) -> StructuralResult:
        """Return structural evidence without asserting AC or OPF equivalence."""
        checks: dict[str, bool] = {}
        details: dict[str, str] = {}
        adapter = PypsaAdapter()
        try:
            adapter.validate_model(reduced, adapter.identity_sidecar(reduced, "target"))
            checks["terminals"] = True
        except Exception as exc:
            checks["terminals"] = False
            details["terminals"] = str(exc)
        current_signature = adapter.topology_signature(original, sidecar)
        checks["source_unchanged"] = (original_topology_signature is None or
                                      current_signature == original_topology_signature)
        if not checks["source_unchanged"]:
            details["source_unchanged"] = "Original topology signature changed"

        inventory = adapter.inventory(original, sidecar)
        expected = {
            "bus_map": {item.source_id for item in inventory.items if item.component == "Bus"},
            "branch_map": {item.source_id for item in inventory.items
                           if item.component in _BRANCHES},
            "device_map": {item.source_id for item in inventory.items
                           if item.component not in (*_BRANCHES, "Bus")},
        }
        checks["mapping_coverage"] = all(
            {row.source_id for row in getattr(mappings, name)} == sources and
            len(getattr(mappings, name)) == len(sources)
            for name, sources in expected.items()
        )
        if not checks["mapping_coverage"]:
            details["mapping_coverage"] = "Source IDs are missing or duplicated in maps"
        bus_targets = {row.source_id: row.target_id for row in mappings.bus_map}
        reduced_buses = set(str(name) for name in reduced.buses.index)
        checks["bus_targets"] = all(
            row.target_id is not None and
            (row.target_id.encode() in reduced_buses or
             row.target_id.source_key in reduced_buses)
            for row in mappings.bus_map
        )
        checks["branch_targets"] = True
        for row in mappings.branch_map:
            source_name = sidecar.backend_name(row.source_id)
            source_branch = _component_frame(original, row.source_id.component).loc[source_name]
            target_bus0 = bus_targets.get(sidecar.source_id("Bus", str(source_branch.bus0)))
            target_bus1 = bus_targets.get(sidecar.source_id("Bus", str(source_branch.bus1)))
            if row.mapping_type == "absorbed_internal":
                valid = target_bus0 == target_bus1 and row.target_id is None
            else:
                target_frame = _component_frame(reduced, row.source_id.component)
                valid = _target_name(row, target_frame) is not None
            if not valid:
                checks["branch_targets"] = False
                details["branch_targets"] = f"Invalid branch mapping: {row.source_id.encode()}"
                break
        checks["device_targets"] = all(
            _target_name(row, _component_frame(reduced, row.source_id.component)) is not None
            for row in mappings.device_map
        )
        checks["protected_identity"] = all(
            row.target_id == row.source_id
            for row in (*mappings.bus_map, *mappings.branch_map, *mappings.device_map)
            if row.source_id in protection.preserved_components
        ) and protection.preserved_components.issubset(
            {row.source_id for row in (*mappings.bus_map, *mappings.branch_map,
                                        *mappings.device_map)})
        checks["protected_buses"] = all(
            bus_targets.get(source) == source
            for source in protection.terminal_buses | protection.singleton_buses
        )
        checks["cannot_link"] = all(
            len({bus_targets.get(source) for source in pair}) == len(pair)
            for pair in protection.cannot_link
        )

        original_graph = _graph(original)
        reduced_graph = _graph(reduced)
        reduced_components = {
            node: index for index, component in enumerate(nx.connected_components(reduced_graph))
            for node in component
        }
        original_component_targets: list[set[int]] = []
        for component in nx.connected_components(original_graph):
            target_names = set()
            for name in component:
                source = sidecar.source_id("Bus", name)
                target = bus_targets.get(source)
                if target is not None:
                    target_names.add(target.encode() if target.encode() in reduced_buses
                                     else target.source_key)
            original_component_targets.append(
                {reduced_components[name] for name in target_names
                 if name in reduced_components}
            )
        checks["component_boundaries"] = (
            all(len(indices) == 1 for indices in original_component_targets) and
            len({next(iter(indices)) for indices in original_component_targets
                 if indices}) == len(original_component_targets)
        )
        original_injection = _injection_totals(original)
        reduced_injection = _injection_totals(reduced)
        checks["injection_conservation"] = all(
            abs(value - reduced_injection.get(key, 0.0)) <= 1e-7 * max(1.0, abs(value))
            for key, value in original_injection.items()
        )
        if not checks["injection_conservation"]:
            details["injection_conservation"] = "Declared static injection totals changed"
        status = "STRUCTURALLY_VALID" if all(checks.values()) else "STRUCTURALLY_INVALID"
        return StructuralResult(status, checks, details)
