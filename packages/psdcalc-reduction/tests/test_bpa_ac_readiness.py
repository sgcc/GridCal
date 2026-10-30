"""Offline BPA AC readiness and adapter contract tests."""

from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace

import pytest

from psdcalc.reduction.readiness.bpa_ac import BpaAcInventory, scan_bpa_dat
from psdcalc.reduction.readiness.psdcase_ac import PsdCaseAcAdapter
from psdcalc.reduction.exceptions import BackendCapabilityError


def test_dc_gate_rejects_known_family(tmp_path: Path) -> None:
    """Every known DC family blocks AC-only adaptation."""
    path = tmp_path / "case.dat"
    path.write_bytes(b"B  AC\nBZ+ DC\n")
    report = scan_bpa_dat(path)
    assert report.known_dc_total == 1
    assert report.dc_cards["BZ+"] == 1
    assert not report.ac_only


@pytest.mark.parametrize("card", [
    b"BD", b"BM", b"BZ", b"BZ+", b"BA", b"BA1", b"BA2", b"BB",
    b"DC", b"LD", b"LM", b"LZ", b"LY",
])
def test_every_known_dc_card_blocks_ac_only(tmp_path: Path, card: bytes) -> None:
    """Variant cards inherit their known DC family Gate."""
    path = tmp_path / "case.dat"
    path.write_bytes(card + b" test\n")
    with pytest.raises(BackendCapabilityError, match="AC-only Gate"):
        scan_bpa_dat(path).require_supported_ac()


def _equipment(key: str, **attributes: object) -> SimpleNamespace:
    return SimpleNamespace(key=key, name=key, active=True, attributes=attributes)


def _case_with_three_winding_and_lplus() -> SimpleNamespace:
    buses = {
        "h": SimpleNamespace(key="h", name="h", active=True, base_kv=230.0,
                             card_type="BE", zone=None, vm_set_pu=1.01),
        "j": SimpleNamespace(key="j", name="j", active=True, base_kv=230.0,
                             card_type="B ", zone=None, vm_set_pu=None),
        "m": SimpleNamespace(key="m", name="m", active=True, base_kv=115.0,
                             card_type="B ", zone=None, vm_set_pu=None),
        "l": SimpleNamespace(key="l", name="l", active=True, base_kv=10.5,
                             card_type="B ", zone=None, vm_set_pu=None),
        "z": SimpleNamespace(key="z", name="z", active=True, base_kv=1.0,
                             card_type="B ", zone=None, vm_set_pu=None),
    }
    def leg(name: str, outer: str, kv: float) -> dict[str, object]:
        return {"from": outer, "to": "z", "from_kv": kv, "to_kv": 1.0,
                "r_pu": 0.01, "x_pu": 0.1, "r_pu_effective": 0.01,
                "x_pu_effective": 0.1, "rate_mva": 100.0,
                "tap1_kv": kv, "tap2_kv": 1.0}
    group = _equipment("T3|one", h_bus="h", m_bus="m", l_bus="l",
                       center_bus="z", h_leg=leg("th", "h", 230.0),
                       m_leg=leg("tm", "m", 115.0),
                       l_leg=leg("tl", "l", 10.5),
                       leg_keys=("th", "tm", "tl"), ref_device_id="one")
    return SimpleNamespace(
        name="fixture", base_mva=100.0, ac_buses=buses,
        loads={}, generators={"h:EQUIV": _equipment(
            "h:EQUIV", bus_key="h", p_mw=0.0, card_type="BE",
            vm_set_pu=1.01, qmin_mvar=-999999.0, qmax_mvar=999999.0,
            unlimited_q_voltage_control=True)},
        fixed_shunts={}, ac_lines={"lhj": _equipment(
            "lhj", **{"from": "h", "to": "j", "r_pu": 0.01,
                      "x_pu": 0.1, "b_half_pu": 0.0, "length_km": 12.5,
                      "from_reactor_mvar": 4.0, "to_reactor_mvar": -2.0})},
        transformers2w={}, transformers3w={"T3|one": group},
        dc_nodes={}, dc_lines={}, converters={}, dc_systems={},
        diagnostics=[],
        attributes={"transformer3w_grouping": {"grouped": 1,
                    "incomplete": 0, "ambiguous": 0, "consumed_legs": 3},
                    "plus_h_equivalents": {}},
    )


