"""Synthetic PR-024A Stage-3 attribution and candidate acceptance tests."""

from __future__ import annotations

from dataclasses import fields
import hashlib

import pytest

from psdcalc.reduction.adaptive.actions import ActionOperation, MappingTransition, ReductionAction
from psdcalc.reduction.adaptive.attribution import (
    AttributionError,
    ErrorAttributionEngine,
    MetricFamily,
    MetricSupport,
    Stage3AttributionInput,
)
from psdcalc.reduction.adaptive.contracts import AttributionStatus
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.fidelity.contracts import (
    PowerFlowCoverage,
    PowerFlowErrorSummary,
    PowerFlowInjectionMetrics,
    PowerFlowLossMetrics,
    PowerFlowMetricSet,
    PowerFlowMetricThresholds,
)
from psdcalc.reduction.fidelity.mapping import (
    PfBranchComparisonEntry,
    PfBusComparisonEntry,
    PfComparisonMapping,
    PfCutsetComparisonEntry,
)
from psdcalc.reduction.fidelity.metrics import (
    PowerFlowComparison,
    PowerFlowResidualEvidence,
    compare_power_flow_results,
    evaluate_metric_thresholds_detailed,
)
from psdcalc.reduction.fidelity.pypsa_executor import (
    PowerFlowExecutionResult,
    PowerFlowExecutionStatus,
)
from psdcalc.reduction.fidelity.contracts import PowerFlowMetricPolicy
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry


NS = "stage3"
THRESHOLD_DIGEST = hashlib.sha256(b"synthetic-frozen-thresholds").hexdigest()
SOURCE_DIGEST = hashlib.sha256(b"synthetic-source").hexdigest()
TARGET_DIGEST = hashlib.sha256(b"synthetic-target").hexdigest()


def _sid(component: str, name: str) -> StableId:
    return StableId(NS, component, name)


BUS_A, BUS_B, BUS_C = (_sid("Bus", name) for name in ("a", "b", "c"))
LINE_AB, LINE_BC, TARGET_LINE = (
    _sid("Line", name) for name in ("ab", "bc", "quotient-bc")
)


def _summary(value: float) -> PowerFlowErrorSummary:
    """Build a nonempty aggregate metric summary for a synthetic comparison."""
    return PowerFlowErrorSummary(1, value, value, value, value)


def _metrics(*, vm_max: float = 0.05, cutset_p: float = 0.0,
             cutset_q: float = 0.0, branch_p: float = 0.0,
             branch_q: float = 0.0, angle: float = 0.0,
             injection_p: float = 0.0, injection_q: float = 0.0,
             loss_relative: float = 0.0) -> PowerFlowMetricSet:
    """Return aggregate PR-022 metrics without recomputing any metric logic."""
    return PowerFlowMetricSet(
        bus_voltage=_summary(vm_max), angle=_summary(angle),
        branch_p=_summary(branch_p), branch_q=_summary(branch_q),
        cutset_p=_summary(cutset_p), cutset_q=_summary(cutset_q),
        cluster_injection=PowerFlowInjectionMetrics(
            _summary(injection_p), _summary(injection_q)),
        passive_branch_losses=PowerFlowLossMetrics(loss_relative, loss_relative),
        bus_voltage_preserved=_summary(0.0), bus_voltage_aggregated=_summary(vm_max),
        branch_p1=_summary(branch_p), branch_q1=_summary(branch_q),
    )


def _thresholds(**overrides: float) -> PowerFlowMetricThresholds:
    """Return a frozen synthetic threshold set with selected strict limits."""
    values = {field.name: 1.0 for field in fields(PowerFlowMetricThresholds)}
    values.update(overrides)
    return PowerFlowMetricThresholds(**values)


