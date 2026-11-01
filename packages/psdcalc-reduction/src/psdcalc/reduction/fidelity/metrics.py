"""Deterministic error summaries and structural-stage PF comparison metrics."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Mapping, Sequence

import numpy as np

from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.fidelity.contracts import (
    POWER_FLOW_DIAGNOSTIC_THRESHOLD_FIELDS,
    POWER_FLOW_HARD_THRESHOLD_FIELDS,
    PfFidelityStatus,
    PowerFlowCoverage,
    PowerFlowErrorSummary,
    PowerFlowInjectionMetrics,
    PowerFlowLossMetrics,
    PowerFlowMetricPolicy,
    PowerFlowMetricSet,
    PowerFlowMetricThresholds,
)
from psdcalc.reduction.fidelity.mapping import PfComparisonMapping, PfMappingError
from psdcalc.reduction.fidelity.pypsa_executor import (
    PowerFlowExecutionResult,
    PowerFlowExecutionStatus,
)


class MetricError(ValueError):
    """Raised when metric inputs are incomplete, unsupported, or non-finite."""


@dataclass(frozen=True)
class PowerFlowThresholdEvaluation:
    """Hard acceptance outcome plus separate diagnostic threshold failures."""

    fidelity_status: PfFidelityStatus
    hard_failures: tuple[str, ...]
    diagnostic_failures: tuple[str, ...]


@dataclass(frozen=True)
class PowerFlowComparison:
    """Aggregate metrics and coverage from one validated S1-to-S4 comparison."""

    metrics: PowerFlowMetricSet
    coverage: PowerFlowCoverage
    angle_component_offsets_deg: Mapping[str, float]


def summarize_errors(
    reference: Sequence[float], candidate: Sequence[float],
) -> PowerFlowErrorSummary:
    """Summarize aligned values with NumPy linear-interpolated P95 of |error|."""
    if len(reference) != len(candidate):
        raise MetricError("reference and candidate samples must have equal length")
    ref = _finite_array(reference, "reference")
    cand = _finite_array(candidate, "candidate")
    if ref.size == 0:
        return PowerFlowErrorSummary(0, None, None, None, None)
    absolute = np.abs(ref - cand)
    return PowerFlowErrorSummary(
        sample_count=int(absolute.size),
        rmse=float(np.sqrt(np.mean(np.square(absolute)))),
        p95_absolute=float(np.quantile(absolute, 0.95, method="linear")),
        max_absolute=float(np.max(absolute)),
        mean_absolute=float(np.mean(absolute)),
    )


def wrap_deg(value: float) -> float:
    """Wrap a finite angle to the half-open interval [-180, 180) degrees."""
    number = _finite_scalar(value, "angle")
    return (number + 180.0) % 360.0 - 180.0


def align_component_angles(
    source_angles_deg: Mapping[StableId, float],
    target_angles_deg: Mapping[StableId, float],
    bus_alignment: Mapping[StableId, tuple[StableId, str]],
) -> tuple[dict[StableId, float], dict[str, float]]:
    """Remove one unweighted circular gauge offset per source component.

    ``bus_alignment`` maps each source Bus to its target Bus and source connected
    component label. An ambiguous circular mean fails closed instead of choosing
    an arbitrary reference angle.
    """
    if set(source_angles_deg) != set(bus_alignment):
        raise MetricError("source angle coverage does not match bus alignment")
    component_deltas: dict[str, list[float]] = defaultdict(list)
    for source_id, (target_id, component_id) in bus_alignment.items():
        if target_id not in target_angles_deg:
            raise MetricError("target angle coverage is incomplete")
        if not isinstance(component_id, str) or not component_id:
            raise MetricError("angle component label is invalid")
        component_deltas[component_id].append(
            wrap_deg(_finite_scalar(source_angles_deg[source_id], "source angle") -
                     _finite_scalar(target_angles_deg[target_id], "target angle")))
    offsets: dict[str, float] = {}
    for component_id, values in sorted(component_deltas.items()):
        radians = np.radians(np.asarray(values, dtype=float))
        sine, cosine = float(np.mean(np.sin(radians))), float(np.mean(np.cos(radians)))
        if math.hypot(sine, cosine) <= 1e-12:
            raise MetricError("component angle alignment has an ambiguous circular mean")
        offsets[component_id] = wrap_deg(math.degrees(math.atan2(sine, cosine)))
    errors = {
        source_id: wrap_deg(
            _finite_scalar(source_angles_deg[source_id], "source angle") -
            (_finite_scalar(target_angles_deg[target_id], "target angle") + offsets[component_id]))
        for source_id, (target_id, component_id) in bus_alignment.items()
    }
    return errors, offsets


def relative_error(error: float, reference: float, *, denominator_floor: float) -> float:
    """Return |error| / max(|reference|, floor), validating the floor."""
    residual = abs(_finite_scalar(error, "error"))
    reference_value = abs(_finite_scalar(reference, "reference"))
    floor = _finite_scalar(denominator_floor, "denominator_floor")
    if floor <= 0.0:
        raise MetricError("denominator_floor must be finite and > 0")
    return residual / max(reference_value, floor)


def compare_power_flow_results(
    source: PowerFlowExecutionResult,
    target: PowerFlowExecutionResult,
    mapping: PfComparisonMapping,
    policy: PowerFlowMetricPolicy,
) -> PowerFlowComparison:
    """Reduce two converged synthetic or production solves to aggregate metrics.

    Branch port values are aligned by terminal identity, never by backend row
    order. Each target corridor contributes one branch sample, while each
    cluster-local cutset contributes one cutset sample. Absorbed internal
    branches are counted as excluded and never assigned zero target flow.
    """
    if not isinstance(mapping, PfComparisonMapping):
        raise PfMappingError("PF comparison mapping must be validated before metrics")
    if (source.status is not PowerFlowExecutionStatus.PF_CONVERGED or not source.converged or
            target.status is not PowerFlowExecutionStatus.PF_CONVERGED or not target.converged):
        raise MetricError("metrics require converged source and target PF results")

    source_bus_ids = {entry.source_bus_id for entry in mapping.bus_entries}
    target_bus_ids = {entry.target_bus_id for entry in mapping.bus_entries}
    _require_exact_coverage(source.bus_vm_pu, source_bus_ids, "source Bus voltage")
    _require_exact_coverage(source.bus_va_deg, source_bus_ids, "source Bus angle")
    _require_exact_coverage(source.bus_p_mw, source_bus_ids, "source solved Bus P")
    _require_exact_coverage(source.bus_q_mvar, source_bus_ids, "source solved Bus Q")
    _require_exact_coverage(target.bus_vm_pu, target_bus_ids, "target Bus voltage")
    _require_exact_coverage(target.bus_va_deg, target_bus_ids, "target Bus angle")
    _require_exact_coverage(target.bus_p_mw, target_bus_ids, "target solved Bus P")
    _require_exact_coverage(target.bus_q_mvar, target_bus_ids, "target solved Bus Q")

    bus_lookup = {entry.source_bus_id: entry for entry in mapping.bus_entries}
    bus_voltage_errors = {
        source_id: _finite_scalar(source.bus_vm_pu[source_id], "source Vm") -
        _finite_scalar(target.bus_vm_pu[bus_lookup[source_id].target_bus_id], "target Vm")
        for source_id in sorted(source_bus_ids)
    }
    preserved_errors = [bus_voltage_errors[e.source_bus_id] for e in mapping.bus_entries
                        if e.comparison_type == "preserved_singleton"]
    aggregated_errors = [bus_voltage_errors[e.source_bus_id] for e in mapping.bus_entries
                         if e.comparison_type == "aggregated_group_member"]
    angle_alignment = {
        entry.source_bus_id: (entry.target_bus_id, entry.source_component_id)
        for entry in mapping.bus_entries
    }
    angle_errors, offsets = align_component_angles(
        source.bus_va_deg, target.bus_va_deg, angle_alignment)

    source_p0 = source.branch_p0
    source_q0 = source.branch_q0
    source_p1 = source.branch_p1
    source_q1 = source.branch_q1
    source_branch_ids = {entry.source_branch_id for entry in mapping.branch_entries}
    _require_exact_coverage(source_p0, source_branch_ids, "source branch P0")
    _require_exact_coverage(source_q0, source_branch_ids, "source branch Q0")
    _require_exact_coverage(source_p1, source_branch_ids, "source branch P1")
    _require_exact_coverage(source_q1, source_branch_ids, "source branch Q1")
    target_branch_ids = set(mapping.corridor_members)
    _require_exact_coverage(target.branch_p0, target_branch_ids, "target branch P0")
    _require_exact_coverage(target.branch_q0, target_branch_ids, "target branch Q0")
    _require_exact_coverage(target.branch_p1, target_branch_ids, "target branch P1")
    _require_exact_coverage(target.branch_q1, target_branch_ids, "target branch Q1")

    branch_errors: dict[str, list[float]] = {name: [] for name in ("p0", "q0", "p1", "q1")}
    for target_id, members in mapping.corridor_members.items():
        totals = {name: 0.0 for name in branch_errors}
        for member in members:
            source_port0 = 0 if member.orientation_sign == 1 else 1
            source_port1 = 1 - source_port0
            totals["p0"] += source.branch_p0[member.source_branch_id] if source_port0 == 0 else source.branch_p1[member.source_branch_id]
            totals["q0"] += source.branch_q0[member.source_branch_id] if source_port0 == 0 else source.branch_q1[member.source_branch_id]
            totals["p1"] += source.branch_p1[member.source_branch_id] if source_port1 == 1 else source.branch_p0[member.source_branch_id]
            totals["q1"] += source.branch_q1[member.source_branch_id] if source_port1 == 1 else source.branch_q0[member.source_branch_id]
        for port, target_values in (
                ("p0", target.branch_p0), ("q0", target.branch_q0),
                ("p1", target.branch_p1), ("q1", target.branch_q1)):
            branch_errors[port].append(totals[port] - target_values[target_id])

    cutset_p_errors: list[float] = []
    cutset_q_errors: list[float] = []
    for cluster_id, entries in mapping.cutsets_by_cluster.items():
        source_p = sum(_branch_terminal(source, entry.source_branch_id,
                                        entry.source_terminal_index, "p") for entry in entries)
        source_q = sum(_branch_terminal(source, entry.source_branch_id,
                                        entry.source_terminal_index, "q") for entry in entries)
        targets_by_id: dict[StableId, int] = {}
        for entry in entries:
            previous = targets_by_id.setdefault(entry.target_branch_id,
                                                entry.target_terminal_index)
            if previous != entry.target_terminal_index:
                raise MetricError("one target cutset branch has inconsistent terminal ownership")
        target_p = sum(_branch_terminal(target, branch_id, terminal, "p")
                       for branch_id, terminal in targets_by_id.items())
        target_q = sum(_branch_terminal(target, branch_id, terminal, "q")
                       for branch_id, terminal in targets_by_id.items())
        cutset_p_errors.append(source_p - target_p)
        cutset_q_errors.append(source_q - target_q)

    source_cluster_p: dict[StableId, float] = defaultdict(float)
    source_cluster_q: dict[StableId, float] = defaultdict(float)
    for entry in mapping.bus_entries:
        source_cluster_p[entry.target_bus_id] += source.bus_p_mw[entry.source_bus_id]
        source_cluster_q[entry.target_bus_id] += source.bus_q_mvar[entry.source_bus_id]
    injection_p = [source_cluster_p[bus] - target.bus_p_mw[bus] for bus in sorted(target_bus_ids)]
    injection_q = [source_cluster_q[bus] - target.bus_q_mvar[bus] for bus in sorted(target_bus_ids)]

    source_losses = _passive_branch_loss(source)
    target_losses = _passive_branch_loss(target)
    loss_difference = abs(source_losses - target_losses)
    source_balance = sum(source.bus_p_mw.values())
    target_balance = sum(target.bus_p_mw.values())
    loss_metrics = PowerFlowLossMetrics(
        passive_branch_active_abs_diff_mw=loss_difference,
        passive_branch_active_relative_diff=relative_error(
            loss_difference, source_losses,
            denominator_floor=policy.loss_denominator_floor_mw),
        total_active_balance_abs_diff_mw=abs(source_balance - target_balance),
    )

    metrics = PowerFlowMetricSet(
        bus_voltage=summarize_errors([0.0] * len(bus_voltage_errors),
                                     [bus_voltage_errors[key] for key in sorted(bus_voltage_errors)]),
        angle=summarize_errors([0.0] * len(angle_errors),
                               [angle_errors[key] for key in sorted(angle_errors)]),
        branch_p=summarize_errors([0.0] * len(branch_errors["p0"]), branch_errors["p0"]),
        branch_q=summarize_errors([0.0] * len(branch_errors["q0"]), branch_errors["q0"]),
        cutset_p=summarize_errors([0.0] * len(cutset_p_errors), cutset_p_errors),
        cutset_q=summarize_errors([0.0] * len(cutset_q_errors), cutset_q_errors),
        cluster_injection=PowerFlowInjectionMetrics(
            summarize_errors([0.0] * len(injection_p), injection_p),
            summarize_errors([0.0] * len(injection_q), injection_q)),
        passive_branch_losses=loss_metrics,
        bus_voltage_preserved=summarize_errors([0.0] * len(preserved_errors), preserved_errors),
        bus_voltage_aggregated=summarize_errors([0.0] * len(aggregated_errors), aggregated_errors),
        branch_p1=summarize_errors([0.0] * len(branch_errors["p1"]), branch_errors["p1"]),
        branch_q1=summarize_errors([0.0] * len(branch_errors["q1"]), branch_errors["q1"]),
    )
    coverage = PowerFlowCoverage(
        source_bus_count=mapping.coverage.source_bus_count,
        mapped_bus_count=mapping.coverage.mapped_bus_count,
        source_branch_count=mapping.coverage.source_branch_count,
        comparable_branch_count=mapping.coverage.comparable_branch_count,
        absorbed_internal_branch_count=mapping.coverage.absorbed_internal_branch_count,
        target_corridor_count=mapping.coverage.target_corridor_count,
        preserved_bus_count=mapping.coverage.preserved_bus_count,
        aggregated_bus_count=mapping.coverage.aggregated_bus_count,
        excluded_object_count=mapping.coverage.excluded_object_count,
    )
    return PowerFlowComparison(metrics, coverage, offsets)


def reference_cutset_magnitudes(
    source: PowerFlowExecutionResult, mapping: PfComparisonMapping,
) -> tuple[float, float]:
    """Return max absolute S1 P/Q aggregate over the mapped cutsets by cluster."""
    if source.status is not PowerFlowExecutionStatus.PF_CONVERGED or not source.converged:
        raise MetricError("cutset reference magnitudes require a converged source PF")
    p_values: list[float] = []
    q_values: list[float] = []
    for entries in mapping.cutsets_by_cluster.values():
        p_values.append(sum(_branch_terminal(source, entry.source_branch_id,
                                             entry.source_terminal_index, "p") for entry in entries))
        q_values.append(sum(_branch_terminal(source, entry.source_branch_id,
                                             entry.source_terminal_index, "q") for entry in entries))
    return (max((abs(value) for value in p_values), default=0.0),
            max((abs(value) for value in q_values), default=0.0))


def evaluate_metric_thresholds(
    metrics: PowerFlowMetricSet | None,
    thresholds: PowerFlowMetricThresholds,
) -> PfFidelityStatus:
    """Return acceptance status from HARD thresholds only."""
    return evaluate_metric_thresholds_detailed(metrics, thresholds).fidelity_status


def evaluate_metric_thresholds_detailed(
    metrics: PowerFlowMetricSet | None,
    thresholds: PowerFlowMetricThresholds,
) -> PowerFlowThresholdEvaluation:
    """Evaluate HARD acceptance and DIAGNOSTIC thresholds as separate outcomes."""
    if not thresholds.is_frozen:
        raise MetricError("fidelity acceptance thresholds are not configured")
    if metrics is None:
        raise MetricError("metrics are unavailable")
    hard_values: dict[str, tuple[float | None, float | None]] = {
        "vm_max_pu": (metrics.bus_voltage.max_absolute, thresholds.vm_max_pu),
        "vm_rmse_pu": (metrics.bus_voltage.rmse, thresholds.vm_rmse_pu),
        "cutset_p_max_mw": (metrics.cutset_p.max_absolute, thresholds.cutset_p_max_mw),
        "cutset_q_max_mvar": (metrics.cutset_q.max_absolute, thresholds.cutset_q_max_mvar),
        "loss_rel_max": (metrics.passive_branch_losses.passive_branch_active_relative_diff,
                          thresholds.loss_rel_max),
    }
    diagnostic_values: dict[str, tuple[float | None, float | None]] = {
        "vm_p95_pu": (metrics.bus_voltage.p95_absolute, thresholds.vm_p95_pu),
        "angle_rmse_deg": (metrics.angle.rmse, thresholds.angle_rmse_deg),
        "angle_p95_deg": (metrics.angle.p95_absolute, thresholds.angle_p95_deg),
        "angle_max_deg": (metrics.angle.max_absolute, thresholds.angle_max_deg),
        "branch_p_rmse_mw": (metrics.branch_p.rmse, thresholds.branch_p_rmse_mw),
        "branch_p_p95_mw": (metrics.branch_p.p95_absolute, thresholds.branch_p_p95_mw),
        "branch_p_max_mw": (metrics.branch_p.max_absolute, thresholds.branch_p_max_mw),
        "branch_q_rmse_mvar": (metrics.branch_q.rmse, thresholds.branch_q_rmse_mvar),
        "branch_q_p95_mvar": (metrics.branch_q.p95_absolute, thresholds.branch_q_p95_mvar),
        "branch_q_max_mvar": (metrics.branch_q.max_absolute, thresholds.branch_q_max_mvar),
        "cutset_p_rmse_mw": (metrics.cutset_p.rmse, thresholds.cutset_p_rmse_mw),
        "cutset_q_rmse_mvar": (metrics.cutset_q.rmse, thresholds.cutset_q_rmse_mvar),
        "injection_p_rmse_mw": (metrics.cluster_injection.p_mw.rmse, thresholds.injection_p_rmse_mw),
        "injection_p_max_mw": (metrics.cluster_injection.p_mw.max_absolute, thresholds.injection_p_max_mw),
        "injection_q_rmse_mvar": (metrics.cluster_injection.q_mvar.rmse, thresholds.injection_q_rmse_mvar),
        "injection_q_max_mvar": (metrics.cluster_injection.q_mvar.max_absolute, thresholds.injection_q_max_mvar),
        "loss_abs_max_mw": (metrics.passive_branch_losses.passive_branch_active_abs_diff_mw,
                            thresholds.loss_abs_max_mw),
    }
    if set(hard_values) != POWER_FLOW_HARD_THRESHOLD_FIELDS:
        raise MetricError("HARD threshold classification does not match its evaluator")
    if set(diagnostic_values) != POWER_FLOW_DIAGNOSTIC_THRESHOLD_FIELDS:
        raise MetricError("DIAGNOSTIC threshold classification does not match its evaluator")
    hard_failures = _threshold_failures(hard_values, required=True)
    diagnostic_failures = _threshold_failures(diagnostic_values, required=False)
    status = (PfFidelityStatus.PF_FIDELITY_OUTSIDE_TOLERANCE if hard_failures else
              PfFidelityStatus.PF_FIDELITY_WITHIN_TOLERANCE)
    return PowerFlowThresholdEvaluation(status, hard_failures, diagnostic_failures)


def _threshold_failures(
    values: Mapping[str, tuple[float | None, float | None]], *, required: bool,
) -> tuple[str, ...]:
    """Return exceeded threshold names, failing on missing required observations."""
    failures: list[str] = []
    for name, (measured, threshold) in values.items():
        if threshold is None and not required:
            continue
        if measured is None or threshold is None:
            raise MetricError(f"configured threshold has no measured metric: {name}")
        if float(measured) > float(threshold):
            failures.append(name)
    return tuple(sorted(failures))


def _canonical_snapshot_value(value: object) -> object:
    """Normalize immutable snapshot labels independently of NumPy scalar wrappers."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, tuple):
        return {"type": "tuple", "items": [_canonical_snapshot_value(item) for item in value]}
    if isinstance(value, (datetime, date)):
        return {"type": "timestamp", "value": value.isoformat()}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int):
        return {"type": "int", "value": value}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MetricError("snapshot label must be finite")
        return {"type": "float", "value": value}
    if isinstance(value, str):
        return {"type": "str", "value": value}
    raise MetricError(f"unsupported snapshot label type: {type(value).__name__}")


