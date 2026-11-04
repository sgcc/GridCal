"""Contract tests for replayable adaptive PF and Fidelity observations."""

from __future__ import annotations

import math

import pytest

from psdcalc.reduction.adaptive.observations import (
    AdaptiveEvaluationObservation,
    AdaptiveObservationError,
    AdaptiveObservationRecorder,
    MetricValueObservation,
    NumericObservationBundle,
    NumericFidelityObservation,
    NumericObservationValue,
    ObservationAvailability,
    PerformanceTimingObservation,
    SubnetworkIterationObservation,
    fidelity_comparison_observation,
    power_flow_execution_observation,
)
from psdcalc.reduction.fidelity.contracts import (
    POWER_FLOW_DIAGNOSTIC_THRESHOLD_FIELDS,
    POWER_FLOW_HARD_THRESHOLD_FIELDS,
    PowerFlowErrorSummary,
    PowerFlowInjectionMetrics,
    PowerFlowLossMetrics,
    PowerFlowMetricSet,
    PowerFlowMetricThresholds,
)
from psdcalc.reduction.fidelity.metrics import evaluate_metric_thresholds_detailed
from psdcalc.reduction.fidelity.persistence import ResolvedPowerFlowThresholds
from psdcalc.reduction.fidelity.pypsa_executor import (
    PowerFlowExecutionResult,
    PowerFlowExecutionStatus,
    PowerFlowSubnetworkStatus,
)


def test_numeric_observation_requires_explicit_missing_value_state() -> None:
    """A missing value is not encoded as a numeric zero."""
    missing = NumericObservationValue(None, ObservationAvailability.NOT_RECORDED)
    not_applicable = NumericObservationValue(None, ObservationAvailability.NOT_APPLICABLE)

    assert missing.to_dict() == {"availability": "NOT_RECORDED", "value": None}
    assert not_applicable.to_dict() == {"availability": "NOT_APPLICABLE", "value": None}
    with pytest.raises(AdaptiveObservationError, match="availability"):
        NumericObservationValue(None, ObservationAvailability.RECORDED)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_numeric_observation_rejects_non_finite_scalars(value: float) -> None:
    """Numeric evidence never serializes NaN or Infinity."""
    with pytest.raises(AdaptiveObservationError, match="finite"):
        NumericObservationValue(value, ObservationAvailability.RECORDED)


def test_timing_observation_requires_seconds_and_nonnegative_duration() -> None:
    """Timing is explicitly scoped and finite; it is not a semantic scalar."""
    valid = PerformanceTimingObservation(
        operation_name="source_pf_total",
        role="source",
        duration=0.125,
        duration_unit="s",
        timing_scope="PypsaAcPowerFlowExecutor.solve end-to-end",
        run_id="run-synthetic",
        process_identity="pid:1",
        backend="pypsa",
        backend_version="1.2.4",
    )

    assert valid.duration == 0.125
    with pytest.raises(AdaptiveObservationError, match="duration_unit"):
        PerformanceTimingObservation(
            operation_name="source_pf_total", role="source", duration=1.0,
            duration_unit="ms", timing_scope="same scope", run_id="run-synthetic",
            process_identity="pid:1", backend="pypsa", backend_version="1.2.4",
        )
    with pytest.raises(AdaptiveObservationError, match="finite"):
        PerformanceTimingObservation(
            operation_name="source_pf_total", role="source", duration=math.inf,
            duration_unit="s", timing_scope="same scope", run_id="run-synthetic",
            process_identity="pid:1", backend="pypsa", backend_version="1.2.4",
        )


def test_timing_events_do_not_change_numeric_observation_identity() -> None:
    """Timing rows use a separate ID sequence from semantic numeric rows."""
    without_timing = AdaptiveObservationRecorder()
    with_timing = AdaptiveObservationRecorder()
    without_timing.bind_run_id("run-synthetic")
    with_timing.bind_run_id("run-synthetic")
    with_timing.add_timing(
        operation_name="source_pf_total", role="source", duration_seconds=0.5,
        timing_scope="synthetic executor timing", backend="pypsa", backend_version="1.2.4",
    )
    assert without_timing.next_id("pf_execution", "source", "a" * 64) == \
        with_timing.next_id("pf_execution", "source", "a" * 64)