def _mapping_bundle() -> MappingBundle:
    """Map three source Buses into two target Buses and absorb one internal Line."""
    return MappingBundle(
        bus_map=(
            MapEntry(BUS_A, BUS_A, "preserved", "stage2"),
            MapEntry(BUS_B, BUS_A, "clustered", "stage2"),
            MapEntry(BUS_C, BUS_C, "preserved", "stage2"),
        ),
        branch_map=(
            MapEntry(LINE_AB, None, "absorbed_internal", "stage2"),
            MapEntry(LINE_BC, TARGET_LINE, "aggregated", "stage2"),
        ),
        device_map=(),
        control_map=(),
    )


def _comparison_mapping() -> PfComparisonMapping:
    """Build the already-derived PR-022 comparison projection for the fixture."""
    branch = PfBranchComparisonEntry(
        LINE_BC, TARGET_LINE, "Line", BUS_B, BUS_C, BUS_A, BUS_C, 1,
        "aggregated_corridor_member",
    )
    cutset = PfCutsetComparisonEntry(BUS_A, LINE_BC, TARGET_LINE, 1, 0, 0)
    return PfComparisonMapping(
        bus_entries=(
            PfBusComparisonEntry(BUS_A, BUS_A, "aggregated_group_member", "cc0", "tc0"),
            PfBusComparisonEntry(BUS_B, BUS_A, "aggregated_group_member", "cc0", "tc0"),
            PfBusComparisonEntry(BUS_C, BUS_C, "preserved_singleton", "cc0", "tc0"),
        ),
        branch_entries=(branch,),
        component_correspondence=(),
        cutsets_by_cluster={BUS_A: (cutset,)},
        corridor_members={TARGET_LINE: (branch,)},
        mapping_digest=hashlib.sha256(b"derived-mapping").hexdigest(),
        coverage=PowerFlowCoverage(3, 3, 2, 1, 1, 1, 1, 2, 0),
    )


def _comparison(metrics: PowerFlowMetricSet, *, vm_errors: dict[StableId, float] | None = None,
                cutset_p: float = 0.0, cutset_q: float = 0.0,
                branch_p: float = 0.0, branch_q: float = 0.0,
                injection_p: float = 0.0, injection_q: float = 0.0) -> PowerFlowComparison:
    """Bind aggregate PR-022 metrics to the extraction evidence from that comparison."""
    residuals = PowerFlowResidualEvidence(
        bus_voltage_by_source_id=vm_errors or {BUS_A: 0.0, BUS_B: 0.05, BUS_C: 0.0},
        angle_by_source_id={BUS_A: 0.0, BUS_B: 0.0, BUS_C: 0.0},
        branch_p_by_target_id={TARGET_LINE: branch_p},
        branch_q_by_target_id={TARGET_LINE: branch_q},
        cutset_p_by_target_bus_id={BUS_A: cutset_p},
        cutset_q_by_target_bus_id={BUS_A: cutset_q},
        injection_p_by_target_bus_id={BUS_A: injection_p, BUS_C: 0.0},
        injection_q_by_target_bus_id={BUS_A: injection_q, BUS_C: 0.0},
    )
    return PowerFlowComparison(
        metrics, _comparison_mapping().coverage, {}, residuals,
    )


def _action(action_id: str, *, bus: StableId = BUS_B,
            branch: StableId | None = LINE_AB, dependencies: tuple[str, ...] = (),
            protected: bool = False, reversible: bool = True,
            operation: ActionOperation = ActionOperation.RADIAL_ABSORPTION) -> ReductionAction:
    """Create an action with explicit source and mapping identities."""
    source_ids = (bus,) + ((branch,) if branch is not None else ())
    transitions = [MappingTransition(bus, BUS_A, "clustered")]
    if branch is not None:
        transitions.append(MappingTransition(branch, None, "absorbed_internal"))
    return ReductionAction.create(
        action_id=action_id, operation=operation, source_ids=source_ids,
        target_ids=(BUS_A,), mapping_delta=tuple(transitions),
        voltage_layer="110", component_id="component-0",
        region_fingerprint="declared-action-region", dependencies=dependencies,
        protected=protected, reversible=reversible,
        protection_reasons=("synthetic protected action",) if protected else (),
        audit_reason="synthetic Stage-3 action",
    )


