"""Data contracts for AC reduction-fidelity evidence; this module runs no solver."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


PF_FIDELITY_SOURCE_STAGE: Literal["PYPSA_FULL_PROJECTION"] = "PYPSA_FULL_PROJECTION"
PF_FIDELITY_TARGET_STAGE: Literal["FINAL_RELOADED"] = "FINAL_RELOADED"
PF_FIDELITY_VALID_STRUCTURAL_STATUS = "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"
PF_FIDELITY_INVALID_STRUCTURAL_STATUS = "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID"


class PowerFlowContractError(ValueError):
    """Raised when an operating-point or fidelity-report contract is invalid."""


class PfSolveStatus(StrEnum):
    """Outcome of one side's PF attempt, independent from reduction structure."""

    NOT_RUN = "NOT_RUN"
    CONVERGED = "CONVERGED"
    NOT_CONVERGED = "NOT_CONVERGED"
    FAILED = "FAILED"


class PfFidelityStatus(StrEnum):
    """Result status for the S1-to-S4 reduction-fidelity comparison."""

    SOURCE_PF_NOT_CONVERGED = "SOURCE_PF_NOT_CONVERGED"
    TARGET_PF_NOT_CONVERGED = "TARGET_PF_NOT_CONVERGED"
    PF_MAPPING_INVALID = "PF_MAPPING_INVALID"
    PF_FIDELITY_UNSUPPORTED_SNAPSHOT_SCOPE = "PF_FIDELITY_UNSUPPORTED_SNAPSHOT_SCOPE"
    PF_FIDELITY_MEASURED = "PF_FIDELITY_MEASURED"
    PF_FIDELITY_WITHIN_TOLERANCE = "PF_FIDELITY_WITHIN_TOLERANCE"
    PF_FIDELITY_OUTSIDE_TOLERANCE = "PF_FIDELITY_OUTSIDE_TOLERANCE"
    FAILED = "FAILED"


def _valid_sha256(value: str, field_name: str) -> None:
    """Validate a lowercase SHA-256 digest used to bind fidelity evidence."""
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise PowerFlowContractError(f"{field_name} must be a lowercase SHA-256 digest")


