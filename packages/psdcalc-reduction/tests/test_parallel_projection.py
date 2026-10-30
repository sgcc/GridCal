"""Programmatic L0-to-L1 provenance projection contracts."""

import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.exceptions import MappingError
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.topology.parallel_provenance import (
    LPlusAssociation, LineCircuitProvenance, ParallelSourceLedger, SourceProfileContract,
)
from psdcalc.reduction.topology.parallel_projection import (
    project_parallel_source_ledger, reject_derived_protected_lines,
)


def _case(kind: str = "absorbed_internal"):
    source = pypsa.Network()
    for bus in ("a", "b", "c"):
        source.add("Bus", bus, v_nom=110)
    source.add("Line", "ab", bus0="a", bus1="b", x=0.1)
    source.add("Line", "bc", bus0="b", bus1="c", x=0.1)
    ids = PypsaAdapter().identity_sidecar(source, "n")
    child = pypsa.Network()
    for bus in ("a", "b"):
        child.add("Bus", bus, v_nom=110)
    child.add("Line", "ab", bus0="a", bus1="b", x=0.1)
    if kind == "aggregated":
        child.add("Line", "derived", bus0="a", bus1="b", x=0.2)
    child_ids = PypsaAdapter().identity_sidecar(child, "n")
    ab, bc = (ids.source_id("Line", name) for name in ("ab", "bc"))
    rows = (MapEntry(ab, ab, "preserved", "topology"),
            MapEntry(bc, child_ids.source_id("Line", "derived")
                     if kind == "aggregated" else None, kind, "topology"))
    ledger = ParallelSourceLedger(SourceProfileContract("fixture", "required"), (
        LineCircuitProvenance(ab, "L:1", "1", "L raw"),
        LineCircuitProvenance(bc, "L:2", "2", "L raw"),
    ))
    maps = MappingBundle(tuple(MapEntry(ids.source_id("Bus", name),
        child_ids.source_id("Bus", name if name != "c" else "b"),
        "preserved" if name != "c" else "aggregated", "topology")
        for name in ("a", "b", "c")), rows, ())
    return source, ids, child, child_ids, maps, ledger, ab, bc


def test_absorbed_line_keeps_l0_evidence_but_not_l1_circuit(tmp_path) -> None:
    """Absorption produces lineage only; source ledger stays complete."""
    source, ids, child, child_ids, maps, ledger, ab, bc = _case()
    before = ledger.save_parquet(tmp_path / "l0")
    projected = project_parallel_source_ledger(
        ledger, maps, source, ids, child, child_ids)
    assert len(ledger.lines) == 2
    assert [item.line_id for item in projected.ledger.lines] == [ab]
    assert projected.lineage[1].mapping_type == "absorbed_internal"
    assert projected.lineage[1].target_id is None
    assert projected.summary["l0_to_l1_absorbed_internal_count"] == 1
    projected.ledger.save_parquet(tmp_path / "l1")
    assert ParallelSourceLedger.load_parquet(tmp_path / "l1") == projected.ledger
    assert ParallelSourceLedger.load_parquet(before[0].parent) == ledger


def test_derived_line_has_no_bpa_circuit_and_cannot_be_protected() -> None:
    """Aggregated target receives lineage, never a fabricated source L card."""
    source, ids, child, child_ids, maps, ledger, ab, bc = _case("aggregated")
    projected = project_parallel_source_ledger(
        ledger, maps, source, ids, child, child_ids)
    derived, = projected.derived_lines
    assert derived not in {item.line_id for item in projected.ledger.lines}
    assert projected.summary["l0_to_l1_aggregated_count"] == 1
    with pytest.raises(MappingError, match="DERIVED_L1_LINE"):
        reject_derived_protected_lines(projected, frozenset({derived}))


def test_protected_source_line_absorption_fails_before_builder() -> None:
    """A protected member requires an exact one-to-one L1 Line identity."""
    source, ids, child, child_ids, maps, ledger, ab, bc = _case()
    with pytest.raises(MappingError, match="PROTECTED_SOURCE_LINE_LOST_IN_L1"):
        project_parallel_source_ledger(
            ledger, maps, source, ids, child, child_ids,
            protected_lines=frozenset({bc}))


def test_duplicate_or_missing_line_map_fails() -> None:
    """Projection never guesses a Line relation from backend names."""
    source, ids, child, child_ids, maps, ledger, ab, bc = _case()
    bad = MappingBundle(maps.bus_map, maps.branch_map[:1], ())
    with pytest.raises(MappingError, match="missing or duplicate"):
        project_parallel_source_ledger(ledger, bad, source, ids, child, child_ids)


def test_preserved_blank_circuit_stays_blank_through_l1_reload(tmp_path) -> None:
    """Projection never assigns a generated circuit ID to a blank source card."""
    source, ids, child, child_ids, maps, ledger, ab, bc = _case()
    blank = LineCircuitProvenance(ab, "L:1", "", "L raw")
    ledger = ParallelSourceLedger(
        SourceProfileContract("fixture", "required", "unique_source_card"),
        (blank, ledger.lines[1]))
    projected = project_parallel_source_ledger(
        ledger, maps, source, ids, child, child_ids)
    assert projected.ledger.lines[0].circuit_id == ""
    projected.ledger.save_parquet(tmp_path / "l1")
    assert ParallelSourceLedger.load_parquet(tmp_path / "l1") == projected.ledger


def test_lplus_base_shunt_and_terminal_require_exact_l1_identity(tmp_path) -> None:
    """An L+ owner survives with its typed blank source state or fails closed."""
    source, _, child, _, maps, ledger, ab, _ = _case()
    source.add("ShuntImpedance", "reactor", bus="a", b=0.0)
    child.add("ShuntImpedance", "reactor", bus="a", b=0.0)
    ids = PypsaAdapter().identity_sidecar(source, "n")
    child_ids = PypsaAdapter().identity_sidecar(child, "n")
    shunt = ids.source_id("ShuntImpedance", "reactor")
    association = LPlusAssociation(ab, "L+:3", "FROM",
                                   ids.source_id("Bus", "a"), shunt,
                                   None, "blank")
    ledger = ParallelSourceLedger(ledger.profile, ledger.lines, (association,))
    maps = MappingBundle(maps.bus_map, maps.branch_map,
                         (MapEntry(shunt, shunt, "preserved", "topology"),))
    projection = project_parallel_source_ledger(
        ledger, maps, source, ids, child, child_ids)
    projection.ledger.save_parquet(tmp_path / "l1")
    assert ParallelSourceLedger.load_parquet(tmp_path / "l1").l_plus == (association,)
    bad = MappingBundle(maps.bus_map, maps.branch_map, ())
    with pytest.raises(MappingError, match="missing or duplicate"):
        project_parallel_source_ledger(ledger, bad, source, ids, child, child_ids)