def _input(*, metric_values: PowerFlowMetricSet | None = None,
           hard_limits: dict[str, float] | None = None,
           diagnostic_limits: dict[str, float] | None = None,
           actions: tuple[ReductionAction, ...] | None = None,
           active_ids: tuple[str, ...] | None = None,
           comparison: PowerFlowComparison | None = None,
           mappings: MappingBundle | None = None) -> Stage3AttributionInput:
    """Assemble one bound synthetic input using the authoritative PR-022 evaluator."""
    metrics = metric_values or _metrics()
    threshold_overrides = {**(hard_limits or {}), **(diagnostic_limits or {})}
    thresholds = _thresholds(**threshold_overrides)
    evaluation = evaluate_metric_thresholds_detailed(
        metrics, thresholds, threshold_digest=THRESHOLD_DIGEST)
    catalog = (_action("act-b"),) if actions is None else actions
    active = active_ids if active_ids is not None else tuple(item.action_id for item in catalog)
    return Stage3AttributionInput(
        comparison=comparison or _comparison(metrics),
        threshold_evaluation=evaluation,
        comparison_mapping=_comparison_mapping(),
        mappings=mappings or _mapping_bundle(),
        actions=catalog,
        active_action_ids=active,
        source_comparison_identity=SOURCE_DIGEST,
        target_comparison_identity=TARGET_DIGEST,
    )


def test_bus_vm_hard_violation_localizes_one_action_and_candidate() -> None:
    """A Bus Vm max violation uses PR-022 residual identities to reach one action."""
    result = ErrorAttributionEngine().run(_input(
        metric_values=_metrics(vm_max=0.05), hard_limits={"vm_max_pu": 0.01}))
    assert len(result.attributions) == 1
    attribution = result.attributions[0]
    assert attribution.metric_name == "vm_max_pu"
    assert attribution.status is AttributionStatus.ATTRIBUTABLE
    assert attribution.source_ids == (BUS_B,)
    assert len(result.regions) == 1
    assert result.candidates[0].direct_action_ids == ("act-b",)
    assert result.candidates[0].deactivated_action_ids == ("act-b",)
    assert attribution.normalized_violation == pytest.approx(4.0)


def test_pr022_comparison_exposes_identity_indexed_residuals_without_recalculation() -> None:
    """The existing comparison emits Bus and cutset residual identities for Stage 3."""
    mapping = _comparison_mapping()
    source = PowerFlowExecutionResult(
        status=PowerFlowExecutionStatus.PF_CONVERGED, converged=True,
        snapshot=None, subnetwork_status=(),
        bus_vm_pu={BUS_A: 1.0, BUS_B: 1.05, BUS_C: 1.0},
        bus_va_deg={BUS_A: 0.0, BUS_B: 0.0, BUS_C: 0.0},
        bus_p_mw={BUS_A: 0.0, BUS_B: 1.0, BUS_C: -1.0},
        bus_q_mvar={BUS_A: 0.0, BUS_B: 0.2, BUS_C: -0.2},
        branch_p0={LINE_BC: 1.0}, branch_q0={LINE_BC: 0.1},
        branch_p1={LINE_BC: -0.9}, branch_q1={LINE_BC: -0.08},
    )
    target = PowerFlowExecutionResult(
        status=PowerFlowExecutionStatus.PF_CONVERGED, converged=True,
        snapshot=None, subnetwork_status=(),
        bus_vm_pu={BUS_A: 1.0, BUS_C: 1.0},
        bus_va_deg={BUS_A: 0.0, BUS_C: 0.0},
        bus_p_mw={BUS_A: 1.0, BUS_C: -1.0},
        bus_q_mvar={BUS_A: 0.2, BUS_C: -0.2},
        branch_p0={TARGET_LINE: 1.0}, branch_q0={TARGET_LINE: 0.1},
        branch_p1={TARGET_LINE: -0.9}, branch_q1={TARGET_LINE: -0.08},
    )
    comparison = compare_power_flow_results(
        source, target, mapping,
        PowerFlowMetricPolicy(1e-6, 1e-6, 1e-6),
    )
    assert comparison.residuals is not None
    assert comparison.residuals.bus_voltage_by_source_id[BUS_B] == pytest.approx(0.05)
    assert comparison.residuals.cutset_p_by_target_bus_id[BUS_A] == pytest.approx(0.0)


