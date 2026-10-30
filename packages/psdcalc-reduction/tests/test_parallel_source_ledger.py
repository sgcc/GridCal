"""External provenance and L+ ownership contracts for offline fixtures."""

import pytest
import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.exceptions import IdentityError
from psdcalc.reduction.topology.parallel_provenance import (
    LPlusAssociation, LineCircuitProvenance, ParallelSourceLedger,
    SourceProfileContract,
)


def _source() -> tuple[pypsa.Network, object]:
    n = pypsa.Network()
    n.add("Bus", "a", v_nom=110)
    n.add("Bus", "b", v_nom=110)
    n.add("Line", "line", bus0="a", bus1="b", r=0.1, x=0.2)
    n.add("ShuntImpedance", "from", bus="a", b=-0.5)
    n.add("ShuntImpedance", "to", bus="b", b=-0.7)
    return n, PypsaAdapter().identity_sidecar(n, "test")


def _association(side: str = "FROM", card: str = "card") -> LPlusAssociation:
    n, ids = _source()
    bus = "a" if side == "FROM" else "b"
    shunt = "from" if side == "FROM" else "to"
    return LPlusAssociation(ids.source_id("Line", "line"), card, side,
                            ids.source_id("Bus", bus),
                            ids.source_id("ShuntImpedance", shunt),
                            -0.5 if side == "FROM" else -0.7)


def test_l_plus_association_requires_three_unique_keys() -> None:
    n, ids = _source()
    profile = SourceProfileContract("programmatic", "unavailable")
    for duplicate in (
        (_association(), _association()),
        (_association(card="card1"), _association(card="card2")),
        (_association(), _association("TO", "card"), _association("TO", "card")),
    ):
        with pytest.raises(IdentityError):
            ParallelSourceLedger(profile, (), duplicate).validate(n, ids)


def test_l_plus_both_sides_and_profile_round_trip(tmp_path) -> None:
    n, ids = _source()
    ledger = ParallelSourceLedger(
        SourceProfileContract("programmatic", "unavailable"), (),
        (_association("FROM", "card"), _association("TO", "card")))
    ledger.validate(n, ids)
    ledger.save_parquet(tmp_path)
    loaded = ParallelSourceLedger.load_parquet(tmp_path)
    assert loaded == ledger
    loaded.validate(n, ids)


def test_required_circuit_ids_and_raw_cards_are_unique() -> None:
    n, ids = _source()
    line = ids.source_id("Line", "line")
    required = SourceProfileContract("bpa_strict", "required")
    with pytest.raises(IdentityError):
        ParallelSourceLedger(required, (
            LineCircuitProvenance(line, "dat:1:hash", "", "L raw"),
        ), ()).validate(n, ids)
    ParallelSourceLedger(required, (
        LineCircuitProvenance(line, "dat:1:hash", "1", "L raw"),
    ), ()).validate(n, ids)


def test_duplicate_circuit_id_or_source_card_fails() -> None:
    """Circuit namespace is one canonical corridor, independent of names."""
    n, _ = _source()
    n.add("Line", "second", bus0="b", bus1="a", r=0.2, x=0.3)
    ids = PypsaAdapter().identity_sidecar(n, "test")
    first = ids.source_id("Line", "line")
    second = ids.source_id("Line", "second")
    required = SourceProfileContract("bpa_strict", "required")
    for cards, circuits in (
        (("card-1", "card-2"), ("1", "1")),
        (("card-1", "card-1"), ("1", "2")),
    ):
        ledger = ParallelSourceLedger(required, (
            LineCircuitProvenance(first, cards[0], circuits[0], "L raw 1"),
            LineCircuitProvenance(second, cards[1], circuits[1], "L raw 2")))
        with pytest.raises(IdentityError):
            ledger.validate(n, ids)


def test_audited_bpa_profiles_require_l_card_circuit_contract() -> None:
    """The two audited cases cannot declare circuit IDs unavailable."""
    for case_id in ("BPA10056814", "BPA10056819"):
        with pytest.raises(IdentityError):
            SourceProfileContract(case_id, "unavailable")
        profile = SourceProfileContract.audited_bpa_case(case_id)
        n, ids = _source()
        line = ids.source_id("Line", "line")
        with pytest.raises(IdentityError, match="L card"):
            ParallelSourceLedger(profile, (
                LineCircuitProvenance(line, "card", "1", "not an L card"),
            )).validate(n, ids)
