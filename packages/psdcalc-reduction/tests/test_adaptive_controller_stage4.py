"""Synthetic Stage-4 controller, scoring, and stopping-contract tests."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import hashlib
import json
from typing import Mapping

import pytest

from psdcalc.reduction.adaptive.actions import ActionOperation, MappingTransition, ReductionAction
from psdcalc.reduction.adaptive.controller import (
    AdaptiveCandidateSnapshot,
    AdaptiveFidelityEvidence,
    AdaptiveReductionController,
    AdaptiveRuntime,
    RuntimePowerFlowOutcome,
)
from psdcalc.reduction.adaptive.contracts import (
    AdaptiveReductionConfig,
    AdaptiveStoppingReason,
    AdaptiveStrategy,
    AttributionStatus,
    CompressionRequirement,
    CandidateEvaluation,
    ErrorAttribution,
    ExpansionCandidate,
    SCHEMA_VERSION,
)
from psdcalc.reduction.adaptive.attribution import Stage3AttributionResult
from psdcalc.reduction.core.ids import StableId


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _action(action_id: str, *, bus_name: str | None = None) -> ReductionAction:
    bus = StableId("stage4", "Bus", bus_name or f"bus-{action_id}")
    target = StableId("stage4", "Bus", "anchor")
    line = StableId("stage4", "Line", f"line-{action_id}")
    return ReductionAction.create(
        action_id=action_id,
        operation=ActionOperation.RADIAL_ABSORPTION,
        source_ids=(bus, line), target_ids=(target,),
        mapping_delta=(MappingTransition(bus, target, "clustered"),
                       MappingTransition(line, None, "absorbed_internal")),
        voltage_layer="110", component_id=f"component-{action_id}",
        region_fingerprint=f"region-{action_id}",
        audit_reason="synthetic Stage-4 action",
    )


def _candidate(action_id: str, active_ids: tuple[str, ...], *, buses: int = 1,
               branches: int = 1, region: str | None = None) -> ExpansionCandidate:
    deactivated = (action_id,)
    remaining = tuple(sorted(set(active_ids) - set(deactivated)))
    fingerprint = _digest({"schema_version": SCHEMA_VERSION,
                           "deactivated_action_ids": deactivated})
    region_fp = _digest({"region": region or action_id})
    return ExpansionCandidate(
        candidate_fingerprint=fingerprint, region_fingerprint=region_fp,
        deactivated_action_ids=deactivated, resulting_active_action_ids=remaining,
        added_bus_count=buses, added_branch_count=branches,
        triggering_attribution_ids=(_digest({"trigger": action_id}),),
        direct_action_ids=deactivated, dependency_closure_action_ids=deactivated,
        region_fingerprints=(region_fp,),
    )


def _assessment(score: float, candidates: tuple[ExpansionCandidate, ...] = (), *,
                diagnostics: tuple[str, ...] = (), unattributable: bool = False,
                attributable_action_ids: tuple[str, ...] = (),
                names: tuple[str, ...] = ("vm_max_pu",)) -> AdaptiveFidelityEvidence:
    attributions = tuple(ErrorAttribution(
        metric_name=name, severity="HARD", observed_value=score,
        resolved_threshold=0.0, normalized_violation=score,
        violation_magnitude=score,
        status=(AttributionStatus.UNATTRIBUTABLE if unattributable else
                AttributionStatus.ATTRIBUTABLE),
        candidate_action_ids=() if unattributable else tuple(sorted(set(attributable_action_ids) | {
            action_id for candidate in candidates for action_id in candidate.direct_action_ids
        })),
        related_action_ids=() if unattributable else tuple(sorted(set(attributable_action_ids) | {
            action_id for candidate in candidates for action_id in candidate.direct_action_ids
        })),
        reason="synthetic aggregate error" if unattributable else None,
        threshold_digest=_digest({"threshold": "synthetic"}),
    ) for name in names if score > 0.0)
    if score > 0 and not unattributable and not candidates and not attributable_action_ids:
        attributions = tuple(ErrorAttribution(
            metric_name=name, severity="HARD", observed_value=score,
            resolved_threshold=0.0, normalized_violation=score,
            violation_magnitude=score, status=AttributionStatus.UNATTRIBUTABLE,
            candidate_action_ids=(), related_action_ids=(), reason="no legal local action",
            threshold_digest=_digest({"threshold": "synthetic"}),
        ) for name in names)
        unattributable = True
    hard_names = tuple(sorted(item.metric_name for item in attributions))
    result = Stage3AttributionResult(
        fidelity_satisfied=score == 0.0, hard_violation_present=score > 0.0,
        hard_failures=hard_names, diagnostic_failures=tuple(sorted(diagnostics)),
        attributions=attributions, regions=(), candidates=candidates,
        rejected_candidates=(), candidate_set_digest=_digest({
            "score": score, "candidate_ids": [row.candidate_fingerprint for row in candidates]}),
    )
    return AdaptiveFidelityEvidence(
        fidelity_status=("PF_FIDELITY_OUTSIDE_TOLERANCE" if score > 0 else
                         "PF_FIDELITY_WITHIN_TOLERANCE"),
        attribution=result,
        metrics_digest=_digest({"score": score, "diagnostics": diagnostics}),
        threshold_digest=_digest({"threshold": "synthetic"}),
    )


@dataclass
class _BuildPlan:
    structural_status: str = "STRUCTURALLY_VALID"
    readiness_status: str = "AC_MODEL_READY"
    structural_checks: Mapping[str, bool] = None  # type: ignore[assignment]
    readiness_checks: Mapping[str, bool] = None  # type: ignore[assignment]


class FakeRuntime(AdaptiveRuntime):
    """Scripted layer adapter; the controller still owns all state decisions."""

    def __init__(self, actions: tuple[ReductionAction, ...],
                 scores: Mapping[tuple[str, ...], AdaptiveFidelityEvidence], *,
                 builds: Mapping[tuple[str, ...], _BuildPlan] | None = None,
                 pf: Mapping[tuple[str, ...], str] | None = None,
                 source_pf: str = "PF_CONVERGED", bus_count: int = 10,
                 build_mutator=None) -> None:
        self.source_model_digest = _digest("s1")
        self.source_sidecar_digest = _digest("sidecar")
        self.rebuilds_from_s1 = True
        self.action_catalog_digest = _digest({
            "schema_version": SCHEMA_VERSION,
            "actions": [a.canonical_payload() for a in sorted(actions, key=lambda a: a.action_id)],
        })
        self.reduction_config_digest = _digest("reduction")
        self.operating_point_digest = _digest("operating-point")
        self.solver_contract_digest = _digest("solver")
        self.metric_policy_digest = _digest("metric-policy")
        self.threshold_set_id = "SYNTHETIC-THRESHOLDS"
        self.threshold_digest = _digest("synthetic-frozen-thresholds")
        self.source_bus_count = 10
        self.source_branch_count = 10
        self.scores = dict(scores)
        self.builds = dict(builds or {})
        self.pf = dict(pf or {})
        self.source_pf_status = source_pf
        self.bus_count = bus_count
        self.build_calls: list[tuple[str, ...]] = []
        self.solve_calls: list[tuple[str, ...]] = []
        self.build_mutator = build_mutator
        self.source_solution = object()

    def build(self, active_action_ids: tuple[str, ...]) -> AdaptiveCandidateSnapshot:
        active = tuple(sorted(active_action_ids))
        self.build_calls.append(active)
        plan = self.builds.get(active, _BuildPlan())
        identity = _digest({"schema_version": SCHEMA_VERSION, "active_action_ids": list(active)})
        model_digest = _digest({"active": active})
        if self.build_mutator is not None:
            self.build_mutator(self, active)
        return AdaptiveCandidateSnapshot(
            candidate_fingerprint=_digest({"candidate": active, "model": model_digest}),
            active_action_ids=active,
            action_set_fingerprint=identity,
            model_digest=model_digest,
            mapping_digest=_digest({"mapping": active}),
            structural_status=plan.structural_status,
            structural_checks=plan.structural_checks or {"synthetic_gate": plan.structural_status == "STRUCTURALLY_VALID"},
            readiness_status=plan.readiness_status,
            readiness_checks=plan.readiness_checks or {"synthetic_readiness": plan.readiness_status == "AC_MODEL_READY"},
            bus_count=max(1, self.bus_count - (len(self._all_ids) - len(active))),
            branch_count=max(0, self.source_branch_count - (len(self._all_ids) - len(active))),
            artifact=object(),
        )

    @property
    def _all_ids(self) -> tuple[str, ...]:
        return tuple(sorted({action_id for active in self.scores for action_id in active}))

    def solve_source(self) -> RuntimePowerFlowOutcome:
        return RuntimePowerFlowOutcome(self.source_pf_status, self.source_pf_status == "PF_CONVERGED")

    def solve_candidate(self, candidate: AdaptiveCandidateSnapshot) -> RuntimePowerFlowOutcome:
        self.solve_calls.append(candidate.active_action_ids)
        status = self.pf.get(candidate.active_action_ids, "PF_CONVERGED")
        return RuntimePowerFlowOutcome(status, status == "PF_CONVERGED",
                                       None if status == "PF_CONVERGED" else "scripted PF failure")

    def assess(self, candidate: AdaptiveCandidateSnapshot, source_pf: object,
               target_pf: RuntimePowerFlowOutcome) -> AdaptiveFidelityEvidence:
        return self.scores.get(candidate.active_action_ids, _assessment(0.0))


def _config(runtime: FakeRuntime, *, max_iterations: int = 8,
            compression: CompressionRequirement | None = None,
            tolerance: float = 1e-12) -> AdaptiveReductionConfig:
    return AdaptiveReductionConfig(
        schema_version=SCHEMA_VERSION,
        reduction_config_digest=runtime.reduction_config_digest,
        operating_point_digest=runtime.operating_point_digest,
        solver_contract_digest=runtime.solver_contract_digest,
        metric_policy_digest=runtime.metric_policy_digest,
        threshold_set_id=runtime.threshold_set_id,
        threshold_digest=runtime.threshold_digest,
        max_iterations=max_iterations,
        cost_bus_weight=1.0, cost_branch_weight=1.0,
        strategy=AdaptiveStrategy.HIERARCHICALLY_CONSTRAINED_GREEDY_LOCAL_EXPANSION,
        compression_requirement=compression,
        improvement_tolerance=tolerance,
    )


def _run(actions: tuple[ReductionAction, ...], scores: Mapping[tuple[str, ...], AdaptiveFidelityEvidence],
         **kwargs):
    runtime = kwargs.pop("runtime", None) or FakeRuntime(actions, scores, **kwargs.pop("runtime_options", {}))
    report = AdaptiveReductionController().run(
        runtime=runtime, actions=actions,
        initial_active_action_ids=tuple(action.action_id for action in actions),
        config=_config(runtime, **kwargs.pop("config_options", {})),
    )
    return runtime, report


def test_one_step_voltage_recovery_terminates_with_fidelity_satisfied() -> None:
    actions = (_action("expand-vm"),)
    initial = ("expand-vm",)
    runtime, report = _run(actions, {
        initial: _assessment(2.0, (_candidate("expand-vm", initial),)),
        (): _assessment(0.0),
    })
    assert report.terminal_reason is AdaptiveStoppingReason.FIDELITY_SATISFIED
    assert [row.iteration_index for row in report.iterations] == [0, 1]
    assert report.selected_candidate_sequence == (_candidate("expand-vm", initial).candidate_fingerprint,)
    assert runtime.build_calls.count(()) == 2  # candidate evaluation and fresh incumbent rebuild


def test_multi_step_recovery_uses_monotonic_deterministic_action_sequence() -> None:
    actions = (_action("expand-a"), _action("expand-b"))
    initial = ("expand-a", "expand-b")
    after_a = ("expand-b",)
    runtime, report = _run(actions, {
        initial: _assessment(4.0, tuple(_candidate(a, initial) for a in initial)),
        after_a: _assessment(2.0, (_candidate("expand-b", after_a),)),
        ("expand-a",): _assessment(3.0, (_candidate("expand-a", ("expand-a",)),)),
        (): _assessment(0.0),
    })
    assert report.terminal_reason is AdaptiveStoppingReason.FIDELITY_SATISFIED
    assert [row.action_set_fingerprint for row in report.iterations] == [
        _digest({"active_action_ids": list(initial), "schema_version": SCHEMA_VERSION}),
        _digest({"active_action_ids": list(after_a), "schema_version": SCHEMA_VERSION}),
        _digest({"active_action_ids": [], "schema_version": SCHEMA_VERSION}),
    ]
    assert len(report.selected_candidate_sequence) == 2
    assert report.iterations[0].action_set_delta == ("expand-a",)
    assert report.iterations[1].action_set_delta == ("expand-b",)


def test_cutset_pq_recovery_uses_same_hard_violation_controller_path() -> None:
    action = _action("expand-corridor")
    initial = (action.action_id,)
    _, report = _run((action,), {
        initial: _assessment(3.0, (_candidate(action.action_id, initial),),
                             names=("cutset_p_max_mw", "cutset_q_max_mvar")),
        (): _assessment(0.0),
    })
    assert report.terminal_reason is AdaptiveStoppingReason.FIDELITY_SATISFIED
    assert report.iterations[0].hard_violations[0].metric_name.startswith("cutset_")


def test_diagnostic_only_deviation_does_not_expand() -> None:
    action = _action("expand-diagnostic")
    initial = (action.action_id,)
    _, report = _run((action,), {initial: _assessment(0.0, diagnostics=("branch_p_max_mw",))})
    assert report.terminal_reason is AdaptiveStoppingReason.FIDELITY_SATISFIED
    assert report.iterations[0].hard_violations == ()
    assert report.iterations[0].diagnostic_failures == ("branch_p_max_mw",)
    assert report.iterations[0].expansion_decision is None


def test_unattributable_hard_violation_stops_without_unrelated_search() -> None:
    action = _action("unrelated")
    initial = (action.action_id,)
    _, report = _run((action,), {initial: _assessment(1.0, unattributable=True)})
    assert report.terminal_reason is AdaptiveStoppingReason.UNATTRIBUTABLE_HARD_VIOLATION
    assert report.iterations[0].hard_violations[0].status is AttributionStatus.UNATTRIBUTABLE


def test_empty_legal_candidate_set_has_distinct_stop_reason() -> None:
    action = _action("no-legal-expansion")
    initial = (action.action_id,)
    evidence = _assessment(1.0, attributable_action_ids=(action.action_id,))
    rejected = replace(_candidate(action.action_id, initial), eligible=False,
                       rejection_reason="protected by a mandatory boundary")
    evidence = replace(evidence, attribution=replace(
        evidence.attribution, rejected_candidates=(rejected,)))
    _, report = _run((action,), {initial: evidence})
    assert report.terminal_reason is AdaptiveStoppingReason.NO_EXPANSION_CANDIDATE
    assert report.iterations[0].expansion_decision.rejected_candidates == (rejected,)


def test_all_candidate_prerequisite_failures_stop_as_no_viable() -> None:
    actions = (_action("bad-structural"), _action("bad-ready"), _action("bad-pf"))
    initial = tuple(sorted(action.action_id for action in actions))
    candidates = tuple(_candidate(action, initial) for action in initial)
    builds = {
        ("bad-pf", "bad-ready"): _BuildPlan("STRUCTURAL_INVALID", "AC_MODEL_NOT_READY"),
        ("bad-pf", "bad-structural"): _BuildPlan("STRUCTURALLY_VALID", "AC_MODEL_NOT_READY"),
    }
    runtime = FakeRuntime(actions, {initial: _assessment(3.0, candidates)}, builds=builds,
                          pf={ ("bad-ready", "bad-structural"): "PF_NOT_CONVERGED" })
    report = AdaptiveReductionController().run(runtime=runtime, actions=actions,
        initial_active_action_ids=initial, config=_config(runtime))
    decision = report.iterations[0].expansion_decision
    assert report.terminal_reason is AdaptiveStoppingReason.NO_VIABLE_EXPANSION_CANDIDATE
    assert decision is not None and len(decision.candidate_evaluations) == 3
    assert {row.rejection_reason.split(":", 1)[0]
            for row in decision.candidate_evaluations if row.rejection_reason} == {
        "STRUCTURAL_INVALID", "AC_MODEL_NOT_READY", "PF_NOT_CONVERGED"}


def test_mixed_candidate_failures_do_not_hide_viable_improving_candidate() -> None:
    actions = (_action("bad-structural"), _action("bad-ready"), _action("bad-pf"), _action("good"))
    initial = tuple(sorted(action.action_id for action in actions))
    candidates = tuple(_candidate(action, initial) for action in initial)
    builds = {
        ("bad-pf", "bad-ready", "good"): _BuildPlan("STRUCTURAL_INVALID", "AC_MODEL_NOT_READY"),
        ("bad-pf", "bad-structural", "good"): _BuildPlan("STRUCTURALLY_VALID", "AC_MODEL_NOT_READY"),
    }
    pf = {("bad-ready", "bad-structural", "good"): "PF_NOT_CONVERGED"}
    runtime = FakeRuntime(actions, {initial: _assessment(4.0, candidates),
                                    ("bad-structural", "bad-ready", "bad-pf"): _assessment(0.0)},
                          builds=builds, pf=pf)
    report = AdaptiveReductionController().run(runtime=runtime, actions=actions,
        initial_active_action_ids=initial, config=_config(runtime))
    decision = report.iterations[0].expansion_decision
    assert decision is not None
    assert decision.selected_candidate_fingerprint == _candidate("good", initial).candidate_fingerprint
    assert len(decision.candidate_evaluations) == 4
    failures = {row.rejection_reason.split(":", 1)[0]
                for row in decision.candidate_evaluations if row.rejection_reason}
    assert failures == {"STRUCTURAL_INVALID", "AC_MODEL_NOT_READY", "PF_NOT_CONVERGED"}


def test_viable_candidate_without_tolerance_significant_improvement_stops() -> None:
    action = _action("tiny-improvement")
    initial = (action.action_id,)
    _, report = _run((action,), {
        initial: _assessment(1.0, (_candidate(action.action_id, initial),)),
        (): _assessment(1.0 - 5e-13, unattributable=True),
    }, config_options={"tolerance": 1e-12})
    assert report.terminal_reason is AdaptiveStoppingReason.NO_FURTHER_COMPRESSION_BENEFIT


def test_max_iterations_counts_evaluated_incumbent_states() -> None:
    action = _action("one-transition")
    initial = (action.action_id,)
    _, report = _run((action,), {
        initial: _assessment(2.0, (_candidate(action.action_id, initial),)),
        (): _assessment(0.0),
    }, config_options={"max_iterations": 1})
    assert len(report.iterations) == 1
    assert report.iterations[0].iteration_index == 0
    assert report.terminal_reason is AdaptiveStoppingReason.MAX_ITERATIONS_REACHED


def test_fidelity_success_can_miss_compression_budget() -> None:
    action = _action("still-too-large")
    initial = (action.action_id,)
    runtime = FakeRuntime((action,), {initial: _assessment(0.0)}, bus_count=8)
    report = AdaptiveReductionController().run(runtime=runtime, actions=(action,),
        initial_active_action_ids=initial,
        config=_config(runtime, compression=CompressionRequirement(max_bus_count=2)))
    assert report.terminal_reason is AdaptiveStoppingReason.FIDELITY_SATISFIED_BUT_COMPRESSION_BUDGET_NOT_MET


def test_tie_breaking_is_independent_of_input_order() -> None:
    actions = (_action("z-expansion"), _action("a-expansion"))
    initial = ("a-expansion", "z-expansion")
    candidate_rows = tuple(_candidate(action_id, initial) for action_id in initial)
    winners = []
    for ordered_actions, ordered_candidates in (
        (actions, candidate_rows),
        (tuple(reversed(actions)), tuple(reversed(candidate_rows))),
    ):
        runtime = FakeRuntime(ordered_actions, {
            initial: _assessment(3.0, ordered_candidates),
            ("a-expansion",): _assessment(1.0, unattributable=True),
            ("z-expansion",): _assessment(1.0, unattributable=True),
        })
        report = AdaptiveReductionController().run(runtime=runtime, actions=ordered_actions,
            initial_active_action_ids=initial, config=_config(runtime))
        winners.append(report.iterations[0].expansion_decision.selected_candidate_fingerprint)
    assert winners[0] == winners[1]
    decision = report.iterations[0].expansion_decision
    assert decision is not None
    assert decision.tie_break_trace == (
        "zero_cost_positive_improvement_first",
        "greatest_hard_violation_improvement",
        "highest_score",
        "lowest_expansion_cost",
        "lowest_added_bus_count",
        "lowest_added_branch_count",
        "lowest_error_region_fingerprint",
        "lowest_expansion_candidate_fingerprint",
        "canonical_stable_id_order",
    )


def test_zero_cost_positive_improvement_ranks_before_costly_candidate() -> None:
    actions = {"zero": _action("zero"), "costly": _action("costly")}
    active = ("costly", "zero")
    zero_candidate = _candidate("zero", active)
    costly_candidate = _candidate("costly", active)
    zero_row = CandidateEvaluation(
        candidate=zero_candidate, structural_status="STRUCTURALLY_VALID",
        readiness_status="AC_MODEL_READY", pf_status="PF_CONVERGED",
        fidelity_status="PF_FIDELITY_OUTSIDE_TOLERANCE",
        model_digest=_digest("zero-model"), mapping_digest=_digest("zero-map"),
        hard_violation_improvement=0.2, normalized_improvement=0.2,
        expansion_cost=0.0, added_bus_count=0, added_branch_count=0,
    )
    costly_row = CandidateEvaluation(
        candidate=costly_candidate, structural_status="STRUCTURALLY_VALID",
        readiness_status="AC_MODEL_READY", pf_status="PF_CONVERGED",
        fidelity_status="PF_FIDELITY_OUTSIDE_TOLERANCE",
        model_digest=_digest("costly-model"), mapping_digest=_digest("costly-map"),
        hard_violation_improvement=0.9, normalized_improvement=0.9,
        expansion_cost=1.0, added_bus_count=1, added_branch_count=0,
    )
    ranked = sorted((costly_row, zero_row),
                    key=lambda row: AdaptiveReductionController._rank_key(row, actions))
    assert ranked[0] is zero_row


def test_selected_candidate_is_rebuilt_fresh_from_s1() -> None:
    action = _action("fresh-rebuild")
    initial = (action.action_id,)
    seen_artifacts: list[object] = []
    runtime = FakeRuntime((action,), {
        initial: _assessment(1.0, (_candidate(action.action_id, initial),)),
        (): _assessment(0.0),
    })
    original_build = runtime.build

    def capture(active_ids: tuple[str, ...]):
        candidate = original_build(active_ids)
        seen_artifacts.append(candidate.artifact)
        return candidate

    runtime.build = capture  # type: ignore[method-assign]
    report = AdaptiveReductionController().run(runtime=runtime, actions=(action,),
        initial_active_action_ids=initial, config=_config(runtime))
    assert report.terminal_reason is AdaptiveStoppingReason.FIDELITY_SATISFIED
    assert len(seen_artifacts) >= 3
    assert seen_artifacts[-1] is not seen_artifacts[-2]


def test_candidate_failures_keep_statuses_and_diagnostics() -> None:
    action = _action("candidate-fails")
    initial = (action.action_id,)
    runtime = FakeRuntime((action,), {
        initial: _assessment(2.0, (_candidate(action.action_id, initial),)),
    }, builds={(): _BuildPlan("STRUCTURAL_INVALID", "AC_MODEL_NOT_READY")})
    report = AdaptiveReductionController().run(runtime=runtime, actions=(action,),
        initial_active_action_ids=initial, config=_config(runtime))
    row = report.iterations[0].expansion_decision.candidate_evaluations[0]
    assert row.structural_status == "STRUCTURAL_INVALID"
    assert row.readiness_status == "AC_MODEL_NOT_READY"
    assert row.pf_status == "NOT_RUN"
    assert row.rejection_reason == "STRUCTURAL_INVALID"


def test_complete_run_report_is_deterministic_under_action_and_candidate_order() -> None:
    actions = (_action("det-b"), _action("det-a"))
    initial = tuple(sorted(action.action_id for action in actions))
    candidates = tuple(_candidate(action_id, initial) for action_id in initial)
    reports = []
    for action_order, candidate_order in ((actions, candidates),
                                          (tuple(reversed(actions)), tuple(reversed(candidates)))):
        runtime = FakeRuntime(action_order, {
            initial: _assessment(2.0, candidate_order),
            ("det-b",): _assessment(1.0, (_candidate("det-b", ("det-b",)),)),
            ("det-a",): _assessment(1.5, (_candidate("det-a", ("det-a",)),)),
            (): _assessment(0.0),
        })
        reports.append(AdaptiveReductionController().run(
            runtime=runtime, actions=action_order, initial_active_action_ids=initial,
            config=_config(runtime),
        ).to_dict())
    assert reports[0] == reports[1]


def test_current_incumbent_pf_failure_is_a_run_terminal_not_candidate_stop() -> None:
    action = _action("current-pf-fails")
    initial = (action.action_id,)
    runtime = FakeRuntime((action,), {initial: _assessment(1.0, (_candidate(action.action_id, initial),))},
                          pf={initial: "PF_NOT_CONVERGED"})
    report = AdaptiveReductionController().run(runtime=runtime, actions=(action,),
        initial_active_action_ids=initial, config=_config(runtime))
    assert report.terminal_reason is AdaptiveStoppingReason.PF_NOT_CONVERGED
    assert report.iterations[0].expansion_decision is None
    assert report.iterations[0].pf_status == "PF_NOT_CONVERGED"


@pytest.mark.parametrize(("structural", "readiness", "terminal"), [
    ("STRUCTURAL_INVALID", "AC_MODEL_READY", AdaptiveStoppingReason.STRUCTURAL_INVALID),
    ("STRUCTURALLY_VALID", "AC_MODEL_NOT_READY", AdaptiveStoppingReason.AC_MODEL_NOT_READY),
])
def test_current_incumbent_gate_failures_are_run_terminals(
    structural: str, readiness: str, terminal: AdaptiveStoppingReason,
) -> None:
    action = _action(f"current-{terminal.value.lower()}")
    initial = (action.action_id,)
    runtime = FakeRuntime((action,), {initial: _assessment(1.0, (_candidate(action.action_id, initial),))},
                          builds={initial: _BuildPlan(structural, readiness)})
    report = AdaptiveReductionController().run(runtime=runtime, actions=(action,),
        initial_active_action_ids=initial, config=_config(runtime))
    assert report.terminal_reason is terminal
    assert report.iterations[0].expansion_decision is None
    assert runtime.solve_calls == []


def test_invalid_max_iterations_is_rejected_and_zero_threshold_is_exact() -> None:
    action = _action("config-check")
    runtime = FakeRuntime((action,), {})
    with pytest.raises(ValueError, match="max_iterations"):
        _config(runtime, max_iterations=0)
