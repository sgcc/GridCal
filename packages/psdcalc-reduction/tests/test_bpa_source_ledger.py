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


def test_unique_blank_source_circuit_id_remains_blank_after_reload(tmp_path: Path) -> None:
    """One uniquely bound L card retains its original blank circuit value."""
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
    ledger = build_bpa_source_ledger(SimpleNamespace(ac_lines={"line": source}),
                                     network, sidecar, dat,
                                     case_id="BPA10056814")
    assert ledger.lines[0].circuit_id == ""
    paths = ledger.save_parquet(tmp_path / "ledger")
    from psdcalc.reduction.topology.parallel_provenance import ParallelSourceLedger
    loaded = ParallelSourceLedger.load_parquet(paths[0].parent)
    assert loaded == ledger
    loaded.validate(network, sidecar)


def test_parallel_blank_source_id_requires_unique_blank_member(tmp_path: Path) -> None:
    """One blank parallel member is source-bound; a second is ambiguous."""
    dat = tmp_path / "source.dat"
    dat.write_bytes(_card(b"L ", b"A", b"B", b" ") + b"\n" +
                    _card(b"L ", b"A", b"B", b"1") + b"\n")
    network = pypsa.Network()
    network.add("Bus", "A", v_nom=110)
    network.add("Bus", "B", v_nom=110)
    for name in ("blank", "one"):
        network.add("Line", name, bus0="A", bus1="B", r=0.1, x=0.2)
    sidecar = PypsaAdapter().identity_sidecar(network, "case")
    def item(name: str, number: int, circuit: str) -> SimpleNamespace:
        return SimpleNamespace(key=name, active=True,
            source_identity=SimpleNamespace(dat_card_type="L", dat_line_numbers=(number,)),
            attributes={"circuit_id": circuit})
    case = SimpleNamespace(ac_lines={"blank": item("blank", 1, ""),
                                     "one": item("one", 2, "1")})
    ledger = build_bpa_source_ledger(case, network, sidecar, dat,
                                     case_id="BPA10056814")
    assert sorted(row.circuit_id for row in ledger.lines) == ["", "1"]
    dat.write_bytes(_card(b"L ", b"A", b"B", b" ") + b"\n" +
                    _card(b"L ", b"A", b"B", b" ") + b"\n")
    case.ac_lines["one"] = item("one", 2, "")
    with pytest.raises(Exception, match="Multiple blank circuit IDs"):
        build_bpa_source_ledger(case, network, sidecar, dat,
                                case_id="BPA10056814")


def test_blank_circuit_lplus_binds_exact_card_and_two_shunts(tmp_path: Path) -> None:
    """Blank circuit remains blank while one L+ card owns both terminal Shunts."""
    dat = tmp_path / "source.dat"
    dat.write_bytes(_card(b"L ", b"A", b"B", b" ") + b"\n" +
                    _card(b"L+", b"A", b"B", b" ") + b"\n")
    network = pypsa.Network()
    for bus in ("A", "B"):
        network.add("Bus", bus, v_nom=110)
    network.add("Line", "line", bus0="A", bus1="B", r=0.1, x=0.2)
    network.add("ShuntImpedance", "line:L+:FROM", bus="A", b=-0.004)
    network.add("ShuntImpedance", "line:L+:TO", bus="B", b=0.002)
    sidecar = PypsaAdapter().identity_sidecar(network, "case")
    source = SimpleNamespace(key="line", active=True,
        source_identity=SimpleNamespace(dat_card_type="L", dat_line_numbers=(1,)),
        attributes={"circuit_id": "", "from_reactor_mvar": 4.0,
                    "to_reactor_mvar": -2.0})
    ledger = build_bpa_source_ledger(SimpleNamespace(ac_lines={"line": source}),
                                     network, sidecar, dat,
                                     case_id="BPA10056814")
    assert ledger.lines[0].circuit_id == ""
    assert len(ledger.l_plus) == 2
    ledger.save_parquet(tmp_path / "ledger")
    from psdcalc.reduction.topology.parallel_provenance import ParallelSourceLedger
    assert ParallelSourceLedger.load_parquet(tmp_path / "ledger") == ledger


def test_duplicate_source_card_binding_fails_even_with_blank_circuit(tmp_path: Path) -> None:
    """Two Lines may not claim the same raw DAT L card."""
    dat = tmp_path / "source.dat"
    dat.write_bytes(_card(b"L ", b"A", b"B", b" ") + b"\n")
    network = pypsa.Network()
    for bus in ("A", "B"):
        network.add("Bus", bus, v_nom=110)
    for name in ("first", "second"):
        network.add("Line", name, bus0="A", bus1="B", r=0.1, x=0.2)
    sidecar = PypsaAdapter().identity_sidecar(network, "case")
    source = lambda name: SimpleNamespace(key=name, active=True,
        source_identity=SimpleNamespace(dat_card_type="L", dat_line_numbers=(1,)),
        attributes={"circuit_id": ""})
    case = SimpleNamespace(ac_lines={name: source(name) for name in ("first", "second")})
    with pytest.raises(Exception, match="Duplicate or blank source Line card identity"):
        build_bpa_source_ledger(case, network, sidecar, dat,
                                case_id="BPA10056814")