@pytest.mark.parametrize(("metric_name", "family", "kwargs"), [
    ("cutset_p_max_mw", MetricFamily.CUTSET_P, {"cutset_p": 2.0}),
    ("cutset_q_max_mvar", MetricFamily.CUTSET_Q, {"cutset_q": 2.0}),
])
def test_cutset_hard_violation_localizes_corridor_action(
    metric_name: str, family: MetricFamily, kwargs: dict[str, float],
) -> None:
    """Cutset P and Q failures localize through the validated corridor projection."""
    metrics = _metrics(**kwargs)
    comparison = _comparison(metrics, **kwargs)
    result = ErrorAttributionEngine().run(_input(
        metric_values=metrics, hard_limits={metric_name: 1.0}, comparison=comparison))
    assert result.attributions[0].status is AttributionStatus.ATTRIBUTABLE
    assert result.regions[0].source_branch_ids == (LINE_BC,)
    assert result.candidates[0].direct_action_ids == ("act-b",)
    assert ErrorAttributionEngine.metric_support(family) is MetricSupport.SUPPORTED


def test_diagnostic_only_deviation_never_creates_candidate() -> None:
    """A branch diagnostic failure is retained but cannot request expansion."""
    metrics = _metrics(branch_p=4.0)
    result = ErrorAttributionEngine().run(_input(
        metric_values=metrics, diagnostic_limits={"branch_p_max_mw": 0.1},
        comparison=_comparison(metrics, branch_p=4.0)))
    assert result.fidelity_satisfied
    assert not result.hard_violation_present
    assert not result.attributions
    assert not result.candidates
    assert "branch_p_max_mw" in result.diagnostic_failures


def test_aggregate_loss_hard_violation_is_explicitly_unattributable() -> None:
    """An aggregate loss failure without local PR-022 evidence creates no candidate."""
    metrics = _metrics(loss_relative=0.5)
    result = ErrorAttributionEngine().run(_input(
        metric_values=metrics, hard_limits={"loss_rel_max": 0.1}))
    assert result.attributions[0].status is AttributionStatus.UNATTRIBUTABLE
    assert "local" in (result.attributions[0].reason or "")
    assert not result.candidates
    assert ErrorAttributionEngine.metric_support(MetricFamily.PASSIVE_LOSS) is (
        MetricSupport.UNATTRIBUTABLE_BY_STAGE3_DESIGN)


def test_protected_related_action_is_excluded_when_eligible_action_exists() -> None:
    """A protected action in the same region is never a legal candidate action."""
    actions = (_action("eligible"), _action("protected", protected=True))
    result = ErrorAttributionEngine().run(_input(
        hard_limits={"vm_max_pu": 0.01}, actions=actions))
    assert result.attributions[0].candidate_action_ids == ("eligible",)
    assert tuple(row.direct_action_ids for row in result.candidates) == (("eligible",),)
    assert all("protected" not in row.deactivated_action_ids for row in result.candidates)


def test_all_protected_related_actions_make_hard_violation_unattributable() -> None:
    """No protected action is silently unprotected to create a candidate."""
    result = ErrorAttributionEngine().run(_input(
        hard_limits={"vm_max_pu": 0.01},
        actions=(_action("protected", protected=True),),
    ))
    assert result.attributions[0].status is AttributionStatus.UNATTRIBUTABLE
    assert not result.attributions[0].candidate_action_ids
    assert not result.candidates