def test_observation_payload_canonicalization_ignores_field_order() -> None:
    """Canonical serialization is independent of dataclass declaration order."""
    left = NumericObservationValue(1.0, ObservationAvailability.RECORDED)
    right = NumericObservationValue.from_dict({"value": 1.0, "availability": "RECORDED"})

    assert left == right
    assert left.canonical_json() == right.canonical_json()


def test_metric_observation_keeps_unthresholded_diagnostic_not_applicable() -> None:
    """No scalar diagnostic threshold is not reported as a pass."""
    row = MetricValueObservation(
        metric_name="angle_rmse_deg",
        severity="DIAGNOSTIC",
        observed_value=NumericObservationValue(0.2, ObservationAvailability.RECORDED),
        unit="deg",
        sample_count=NumericObservationValue(4, ObservationAvailability.RECORDED),
        resolved_threshold=NumericObservationValue(None, ObservationAvailability.NOT_APPLICABLE),
        margin=NumericObservationValue(None, ObservationAvailability.NOT_APPLICABLE),
        status="NOT_APPLICABLE",
        metric_provenance="PowerFlowMetricSet.angle.rmse",
    )

    assert row.status == "NOT_APPLICABLE"
    assert row.resolved_threshold.value is None


def test_numeric_fidelity_observation_round_trip_is_canonical() -> None:
    """Typed observation serialization keeps identities and arrays stable."""
    observation = NumericFidelityObservation(
        observation_id="a" * 64,
        observation_kind="pf_execution",
        role="source",
        snapshot_identity=NumericObservationValue("snapshot-0", ObservationAvailability.RECORDED),
        pf_status="PF_CONVERGED",
        source_pf_status="PF_CONVERGED",
        candidate_pf_status="NOT_APPLICABLE",
        subnetwork_observations=(),
        metric_observations=(),
        metric_availability=ObservationAvailability.NOT_APPLICABLE,
        metric_set_digest=None,
        resolved_threshold_digest=None,
        source_model_digest="b" * 64,
        candidate_model_digest=None,
        candidate_identity=None,
        data_provenance="PyPSA AC executor result",
    )

    restored = NumericFidelityObservation.from_dict(observation.to_dict())
    assert restored == observation
    assert restored.canonical_json() == observation.canonical_json()


def test_pf_projection_keeps_failed_subnetwork_numerics_and_reason() -> None:
    """PF projection retains each executor subnetwork outcome without another solve."""
    result = PowerFlowExecutionResult(
        status=PowerFlowExecutionStatus.PF_NOT_CONVERGED,
        converged=False,
        snapshot="snapshot-0",
        subnetwork_status=(
            PowerFlowSubnetworkStatus(0, True, 3, 1e-9),
            PowerFlowSubnetworkStatus(1, False, 7, 0.25),
        ),
        failure_reason="one or more subnetworks did not converge",
    )
    observation = power_flow_execution_observation(
        result,
        observation_id="a" * 64,
        role="source",
        source_model_digest="b" * 64,
        source_sidecar_digest="c" * 64,
        solver_contract_digest="d" * 64,
        operating_point_digest="e" * 64,
        threshold_set_id="PR022_AC_PF_FIDELITY_V1",
    )

    assert observation.pf_status == "PF_NOT_CONVERGED"
    assert observation.failure_reason == "one or more subnetworks did not converge"
    assert [row.iterations.value for row in observation.subnetwork_observations] == [3, 7]
    assert [row.final_mismatch.value for row in observation.subnetwork_observations] == [1e-9, 0.25]


