"""Canonical Line and L+ semantics for protected-member structural Gates."""

from collections.abc import Mapping
from datetime import date, datetime
import hashlib
import json
import math
from numbers import Real

import numpy as np
import pandas as pd
import pypsa

from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import (
    BackendCapabilityError, IdentityError, MappingError,
)
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.active import branch_is_active
from psdcalc.reduction.topology.parallel_corridors import ProtectedParallelPlan
from psdcalc.reduction.topology.parallel_provenance import (
    LineCircuitProvenance, ParallelSourceLedger, SourceProfileContract,
)


def _scalar(value: object) -> object:
    """Encode typed public-table values without storage dtype differences."""
    if isinstance(value, (tuple, list)):
        return [ _scalar(item) for item in value ]
    if isinstance(value, (pd.Timestamp, pd.Timedelta, datetime, date)):
        return ["datetime", value.isoformat()]
    try:
        if bool(pd.isna(value)):
            return ["missing"]
    except (TypeError, ValueError):
        pass
    if isinstance(value, (bool, np.bool_)):
        return ["bool", bool(value)]
    if isinstance(value, (Real, np.integer, np.floating)):
        number = float(value)
        return ["number", number if math.isfinite(number) else str(number)]
    if isinstance(value, str):
        return ["string", value]
    raise BackendCapabilityError(
        f"Unsupported populated semantic value type: {type(value).__name__}")


def _row(row: pd.Series, excluded: frozenset[str] = frozenset()) -> dict[str, object]:
    """Encode every supported public static column except named references."""
    return {str(key): _scalar(value) for key, value in sorted(row.items())
            if str(key) not in excluded}


def _dynamic(network: pypsa.Network, component_name: str,
             backend_name: str,
             dense_view: pypsa.Network | None = None) -> dict[str, object]:
    """Encode input dynamics, presence masks and effective dense values."""
    component = network.components[component_name]
    view: pypsa.Network | None = None
    output: dict[str, object] = {}
    for attribute, frame in sorted(component.dynamic.items()):
        if backend_name not in frame.columns:
            continue
        if attribute not in component.defaults.index or not str(
                component.defaults.loc[attribute, "status"]).startswith("Input"):
            raise BackendCapabilityError(
                f"Unsupported populated {component_name} dynamic attribute: {attribute}")
        raw = frame[backend_name].reindex(network.snapshots)
        if view is None:
            view = dense_view or network.copy()
            if dense_view is None:
                view.set_snapshots(network.snapshots.copy(deep=True))
        try:
            dense = view.get_switchable_as_dense(
                component_name, attribute, snapshots=view.snapshots,
                inds=pd.Index([backend_name]),
            )[backend_name].reindex(view.snapshots)
        except (KeyError, ValueError, AttributeError) as exc:
            raise BackendCapabilityError(
                f"Cannot read {component_name}.{attribute} dynamic values") from exc
        fallback = component.static.loc[backend_name, attribute]
        effective = dense.fillna(fallback)
        if attribute == "active" and any(
                bool(value) != bool(fallback) for value in effective):
            raise BackendCapabilityError(
                f"Dynamic {component_name}.active topology is unsupported")
        output[attribute] = {
            "present": [bool(pd.notna(value)) for value in raw],
            "raw": [_scalar(value) for value in raw],
            "effective": [_scalar(value) for value in effective],
        }
    return output


def _reference(network: pypsa.Network, table_name: str,
               name: object) -> object:
    """Encode one full referenced metadata row or fail if missing."""
    if pd.isna(name) or str(name) == "":
        return None
    table = network.components[table_name].static
    if str(name) not in table.index:
        raise MappingError(f"Missing {table_name} reference: {name}")
    return {"name": str(name), "row": _row(table.loc[str(name)])}


