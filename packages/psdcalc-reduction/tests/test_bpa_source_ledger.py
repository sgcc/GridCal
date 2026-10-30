"""Programmatic DAT provenance binding for the strict BPA source profile."""

from pathlib import Path
from types import SimpleNamespace

import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.readiness.source_ledger import build_bpa_source_ledger
from psdcalc.reduction.exceptions import BackendCapabilityError


def _card(kind: bytes, left: bytes, right: bytes, circuit: bytes) -> bytes:
    return kind + b"    " + left.ljust(12) + b" " + right.ljust(12) + circuit + b" " * 36


def test_source_lplus_reactor_value_is_provenance_not_pypsa_b(tmp_path: Path) -> None:
    """Exact L+ source card binds two Shunts despite projected units differing."""
    dat = tmp_path / "source.dat"
    dat.write_bytes(_card(b"L ", b"A", b"B", b"1") + b"\n" +
                    _card(b"L+", b"A", b"B", b"1") + b"\n")
    network = pypsa.Network()
    for name in ("A", "B"):
        network.add("Bus", name, v_nom=110)
    network.add("Line", "line", bus0="A", bus1="B", r=0.1, x=0.2)
    network.add("ShuntImpedance", "line:L+:FROM", bus="A", b=-0.004)
    network.add("ShuntImpedance", "line:L+:TO", bus="B", b=0.002)
    sidecar = PypsaAdapter().identity_sidecar(network, "case")
    source = SimpleNamespace(key="line", active=True,
        source_identity=SimpleNamespace(dat_card_type="L", dat_line_numbers=(1,)),
        attributes={"circuit_id": "1", "from_reactor_mvar": 4.0,
                    "to_reactor_mvar": -2.0})
    case = SimpleNamespace(ac_lines={"line": source})
    ledger = build_bpa_source_ledger(case, network, sidecar, dat,
                                      case_id="BPA10056814")
    assert len(ledger.lines) == 1
    assert len(ledger.l_plus) == 2
    assert {row.signed_reactor_value for row in ledger.l_plus} == {4.0, -2.0}
    assert {row.circuit_id for row in ledger.lines} == {"1"}
    ledger.validate(network, sidecar)


def test_source_ledger_rejects_unbound_lplus(tmp_path: Path) -> None:
    """A source L+ card cannot disappear from the authoritative ledger."""
    dat = tmp_path / "source.dat"
    dat.write_bytes(_card(b"L ", b"A", b"B", b"1") + b"\n" +
                    _card(b"L+", b"C", b"D", b"1") + b"\n")
    network = pypsa.Network()
    network.add("Bus", "A", v_nom=110)
    network.add("Bus", "B", v_nom=110)
    network.add("Line", "line", bus0="A", bus1="B", r=0.1, x=0.2)
    sidecar = PypsaAdapter().identity_sidecar(network, "case")
    source = SimpleNamespace(key="line", active=True,
        source_identity=SimpleNamespace(dat_card_type="L", dat_line_numbers=(1,)),
        attributes={"circuit_id": "1"})
    with pytest.raises(BackendCapabilityError, match="Unbound L\\+"):
        build_bpa_source_ledger(SimpleNamespace(ac_lines={"line": source}),
                                network, sidecar, dat,
                                case_id="BPA10056814")


def test_strict_bpa_profile_rejects_blank_source_circuit_id(tmp_path: Path) -> None:
    """A missing L-card circuit ID cannot be inferred from its backend name."""
    dat = tmp_path / "source.dat"
    dat.write_bytes(_card(b"L ", b"A", b"B", b" ") + b"\n")
    network = pypsa.Network()
    network.add("Bus", "A", v_nom=110)
    network.add("Bus", "B", v_nom=110)
    network.add("Line", "line", bus0="A", bus1="B", r=0.1, x=0.2)
    sidecar = PypsaAdapter().identity_sidecar(network, "case")
    source = SimpleNamespace(key="line", active=True,
        source_identity=SimpleNamespace(dat_card_type="L", dat_line_numbers=(1,)),
        attributes={"circuit_id": ""})
    with pytest.raises(BackendCapabilityError, match="circuit ID"):
        build_bpa_source_ledger(SimpleNamespace(ac_lines={"line": source}),
                                network, sidecar, dat,
                                case_id="BPA10056814")
