"""Offline provenance tests for BPA parallel-corridor auditing."""

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.exceptions import BackendCapabilityError
from psdcalc.reduction.readiness.parallel_corridor import ParallelCorridorAudit


def _card(kind: bytes, left: bytes, right: bytes, circuit: bytes) -> bytes:
    return kind + b"    " + left.ljust(12) + b" " + right.ljust(12) + circuit + b" " * 36


def _source(name: str, line_number: int, circuit: str) -> SimpleNamespace:
    return SimpleNamespace(
        key=name, active=True,
        source_identity=SimpleNamespace(dat_card_type="L", dat_line_numbers=(line_number,)),
        attributes={"circuit_id": circuit, "from_reactor_mvar": 2.0,
                    "to_reactor_mvar": -1.0} if name == "l1" else {"circuit_id": circuit},
    )


def test_parallel_audit_preserves_raw_provenance_and_cycle_type(tmp_path: Path) -> None:
    """A true double circuit is distinct from its L+ extension and triangle cycle."""
    dat = tmp_path / "case.dat"
    raw = [
        _card(b"L ", b"A", b"B", b"1"),
        _card(b"L ", b"A", b"B", b"2"),
        _card(b"L+", b"A", b"B", b"1"),
        _card(b"L ", b"B", b"C", b"1"),
        _card(b"L ", b"C", b"A", b"1"),
        _card(b"L ", b"D", b"E", b"1"),
        _card(b"L ", b"D", b"E", b"2"),
    ]
    dat.write_bytes(b"\n".join(raw) + b"\n")
    network = pypsa.Network()
    for bus in "ABCDE":
        network.add("Bus", bus, v_nom=115.0)
    lines = {"l1": ("A", "B"), "l2": ("A", "B"),
             "l3": ("B", "C"), "l4": ("C", "A"),
             "l5": ("D", "E"), "l6": ("D", "E")}
    for name, (left, right) in lines.items():
        network.add("Line", name, bus0=left, bus1=right, r=0.01, x=0.1,
                    g=0.0, b=0.0, length=10.0, s_nom=50.0)
    sidecar = PypsaAdapter().identity_sidecar(network, "demo")
    case = SimpleNamespace(ac_lines={
        "l1": _source("l1", 1, "1"), "l2": _source("l2", 2, "2"),
        "l3": _source("l3", 4, "1"), "l4": _source("l4", 5, "1"),
        "l5": _source("l5", 6, "1"), "l6": _source("l6", 7, "2"),
    })
    targets = {sidecar.source_id("Line", "l1").encode(): "sid:target"}
    result = ParallelCorridorAudit().run(
        network=network, sidecar=sidecar, case=case, dat_path=dat,
        quotient_targets=targets,
    )
    rows = {row["backend_name"]: row for row in result.rows}
    assert result.summary["parallel_corridor_count"] == 2
    assert result.summary["genuine_double_circuit_count"] == 2
    assert result.summary["l_plus_false_parallel_count"] == 0
    assert rows["l1"]["cycle_type"] == "both"
    assert rows["l5"]["cycle_type"] == "parallel_only"
    assert rows["l1"]["dat_raw_card"] == raw[0]
    assert rows["l1"]["l_plus_raw_card"] == raw[2]
    assert rows["l1"]["current_quotient_target"] == "sid:target"
    assert rows["l1"]["classification"] == "genuine_parallel_circuits"
    assert all(row["card_type"] == "L" for row in result.rows)
    paths = result.write(tmp_path / "audit")
    assert pd.read_parquet(paths.parquet_path).shape[0] == 4
    assert paths.summary_path.is_file()


def test_parallel_audit_rejects_source_card_mismatch(tmp_path: Path) -> None:
    """A Line bound to an L+ raw card cannot be classified as a real circuit."""
    dat = tmp_path / "case.dat"
    dat.write_bytes(_card(b"L+", b"A", b"B", b"1") + b"\n")
    network = pypsa.Network()
    network.add("Bus", "A", v_nom=115.0)
    network.add("Bus", "B", v_nom=115.0)
    network.add("Line", "l1", bus0="A", bus1="B", r=0.01, x=0.1)
    sidecar = PypsaAdapter().identity_sidecar(network, "demo")
    source = _source("l1", 1, "1")
    source.attributes = {"circuit_id": "1"}
    case = SimpleNamespace(ac_lines={"l1": source})
    with pytest.raises(BackendCapabilityError, match="DAT L card"):
        ParallelCorridorAudit().run(network=network, sidecar=sidecar,
                                    case=case, dat_path=dat)


def test_parallel_audit_rejects_orphan_lplus(tmp_path: Path) -> None:
    """An unbound L+ cannot disappear from a source-provenance audit."""
    dat = tmp_path / "case.dat"
    dat.write_bytes(_card(b"L ", b"A", b"B", b"1") + b"\n" +
                    _card(b"L+", b"C", b"D", b"1") + b"\n")
    network = pypsa.Network()
    network.add("Bus", "A", v_nom=115.0)
    network.add("Bus", "B", v_nom=115.0)
    network.add("Line", "l1", bus0="A", bus1="B", r=0.01, x=0.1)
    sidecar = PypsaAdapter().identity_sidecar(network, "demo")
    source = _source("l1", 1, "1")
    source.attributes = {"circuit_id": "1"}
    case = SimpleNamespace(ac_lines={"l1": source})
    with pytest.raises(BackendCapabilityError, match="Unbound L\\+"):
        ParallelCorridorAudit().run(network=network, sidecar=sidecar,
                                    case=case, dat_path=dat)
