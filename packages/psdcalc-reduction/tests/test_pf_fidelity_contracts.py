from __future__ import annotations

import math

import pytest

from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.fidelity.contracts import (
    PF_FIDELITY_SOURCE_STAGE,
    PF_FIDELITY_TARGET_STAGE,
    PfFidelityStatus,
    PfSolveStatus,
    PowerFlowContractError,
    PowerFlowCoverage,
    PowerFlowErrorSummary,
    PowerFlowFidelityReport,
    PowerFlowInjectionMetrics,
    PowerFlowLossMetrics,
    PowerFlowMetricPolicy,
    PowerFlowMetricSet,
    PowerFlowMetricThresholds,
    PowerFlowOperatingPointContract,
)


def _operating_point(*, snapshots: tuple[object, ...] = ("static-0",)) -> PowerFlowOperatingPointContract:
    return PowerFlowOperatingPointContract(
        snapshots=snapshots,
        solver="pypsa.newton_raphson",
        tolerance=1e-6,
        max_iterations=20,
        distribute_slack=False,
        slack_weights=None,
        use_seed=False,
        control_policy="preserve_source_controls",
        tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest="a" * 64,
        device_schedule_digest="b" * 64,
    )


def _summary(n: int = 2) -> PowerFlowErrorSummary:
    return PowerFlowErrorSummary(
        sample_count=n,
        rmse=0.01,
        p95_absolute=0.02,
        max_absolute=0.03,
    )


def _report(
    status: PfFidelityStatus,
    *,
    source_status: PfSolveStatus,
    target_status: PfSolveStatus,
    metrics_present: bool = False,
    thresholds_digest: str | None = None,
    target_structural_status: str = "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
) -> PowerFlowFidelityReport:
    metrics = _summary() if metrics_present else None
    return PowerFlowFidelityReport(
        model_id="case-a",
        run_id="run-a",
        config_sha256="c" * 64,
        structural_report_digest="d" * 64,
        source_structural_report_digest="e" * 64,
        mapping_digest="f" * 64,
        s1_model_digest="1" * 64,
        s4_model_digest="2" * 64,
        source_stage=PF_FIDELITY_SOURCE_STAGE,
        target_stage=PF_FIDELITY_TARGET_STAGE,
        target_structural_status=target_structural_status,
        operating_point=_operating_point(),
        source_pf_status=source_status,
        target_pf_status=target_status,
        bus_voltage_metrics=metrics,
        angle_metrics=metrics,
        branch_p_metrics=metrics,
        branch_q_metrics=metrics,
        cutset_p_metrics=metrics,
        cutset_q_metrics=metrics,
        injection_metrics=(PowerFlowInjectionMetrics(metrics, metrics)
                           if metrics_present else None),
        loss_metrics=(PowerFlowLossMetrics(0.1, 0.01, 0.1)
                      if metrics_present else None),
        coverage=PowerFlowCoverage(
            source_bus_count=2,
            mapped_bus_count=2,
            source_branch_count=1,
            comparable_branch_count=1,
            absorbed_internal_branch_count=0,
            target_corridor_count=1,
        ),
        fidelity_status=status,
        thresholds_digest=thresholds_digest,
        failure_reason=("solver did not converge"
                        if status in {PfFidelityStatus.SOURCE_PF_NOT_CONVERGED,
                                      PfFidelityStatus.TARGET_PF_NOT_CONVERGED,
                                      PfFidelityStatus.PF_MAPPING_INVALID,
                                      PfFidelityStatus.FAILED}
                        else None),
    )


def test_operating_point_requires_one_snapshot_for_solver_use() -> None:
    assert _operating_point().require_supported_snapshot_scope() is None

    for snapshots in ((), ("s0", "s1")):
        contract = _operating_point(snapshots=snapshots)
        with pytest.raises(PowerFlowContractError, match="PF_FIDELITY_UNSUPPORTED_SNAPSHOT_SCOPE"):
            contract.require_supported_snapshot_scope()


@pytest.mark.parametrize("tolerance", [0.0, -1.0, math.nan, math.inf])
def test_operating_point_rejects_invalid_solver_tolerance(tolerance: float) -> None:
    with pytest.raises(PowerFlowContractError):
        PowerFlowOperatingPointContract(
            snapshots=("static-0",), solver="pypsa.newton_raphson",
            tolerance=tolerance, max_iterations=None, distribute_slack=False,
            slack_weights=None, use_seed=False, control_policy="fixed",
            tap_policy="fixed", phase_shift_policy="fixed",
            topology_digest="a" * 64, device_schedule_digest="b" * 64,
        )