class LineSemanticHash:
    """Hash all public Line semantics with ordered StableId terminals."""

    def digest(
        self, network: pypsa.Network, sidecar: IdentitySidecar,
        line_id: StableId, *,
        busmap: Mapping[StableId, StableId] | None = None,
        provenance: LineCircuitProvenance | None = None,
        source_profile: SourceProfileContract | None = None,
        dense_view: pypsa.Network | None = None,
    ) -> str:
        """Return a canonical SHA-256 digest, independent of backend names."""
        if line_id.component != "Line":
            raise MappingError("LineSemanticHash requires a Line StableId")
        name = sidecar.backend_name(line_id)
        row = network.lines.loc[name]
        terminals = [sidecar.source_id("Bus", str(row[column]))
                     for column in ("bus0", "bus1")]
        if busmap is not None:
            try:
                terminals = [busmap[item] for item in terminals]
            except KeyError as exc:
                raise MappingError("Line terminal is absent from busmap") from exc
        payload = {
            "line_id": line_id.encode(),
            "terminals": [item.encode() for item in terminals],
            "static": _row(row, frozenset({"bus0", "bus1", "sub_network"})),
            "dynamic": _dynamic(network, "Line", name, dense_view),
            "line_type": _reference(network, "LineType", row.get("type", "")),
            "carrier": (_reference(network, "Carrier", row.get("carrier", ""))
                        if "carrier" in row.index else None),
            "snapshots": [_scalar(value) for value in network.snapshots.tolist()],
            "snapshot_names": [_scalar(value) for value in network.snapshots.names],
            "investment_periods": [
                _scalar(value) for value in network.investment_periods.tolist()],
            "source_profile": (None if source_profile is None else {
                "name": source_profile.name,
                "circuit_ids": source_profile.circuit_ids,
            }),
            "provenance": (None if provenance is None else {
                "source_card_identity": provenance.source_card_identity,
                "circuit_id": provenance.circuit_id,
                "raw_card": provenance.raw_card,
            }),
        }
        return hashlib.sha256(json.dumps(
            payload, sort_keys=True, ensure_ascii=False,
            separators=(",", ":")).encode()).hexdigest()


