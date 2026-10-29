"""Strict PSDCase AC-device projection to a PyPSA structural network.

The input is the public PSDCase object returned by ``load_psd_case``. This
module does not import VeraGrid and never mutates that object. The projection
is a structural input for Phase 1.5, not a power-flow equivalence assertion.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Any, Mapping, Protocol, Sequence

import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.ids import IdentitySidecar
from psdcalc.reduction.exceptions import BackendCapabilityError
from psdcalc.reduction.readiness.bpa_ac import BpaAcInventory


class _Equipment(Protocol):
    key: str
    active: bool
    attributes: Mapping[str, Any]


class _Bus(_Equipment, Protocol):
    base_kv: float
    card_type: str
    vm_set_pu: float | None
    zone: str | None


class PsdCaseAcLike(Protocol):
    """PSDCase fields consumed by the structural AC projection."""

    base_mva: float
    ac_buses: Mapping[str, _Bus]
    loads: Mapping[str, _Equipment]
    generators: Mapping[str, _Equipment]
    fixed_shunts: Mapping[str, _Equipment]
    ac_lines: Mapping[str, _Equipment]
    transformers2w: Mapping[str, _Equipment]
    transformers3w: Mapping[str, _Equipment]
    dc_nodes: Mapping[str, _Equipment]
    dc_lines: Mapping[str, _Equipment]
    converters: Mapping[str, _Equipment]
    dc_systems: Mapping[str, _Equipment]
    attributes: Mapping[str, Any]
    diagnostics: Sequence[Any]


@dataclass(frozen=True)
class PsdCaseAcProjection:
    """PyPSA input with external identities and explicit 3W group evidence."""

    network: pypsa.Network
    sidecar: IdentitySidecar
    three_winding_groups: Mapping[str, tuple[str, str, str, str]]
    inactive_ref_three_winding_count: int
    preserved_lplus_count: int
    preserved_be_count: int


def _finite_number(value: Any, context: str, *, default: float | None = None) -> float:
    """Read one numeric source value without coercing invalid data to zero."""
    if value is None:
        if default is None:
            raise BackendCapabilityError(f"Missing numeric BPA value: {context}")
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BackendCapabilityError(f"Non-numeric BPA value: {context}")
    number = float(value)
    if not isfinite(number):
        raise BackendCapabilityError(f"Non-finite BPA value: {context}")
    return number


class PsdCaseAcAdapter:
    """Map supported PSDCase AC objects to PyPSA with explicit failure Gates."""

    def adapt(
        self, case: PsdCaseAcLike, inventory: BpaAcInventory,
        *, namespace: str,
    ) -> PsdCaseAcProjection:
        """Build a detached network and sidecar from an audited PSDCase."""
        inventory.require_supported_ac()
        if any(getattr(item, "code", None) == "PSD_DAT_CARD_PARSE"
               for item in case.diagnostics):
            raise BackendCapabilityError("PSDCase DAT parse diagnostic blocks AC projection")
        if not namespace:
            raise BackendCapabilityError("BPA source namespace must be non-empty")
        base = _finite_number(case.base_mva, "base_mva")
        if base <= 0:
            raise BackendCapabilityError("BPA base_mva must be positive")
        if any((case.dc_nodes, case.dc_lines, case.converters, case.dc_systems)):
            raise BackendCapabilityError("Parsed DC or regulator/control objects require another adapter")
        expected_buses = sum(inventory.card_counts.get(key, 0)
                             for key in ("B ", "BQ", "BS", "BE", "BG"))
        if expected_buses != len(case.ac_buses):
            raise BackendCapabilityError("BPA bus cards and parsed buses differ")
        if inventory.card_counts.get("L ", 0) != len(case.ac_lines):
            raise BackendCapabilityError("BPA L cards and parsed lines differ")
        if inventory.card_counts.get("T ", 0) != (
            len(case.transformers2w) + 3 * len(case.transformers3w)
        ):
            raise BackendCapabilityError("BPA T cards and parsed transformer legs differ")
        if any(any(abs(_finite_number(value, "+H equivalent")) > 1e-9
                       for value in pair)
               for pair in case.attributes.get("plus_h_equivalents", {}).values()):
            raise BackendCapabilityError("Nonzero +H equivalent has no Phase 1.5 projection")
        inactive_ref = self._check_three_winding_ref(case)

        network = pypsa.Network()
        buses = case.ac_buses
        for key, bus in sorted(buses.items()):
            if key != bus.key:
                raise BackendCapabilityError("BPA bus mapping key disagrees with source key")
            if not bus.active:
                raise BackendCapabilityError(f"Inactive BPA Bus requires an active-state adapter: {key}")
            voltage = _finite_number(bus.base_kv, f"Bus {key} voltage")
            if voltage <= 0:
                raise BackendCapabilityError(f"Bus {key} has nonpositive voltage")
            network.add("Bus", key, v_nom=voltage,
                        country=bus.zone or "",
                        v_mag_pu_set=_finite_number(bus.vm_set_pu,
                            f"Bus {key} voltage setpoint", default=1.0))

        be_count = 0
        for key, device in sorted(case.generators.items()):
            a = device.attributes
            bus = self._bus(buses, a.get("bus_key"), f"Generator {key}")
            card = str(a.get("card_type", ""))
            if card == "BE":
                if not a.get("unlimited_q_voltage_control"):
                    raise BackendCapabilityError("BE voltage-control semantics are incomplete")
                be_count += 1
            control = ("Slack" if card == "BS" else
                       "PQ" if "fixed_q_mvar" in a else
                       "PV" if card in {"BE", "BQ", "BG"} else "PQ")
            p_set = _finite_number(a.get("p_mw"), f"Generator {key} P")
            p_nom = _finite_number(a.get("pmax_mw"), f"Generator {key} Pmax",
                                   default=max(base, abs(p_set)))
            network.add("Generator", key, bus=bus, control=control,
                        p_set=p_set, q_set=_finite_number(a.get("fixed_q_mvar"),
                                                          f"Generator {key} Q", default=0.0),
                        p_nom=max(p_nom, abs(p_set)), active=bool(device.active))
        if be_count != inventory.card_counts.get("BE", 0):
            raise BackendCapabilityError("BE cards and PV-equivalent generators differ")

        for key, device in sorted(case.loads.items()):
            a = device.attributes
            if a.get("constant_current"):
                raise BackendCapabilityError("Constant-current BPA load needs a ZIP-capable adapter")
            network.add("Load", key,
                        bus=self._bus(buses, a.get("bus_key"), f"Load {key}"),
                        p_set=_finite_number(a.get("p_mw"), f"Load {key} P"),
                        q_set=_finite_number(a.get("q_mvar"), f"Load {key} Q"),
                        active=bool(device.active))

        for key, device in sorted(case.fixed_shunts.items()):
            a = device.attributes
            bus = self._bus(buses, a.get("bus_key"), f"Shunt {key}")
            v2 = _finite_number(buses[bus].base_kv, f"Shunt {key} voltage") ** 2
            network.add("ShuntImpedance", key, bus=bus,
                        g=_finite_number(a.get("g_mw"), f"Shunt {key} G") / v2,
                        b=_finite_number(a.get("b_mvar"), f"Shunt {key} B") / v2,
                        active=bool(device.active))

        lplus = 0
        for key, device in sorted(case.ac_lines.items()):
            a = device.attributes
            left = self._bus(buses, a.get("from"), f"Line {key} bus0")
            right = self._bus(buses, a.get("to"), f"Line {key} bus1")
            v = buses[left].base_kv
            if v != buses[right].base_kv:
                raise BackendCapabilityError(f"Cross-voltage L card {key} cannot enter a Line layer")
            z_base = v * v / base
            y_base = 1.0 / z_base
            rating = _finite_number(a.get("rating_current_a"),
                                     f"Line {key} current rating", default=0.0)
            s_nom = sqrt(3.0) * v * rating / 1000.0 if rating > 0 else base
            network.add("Line", key, bus0=left, bus1=right,
                        r=_finite_number(a.get("r_pu"), f"Line {key} R", default=0.0) * z_base,
                        x=_finite_number(a.get("x_pu"), f"Line {key} X", default=0.0) * z_base,
                        g=2.0 * _finite_number(a.get("g_half_pu"), f"Line {key} G", default=0.0) * y_base,
                        b=2.0 * _finite_number(a.get("b_half_pu"), f"Line {key} B", default=0.0) * y_base,
                        s_nom=s_nom,
                        length=_finite_number(a.get("length_km"),
                                              f"Line {key} length", default=0.0),
                        active=bool(device.active))
            if "from_reactor_mvar" in a or "to_reactor_mvar" in a:
                if "from_reactor_mvar" not in a or "to_reactor_mvar" not in a:
                    raise BackendCapabilityError(f"Partial L+ terminal data on Line {key}")
                lplus += 1
                for terminal, bus in (("FROM", left), ("TO", right)):
                    q = _finite_number(a[f"{terminal.lower()}_reactor_mvar"],
                                       f"Line {key} L+ {terminal}", default=0.0)
                    network.add("ShuntImpedance", f"{key}:L+:{terminal}", bus=bus,
                                g=0.0, b=-q / (buses[bus].base_kv ** 2),
                                active=bool(device.active))
        if lplus != inventory.card_counts.get("L+", 0):
            raise BackendCapabilityError("L+ cards and parsed terminal reactors differ")

        for key, device in sorted(case.transformers2w.items()):
            self._add_transformer(network, buses, key, device.attributes, base,
                                  bool(device.active))
        groups: dict[str, tuple[str, str, str, str]] = {}
        for key, device in sorted(case.transformers3w.items()):
            a = device.attributes
            legs = tuple(a.get("leg_keys", ()))
            if len(legs) != 3 or len(set(legs)) != 3:
                raise BackendCapabilityError(f"Incomplete three-winding leg identity: {key}")
            center = self._bus(buses, a.get("center_bus"), f"3W {key} center")
            for role, leg_key in zip("hml", legs):
                leg = a.get(f"{role}_leg")
                if not isinstance(leg, Mapping):
                    raise BackendCapabilityError(f"Missing three-winding {role} leg: {key}")
                self._add_transformer(network, buses, leg_key, leg, base,
                                      bool(device.active))
                if center not in (leg.get("from"), leg.get("to")):
                    raise BackendCapabilityError(f"3W {key} leg misses its center bus")
            groups[key] = (str(legs[0]), str(legs[1]), str(legs[2]), center)

        sidecar = PypsaAdapter().identity_sidecar(network, namespace)
        return PsdCaseAcProjection(network, sidecar, groups, inactive_ref,
                                   lplus, be_count)

    @staticmethod
    def _bus(buses: Mapping[str, _Bus], key: Any, context: str) -> str:
        """Resolve a source bus key without a name or index fallback."""
        if not isinstance(key, str) or key not in buses:
            raise BackendCapabilityError(f"Missing BPA bus reference for {context}")
        return key

    @staticmethod
    def _add_transformer(
        network: pypsa.Network, buses: Mapping[str, _Bus], key: str,
        attributes: Mapping[str, Any], base_mva: float, active: bool,
    ) -> None:
        """Preserve one original T leg as a two-terminal PyPSA Transformer."""
        left = PsdCaseAcAdapter._bus(buses, attributes.get("from"), f"T {key} bus0")
        right = PsdCaseAcAdapter._bus(buses, attributes.get("to"), f"T {key} bus1")
        v0 = buses[left].base_kv
        v1 = buses[right].base_kv
        if v0 == v1:
            raise BackendCapabilityError(f"Same-voltage T leg is unsupported: {key}")
        tap0 = _finite_number(attributes.get("tap1_kv"), f"T {key} tap1", default=v0)
        tap1 = _finite_number(attributes.get("tap2_kv"), f"T {key} tap2", default=v1)
        ratio = (tap0 / v0) / (tap1 / v1)
        if not 0.5 <= ratio <= 2.0:
            raise BackendCapabilityError(f"T {key} tap ratio is outside the supported range")
        rate = _finite_number(attributes.get("rate_mva"), f"T {key} rate",
                              default=base_mva)
        if rate <= 0:
            raise BackendCapabilityError(f"T {key} rate must be positive")
        multiplier = rate / base_mva
        network.add("Transformer", key, bus0=left, bus1=right,
                    r=_finite_number(attributes.get("r_pu_effective", attributes.get("r_pu")),
                                     f"T {key} R", default=0.0) * multiplier,
                    x=_finite_number(attributes.get("x_pu_effective", attributes.get("x_pu")),
                                     f"T {key} X", default=0.0) * multiplier,
                    g=_finite_number(attributes.get("g_pu"), f"T {key} G", default=0.0) / multiplier,
                    b=_finite_number(attributes.get("b_pu"), f"T {key} B", default=0.0) / multiplier,
                    s_nom=rate, tap_ratio=ratio, tap_side=0,
                    active=active)

    @staticmethod
    def _check_three_winding_ref(case: PsdCaseAcLike) -> int:
        """Allow only inactive unmatched REF X records with no parsed T legs."""
        grouping = case.attributes.get("transformer3w_grouping", {})
        incomplete = int(grouping.get("incomplete", 0))
        if grouping.get("grouped", len(case.transformers3w)) != len(case.transformers3w):
            raise BackendCapabilityError("Three-winding group count disagrees with parsed objects")
        if grouping.get("consumed_legs", 3 * len(case.transformers3w)) != 3 * len(case.transformers3w):
            raise BackendCapabilityError("Three-winding consumed-leg count disagrees with parsed objects")
        if grouping.get("ambiguous", 0):
            raise BackendCapabilityError("Ambiguous REF three-winding grouping")
        ref_data = case.attributes.get("ref_data")
        if incomplete and ref_data is None:
            raise BackendCapabilityError("Unresolved three-winding REF records need audit")
        if ref_data is None:
            return 0
        grouped = {str(item.attributes.get("ref_device_id"))
                   for item in case.transformers3w.values()}
        ungrouped = [record for record in ref_data.transformers_3w
                     if str(record.device_id) not in grouped]
        if len(ungrouped) != incomplete or any(record.in_service != "F"
                                              for record in ungrouped):
            raise BackendCapabilityError("Active or unaccounted REF three-winding group")
        surviving_pairs = {frozenset((item.attributes.get("from"),
                                     item.attributes.get("to")))
                           for item in case.transformers2w.values()}
        for record in ungrouped:
            if len(record.keys) != 4:
                raise BackendCapabilityError("Incomplete REF X endpoint identity")
            h, m, l, center = (raw.hex().upper() for raw in record.keys)
            if any(frozenset((outer, center)) in surviving_pairs
                   for outer in (h, m, l)):
                raise BackendCapabilityError("Inactive REF X has an orphan T leg")
        return len(ungrouped)