def test_adapter_preserves_be_lplus_and_three_winding_star() -> None:
    """Supported AC devices retain stable identities and explicit 3W grouping."""
    case = _case_with_three_winding_and_lplus()
    original = deepcopy(case)
    inventory = BpaAcInventory(Path("case.dat"), 10,
                               {"BE": 1, "B ": 4, "L ": 1, "L+": 1, "T ": 3},
                               {}, {}, {})
    result = PsdCaseAcAdapter().adapt(case, inventory, namespace="demo")
    assert len(result.network.buses) == 5
    assert set(result.network.transformers.index) == {"th", "tm", "tl"}
    assert result.network.lines.loc["lhj", "length"] == 12.5
    assert result.three_winding_groups["T3|one"] == ("th", "tm", "tl", "z")
    assert result.network.generators.loc["h:EQUIV", "control"] == "PV"
    assert set(result.network.shunt_impedances.index) == {"lhj:L+:FROM", "lhj:L+:TO"}
    assert result.sidecar.source_id("Transformer", "th").source_key == "th"
    assert case == original


def test_source_zone_survives_as_clustering_region() -> None:
    """The Phase 1.5 region Gate needs the source zone after NetCDF export."""
    case = _case_with_three_winding_and_lplus()
    case.ac_buses["h"].zone = "Z1"
    case.ac_buses["j"].zone = "Z2"
    inventory = BpaAcInventory(Path("case.dat"), 10,
                               {"BE": 1, "B ": 4, "L ": 1, "L+": 1, "T ": 3},
                               {}, {}, {})
    result = PsdCaseAcAdapter().adapt(case, inventory, namespace="demo")
    assert result.network.buses.loc["h", "country"] == "Z1"
    assert result.network.buses.loc["j", "country"] == "Z2"


def test_adapter_rejects_unmatched_lplus() -> None:
    """An L+ record without a parsed L target cannot disappear silently."""
    case = _case_with_three_winding_and_lplus()
    inventory = BpaAcInventory(Path("case.dat"), 10,
                               {"BE": 1, "B ": 4, "L ": 1, "L+": 2, "T ": 3},
                               {}, {}, {})
    with pytest.raises(BackendCapabilityError, match="L\\+"):
        PsdCaseAcAdapter().adapt(case, inventory, namespace="demo")


def test_unrecognized_active_card_is_reported(tmp_path: Path) -> None:
    """Unknown data cards appear in evidence and block adaptation."""
    path = tmp_path / "case.dat"
    path.write_bytes(b"XX unknown\n. comment\n")
    report = scan_bpa_dat(path)
    assert report.unsupported_cards == {"XX": 1}
    assert report.annotation_counts == {". ": 1}
    with pytest.raises(BackendCapabilityError, match="Unsupported BPA"):
        report.require_supported_ac()


def test_unparsed_plus_h_mode_is_reported(tmp_path: Path) -> None:
    """The PSDCase parser only applies W-mode +H1/+H2 records."""
    path = tmp_path / "case.dat"
    path.write_bytes(b"+H2" + b" " * 17 + b"X" + b" " * 80 + b"\n")
    report = scan_bpa_dat(path)
    assert report.unsupported_cards == {"+H_non_W": 1}


@pytest.mark.parametrize("card", [b"R ", b"+ "])
def test_unimplemented_control_or_continuation_card_fails_closed(
    tmp_path: Path, card: bytes,
) -> None:
    """R controls and generic continuations need an audited projection."""
    path = tmp_path / "case.dat"
    path.write_bytes(card + b" source record\n")
    with pytest.raises(BackendCapabilityError, match="Unsupported BPA"):
        scan_bpa_dat(path).require_supported_ac()