def operating_point_digest(contract: object) -> str:
    """Return canonical digest of a PowerFlowOperatingPointContract."""
    try:
        payload = asdict(contract)
    except (TypeError, ValueError) as exc:
        raise MetricError("operating point cannot be canonically serialized") from exc
    payload["snapshots"] = [_canonical_snapshot_value(value) for value in payload["snapshots"]]
    return _digest(payload)


def metric_policy_digest(policy: PowerFlowMetricPolicy) -> str:
    """Return canonical SHA-256 of denominator-floor normalization settings."""
    return _digest(asdict(policy))


def thresholds_digest(thresholds: PowerFlowMetricThresholds) -> str:
    """Return canonical SHA-256 of an explicit fidelity threshold set."""
    return _digest(asdict(thresholds))


def _branch_terminal(
    result: PowerFlowExecutionResult, branch_id: StableId, terminal: int, quantity: str,
) -> float:
    """Read one terminal P/Q value using the PyPSA branch-output naming contract."""
    mapping = getattr(result, f"branch_{quantity}{terminal}")
    try:
        return _finite_scalar(mapping[branch_id], f"branch {quantity}{terminal}")
    except KeyError as exc:
        raise MetricError("branch terminal output coverage is incomplete") from exc


def _passive_branch_loss(result: PowerFlowExecutionResult) -> float:
    """Sum Line and Transformer p0+p1 losses, excluding shunts and Links."""
    if set(result.branch_p0) != set(result.branch_p1):
        raise MetricError("passive branch P terminal coverage is inconsistent")
    total = sum(_finite_scalar(result.branch_p0[key], "branch p0") +
                _finite_scalar(result.branch_p1[key], "branch p1")
                for key in result.branch_p0)
    return _finite_scalar(total, "passive branch loss")


def _require_exact_coverage(values: Mapping[StableId, float], expected: set[StableId], label: str) -> None:
    """Fail closed if a solved-output table has missing or unexpected StableIds."""
    if set(values) != expected:
        raise MetricError(f"{label} coverage does not match validated mapping")
    for value in values.values():
        _finite_scalar(value, label)


def _finite_array(values: Sequence[float], label: str) -> np.ndarray:
    """Convert a numeric sequence to finite float64 values without bool coercion."""
    if any(isinstance(value, bool) or not isinstance(value, (int, float, np.number))
           for value in values):
        raise MetricError(f"{label} values must be numeric")
    array = np.asarray(values, dtype=np.float64)
    if not np.isfinite(array).all():
        raise MetricError(f"{label} values must be finite")
    return array


def _finite_scalar(value: object, label: str) -> float:
    """Validate and normalize one finite scalar value."""
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        raise MetricError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise MetricError(f"{label} must be finite")
    return number


def _digest(payload: object) -> str:
    """Hash compact, key-sorted JSON with no NaN extensions."""
    try:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False, allow_nan=False, default=str)
    except (TypeError, ValueError) as exc:
        raise MetricError("digest payload is not canonical JSON") from exc
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