def test_metric_policy_and_threshold_schema_are_separate() -> None:
    policy = PowerFlowMetricPolicy(
        p_denominator_floor_mw=0.1,
        q_denominator_floor_mvar=0.2,
        loss_denominator_floor_mw=0.3,
    )
    thresholds = PowerFlowMetricThresholds()
    assert policy.p_denominator_floor_mw == 0.1
    assert thresholds.is_frozen is False

    frozen = PowerFlowMetricThresholds(
        vm_max_pu=0.02,
        vm_rmse_pu=0.01,
        angle_max_deg=0.1,
        branch_p_max_mw=1.0,
        branch_q_max_mvar=1.0,
        cutset_p_max_mw=2.0,
        cutset_q_max_mvar=2.0,
        loss_abs_max_mw=1.0,
        loss_rel_max=0.05,
    )
    assert frozen.is_frozen is True


def test_metric_summary_validates_counts_and_finite_values() -> None:
    assert _summary().sample_count == 2
    with pytest.raises(PowerFlowContractError):
        PowerFlowErrorSummary(sample_count=0, rmse=0.0, p95_absolute=0.0,
                              max_absolute=0.0)
    with pytest.raises(PowerFlowContractError):
        PowerFlowErrorSummary(sample_count=1, rmse=math.inf,
                              p95_absolute=0.0, max_absolute=0.0)


def test_report_keeps_structural_validity_separate_from_pf_failure() -> None:
    report = _report(
        PfFidelityStatus.TARGET_PF_NOT_CONVERGED,
        source_status=PfSolveStatus.CONVERGED,
        target_status=PfSolveStatus.NOT_CONVERGED,
    )
    assert report.target_structural_status == "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"
    assert report.fidelity_status is PfFidelityStatus.TARGET_PF_NOT_CONVERGED
    assert report.bus_voltage_metrics is None


def test_report_requires_mapping_and_source_convergence_before_metrics() -> None:
    with pytest.raises(PowerFlowContractError, match="metrics"):
        _report(
            PfFidelityStatus.SOURCE_PF_NOT_CONVERGED,
            source_status=PfSolveStatus.NOT_CONVERGED,
            target_status=PfSolveStatus.NOT_RUN,
            metrics_present=True,
        )

    with pytest.raises(PowerFlowContractError, match="threshold"):
        _report(
            PfFidelityStatus.PF_FIDELITY_WITHIN_TOLERANCE,
            source_status=PfSolveStatus.CONVERGED,
            target_status=PfSolveStatus.CONVERGED,
            metrics_present=True,
        )


def test_report_accepts_measured_status_before_thresholds_are_frozen() -> None:
    report = _report(
        PfFidelityStatus.PF_FIDELITY_MEASURED,
        source_status=PfSolveStatus.CONVERGED,
        target_status=PfSolveStatus.CONVERGED,
        metrics_present=True,
    )
    assert report.fidelity_status is PfFidelityStatus.PF_FIDELITY_MEASURED
    assert report.thresholds_digest is None
    assert isinstance(report.metric_set, PowerFlowMetricSet)


def test_mapping_invalid_must_fail_before_solver_and_invalid_structure_cannot_claim_fidelity() -> None:
    with pytest.raises(PowerFlowContractError, match="before either PF"):
        _report(
            PfFidelityStatus.PF_MAPPING_INVALID,
            source_status=PfSolveStatus.CONVERGED,
            target_status=PfSolveStatus.NOT_RUN,
        )

    with pytest.raises(PowerFlowContractError, match="structurally valid"):
        _report(
            PfFidelityStatus.PF_FIDELITY_MEASURED,
            source_status=PfSolveStatus.CONVERGED,
            target_status=PfSolveStatus.CONVERGED,
            metrics_present=True,
            target_structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID",
        )


def test_unsupported_snapshot_scope_can_be_recorded_without_solving() -> None:
    report = PowerFlowFidelityReport(
        model_id="case-a", run_id="run-a", config_sha256="c" * 64,
        structural_report_digest="d" * 64,
        source_structural_report_digest="e" * 64,
        mapping_digest="f" * 64, s1_model_digest="1" * 64,
        s4_model_digest="2" * 64,
        source_stage=PF_FIDELITY_SOURCE_STAGE,
        target_stage=PF_FIDELITY_TARGET_STAGE,
        target_structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
        operating_point=_operating_point(snapshots=("s0", "s1")),
        source_pf_status=PfSolveStatus.NOT_RUN,
        target_pf_status=PfSolveStatus.NOT_RUN,
        bus_voltage_metrics=None, angle_metrics=None,
        branch_p_metrics=None, branch_q_metrics=None,
        cutset_p_metrics=None, cutset_q_metrics=None,
        injection_metrics=None, loss_metrics=None,
        coverage=PowerFlowCoverage(2, 2, 0, 0, 0, 0),
        fidelity_status=PfFidelityStatus.PF_FIDELITY_UNSUPPORTED_SNAPSHOT_SCOPE,
        failure_reason="time-series scope is unsupported in this stage",
    )
    assert len(report.operating_point.snapshots) == 2