def shunt_semantic_hash(network: pypsa.Network,
                        sidecar: IdentitySidecar, shunt_id: StableId,
                        *, busmap: Mapping[StableId, StableId] | None = None,
                        dense_view: pypsa.Network | None = None) -> str:
    """Hash a derived Shunt's full semantics and translated Bus owner."""
    if shunt_id.component != "ShuntImpedance":
        raise MappingError("L+ association requires a ShuntImpedance StableId")
    name = sidecar.backend_name(shunt_id)
    row = network.shunt_impedances.loc[name]
    bus = sidecar.source_id("Bus", str(row.bus))
    if busmap is not None:
        bus = busmap[bus]
    payload = {
        "shunt_id": shunt_id.encode(), "bus": bus.encode(),
        "static": _row(row, frozenset({"bus", "sub_network"})),
        "dynamic": _dynamic(network, "ShuntImpedance", name, dense_view),
        "snapshots": [_scalar(value) for value in network.snapshots.tolist()],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


class ProtectedParallelStructuralValidator:
    """Check protected Line identity and semantics on memory or reload views."""

    def check(
        self, source: pypsa.Network, target: pypsa.Network,
        source_sidecar: IdentitySidecar, target_sidecar: IdentitySidecar,
        mappings: MappingBundle, plan: ProtectedParallelPlan,
        busmap: Mapping[StableId, StableId], *,
        source_ledger: ParallelSourceLedger | None = None,
        persisted_ledger: ParallelSourceLedger | None = None,
        protection: ProtectionSet | None = None,
        mapping_round_trip: bool = True,
        sidecar_round_trip: bool = True,
        object_results: dict[str, dict[str, bool]] | None = None,
    ) -> tuple[dict[str, bool], dict[str, str]]:
        """Return named fail-closed checks without assigning a final status."""
        checks = {key: True for key in (
            "protected_parallel_singleton_endpoints",
            "protected_parallel_member_coverage",
            "protected_parallel_corridor_coverage",
            "protected_parallel_active_state_sets",
            "protected_parallel_multiplicity",
            "protected_parallel_line_identity",
            "protected_parallel_line_semantics",
            "protected_parallel_type_references",
            "protected_parallel_mapping_round_trip",
            "protected_parallel_target_provenance",
            "l_plus_source_identity_uniqueness",
            "l_plus_base_side_uniqueness",
            "l_plus_shunt_identity_uniqueness",
            "l_plus_association_coverage",
            "l_plus_terminal_owner_consistency",
            "l_plus_semantic_round_trip",
            "parallel_evidence_round_trip",
        )}
        details: dict[str, str] = {}
        line_results: dict[str, bool] = {}
        association_results: dict[str, bool] = {}
        if object_results is not None:
            object_results["line_semantic_hash"] = line_results
            object_results["l_plus_association"] = association_results

        def fail(key: str, reason: str) -> None:
            checks[key] = False
            details.setdefault(key, reason)

        if not mapping_round_trip:
            fail("protected_parallel_mapping_round_trip",
                 "Parquet mapping differs from in-memory mapping")
            fail("parallel_evidence_round_trip",
                 "Persisted mapping differs from Builder mapping")
        if not sidecar_round_trip:
            fail("parallel_evidence_round_trip",
                 "Target sidecar differs from Builder binding")

        members_by_target: dict[tuple[StableId, StableId], set[StableId]] = {}
        active_by_target: dict[tuple[StableId, StableId], set[StableId]] = {}
        for name, row in target.lines.iterrows():
            try:
                sid = target_sidecar.source_id("Line", str(name))
                pair = tuple(sorted((
                    target_sidecar.source_id("Bus", str(row.bus0)),
                    target_sidecar.source_id("Bus", str(row.bus1)))))
            except (IdentityError, KeyError) as exc:
                fail("protected_parallel_member_coverage", str(exc))
                continue
            members_by_target.setdefault(pair, set()).add(sid)
            if branch_is_active(row):
                active_by_target.setdefault(pair, set()).add(sid)

        counts: dict[StableId, int] = {}
        for bus in busmap.values():
            counts[bus] = counts.get(bus, 0) + 1
        for endpoint in sorted(plan.mandatory_singletons):
            try:
                mapped = busmap[endpoint]
                backend = target_sidecar.backend_name(mapped)
                if counts[mapped] != 1 or backend not in target.buses.index:
                    fail("protected_parallel_singleton_endpoints",
                         f"Protected Bus is not a surviving singleton: {endpoint.encode()}")
            except (KeyError, IdentityError):
                fail("protected_parallel_singleton_endpoints",
                     f"Protected Bus target is missing: {endpoint.encode()}")

        protected_rows = [row for row in mappings.line_map
                          if row.source_id in plan.protected_lines]
        expected = set(plan.protected_lines)
        mapped = {row.source_id for row in protected_rows}
        if (len(protected_rows) != len(expected) or mapped != expected or
                any(row.target_id != row.source_id or
                    row.mapping_type != "preserved" for row in protected_rows)):
            fail("protected_parallel_member_coverage",
                 "Protected Line map must contain one preserved identity row per member")
            fail("protected_parallel_line_identity",
                 "Protected source and target Line StableIds differ")
        if {row.target_id for row in protected_rows} != expected:
            fail("protected_parallel_target_provenance",
                 "Protected target Line does not trace to one source member")
        bound_line_names = {row["backend_name"]
                            for row in target_sidecar.to_rows()
                            if row["component"] == "Line"}
        target_ids = {target_sidecar.source_id("Line", str(name))
                      for name in target.lines.index
                      if str(name) in bound_line_names}
        if not expected <= target_ids:
            fail("protected_parallel_member_coverage",
                 "A protected target Line StableId is absent or substituted")
            fail("protected_parallel_line_identity",
                 "A protected target Line StableId is absent or substituted")
            fail("protected_parallel_target_provenance",
                 "Protected target Line lacks source StableId provenance")
        for corridor in plan.corridors:
            if not corridor.protected:
                continue
            try:
                pair = tuple(sorted(busmap[bus] for bus in corridor.endpoints))
            except KeyError:
                fail("protected_parallel_singleton_endpoints",
                     "A protected corridor endpoint has no target")
                continue
            if pair[0] == pair[1]:
                fail("protected_parallel_singleton_endpoints",
                     "A protected corridor collapsed to a Bus self-loop")
            actual = members_by_target.get(pair, set())
            if actual != set(corridor.members):
                fail("protected_parallel_corridor_coverage",
                     f"Protected corridor changed: {corridor.endpoints}")
                fail("protected_parallel_multiplicity",
                     f"Corridor member set changed: {corridor.endpoints}")
                fail("protected_parallel_member_coverage",
                     f"Corridor member identity changed: {corridor.endpoints}")
            active = active_by_target.get(pair, set())
            if (active != set(corridor.active_members) or
                    (actual - active) != set(corridor.inactive_members)):
                fail("protected_parallel_active_state_sets",
                     f"Corridor active/inactive sets changed: {corridor.endpoints}")

        provenance = ({item.line_id: item for item in source_ledger.lines}
                      if source_ledger is not None else {})
        persisted_provenance = ({item.line_id: item for item in persisted_ledger.lines}
                                if persisted_ledger is not None else provenance)
        source_dense = None
        target_dense = None
        if expected and any(
                not frame.empty and any(
                    source_sidecar.backend_name(line_id) in frame.columns
                    for line_id in expected)
                for frame in source.components["Line"].dynamic.values()):
            source_dense = source.copy()
            source_dense.set_snapshots(source.snapshots.copy(deep=True))
        if expected and any(
                not frame.empty and any(
                    str(name) in frame.columns for name in target.lines.index)
                for frame in target.components["Line"].dynamic.values()):
            target_dense = target.copy()
            target_dense.set_snapshots(target.snapshots.copy(deep=True))
        for line_id in sorted(expected):
            line_results[line_id.encode()] = False
            try:
                source_hash = LineSemanticHash().digest(
                    source, source_sidecar, line_id, busmap=busmap,
                    provenance=provenance.get(line_id),
                    source_profile=(source_ledger.profile
                                    if source_ledger is not None else None),
                    dense_view=source_dense)
                target_hash = LineSemanticHash().digest(
                    target, target_sidecar, line_id,
                    provenance=persisted_provenance.get(line_id),
                    source_profile=(persisted_ledger.profile
                                    if persisted_ledger is not None else
                                    source_ledger.profile
                                    if source_ledger is not None else None),
                    dense_view=target_dense)
                if source_hash != target_hash:
                    fail("protected_parallel_line_semantics",
                         f"Full Line semantic hash changed: {line_id.encode()}")
                else:
                    line_results[line_id.encode()] = True
                source_type = source.lines.loc[
                    source_sidecar.backend_name(line_id)].get("type", "")
                target_type = target.lines.loc[
                    target_sidecar.backend_name(line_id)].get("type", "")
                if (_reference(source, "LineType", source_type) !=
                        _reference(target, "LineType", target_type)):
                    fail("protected_parallel_type_references",
                         f"LineType changed: {line_id.encode()}")
                source_carrier = source.lines.loc[
                    source_sidecar.backend_name(line_id)].get("carrier", "")
                target_carrier = target.lines.loc[
                    target_sidecar.backend_name(line_id)].get("carrier", "")
                if (_reference(source, "Carrier", source_carrier) !=
                        _reference(target, "Carrier", target_carrier)):
                    fail("protected_parallel_type_references",
                         f"Carrier changed: {line_id.encode()}")
            except (IdentityError, MappingError, BackendCapabilityError,
                    KeyError, ValueError, TypeError) as exc:
                fail("protected_parallel_line_semantics",
                     f"Cannot compare Line {line_id.encode()}: {exc}")
                fail("protected_parallel_type_references",
                     f"Cannot compare Line type {line_id.encode()}: {exc}")

        if source_ledger is not None:
            if persisted_ledger is not None and persisted_ledger != source_ledger:
                fail("parallel_evidence_round_trip",
                     "Reloaded source profile/circuit/L+ ledger changed")
            association_ledger = persisted_ledger or source_ledger
            cards: set[tuple[str, str]] = set()
            card_owners: dict[str, StableId] = {}
            base_sides: set[tuple[StableId, str]] = set()
            shunts: set[StableId] = set()
            for item in association_ledger.l_plus:
                card_side = (item.source_card_identity, item.terminal_side)
                if (card_side in cards or
                        item.source_card_identity in card_owners and
                        card_owners[item.source_card_identity] != item.base_line_id):
                    fail("l_plus_source_identity_uniqueness",
                         "L+ source-card identity is reused")
                cards.add(card_side)
                card_owners[item.source_card_identity] = item.base_line_id
                key = (item.base_line_id, item.terminal_side)
                if key in base_sides:
                    fail("l_plus_base_side_uniqueness",
                         "L+ base Line and terminal side are duplicated")
                base_sides.add(key)
                if item.shunt_id in shunts:
                    fail("l_plus_shunt_identity_uniqueness",
                         "L+ Shunt StableId is reused")
                shunts.add(item.shunt_id)
            shunt_rows = [row for row in mappings.device_map
                          if source_ledger is not None and row.source_id in {
                              item.shunt_id for item in source_ledger.l_plus}]
            for item in source_ledger.l_plus:
                association_results[item.shunt_id.encode()] = False
                owner_rows = [row for row in shunt_rows
                              if row.source_id == item.shunt_id]
                mapping_ok = (len(owner_rows) == 1 and
                        owner_rows[0].target_id == item.shunt_id and
                        owner_rows[0].mapping_type == "preserved" and
                        (protection is None or
                         item.shunt_id in protection.preserved_components))
                if not mapping_ok:
                    fail("l_plus_association_coverage",
                         f"L+ Shunt identity/action/map lost: {item.shunt_id.encode()}")
                try:
                    target_name = target_sidecar.backend_name(item.shunt_id)
                    if target_name not in target.shunt_impedances.index:
                        raise MappingError("L+ target Shunt is absent")
                    expected_bus = busmap[item.terminal_bus_id]
                    actual_bus = target_sidecar.source_id(
                        "Bus", str(target.shunt_impedances.loc[target_name, "bus"]))
                    base = target.lines.loc[
                        target_sidecar.backend_name(item.base_line_id)]
                    terminal = "bus0" if item.terminal_side == "FROM" else "bus1"
                    base_bus = target_sidecar.source_id("Bus", str(base[terminal]))
                    owner_ok = actual_bus == expected_bus and base_bus == expected_bus
                    if not owner_ok:
                        fail("l_plus_terminal_owner_consistency",
                             f"L+ terminal owner changed: {item.shunt_id.encode()}")
                    semantic_ok = (shunt_semantic_hash(
                            source, source_sidecar, item.shunt_id, busmap=busmap) !=
                            shunt_semantic_hash(target, target_sidecar,
                                                item.shunt_id))
                    if semantic_ok:
                        fail("l_plus_semantic_round_trip",
                             f"L+ Shunt semantics changed: {item.shunt_id.encode()}")
                    association_results[item.shunt_id.encode()] = (
                        mapping_ok and owner_ok and not semantic_ok)
                except (IdentityError, MappingError, KeyError,
                        BackendCapabilityError) as exc:
                    fail("l_plus_terminal_owner_consistency",
                         f"L+ Shunt cannot be checked: {exc}")
                    fail("l_plus_association_coverage",
                         f"L+ Shunt cannot be checked: {exc}")
                    fail("l_plus_semantic_round_trip",
                         f"L+ Shunt cannot be checked: {exc}")
        return checks, details