def test_fidelity_projection_uses_existing_metric_groups_and_zero_limits() -> None:
    """All existing metrics are projected with evaluator-owned severity and status."""
    summary = PowerFlowErrorSummary(2, 0.0, 0.0, 0.0, 0.0)
    metrics = PowerFlowMetricSet(
        bus_voltage=summary,
        angle=summary,
        branch_p=summary,
        branch_q=summary,
        cutset_p=summary,
        cutset_q=summary,
        cluster_injection=PowerFlowInjectionMetrics(summary, summary),
        passive_branch_losses=PowerFlowLossMetrics(0.0, 0.0),
    )
    limits = PowerFlowMetricThresholds(**{name: 0.0 for name in PowerFlowMetricThresholds.__dataclass_fields__})
    resolved = ResolvedPowerFlowThresholds(
        threshold_set_id="PR022_AC_PF_FIDELITY_V1",
        threshold_set_version="1",
        threshold_set_sha256="f" * 64,
        definition_digest="1" * 64,
        reference_cutset_p_mw=1.0,
        reference_cutset_q_mvar=1.0,
        thresholds=limits,
        mapping_coverage_fraction=1.0,
        preserved_comparison_coverage_fraction=1.0,
        digest="2" * 64,
    )
    evaluated = evaluate_metric_thresholds_detailed(metrics, limits, threshold_digest=resolved.digest)
    observation = fidelity_comparison_observation(
        metrics,
        resolved,
        evaluated,
        observation_id="3" * 64,
        role="initial_candidate",
        source_model_digest="4" * 64,
        source_sidecar_digest="5" * 64,
        candidate_model_digest="6" * 64,
        candidate_identity="7" * 64,
        snapshot="snapshot-0",
        source_pf_status="PF_CONVERGED",
        candidate_pf_status="PF_CONVERGED",
    )

    rows = {row.metric_name: row for row in observation.metric_observations}
    assert set(rows) == POWER_FLOW_HARD_THRESHOLD_FIELDS | POWER_FLOW_DIAGNOSTIC_THRESHOLD_FIELDS
    assert len(rows) == 22
    assert rows["vm_max_pu"].resolved_threshold.value == 0.0
    assert rows["vm_max_pu"].margin.value == 0.0
    assert rows["vm_max_pu"].severity == "HARD"
    assert rows["vm_max_pu"].status == "PASS"


def test_observation_bundle_validates_multiple_timing_rows_without_numeric_binding() -> None:
    """Timing IDs are unique and timing duration changes do not alter numeric digest."""
    numeric = NumericFidelityObservation(
        observation_id="a" * 64,
        observation_kind="pf_execution",
        role="source",
        snapshot_identity=NumericObservationValue(None, ObservationAvailability.NOT_RECORDED),
        pf_status="PF_EXECUTION_UNSUPPORTED",
        source_pf_status="PF_EXECUTION_UNSUPPORTED",
        candidate_pf_status="NOT_APPLICABLE",
        subnetwork_observations=(),
        metric_observations=(),
        metric_availability=ObservationAvailability.NOT_APPLICABLE,
        metric_set_digest=None,
        resolved_threshold_digest=None,
        source_model_digest="b" * 64,
        candidate_model_digest=None,
        candidate_identity=None,
        data_provenance="PyPSA AC executor result",
    )
    timing = (
        PerformanceTimingObservation(
            operation_name="source_pf_total", role="source", duration=0.1,
            duration_unit="s", timing_scope="executor end-to-end", run_id="run-synthetic",
            process_identity="pid:1", backend="pypsa", backend_version="1.2.4",
        ),
        PerformanceTimingObservation(
            operation_name="adaptive_controller_total", role="run", duration=0.2,
            duration_unit="s", timing_scope="controller end-to-end", run_id="run-synthetic",
            process_identity="pid:1", backend="pypsa", backend_version="1.2.4",
        ),
    )
    bundle = NumericObservationBundle(
        run_id="run-synthetic",
        numeric_observations=(numeric,),
        performance_timing_observations=timing,
        adaptive_evaluations=(),
    )

    restored = NumericObservationBundle.from_payload(bundle.to_payload())
    assert restored == bundle