def test_eligible_action_with_protected_dependency_is_rejected_with_evidence() -> None:
    """A legal direct action cannot include a protected prerequisite in its closure."""
    prerequisite = _action("locked", protected=True)
    dependent = _action("dependent", dependencies=("locked",))
    result = ErrorAttributionEngine().run(_input(
        hard_limits={"vm_max_pu": 0.01}, actions=(dependent, prerequisite),
        active_ids=("dependent", "locked"),
    ))
    assert not result.candidates
    assert result.attributions[0].status is AttributionStatus.UNATTRIBUTABLE
    assert len(result.rejected_candidates) == 1
    rejected = result.rejected_candidates[0]
    assert not rejected.eligible
    assert "protected" in (rejected.rejection_reason or "")
    assert rejected.triggering_attribution_ids == (result.attributions[0].fingerprint,)


def test_dependency_closure_is_canonical_and_deduplicated() -> None:
    """Deactivating a dependent action closes over its active prerequisites."""
    prerequisite = _action("action-a")
    dependent = _action("action-b", dependencies=("action-a",))
    result = ErrorAttributionEngine().run(_input(
        hard_limits={"vm_max_pu": 0.01}, actions=(dependent, prerequisite),
        active_ids=("action-a", "action-b")))
    dependent_candidate = next(row for row in result.candidates
                               if row.direct_action_ids == ("action-b",))
    assert dependent_candidate.dependency_closure_action_ids == ("action-a", "action-b")
    assert dependent_candidate.deactivated_action_ids == ("action-a", "action-b")
    assert dependent_candidate.added_bus_count == 1
    assert dependent_candidate.added_branch_count == 1


def test_invalid_dependency_graph_fails_closed() -> None:
    """Cycles and missing action dependencies are rejected before attribution."""
    cycle_a = _action("cycle-a", dependencies=("cycle-b",))
    cycle_b = _action("cycle-b", dependencies=("cycle-a",))
    with pytest.raises(AttributionError, match="cycle"):
        ErrorAttributionEngine().run(_input(actions=(cycle_a, cycle_b)))
    missing = _action("missing", dependencies=("absent",))
    with pytest.raises(AttributionError, match="missing"):
        ErrorAttributionEngine().run(_input(actions=(missing,)))


def test_multiple_hard_violations_merge_into_one_candidate() -> None:
    """Two violations reaching one dependency closure produce one candidate."""
    metrics = _metrics(vm_max=0.05, cutset_p=2.0)
    comparison = _comparison(metrics, cutset_p=2.0)
    result = ErrorAttributionEngine().run(_input(
        metric_values=metrics,
        hard_limits={"vm_max_pu": 0.01, "cutset_p_max_mw": 1.0},
        comparison=comparison))
    assert len(result.attributions) == 2
    assert len(result.candidates) == 1
    assert len(result.candidates[0].triggering_attribution_ids) == 2


def test_permuted_metrics_actions_and_region_members_are_identical() -> None:
    """Input ordering cannot change attribution, region, or candidate digests."""
    actions = (_action("act-b"), _action("act-c"))
    baselines = []
    for action_order in (actions, tuple(reversed(actions))):
        for vm_errors in ({BUS_A: 0.0, BUS_B: 0.05, BUS_C: 0.0},
                          {BUS_C: 0.0, BUS_B: 0.05, BUS_A: 0.0}):
            metrics = _metrics(vm_max=0.05)
            comparison = _comparison(metrics, vm_errors=vm_errors)
            result = ErrorAttributionEngine().run(_input(
                metric_values=metrics, hard_limits={"vm_max_pu": 0.01},
                actions=action_order, comparison=comparison,
            ))
            baselines.append((
                tuple(row.fingerprint for row in result.attributions),
                tuple(row.fingerprint for row in result.regions),
                tuple(row.candidate_fingerprint for row in result.candidates),
                result.candidate_set_digest,
            ))
    assert len(set(baselines)) == 1


