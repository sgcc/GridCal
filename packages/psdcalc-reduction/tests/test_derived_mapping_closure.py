"""Synthetic contracts for deterministic secondary quotient mappings."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import json

import pypsa
import pytest

from psdcalc.reduction.adaptive.actions import ActionOperation, MappingTransition, ReductionAction
from psdcalc.reduction.adaptive.candidates import (
    CandidateReconstructionError, PypsaAdaptiveCandidateBuilder,
)
from psdcalc.reduction.adaptive.evidence import (
    AdaptiveEvidenceError, AdaptiveReductionEvidenceStore,
)
from psdcalc.reduction.adaptive.derived_mapping import (
    DerivedClosureError, DerivedMappingClosure, validate_derived_closures,
)
from psdcalc.reduction.adaptive.derived_mapping_pypsa import PypsaDerivedMappingAdapter
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import AggregationConfig, ParallelCorridorConfig
from psdcalc.reduction.fidelity.persistence import canonical_model_digest
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.protection.effective import EffectiveProtectionResolver
from psdcalc.reduction.topology.parallel_provenance import (
    LPlusAssociation, ParallelSourceLedger, SourceProfileContract,
)

from test_adaptive_candidate_stage2 import _config, _empty_ledger, _radial_action, _source
from test_adaptive_persistence_stage5 import _persist


def _parallel_source(count: int = 2, *, inactive_last: bool = False) -> pypsa.Network:
    """Create a radial action plus an ordinary cyclic parallel corridor."""
    network, _ = _source()
    for bus in ("h", "i", "j"):
        network.add("Bus", bus, v_nom=110.0)
    for index in range(count):
        network.add("Line", f"hi-{index}", bus0="i" if index % 2 else "h",
                    bus1="h" if index % 2 else "i", r=0.01 + index * 0.01,
                    x=0.1 + index * 0.01,
                    active=not (inactive_last and index == count - 1))
    network.add("Line", "ij", bus0="i", bus1="j", r=0.01, x=0.1)
    network.add("Line", "jh", bus0="j", bus1="h", r=0.01, x=0.1)
    return network


def _build_parallel(tmp_path: Path, count: int = 2):
    """Run a full candidate materialization with an unrelated parallel group."""
    source = _parallel_source(count)
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    action = _radial_action(sidecar)
    candidate = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar, actions=(action,),
        reduction_config=_config(tmp_path), source_ledger=_empty_ledger(),
    ).build((action.action_id,))
    return source, sidecar, candidate


def _identity_busmap(source: pypsa.Network, sidecar):
    """Return a complete StableId identity Bus partition."""
    return {sidecar.source_id("Bus", str(name)): sidecar.source_id("Bus", str(name))
            for name in source.buses.index}


def _contract_adapter(tmp_path: Path, source: pypsa.Network,
                      stable_busmap: dict, *, protection: ProtectionSet | None = None,
                      ledger: ParallelSourceLedger | None = None) -> PypsaDerivedMappingAdapter:
    """Bind one contract-level adapter to a supplied exact Bus partition."""
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    protected = protection or ProtectionSet(frozenset(), frozenset(), frozenset(),
                                            frozenset(), frozenset())
    effective = EffectiveProtectionResolver().resolve(
        network=source, sidecar=sidecar, config=_config(tmp_path),
        protection=protected, source_ledger=ledger or _empty_ledger(),
    )
    backend = {sidecar.backend_name(source_id): sidecar.backend_name(target_id)
               for source_id, target_id in stable_busmap.items()}
    count = len(set(stable_busmap.values()))
    plan = PartitionPlan(stable_busmap, backend, count, count, "reject")
    return PypsaDerivedMappingAdapter(
        source=source, sidecar=sidecar, partition=plan, config=_config(tmp_path),
        effective=effective, source_model_digest=canonical_model_digest(source, sidecar),
        source_sidecar_digest="a" * 64, partition_digest="b" * 64,
    )


def test_existing_ordinary_parallel_pair_is_a_legal_derived_effect(tmp_path: Path) -> None:
    """A direct radial action need not own an unrelated ordinary corridor."""
    source, _ = _source()
    for bus in ("h", "i", "j"):
        source.add("Bus", bus, v_nom=110.0)
    source.add("Line", "hi-1", bus0="h", bus1="i", r=0.01, x=0.1)
    source.add("Line", "hi-2", bus0="h", bus1="i", r=0.02, x=0.2)
    source.add("Line", "ij", bus0="i", bus1="j", r=0.01, x=0.1)
    source.add("Line", "jh", bus0="j", bus1="h", r=0.01, x=0.1)
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    action = _radial_action(sidecar)
    candidate = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar, actions=(action,),
        reduction_config=_config(tmp_path), source_ledger=_empty_ledger(),
    ).build((action.action_id,))

    rows = {row.source_id: row for row in candidate.mappings.branch_map}
    first = rows[sidecar.source_id("Line", "hi-1")]
    second = rows[sidecar.source_id("Line", "hi-2")]
    assert first.target_id == second.target_id
    assert first.mapping_type == second.mapping_type == "aggregated"
    assert candidate.identity.derived_mapping_closure_digest


def test_four_member_ordinary_group_and_reversed_orientation(tmp_path: Path) -> None:
    """Four Line StableIds form one group despite reversed source terminals."""
    _, sidecar, candidate = _build_parallel(tmp_path, 4)
    groups = [effect for effect in candidate.derived_mapping_closure.effects
              if effect.effect_type == "MANY_TO_ONE_BRANCH_AGGREGATION"]
    assert len(groups) == 1
    assert groups[0].source_ids == tuple(sorted(
        sidecar.source_id("Line", f"hi-{index}") for index in range(4)))


def test_inactive_member_and_nongrouper_attributes_remain_in_group(tmp_path: Path) -> None:
    """Active, carrier, and electrical values are not Line grouping keys."""
    source = _parallel_source(2, inactive_last=True)
    source.lines.loc["hi-0", "carrier"] = "carrier-a"
    source.lines.loc["hi-1", "carrier"] = "carrier-b"
    source.lines.loc["hi-0", "type"] = "line-type-a"
    source.lines.loc["hi-1", "type"] = "line-type-b"
    source.lines.loc["hi-1", "s_nom"] = 80.0
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    group = [effect for effect in _contract_adapter(
        tmp_path, source, _identity_busmap(source, sidecar)).expected().effects
        if effect.effect_type == "MANY_TO_ONE_BRANCH_AGGREGATION"]
    assert len(group) == 1
    assert len(group[0].source_ids) == 2


def test_one_bus_action_induces_group_and_deactivation_removes_it(tmp_path: Path) -> None:
    """Recompute from S1 after action deactivation, never patch a prior closure."""
    source = pypsa.Network()
    for bus in ("a", "b", "x"):
        source.add("Bus", bus, v_nom=110.0)
    source.add("Line", "ax", bus0="a", bus1="x", r=0.01, x=0.1)
    source.add("Line", "bx", bus0="b", bus1="x", r=0.02, x=0.2)
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    identity = _identity_busmap(source, sidecar)
    merged = dict(identity)
    merged[sidecar.source_id("Bus", "b")] = sidecar.source_id("Bus", "a")
    empty = _contract_adapter(tmp_path, source, identity).expected()
    active = _contract_adapter(tmp_path, source, merged).expected()
    assert empty.effects == ()
    assert len(active.effects) == 1
    assert active.effects[0].effect_type == "MANY_TO_ONE_BRANCH_AGGREGATION"
    assert active.digest != empty.digest
    assert _contract_adapter(tmp_path, source, identity).expected().digest == empty.digest


def test_single_bus_action_materializes_its_derived_group(tmp_path: Path) -> None:
    """The exact active action set drives an induced many-to-one Line mapping."""
    source = pypsa.Network()
    for bus in ("a", "b", "x"):
        source.add("Bus", bus, v_nom=110.0)
    source.add("Line", "ax", bus0="a", bus1="x", r=0.01, x=0.1)
    source.add("Line", "bx", bus0="b", bus1="x", r=0.02, x=0.2, active=False)
    source.add("Line", "ab", bus0="a", bus1="b", r=0.03, x=0.3)
    source.add("Generator", "slack", bus="x", control="Slack", p_nom=100.0)
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    bus_a = sidecar.source_id("Bus", "a")
    bus_b = sidecar.source_id("Bus", "b")
    action = ReductionAction.create(
        action_id="merge-b-to-a", operation=ActionOperation.RADIAL_ABSORPTION,
        source_ids=(bus_a, bus_b, sidecar.source_id("Line", "ab")), target_ids=(bus_a,),
        mapping_delta=(MappingTransition(bus_a, bus_a, "preserved"),
                       MappingTransition(bus_b, bus_a, "clustered"),
                       MappingTransition(sidecar.source_id("Line", "ab"), None,
                                         "absorbed_internal")),
        voltage_layer="110", component_id="component-a",
        region_fingerprint="region-a", audit_reason="synthetic one-action induced group",
    )
    candidate = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar, actions=(action,),
        reduction_config=_config(tmp_path), source_ledger=_empty_ledger(),
    ).build((action.action_id,))
    group = [effect for effect in candidate.derived_mapping_closure.effects
             if effect.effect_type == "MANY_TO_ONE_BRANCH_AGGREGATION"]
    assert len(group) == 1
    assert len(group[0].source_ids) == 2


def test_two_actions_jointly_induce_group(tmp_path: Path) -> None:
    """Neither one-sided Bus merge owns the combined grouping effect."""
    source = pypsa.Network()
    for bus in ("a", "b", "x", "y"):
        source.add("Bus", bus, v_nom=110.0)
    source.add("Line", "ax", bus0="a", bus1="x", r=0.01, x=0.1)
    source.add("Line", "by", bus0="b", bus1="y", r=0.02, x=0.2)
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    identity = _identity_busmap(source, sidecar)
    one = dict(identity)
    one[sidecar.source_id("Bus", "b")] = sidecar.source_id("Bus", "a")
    other = dict(identity)
    other[sidecar.source_id("Bus", "y")] = sidecar.source_id("Bus", "x")
    both = dict(one)
    both[sidecar.source_id("Bus", "y")] = sidecar.source_id("Bus", "x")
    assert not _contract_adapter(tmp_path, source, one).expected().effects
    assert not _contract_adapter(tmp_path, source, other).expected().effects
    result = _contract_adapter(tmp_path, source, both).expected()
    assert len(result.effects) == 1
    assert len(result.effects[0].source_ids) == 2


def test_protected_line_and_ordinary_line_sharing_endpoints(tmp_path: Path) -> None:
    """Identity preservation takes precedence over an ordinary endpoint group."""
    source = _parallel_source(2)
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    protected = sidecar.source_id("Line", "hi-0")
    protection = ProtectionSet(frozenset(), frozenset({protected}), frozenset(),
                               frozenset(), frozenset())
    adapter = _contract_adapter(tmp_path, source, _identity_busmap(source, sidecar),
                                protection=protection)
    assert not any(effect.effect_type == "MANY_TO_ONE_BRANCH_AGGREGATION"
                   for effect in adapter.expected().effects)


def test_l_plus_ledger_base_line_is_not_ordinary_group_member(tmp_path: Path) -> None:
    """An L+ base Line remains identity protected independently of Shunt b."""
    source = _parallel_source(2)
    source.add("ShuntImpedance", "reactor", bus="h", g=0.0, b=-0.01)
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    association = LPlusAssociation(
        base_line_id=sidecar.source_id("Line", "hi-0"),
        source_card_identity="synthetic-l-plus", terminal_side="FROM",
        terminal_bus_id=sidecar.source_id("Bus", "h"),
        shunt_id=sidecar.source_id("ShuntImpedance", "reactor"),
        source_reactor_value=4.0,
    )
    ledger = ParallelSourceLedger(SourceProfileContract("programmatic_pypsa", "unavailable"),
                                  l_plus=(association,))
    adapter = _contract_adapter(tmp_path, source, _identity_busmap(source, sidecar),
                                ledger=ledger)
    assert not adapter.expected().effects
    assert adapter.effective.source_ledger.l_plus == (association,)


def test_protected_member_survives_full_candidate_materialization(tmp_path: Path) -> None:
    """The source identity, not merely the expected closure, survives the quotient."""
    source = _parallel_source(2)
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    action = _radial_action(sidecar)
    protected = sidecar.source_id("Line", "hi-0")
    protection = ProtectionSet(frozenset(), frozenset({protected}), frozenset(),
                               frozenset(), frozenset())
    candidate = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar, actions=(action,),
        reduction_config=_config(tmp_path), protection=protection,
        source_ledger=_empty_ledger(),
    ).build((action.action_id,))
    row = next(row for row in candidate.mappings.branch_map if row.source_id == protected)
    assert row.target_id == protected
    assert row.mapping_type == "preserved"
    assert not any(effect.effect_type == "MANY_TO_ONE_BRANCH_AGGREGATION"
                   for effect in candidate.derived_mapping_closure.effects)


def test_l_plus_base_and_shunt_survive_full_candidate(tmp_path: Path) -> None:
    """The source-ledger L+ identity contract is retained in a fresh quotient."""
    source = _parallel_source(2)
    source.add("ShuntImpedance", "reactor", bus="h", g=0.0, b=-0.01)
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    action = _radial_action(sidecar)
    line_id = sidecar.source_id("Line", "hi-0")
    shunt_id = sidecar.source_id("ShuntImpedance", "reactor")
    association = LPlusAssociation(
        base_line_id=line_id, source_card_identity="synthetic-l-plus",
        terminal_side="FROM", terminal_bus_id=sidecar.source_id("Bus", "h"),
        shunt_id=shunt_id, source_reactor_value=4.0,
    )
    ledger = ParallelSourceLedger(SourceProfileContract("programmatic_pypsa", "unavailable"),
                                  l_plus=(association,))
    config = _config(tmp_path).model_copy(update={"aggregation": AggregationConfig(
        parallel_corridors=ParallelCorridorConfig(policy="preserve_protected_members"))})
    candidate = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar, actions=(action,),
        reduction_config=config, source_ledger=ledger,
    ).build((action.action_id,))
    line_row = next(row for row in candidate.mappings.branch_map if row.source_id == line_id)
    shunt_row = next(row for row in candidate.mappings.device_map if row.source_id == shunt_id)
    assert (line_row.target_id, line_row.mapping_type) == (line_id, "preserved")
    assert (shunt_row.target_id, shunt_row.mapping_type) == (shunt_id, "preserved")
    assert candidate.derived_mapping_closure.effects[0].effect_type == "ABSORBED_INTERNAL"


def test_l_plus_line_cannot_be_legalized_by_ordinary_aggregation(tmp_path: Path) -> None:
    """A source-ledger identity violation produces deterministic failure evidence."""
    source = _parallel_source(2)
    source.add("ShuntImpedance", "reactor", bus="h", g=0.0, b=-0.01)
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    action = _radial_action(sidecar)
    association = LPlusAssociation(
        base_line_id=sidecar.source_id("Line", "hi-0"),
        source_card_identity="synthetic-l-plus", terminal_side="FROM",
        terminal_bus_id=sidecar.source_id("Bus", "h"),
        shunt_id=sidecar.source_id("ShuntImpedance", "reactor"),
        source_reactor_value=4.0,
    )
    ledger = ParallelSourceLedger(SourceProfileContract("programmatic_pypsa", "unavailable"),
                                  l_plus=(association,))
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar, actions=(action,),
        reduction_config=_config(tmp_path), source_ledger=ledger,
    )
    with pytest.raises(CandidateReconstructionError) as raised:
        builder.build((action.action_id,))
    assert raised.value.failure_code == "UNAUTHORIZED_PROTECTED_BRANCH_AGGREGATION"
    assert raised.value.expected_closure_digest is not None
    assert raised.value.offending_identity_fingerprints


def test_missing_unexpected_and_wrong_membership_are_distinct(tmp_path: Path) -> None:
    """Bidirectional comparison rejects missing, extra, and substituted members."""
    source = _parallel_source(2)
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    expected = _contract_adapter(tmp_path, source, _identity_busmap(source, sidecar)).expected()
    empty = DerivedMappingClosure(expected.basis, ())
    with pytest.raises(DerivedClosureError, match="DERIVED_CLOSURE_MISSING_EFFECT"):
        validate_derived_closures(expected, empty)
    with pytest.raises(DerivedClosureError, match="DERIVED_CLOSURE_UNEXPECTED_EFFECT"):
        validate_derived_closures(empty, expected)
    original = next(effect for effect in expected.effects
                    if effect.effect_type == "MANY_TO_ONE_BRANCH_AGGREGATION")
    substitute = sidecar.source_id("Line", "ij")
    members = tuple(sorted((original.source_ids[0], substitute)))
    wrong = replace(original, source_ids=members,
                    eligibility=tuple(sorted((item.encode(), False, False) for item in members)))
    with pytest.raises(DerivedClosureError, match="DERIVED_CLOSURE_MEMBERSHIP_MISMATCH"):
        validate_derived_closures(expected, DerivedMappingClosure(expected.basis, (wrong,)))


def test_actual_map_coverage_and_protected_aggregation_fail_closed(tmp_path: Path) -> None:
    """Actual normalization checks every source row and protected StableId."""
    source, sidecar, candidate = _build_parallel(tmp_path)
    adapter = _contract_adapter(tmp_path, source, _identity_busmap(source, sidecar))
    rows = candidate.mappings.branch_map
    incomplete = replace(candidate.mappings, branch_map=rows[:-1])
    with pytest.raises(DerivedClosureError, match="DERIVED_CLOSURE_MEMBERSHIP_MISMATCH"):
        adapter.actual(incomplete, candidate.network, candidate.sidecar)
    protected = sidecar.source_id("Line", "hi-0")
    protection = ProtectionSet(frozenset(), frozenset({protected}), frozenset(),
                               frozenset(), frozenset())
    candidate_busmap = {row.source_id: row.target_id for row in candidate.mappings.bus_map}
    protected_adapter = _contract_adapter(tmp_path, source, candidate_busmap,
                                          protection=protection)
    with pytest.raises(DerivedClosureError, match="UNAUTHORIZED_PROTECTED_BRANCH_AGGREGATION"):
        protected_adapter.actual(candidate.mappings, candidate.network, candidate.sidecar)
    missing_protected = replace(candidate.mappings, branch_map=tuple(
        row for row in rows if row.source_id != protected))
    with pytest.raises(DerivedClosureError, match="UNAUTHORIZED_PROTECTED_BRANCH_AGGREGATION"):
        protected_adapter.actual(missing_protected, candidate.network, candidate.sidecar)


def test_empty_closure_digest_is_stable(tmp_path: Path) -> None:
    """No secondary effect has an explicit versioned canonical digest."""
    source, sidecar = _source()
    first = _contract_adapter(tmp_path, source, _identity_busmap(source, sidecar)).expected()
    second = _contract_adapter(tmp_path, source, _identity_busmap(source, sidecar)).expected()
    assert first.effects == ()
    assert first.digest == second.digest
    assert first.canonical_payload()["schema"] == "derived-mapping-closure/v1"


def test_permuted_source_and_partition_order_preserves_digest(tmp_path: Path) -> None:
    """Dataframe row and Bus-map iteration order are outside semantic identity."""
    source = _parallel_source(4)
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    busmap = _identity_busmap(source, sidecar)
    first = _contract_adapter(tmp_path, source, busmap).expected()
    source.lines = source.lines.iloc[::-1].copy()
    second = _contract_adapter(tmp_path, source, dict(reversed(list(busmap.items())))).expected()
    assert first.digest == second.digest


def test_fresh_process_recomputes_same_closure_digest(tmp_path: Path) -> None:
    """Fresh Python import and directory produce the same closure identity."""
    source = _parallel_source(2)
    sidecar = PypsaAdapter().identity_sidecar(source, "stage2")
    expected = _contract_adapter(tmp_path, source, _identity_busmap(source, sidecar)).expected().digest
    script = (
        "from pathlib import Path\n"
        "from test_derived_mapping_closure import _parallel_source, _contract_adapter, _identity_busmap\n"
        "from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter\n"
        "n = _parallel_source(2)\n"
        "s = PypsaAdapter().identity_sidecar(n, 'stage2')\n"
        "print(_contract_adapter(Path('pr026-fresh'), n, _identity_busmap(n, s)).expected().digest)\n"
    )
    process = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parent,
                             capture_output=True, text=True, check=True)
    assert process.stdout.strip().splitlines()[-1] == expected


def test_candidate_identity_ignores_only_config_paths(tmp_path: Path) -> None:
    """Full config tracks paths while closure-bound candidate identity does not."""
    _, _, first = _build_parallel(tmp_path / "first")
    _, _, second = _build_parallel(tmp_path / "second")
    assert first.identity.reduction_config_digest != second.identity.reduction_config_digest
    assert first.identity.derived_mapping_closure_digest == second.identity.derived_mapping_closure_digest
    assert first.identity.candidate_fingerprint == second.identity.candidate_fingerprint


def test_actual_map_order_is_irrelevant_and_wrong_membership_fails(tmp_path: Path) -> None:
    """Actual effect uses source membership, not target names or row ordering."""
    source, sidecar, candidate = _build_parallel(tmp_path, 4)
    busmap = {row.source_id: row.target_id for row in candidate.mappings.bus_map}
    adapter = _contract_adapter(tmp_path, source, busmap)
    original = adapter.actual(candidate.mappings, candidate.network, candidate.sidecar)
    reversed_rows = replace(candidate.mappings,
                            branch_map=tuple(reversed(candidate.mappings.branch_map)))
    assert adapter.actual(reversed_rows, candidate.network, candidate.sidecar).digest == original.digest
    line_rows = {row.source_id: row for row in candidate.mappings.branch_map}
    wrong_source = sidecar.source_id("Line", "hi-3")
    unrelated_target = line_rows[sidecar.source_id("Line", "ij")].target_id
    tampered = replace(candidate.mappings, branch_map=tuple(
        replace(row, target_id=unrelated_target) if row.source_id == wrong_source else row
        for row in candidate.mappings.branch_map))
    with pytest.raises(DerivedClosureError, match="DERIVED_CLOSURE_MEMBERSHIP_MISMATCH"):
        adapter.actual(tampered, candidate.network, candidate.sidecar)


def test_persisted_closure_replays_and_tampering_fails(tmp_path: Path) -> None:
    """Replay independently rebuilds every stored expected/actual closure row."""
    run_dir, _ = _persist(tmp_path)
    evidence_path = run_dir / "adaptive/evidence.json"
    artifact_path = run_dir / "adaptive/derived_closures.json"
    evidence = json.loads(evidence_path.read_text())
    closures = json.loads(artifact_path.read_text())
    assert evidence["candidate_identity_schema"] == "adaptive-candidate-identity/v2"
    assert closures
    assert all(row["expected_closure_digest"] == row["actual_closure_digest"]
               for row in closures)
    assert AdaptiveReductionEvidenceStore.replay(run_dir).verified
    closures[0]["expected_closure"]["basis"]["grouping_contract"] = "tampered"
    artifact_path.write_text(json.dumps(closures), encoding="utf-8")
    with pytest.raises(AdaptiveEvidenceError, match="DIGEST_MISMATCH"):
        AdaptiveReductionEvidenceStore.replay(run_dir)


def test_historical_v2_candidate_identity_is_not_reinterpreted(tmp_path: Path) -> None:
    """A v2 envelope lacking the new candidate identity version fails explicitly."""
    run_dir, _ = _persist(tmp_path)
    path = run_dir / "adaptive/evidence.json"
    evidence = json.loads(path.read_text())
    del evidence["candidate_identity_schema"]
    path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(AdaptiveEvidenceError, match="historical candidate identity"):
        AdaptiveReductionEvidenceStore.replay(run_dir)