def _finite_number(value: object, field_name: str, *, minimum: float = 0.0) -> float:
    """Validate a finite real value without accepting bool or numeric coercion."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PowerFlowContractError(f"{field_name} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise PowerFlowContractError(f"{field_name} must be finite and >= {minimum}")
    return number


@dataclass(frozen=True)
class PowerFlowOperatingPointContract:
    """Immutable solver, snapshot, topology, control, and schedule comparison contract.

    The first PR-022 stage represents one selected static snapshot. A contract
    with any other snapshot count remains serializable for failure evidence, but
    ``require_supported_snapshot_scope`` must reject it before a solver call.
    """

    snapshots: tuple[object, ...]
    solver: str
    tolerance: float
    max_iterations: int | None
    distribute_slack: bool
    slack_weights: str | None
    use_seed: bool
    control_policy: str
    tap_policy: str
    phase_shift_policy: str
    topology_digest: str
    device_schedule_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.snapshots, tuple):
            raise PowerFlowContractError("snapshots must be an immutable tuple")
        if any(snapshot is None for snapshot in self.snapshots):
            raise PowerFlowContractError("snapshots cannot contain None")
        if not isinstance(self.solver, str) or not self.solver.strip():
            raise PowerFlowContractError("solver must be non-empty")
        object.__setattr__(self, "tolerance", _finite_number(
            self.tolerance, "tolerance", minimum=math.nextafter(0.0, 1.0)))
        if self.max_iterations is not None and (
                isinstance(self.max_iterations, bool) or
                not isinstance(self.max_iterations, int) or self.max_iterations <= 0):
            raise PowerFlowContractError("max_iterations must be a positive integer or None")
        for name in ("distribute_slack", "use_seed"):
            if not isinstance(getattr(self, name), bool):
                raise PowerFlowContractError(f"{name} must be bool")
        if self.slack_weights is not None and (
                not isinstance(self.slack_weights, str) or not self.slack_weights.strip()):
            raise PowerFlowContractError("slack_weights must be a non-empty string or None")
        if self.distribute_slack and self.slack_weights is None:
            raise PowerFlowContractError(
                "slack_weights must be explicit when distribute_slack is enabled")
        for name in ("control_policy", "tap_policy", "phase_shift_policy"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise PowerFlowContractError(f"{name} must be non-empty")
        _valid_sha256(self.topology_digest, "topology_digest")
        _valid_sha256(self.device_schedule_digest, "device_schedule_digest")

    def require_supported_snapshot_scope(self) -> None:
        """Fail closed unless exactly one static snapshot is selected."""
        if len(self.snapshots) != 1:
            raise PowerFlowContractError(
                PfFidelityStatus.PF_FIDELITY_UNSUPPORTED_SNAPSHOT_SCOPE.value)


@dataclass(frozen=True)
class PowerFlowMetricPolicy:
    """Numerical floors used only for stable relative-error denominators."""

    p_denominator_floor_mw: float
    q_denominator_floor_mvar: float
    loss_denominator_floor_mw: float

    def __post_init__(self) -> None:
        for name in ("p_denominator_floor_mw", "q_denominator_floor_mvar",
                     "loss_denominator_floor_mw"):
            object.__setattr__(self, name, _finite_number(
                getattr(self, name), name, minimum=math.nextafter(0.0, 1.0)))


@dataclass(frozen=True)
class PowerFlowMetricThresholds:
    """Acceptance thresholds, intentionally independent of metric calculation.

    All values are ``None`` until explicitly frozen. This PR defines the schema
    only; it does not set real-case thresholds.
    """

    vm_max_pu: float | None = None
    vm_rmse_pu: float | None = None
    angle_max_deg: float | None = None
    branch_p_max_mw: float | None = None
    branch_q_max_mvar: float | None = None
    cutset_p_max_mw: float | None = None
    cutset_q_max_mvar: float | None = None
    loss_abs_max_mw: float | None = None
    loss_rel_max: float | None = None

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite_number(value, name))

    @property
    def is_frozen(self) -> bool:
        """Return whether every threshold needed by the first-stage schema is set."""
        return all(getattr(self, name) is not None for name in self.__dataclass_fields__)


@dataclass(frozen=True)
class PowerFlowErrorSummary:
    """Absolute-error distribution summary with an explicit sample count."""

    sample_count: int
    rmse: float | None
    p95_absolute: float | None
    max_absolute: float | None
    mean_absolute: float | None = None

    def __post_init__(self) -> None:
        if (isinstance(self.sample_count, bool) or
                not isinstance(self.sample_count, int) or self.sample_count < 0):
            raise PowerFlowContractError("sample_count must be a nonnegative integer")
        values = (self.rmse, self.p95_absolute, self.max_absolute, self.mean_absolute)
        if self.sample_count == 0 and any(value is not None for value in values):
            raise PowerFlowContractError("zero-sample metrics must be None")
        if self.sample_count > 0 and any(value is None for value in values):
            raise PowerFlowContractError("non-empty summaries require RMSE, P95, max, and mean")
        for name in ("rmse", "p95_absolute", "max_absolute", "mean_absolute"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite_number(value, name))

    @property
    def count(self) -> int:
        """Return the sample count using the scalar accumulator terminology."""
        return self.sample_count

    @property
    def p95_abs(self) -> float | None:
        """Return the 95th percentile of absolute residuals."""
        return self.p95_absolute

    @property
    def max_abs(self) -> float | None:
        """Return the maximum absolute residual."""
        return self.max_absolute

    @property
    def mean_abs(self) -> float | None:
        """Return the mean absolute residual."""
        return self.mean_absolute


@dataclass(frozen=True)
class PowerFlowInjectionMetrics:
    """Solved net-injection error summaries, separate for active and reactive power."""

    p_mw: PowerFlowErrorSummary
    q_mvar: PowerFlowErrorSummary

    def __post_init__(self) -> None:
        if not isinstance(self.p_mw, PowerFlowErrorSummary) or not isinstance(
                self.q_mvar, PowerFlowErrorSummary):
            raise PowerFlowContractError("injection metrics require P and Q summaries")


@dataclass(frozen=True)
class PowerFlowLossMetrics:
    """Separate passive branch losses from total active-balance consistency."""

    passive_branch_active_abs_diff_mw: float | None
    passive_branch_active_relative_diff: float | None
    total_active_balance_abs_diff_mw: float | None = None

    def __post_init__(self) -> None:
        for name in ("passive_branch_active_abs_diff_mw",
                     "passive_branch_active_relative_diff",
                     "total_active_balance_abs_diff_mw"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite_number(value, name))


@dataclass(frozen=True)
class PowerFlowMetricSet:
    """Calculation-only collection of structural-stage comparison metrics."""

    bus_voltage: PowerFlowErrorSummary
    angle: PowerFlowErrorSummary
    branch_p: PowerFlowErrorSummary
    branch_q: PowerFlowErrorSummary
    cutset_p: PowerFlowErrorSummary
    cutset_q: PowerFlowErrorSummary
    cluster_injection: PowerFlowInjectionMetrics
    passive_branch_losses: PowerFlowLossMetrics
    bus_voltage_preserved: PowerFlowErrorSummary | None = None
    bus_voltage_aggregated: PowerFlowErrorSummary | None = None
    branch_p1: PowerFlowErrorSummary | None = None
    branch_q1: PowerFlowErrorSummary | None = None

    def __post_init__(self) -> None:
        for name in ("bus_voltage", "angle", "branch_p", "branch_q", "cutset_p", "cutset_q"):
            if not isinstance(getattr(self, name), PowerFlowErrorSummary):
                raise PowerFlowContractError(f"{name} metric must be a PowerFlowErrorSummary")
        if not isinstance(self.cluster_injection, PowerFlowInjectionMetrics):
            raise PowerFlowContractError("cluster_injection must be PowerFlowInjectionMetrics")
        if not isinstance(self.passive_branch_losses, PowerFlowLossMetrics):
            raise PowerFlowContractError("passive_branch_losses must be PowerFlowLossMetrics")
        for name in ("bus_voltage_preserved", "bus_voltage_aggregated", "branch_p1", "branch_q1"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, PowerFlowErrorSummary):
                raise PowerFlowContractError(f"{name} must be PowerFlowErrorSummary or None")


@dataclass(frozen=True)
class PowerFlowCoverage:
    """Counts needed to interpret metric sample coverage and absorbed branches."""

    source_bus_count: int
    mapped_bus_count: int
    source_branch_count: int
    comparable_branch_count: int
    absorbed_internal_branch_count: int
    target_corridor_count: int
    preserved_bus_count: int = 0
    aggregated_bus_count: int = 0
    excluded_object_count: int = 0

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PowerFlowContractError(f"{name} must be a nonnegative integer")
        if self.mapped_bus_count > self.source_bus_count:
            raise PowerFlowContractError("mapped_bus_count exceeds source_bus_count")
        if (self.comparable_branch_count + self.absorbed_internal_branch_count >
                self.source_branch_count):
            raise PowerFlowContractError("branch coverage counts exceed source_branch_count")
        if self.target_corridor_count > self.comparable_branch_count:
            raise PowerFlowContractError("target_corridor_count exceeds comparable branches")
        if self.preserved_bus_count + self.aggregated_bus_count > self.mapped_bus_count:
            raise PowerFlowContractError("Bus comparison counts exceed mapped_bus_count")

    @property
    def bus_coverage_fraction(self) -> float | None:
        """Return mapped source-bus coverage, or None for an empty source inventory."""
        if self.source_bus_count == 0:
            return None
        return self.mapped_bus_count / self.source_bus_count

    @property
    def branch_coverage_fraction(self) -> float | None:
        """Return the fraction of source branches with a target flow comparison."""
        if self.source_branch_count == 0:
            return None
        return self.comparable_branch_count / self.source_branch_count


@dataclass(frozen=True)
class PowerFlowFidelityReport:
    """Independent result schema for S1-to-S4 AC reduction-fidelity evidence."""

    model_id: str
    run_id: str
    config_sha256: str
    structural_report_digest: str
    source_structural_report_digest: str
    mapping_digest: str
    solver_contract_digest: str
    metric_policy_digest: str
    s1_model_digest: str
    s4_model_digest: str
    source_stage: str
    target_stage: str
    target_structural_status: str
    operating_point: PowerFlowOperatingPointContract
    source_pf_status: PfSolveStatus
    target_pf_status: PfSolveStatus
    bus_voltage_metrics: PowerFlowErrorSummary | None
    bus_voltage_preserved_metrics: PowerFlowErrorSummary | None
    bus_voltage_aggregated_metrics: PowerFlowErrorSummary | None
    angle_metrics: PowerFlowErrorSummary | None
    branch_p_metrics: PowerFlowErrorSummary | None
    branch_q_metrics: PowerFlowErrorSummary | None
    branch_p1_metrics: PowerFlowErrorSummary | None
    branch_q1_metrics: PowerFlowErrorSummary | None
    cutset_p_metrics: PowerFlowErrorSummary | None
    cutset_q_metrics: PowerFlowErrorSummary | None
    injection_metrics: PowerFlowInjectionMetrics | None
    loss_metrics: PowerFlowLossMetrics | None
    coverage: PowerFlowCoverage
    fidelity_status: PfFidelityStatus
    thresholds_digest: str | None = None
    failure_reason: str | None = None

    @property
    def metric_set(self) -> PowerFlowMetricSet | None:
        """Return the calculation-only metric set when all metric groups exist."""
        if any(value is None for value in self._metric_values()):
            return None
        assert self.bus_voltage_metrics is not None
        assert self.angle_metrics is not None
        assert self.branch_p_metrics is not None
        assert self.branch_q_metrics is not None
        assert self.cutset_p_metrics is not None
        assert self.cutset_q_metrics is not None
        assert self.injection_metrics is not None
        assert self.loss_metrics is not None
        return PowerFlowMetricSet(
            self.bus_voltage_metrics, self.angle_metrics,
            self.branch_p_metrics, self.branch_q_metrics,
            self.cutset_p_metrics, self.cutset_q_metrics,
            self.injection_metrics, self.loss_metrics,
            self.bus_voltage_preserved_metrics, self.bus_voltage_aggregated_metrics,
            self.branch_p1_metrics, self.branch_q1_metrics,
        )

    def to_public_dict(self) -> dict[str, object]:
        """Serialize aggregate evidence without network names, IDs, or raw arrays."""
        def summary(value: PowerFlowErrorSummary | None) -> dict[str, object] | None:
            if value is None:
                return None
            return {
                "count": value.sample_count,
                "rmse": value.rmse,
                "p95_abs": value.p95_absolute,
                "max_abs": value.max_absolute,
                "mean_abs": value.mean_absolute,
            }

        coverage = {
            "source_bus_count": self.coverage.source_bus_count,
            "mapped_bus_count": self.coverage.mapped_bus_count,
            "preserved_bus_count": self.coverage.preserved_bus_count,
            "aggregated_bus_count": self.coverage.aggregated_bus_count,
            "source_branch_count": self.coverage.source_branch_count,
            "comparable_branch_count": self.coverage.comparable_branch_count,
            "absorbed_internal_branch_count": self.coverage.absorbed_internal_branch_count,
            "target_corridor_count": self.coverage.target_corridor_count,
            "excluded_object_count": self.coverage.excluded_object_count,
            "bus_coverage_fraction": self.coverage.bus_coverage_fraction,
            "branch_coverage_fraction": self.coverage.branch_coverage_fraction,
        }
        return {
            "model_id": self.model_id,
            "run_id": self.run_id,
            "config_sha256": self.config_sha256,
            "structural_report_digest": self.structural_report_digest,
            "source_structural_report_digest": self.source_structural_report_digest,
            "mapping_digest": self.mapping_digest,
            "solver_contract_digest": self.solver_contract_digest,
            "metric_policy_digest": self.metric_policy_digest,
            "s1_model_digest": self.s1_model_digest,
            "s4_model_digest": self.s4_model_digest,
            "source_stage": self.source_stage,
            "target_stage": self.target_stage,
            "target_structural_status": self.target_structural_status,
            "source_pf_status": self.source_pf_status.value,
            "target_pf_status": self.target_pf_status.value,
            "fidelity_status": self.fidelity_status.value,
            "operating_point": {
                "snapshot_count": len(self.operating_point.snapshots),
                "solver": self.operating_point.solver,
                "tolerance": self.operating_point.tolerance,
                "max_iterations": self.operating_point.max_iterations,
                "distribute_slack": self.operating_point.distribute_slack,
                "slack_weights": self.operating_point.slack_weights,
                "use_seed": self.operating_point.use_seed,
                "control_policy": self.operating_point.control_policy,
                "tap_policy": self.operating_point.tap_policy,
                "phase_shift_policy": self.operating_point.phase_shift_policy,
                "topology_digest": self.operating_point.topology_digest,
                "device_schedule_digest": self.operating_point.device_schedule_digest,
            },
            "coverage": coverage,
            "metrics": {
                "bus_voltage": summary(self.bus_voltage_metrics),
                "bus_voltage_preserved": summary(self.bus_voltage_preserved_metrics),
                "bus_voltage_aggregated": summary(self.bus_voltage_aggregated_metrics),
                "angle": summary(self.angle_metrics),
                "branch_p0": summary(self.branch_p_metrics),
                "branch_q0": summary(self.branch_q_metrics),
                "branch_p1": summary(self.branch_p1_metrics),
                "branch_q1": summary(self.branch_q1_metrics),
                "cutset_p": summary(self.cutset_p_metrics),
                "cutset_q": summary(self.cutset_q_metrics),
                "cluster_injection_p": summary(
                    self.injection_metrics.p_mw if self.injection_metrics else None),
                "cluster_injection_q": summary(
                    self.injection_metrics.q_mvar if self.injection_metrics else None),
                "passive_branch_loss_abs_diff_mw": (
                    self.loss_metrics.passive_branch_active_abs_diff_mw
                    if self.loss_metrics else None),
                "passive_branch_loss_relative_diff": (
                    self.loss_metrics.passive_branch_active_relative_diff
                    if self.loss_metrics else None),
            },
            "thresholds_digest": self.thresholds_digest,
        }

    def __post_init__(self) -> None:
        for name in ("model_id", "run_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise PowerFlowContractError(f"{name} must be non-empty")
        for name in ("config_sha256", "structural_report_digest",
                     "source_structural_report_digest", "mapping_digest",
                     "solver_contract_digest", "metric_policy_digest",
                     "s1_model_digest", "s4_model_digest"):
            _valid_sha256(getattr(self, name), name)
        if self.source_stage != PF_FIDELITY_SOURCE_STAGE:
            raise PowerFlowContractError("source_stage must be PYPSA_FULL_PROJECTION")
        if self.target_stage != PF_FIDELITY_TARGET_STAGE:
            raise PowerFlowContractError("target_stage must be FINAL_RELOADED")
        if self.target_structural_status not in {
                PF_FIDELITY_VALID_STRUCTURAL_STATUS,
                PF_FIDELITY_INVALID_STRUCTURAL_STATUS}:
            raise PowerFlowContractError("unknown target structural status")
        if not isinstance(self.operating_point, PowerFlowOperatingPointContract):
            raise PowerFlowContractError("operating_point must be a contract")
        if not isinstance(self.source_pf_status, PfSolveStatus) or not isinstance(
                self.target_pf_status, PfSolveStatus):
            raise PowerFlowContractError("source and target PF statuses must be PfSolveStatus")
        if not isinstance(self.fidelity_status, PfFidelityStatus):
            raise PowerFlowContractError("fidelity_status must be PfFidelityStatus")
        if not isinstance(self.coverage, PowerFlowCoverage):
            raise PowerFlowContractError("coverage must be PowerFlowCoverage")
        for name in ("bus_voltage_metrics", "bus_voltage_preserved_metrics",
                     "bus_voltage_aggregated_metrics", "angle_metrics", "branch_p_metrics",
                     "branch_q_metrics", "branch_p1_metrics", "branch_q1_metrics",
                     "cutset_p_metrics", "cutset_q_metrics"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, PowerFlowErrorSummary):
                raise PowerFlowContractError(f"{name} must be PowerFlowErrorSummary or None")
        if (self.injection_metrics is not None and
                not isinstance(self.injection_metrics, PowerFlowInjectionMetrics)):
            raise PowerFlowContractError("injection_metrics must be PowerFlowInjectionMetrics or None")
        if (self.loss_metrics is not None and
                not isinstance(self.loss_metrics, PowerFlowLossMetrics)):
            raise PowerFlowContractError("loss_metrics must be PowerFlowLossMetrics or None")
        if self.thresholds_digest is not None:
            _valid_sha256(self.thresholds_digest, "thresholds_digest")
        status = self.fidelity_status
        if status is PfFidelityStatus.SOURCE_PF_NOT_CONVERGED:
            if (self.source_pf_status is not PfSolveStatus.NOT_CONVERGED or
                    self.target_pf_status is not PfSolveStatus.NOT_RUN):
                raise PowerFlowContractError("source non-convergence status mismatch")
            self._require_no_metrics()
            self._require_failure_reason()
        elif status is PfFidelityStatus.TARGET_PF_NOT_CONVERGED:
            if (self.source_pf_status is not PfSolveStatus.CONVERGED or
                    self.target_pf_status is not PfSolveStatus.NOT_CONVERGED):
                raise PowerFlowContractError("target non-convergence status mismatch")
            self._require_no_metrics()
            self._require_failure_reason()
        elif status is PfFidelityStatus.PF_FIDELITY_UNSUPPORTED_SNAPSHOT_SCOPE:
            if (len(self.operating_point.snapshots) == 1 or
                    self.source_pf_status is not PfSolveStatus.NOT_RUN or
                    self.target_pf_status is not PfSolveStatus.NOT_RUN):
                raise PowerFlowContractError("unsupported snapshot status mismatch")
            self._require_no_metrics()
            self._require_failure_reason()
        elif status is PfFidelityStatus.PF_MAPPING_INVALID:
            if (self.source_pf_status is not PfSolveStatus.NOT_RUN or
                    self.target_pf_status is not PfSolveStatus.NOT_RUN):
                raise PowerFlowContractError("invalid mapping must fail before either PF attempt")
            self._require_no_metrics()
            self._require_failure_reason()
        elif status is PfFidelityStatus.FAILED:
            self._require_no_metrics()
            self._require_failure_reason()
        elif status is PfFidelityStatus.PF_FIDELITY_MEASURED:
            self._require_valid_target_structure()
            self._require_converged_with_metrics()
            self._require_no_failure_reason()
        elif status in {PfFidelityStatus.PF_FIDELITY_WITHIN_TOLERANCE,
                        PfFidelityStatus.PF_FIDELITY_OUTSIDE_TOLERANCE}:
            self._require_valid_target_structure()
            self._require_converged_with_metrics()
            if self.thresholds_digest is None:
                raise PowerFlowContractError("judged fidelity status requires thresholds digest")
            self._require_no_failure_reason()
        if self.failure_reason is not None and (
                not isinstance(self.failure_reason, str) or not self.failure_reason.strip()):
            raise PowerFlowContractError("failure_reason must be non-empty or None")

    def _metric_values(self) -> tuple[object, ...]:
        """Return all optional metric payloads for status-invariant checks."""
        return (self.bus_voltage_metrics, self.bus_voltage_preserved_metrics,
                self.bus_voltage_aggregated_metrics, self.angle_metrics,
                self.branch_p_metrics, self.branch_q_metrics,
                self.branch_p1_metrics, self.branch_q1_metrics,
                self.cutset_p_metrics, self.cutset_q_metrics,
                self.injection_metrics, self.loss_metrics)

    def _require_no_metrics(self) -> None:
        """Reject metric payloads when a valid comparison cannot be made."""
        if any(value is not None for value in self._metric_values()):
            raise PowerFlowContractError("fidelity metrics must be absent for this status")

    def _require_failure_reason(self) -> None:
        """Require explicit diagnostic text for mapping or execution failures."""
        if not isinstance(self.failure_reason, str) or not self.failure_reason.strip():
            raise PowerFlowContractError("failure status requires failure_reason")

    def _require_no_failure_reason(self) -> None:
        """Reject a failure diagnosis attached to a successful measurement."""
        if self.failure_reason is not None:
            raise PowerFlowContractError("successful fidelity status cannot have failure_reason")

    def _require_converged_with_metrics(self) -> None:
        """Require both solver sides and all first-stage metric groups."""
        if (self.source_pf_status is not PfSolveStatus.CONVERGED or
                self.target_pf_status is not PfSolveStatus.CONVERGED):
            raise PowerFlowContractError("measured fidelity requires both PF sides converged")
        if any(value is None for value in self._metric_values()):
            raise PowerFlowContractError("measured fidelity requires all metric groups")

    def _require_valid_target_structure(self) -> None:
        """Prevent a PF fidelity claim for a target that failed the structural Gate."""
        if self.target_structural_status != PF_FIDELITY_VALID_STRUCTURAL_STATUS:
            raise PowerFlowContractError("PF fidelity requires a structurally valid target")