def test_no_hard_violations_differs_from_hard_violation_without_candidates() -> None:
    """Satisfied fidelity is explicit; an unsupported HARD error stays unsatisfied."""
    satisfied = ErrorAttributionEngine().run(_input(
        metric_values=_metrics(vm_max=0.001), hard_limits={"vm_max_pu": 0.01}))
    assert satisfied.fidelity_satisfied
    assert not satisfied.hard_violation_present
    assert not satisfied.attributions and not satisfied.candidates

    unsupported = _metrics(loss_relative=0.5)
    unresolved = ErrorAttributionEngine().run(_input(
        metric_values=unsupported, hard_limits={"loss_rel_max": 0.1}, actions=(), active_ids=()))
    assert not unresolved.fidelity_satisfied
    assert unresolved.hard_violation_present
    assert unresolved.attributions[0].status is AttributionStatus.UNATTRIBUTABLE
    assert not unresolved.candidates


def test_mapping_conflict_and_unsupported_provenance_fail_closed() -> None:
    """Stage 3 rejects map disagreement and never guesses an aggregate loss region."""
    bad = _mapping_bundle()
    bad_bus = MapEntry(BUS_B, BUS_B, "preserved", "tampered")
    conflict = MappingBundle(
        (bad.bus_map[0], bad_bus, bad.bus_map[2]), bad.branch_map, (), (),
    )
    with pytest.raises(AttributionError, match="MappingBundle"):
        ErrorAttributionEngine().run(_input(
            hard_limits={"vm_max_pu": 0.01}, mappings=conflict))
    result = ErrorAttributionEngine().run(_input(
        metric_values=_metrics(loss_relative=0.5),
        hard_limits={"loss_rel_max": 0.1}, comparison=_comparison(
            _metrics(loss_relative=0.5), vm_errors={}),
    ))
    assert result.attributions[0].status is AttributionStatus.UNATTRIBUTABLE


def test_metric_family_support_contract_is_explicit() -> None:
    """Stage 3 publishes conservative support boundaries for every metric family."""
    support = ErrorAttributionEngine.metric_support
    assert support(MetricFamily.BUS_VM) is MetricSupport.PARTIALLY_SUPPORTED
    assert support(MetricFamily.BUS_ANGLE) is MetricSupport.PARTIALLY_SUPPORTED
    assert support(MetricFamily.BRANCH_P) is MetricSupport.PARTIALLY_SUPPORTED
    assert support(MetricFamily.BRANCH_Q) is MetricSupport.PARTIALLY_SUPPORTED
    assert support(MetricFamily.CUTSET_P) is MetricSupport.SUPPORTED
    assert support(MetricFamily.CUTSET_Q) is MetricSupport.SUPPORTED
    assert support(MetricFamily.INJECTION_PQ) is MetricSupport.PARTIALLY_SUPPORTED
    assert support(MetricFamily.PASSIVE_LOSS) is MetricSupport.UNATTRIBUTABLE_BY_STAGE3_DESIGN


def test_zero_threshold_violation_keeps_finite_absolute_normalization() -> None:
    """A zero upper bound remains valid and reports its finite absolute excess."""
    metrics = _metrics(vm_max=0.25)
    evaluation = evaluate_metric_thresholds_detailed(
        metrics, _thresholds(vm_max_pu=0.0), threshold_digest=THRESHOLD_DIGEST)

    violation = next(item for item in evaluation.hard_violations
                     if item.metric_name == "vm_max_pu")
    assert violation.resolved_threshold == 0.0
    assert violation.violation_magnitude == pytest.approx(0.25)
    assert violation.normalized_violation == pytest.approx(0.25)