def test_psdcase_parse_warning_blocks_projection() -> None:
    """Malformed W-mode +H cards cannot disappear into a warning."""
    case = _case_with_three_winding_and_lplus()
    case.diagnostics = [SimpleNamespace(code="PSD_DAT_CARD_PARSE")]
    inventory = BpaAcInventory(Path("case.dat"), 10,
                               {"BE": 1, "B ": 4, "L ": 1, "L+": 1,
                                "T ": 3, "+H": 1}, {}, {}, {})
    with pytest.raises(BackendCapabilityError, match="PSDCase DAT parse diagnostic"):
        PsdCaseAcAdapter().adapt(case, inventory, namespace="demo")


def test_nonzero_plus_h_equivalent_is_rejected() -> None:
    """An unprojected +H equivalent must cause an explicit capability error."""
    case = _case_with_three_winding_and_lplus()
    case.attributes["plus_h_equivalents"] = {"h": [5.0, 0.0]}
    inventory = BpaAcInventory(Path("case.dat"), 10,
                               {"BE": 1, "B ": 4, "L ": 1, "L+": 1, "T ": 3},
                               {}, {}, {})
    with pytest.raises(BackendCapabilityError, match="Nonzero \\+H"):
        PsdCaseAcAdapter().adapt(case, inventory, namespace="demo")


def test_active_unmatched_ref_three_winding_is_rejected() -> None:
    """Incomplete live REF X semantics cannot be treated as a 2W device."""
    case = _case_with_three_winding_and_lplus()
    case.attributes["transformer3w_grouping"]["incomplete"] = 1
    case.attributes["ref_data"] = SimpleNamespace(transformers_3w=[
        SimpleNamespace(device_id="one", in_service="T"),
        SimpleNamespace(device_id="missing", in_service="T")])
    inventory = BpaAcInventory(Path("case.dat"), 10,
                               {"BE": 1, "B ": 4, "L ": 1, "L+": 1, "T ": 3},
                               {}, {}, {})
    with pytest.raises(BackendCapabilityError, match="Active or unaccounted"):
        PsdCaseAcAdapter().adapt(case, inventory, namespace="demo")


def test_inactive_bus_is_rejected_without_pypsa_active_semantics() -> None:
    """PyPSA Bus has no static active flag to preserve source bus state."""
    case = _case_with_three_winding_and_lplus()
    case.ac_buses["j"].active = False
    inventory = BpaAcInventory(Path("case.dat"), 10,
                               {"BE": 1, "B ": 4, "L ": 1, "L+": 1, "T ": 3},
                               {}, {}, {})
    with pytest.raises(BackendCapabilityError, match="Inactive BPA Bus"):
        PsdCaseAcAdapter().adapt(case, inventory, namespace="demo")


def test_three_winding_group_count_must_match_parsed_objects() -> None:
    """The REF grouping audit cannot disagree with live 3W objects."""
    case = _case_with_three_winding_and_lplus()
    case.attributes["transformer3w_grouping"]["grouped"] = 0
    inventory = BpaAcInventory(Path("case.dat"), 10,
                               {"BE": 1, "B ": 4, "L ": 1, "L+": 1, "T ": 3},
                               {}, {}, {})
    with pytest.raises(BackendCapabilityError, match="group count"):
        PsdCaseAcAdapter().adapt(case, inventory, namespace="demo")


def test_inactive_unmatched_ref_group_with_live_leg_is_rejected() -> None:
    """An incomplete REF X group cannot leave a T leg disguised as a 2W unit."""
    case = _case_with_three_winding_and_lplus()
    case.transformers2w["orphan"] = _equipment(
        "orphan", **{"from": "48", "to": "5A", "from_kv": 230.0,
                     "to_kv": 1.0, "r_pu": 0.01, "x_pu": 0.1})
    case.attributes["transformer3w_grouping"]["incomplete"] = 1
    case.attributes["ref_data"] = SimpleNamespace(transformers_3w=[
        SimpleNamespace(device_id="one", in_service="T"),
        SimpleNamespace(device_id="missing", in_service="F",
                        keys=(b"H", b"M", b"L", b"Z"))])
    inventory = BpaAcInventory(Path("case.dat"), 10,
                               {"BE": 1, "B ": 4, "L ": 1, "L+": 1, "T ": 4},
                               {}, {}, {})
    with pytest.raises(BackendCapabilityError, match="orphan T leg"):
        PsdCaseAcAdapter().adapt(case, inventory, namespace="demo")
