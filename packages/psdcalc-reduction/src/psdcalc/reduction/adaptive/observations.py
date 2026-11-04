"""Typed, replayable numerical and runtime observations for adaptive runs.

The observation layer is a projection of existing PF, metric, and threshold
contracts. It does not execute PF or make Fidelity/controller decisions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
import hashlib
import json
import math
import os
from types import MappingProxyType
from typing import Literal, Mapping

from psdcalc.reduction.fidelity.contracts import (
    POWER_FLOW_DIAGNOSTIC_THRESHOLD_FIELDS,
    POWER_FLOW_HARD_THRESHOLD_FIELDS,
    PowerFlowMetricSet,
    PowerFlowMetricThresholds,
)
from psdcalc.reduction.fidelity.metrics import (
    PowerFlowThresholdEvaluation,
    metric_set_digest,
)
from psdcalc.reduction.fidelity.persistence import ResolvedPowerFlowThresholds
from psdcalc.reduction.fidelity.pypsa_executor import (
    PowerFlowExecutionResult,
    PowerFlowExecutionStatus,
)


NUMERIC_OBSERVATION_SCHEMA = "adaptive-numeric-observations/v1"
OBSERVATION_SCHEMA_VERSION = "1"
_ROLES = {"source", "initial_candidate", "trial_candidate", "incumbent"}
_TIMING_OPERATIONS = {
    "source_pf_total", "source_pf_solver", "candidate_pf_total", "candidate_pf_solver",
    "candidate_reconstruction",
    "structural_ac_readiness", "fidelity_comparison", "attribution",
    "adaptive_controller_total", "evidence_persistence", "fresh_process_replay_total",
}
_PF_STATUSES = {item.value for item in PowerFlowExecutionStatus} | {
    "NOT_RUN", "NOT_APPLICABLE", "PF_FIDELITY_WITHIN_TOLERANCE",
    "PF_FIDELITY_OUTSIDE_TOLERANCE", "FAILED",
}


class AdaptiveObservationError(ValueError):
    """Raised when an observation is incomplete, unsafe, or non-canonical."""


class ObservationAvailability(StrEnum):
    """Explicitly distinguish measured, missing, and inapplicable fields."""

    RECORDED = "RECORDED"
    NOT_RECORDED = "NOT_RECORDED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class NumericObservationValue:
    """One finite scalar with an explicit availability state."""

    value: int | float | str | bool | None
    availability: ObservationAvailability

    def __post_init__(self) -> None:
        """Reject non-finite values and ambiguous null/zero substitutions."""
        if not isinstance(self.availability, ObservationAvailability):
            raise AdaptiveObservationError("availability must be ObservationAvailability")
        if self.availability is ObservationAvailability.RECORDED:
            if self.value is None:
                raise AdaptiveObservationError("RECORDED availability requires a value")
        elif self.value is not None:
            raise AdaptiveObservationError("unavailable values must be None")
        if isinstance(self.value, bool):
            return
        if isinstance(self.value, (int, float)) and not math.isfinite(float(self.value)):
            raise AdaptiveObservationError("numeric observation value must be finite")
        if self.value is not None and not isinstance(self.value, (int, float, str, bool)):
            raise AdaptiveObservationError("observation value must be a JSON scalar")
        if isinstance(self.value, str):
            _reject_path_text(self.value, "observation value")

    def to_dict(self) -> dict[str, object]:
        """Return an order-independent JSON-compatible value payload."""
        return {"availability": self.availability.value, "value": self.value}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> NumericObservationValue:
        """Validate and restore one scalar payload."""
        if not isinstance(payload, Mapping) or set(payload) != {"availability", "value"}:
            raise AdaptiveObservationError("observation value fields do not match schema")
        try:
            availability = ObservationAvailability(payload["availability"])
        except (TypeError, ValueError) as exc:
            raise AdaptiveObservationError("unknown observation availability") from exc
        return cls(payload["value"], availability)  # type: ignore[arg-type]

    def canonical_json(self) -> str:
        """Serialize with sorted keys and finite-number enforcement."""
        return _canonical_json(self.to_dict())


@dataclass(frozen=True)
class SubnetworkIterationObservation:
    """Anonymous authoritative iteration and error summary for one subnetwork."""

    subnetwork_ordinal: int
    converged: bool
    iterations: NumericObservationValue
    final_mismatch: NumericObservationValue
    mismatch_unit: str = "pu"

    def __post_init__(self) -> None:
        """Validate ordinal, convergence state, and explicit engineering units."""
        if isinstance(self.subnetwork_ordinal, bool) or self.subnetwork_ordinal < 0:
            raise AdaptiveObservationError("subnetwork_ordinal must be nonnegative")
        if not isinstance(self.converged, bool):
            raise AdaptiveObservationError("subnetwork converged must be bool")
        if self.iterations.value is not None and (
                isinstance(self.iterations.value, bool) or
                not isinstance(self.iterations.value, int) or self.iterations.value < 0):
            raise AdaptiveObservationError("subnetwork iterations must be a nonnegative integer")
        if self.final_mismatch.value is not None and (
                isinstance(self.final_mismatch.value, bool) or
                not isinstance(self.final_mismatch.value, (int, float))):
            raise AdaptiveObservationError("subnetwork mismatch must be numeric")
        if self.mismatch_unit != "pu":
            raise AdaptiveObservationError("mismatch_unit must be pu")

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-compatible record."""
        return {
            "subnetwork_ordinal": self.subnetwork_ordinal,
            "converged": self.converged,
            "iterations": self.iterations.to_dict(),
            "final_mismatch": self.final_mismatch.to_dict(),
            "mismatch_unit": self.mismatch_unit,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> SubnetworkIterationObservation:
        """Restore one validated subnetwork observation."""
        expected = {"subnetwork_ordinal", "converged", "iterations", "final_mismatch", "mismatch_unit"}
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise AdaptiveObservationError("subnetwork observation fields do not match schema")
        return cls(
            subnetwork_ordinal=payload["subnetwork_ordinal"],  # type: ignore[arg-type]
            converged=payload["converged"],  # type: ignore[arg-type]
            iterations=NumericObservationValue.from_dict(payload["iterations"]),  # type: ignore[arg-type]
            final_mismatch=NumericObservationValue.from_dict(payload["final_mismatch"]),  # type: ignore[arg-type]
            mismatch_unit=payload["mismatch_unit"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class MetricValueObservation:
    """One existing metric with its resolved scalar limit and evaluator result."""

    metric_name: str
    severity: Literal["HARD", "DIAGNOSTIC"]
    observed_value: NumericObservationValue
    unit: str
    sample_count: NumericObservationValue
    resolved_threshold: NumericObservationValue
    margin: NumericObservationValue
    status: Literal["PASS", "FAIL", "NOT_APPLICABLE", "NOT_RECORDED"]
    metric_provenance: str

    def __post_init__(self) -> None:
        """Require valid metric identity, units, and consistent availability."""
        if not self.metric_name or self.severity not in {"HARD", "DIAGNOSTIC"}:
            raise AdaptiveObservationError("metric identity or severity is invalid")
        if not self.unit or not self.metric_provenance:
            raise AdaptiveObservationError("metric unit and provenance must be explicit")
        _reject_path_text(self.metric_provenance, "metric provenance")
        if self.status not in {"PASS", "FAIL", "NOT_APPLICABLE", "NOT_RECORDED"}:
            raise AdaptiveObservationError("invalid threshold status")
        if self.status in {"PASS", "FAIL"}:
            if any(value.availability is not ObservationAvailability.RECORDED for value in (
                    self.observed_value, self.resolved_threshold, self.margin)):
                raise AdaptiveObservationError("PASS/FAIL requires observed value, threshold, and margin")
        if self.status == "NOT_APPLICABLE" and (
                self.resolved_threshold.availability is not ObservationAvailability.NOT_APPLICABLE or
                self.margin.availability is not ObservationAvailability.NOT_APPLICABLE):
            raise AdaptiveObservationError("NOT_APPLICABLE requires unavailable threshold and margin")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible metric row."""
        return {
            "metric_name": self.metric_name,
            "severity": self.severity,
            "observed_value": self.observed_value.to_dict(),
            "unit": self.unit,
            "sample_count": self.sample_count.to_dict(),
            "resolved_threshold": self.resolved_threshold.to_dict(),
            "margin": self.margin.to_dict(),
            "status": self.status,
            "metric_provenance": self.metric_provenance,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> MetricValueObservation:
        """Restore and validate one metric row."""
        expected = {
            "metric_name", "severity", "observed_value", "unit", "sample_count",
            "resolved_threshold", "margin", "status", "metric_provenance",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise AdaptiveObservationError("metric observation fields do not match schema")
        return cls(
            metric_name=payload["metric_name"],  # type: ignore[arg-type]
            severity=payload["severity"],  # type: ignore[arg-type]
            observed_value=NumericObservationValue.from_dict(payload["observed_value"]),  # type: ignore[arg-type]
            unit=payload["unit"],  # type: ignore[arg-type]
            sample_count=NumericObservationValue.from_dict(payload["sample_count"]),  # type: ignore[arg-type]
            resolved_threshold=NumericObservationValue.from_dict(payload["resolved_threshold"]),  # type: ignore[arg-type]
            margin=NumericObservationValue.from_dict(payload["margin"]),  # type: ignore[arg-type]
            status=payload["status"],  # type: ignore[arg-type]
            metric_provenance=payload["metric_provenance"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class NumericFidelityObservation:
    """One PF execution result or one authoritative metric comparison."""

    observation_id: str
    observation_kind: Literal["pf_execution", "fidelity_comparison"]
    role: Literal["source", "initial_candidate", "trial_candidate", "incumbent"]
    snapshot_identity: NumericObservationValue
    pf_status: str
    source_pf_status: str
    candidate_pf_status: str
    subnetwork_observations: tuple[SubnetworkIterationObservation, ...]
    metric_observations: tuple[MetricValueObservation, ...]
    metric_availability: ObservationAvailability
    metric_set_digest: str | None
    resolved_threshold_digest: str | None
    source_model_digest: str
    candidate_model_digest: str | None
    candidate_identity: str | None
    data_provenance: str
    source_sidecar_digest: str | None = None
    candidate_sidecar_digest: str | None = None
    solver_contract_digest: str | None = None
    operating_point_digest: str | None = None
    threshold_set_id: str | None = None
    threshold_set_digest: str | None = None
    failure_reason: str | None = None
    solver_contract_summary: Mapping[str, NumericObservationValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Bind every observation to explicit model, PF, metric, and availability facts."""
        _require_digest(self.observation_id, "observation_id")
        _require_digest(self.source_model_digest, "source_model_digest")
        if self.candidate_model_digest is not None:
            _require_digest(self.candidate_model_digest, "candidate_model_digest")
        if self.candidate_identity is not None:
            _require_digest(self.candidate_identity, "candidate_identity")
        if self.metric_set_digest is not None:
            _require_digest(self.metric_set_digest, "metric_set_digest")
        if self.resolved_threshold_digest is not None:
            _require_digest(self.resolved_threshold_digest, "resolved_threshold_digest")
        for name in ("source_sidecar_digest", "candidate_sidecar_digest", "solver_contract_digest",
                     "operating_point_digest", "threshold_set_digest"):
            value = getattr(self, name)
            if value is not None:
                _require_digest(value, name)
        if self.observation_kind not in {"pf_execution", "fidelity_comparison"}:
            raise AdaptiveObservationError("unknown numeric observation kind")
        if self.role not in _ROLES:
            raise AdaptiveObservationError("unknown numeric observation role")
        if self.pf_status not in _PF_STATUSES or self.source_pf_status not in _PF_STATUSES or self.candidate_pf_status not in _PF_STATUSES:
            raise AdaptiveObservationError("unknown PF observation status")
        if not self.data_provenance:
            raise AdaptiveObservationError("data provenance must be explicit")
        _reject_path_text(self.data_provenance, "data provenance")
        if self.threshold_set_id is not None:
            _reject_path_text(self.threshold_set_id, "threshold_set_id")
        if self.failure_reason is not None:
            _reject_path_text(self.failure_reason, "failure_reason")
        summary: dict[str, NumericObservationValue] = {}
        for key, value in sorted(self.solver_contract_summary.items()):
            if not key or not isinstance(value, NumericObservationValue):
                raise AdaptiveObservationError("solver contract summary must contain typed scalar values")
            summary[key] = value
        object.__setattr__(self, "solver_contract_summary", MappingProxyType(summary))
        if tuple(sorted(item.subnetwork_ordinal for item in self.subnetwork_observations)) != tuple(
                item.subnetwork_ordinal for item in self.subnetwork_observations):
            raise AdaptiveObservationError("subnetwork observations must be ordinal-sorted")
        metric_names = tuple(item.metric_name for item in self.metric_observations)
        if tuple(sorted(set(metric_names))) != metric_names:
            raise AdaptiveObservationError("metric observations must be unique and name-sorted")
        if self.observation_kind == "pf_execution":
            if self.metric_observations or self.metric_set_digest or self.resolved_threshold_digest:
                raise AdaptiveObservationError("PF execution observations cannot contain comparison metrics")
            if self.metric_availability is not ObservationAvailability.NOT_APPLICABLE:
                raise AdaptiveObservationError("PF execution metric availability must be NOT_APPLICABLE")
        else:
            if self.pf_status != "NOT_APPLICABLE":
                raise AdaptiveObservationError("comparison observation pf_status must be NOT_APPLICABLE")
            if self.metric_availability is not ObservationAvailability.RECORDED:
                raise AdaptiveObservationError("comparison metrics must be RECORDED")
            if not self.metric_set_digest or not self.resolved_threshold_digest:
                raise AdaptiveObservationError("comparison observation requires both metric/threshold digests")
            expected = POWER_FLOW_HARD_THRESHOLD_FIELDS | POWER_FLOW_DIAGNOSTIC_THRESHOLD_FIELDS
            if set(metric_names) != expected:
                raise AdaptiveObservationError("comparison observation must contain every evaluator metric")

    def to_dict(self) -> dict[str, object]:
        """Return the canonical observation payload."""
        return {
            "schema": NUMERIC_OBSERVATION_SCHEMA,
            "observation_id": self.observation_id,
            "observation_kind": self.observation_kind,
            "role": self.role,
            "snapshot_identity": self.snapshot_identity.to_dict(),
            "pf_status": self.pf_status,
            "source_pf_status": self.source_pf_status,
            "candidate_pf_status": self.candidate_pf_status,
            "subnetwork_observations": [item.to_dict() for item in self.subnetwork_observations],
            "metric_observations": [item.to_dict() for item in self.metric_observations],
            "metric_availability": self.metric_availability.value,
            "metric_set_digest": self.metric_set_digest,
            "resolved_threshold_digest": self.resolved_threshold_digest,
            "source_model_digest": self.source_model_digest,
            "candidate_model_digest": self.candidate_model_digest,
            "candidate_identity": self.candidate_identity,
            "data_provenance": self.data_provenance,
            "source_sidecar_digest": self.source_sidecar_digest,
            "candidate_sidecar_digest": self.candidate_sidecar_digest,
            "solver_contract_digest": self.solver_contract_digest,
            "operating_point_digest": self.operating_point_digest,
            "threshold_set_id": self.threshold_set_id,
            "threshold_set_digest": self.threshold_set_digest,
            "failure_reason": self.failure_reason,
            "solver_contract_summary": {
                key: value.to_dict() for key, value in self.solver_contract_summary.items()
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> NumericFidelityObservation:
        """Restore one strict schema-v1 numeric observation."""
        expected = {
            "schema", "observation_id", "observation_kind", "role", "snapshot_identity",
            "pf_status", "source_pf_status", "candidate_pf_status", "subnetwork_observations",
            "metric_observations", "metric_availability", "metric_set_digest",
            "resolved_threshold_digest", "source_model_digest", "candidate_model_digest",
            "candidate_identity", "data_provenance", "source_sidecar_digest",
            "candidate_sidecar_digest", "solver_contract_digest", "operating_point_digest",
            "threshold_set_id", "threshold_set_digest", "failure_reason",
            "solver_contract_summary",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected or payload.get("schema") != NUMERIC_OBSERVATION_SCHEMA:
            raise AdaptiveObservationError("numeric observation fields/schema do not match v1")
        try:
            availability = ObservationAvailability(payload["metric_availability"])
        except (TypeError, ValueError) as exc:
            raise AdaptiveObservationError("unknown metric availability") from exc
        subnetworks = payload["subnetwork_observations"]
        metrics = payload["metric_observations"]
        solver_summary = payload["solver_contract_summary"]
        if not isinstance(subnetworks, (list, tuple)) or not isinstance(metrics, (list, tuple)):
            raise AdaptiveObservationError("observation arrays are invalid")
        if not isinstance(solver_summary, Mapping):
            raise AdaptiveObservationError("solver contract summary must be a mapping")
        return cls(
            observation_id=payload["observation_id"],  # type: ignore[arg-type]
            observation_kind=payload["observation_kind"],  # type: ignore[arg-type]
            role=payload["role"],  # type: ignore[arg-type]
            snapshot_identity=NumericObservationValue.from_dict(payload["snapshot_identity"]),  # type: ignore[arg-type]
            pf_status=payload["pf_status"],  # type: ignore[arg-type]
            source_pf_status=payload["source_pf_status"],  # type: ignore[arg-type]
            candidate_pf_status=payload["candidate_pf_status"],  # type: ignore[arg-type]
            subnetwork_observations=tuple(SubnetworkIterationObservation.from_dict(row) for row in subnetworks),
            metric_observations=tuple(MetricValueObservation.from_dict(row) for row in metrics),
            metric_availability=availability,
            metric_set_digest=payload["metric_set_digest"],  # type: ignore[arg-type]
            resolved_threshold_digest=payload["resolved_threshold_digest"],  # type: ignore[arg-type]
            source_model_digest=payload["source_model_digest"],  # type: ignore[arg-type]
            candidate_model_digest=payload["candidate_model_digest"],  # type: ignore[arg-type]
            candidate_identity=payload["candidate_identity"],  # type: ignore[arg-type]
            data_provenance=payload["data_provenance"],  # type: ignore[arg-type]
            source_sidecar_digest=payload["source_sidecar_digest"],  # type: ignore[arg-type]
            candidate_sidecar_digest=payload["candidate_sidecar_digest"],  # type: ignore[arg-type]
            solver_contract_digest=payload["solver_contract_digest"],  # type: ignore[arg-type]
            operating_point_digest=payload["operating_point_digest"],  # type: ignore[arg-type]
            threshold_set_id=payload["threshold_set_id"],  # type: ignore[arg-type]
            threshold_set_digest=payload["threshold_set_digest"],  # type: ignore[arg-type]
            failure_reason=payload["failure_reason"],  # type: ignore[arg-type]
            solver_contract_summary={
                str(key): NumericObservationValue.from_dict(value)
                for key, value in solver_summary.items()
            },  # type: ignore[arg-type]
        )

    def canonical_json(self) -> str:
        """Serialize the canonical payload independent of field declaration order."""
        return _canonical_json(self.to_dict())


@dataclass(frozen=True)
class PerformanceTimingObservation:
    """One monotonic runtime diagnostic, never a semantic fingerprint input."""

    operation_name: str
    role: str
    duration: float | None
    duration_unit: str
    timing_scope: str
    run_id: str
    process_identity: str
    backend: str
    backend_version: str
    availability_status: ObservationAvailability = ObservationAvailability.RECORDED
    candidate_identity: str | None = None
    operation_status: str = "COMPLETED"
    schema_version: str = OBSERVATION_SCHEMA_VERSION
    timing_id: str | None = None

    def __post_init__(self) -> None:
        """Validate duration, scope, role, and anonymous identity fields."""
        if self.operation_name not in _TIMING_OPERATIONS:
            raise AdaptiveObservationError("unsupported timing operation")
        if self.role not in _ROLES | {"evidence", "replay", "run"}:
            raise AdaptiveObservationError("unsupported timing role")
        if self.duration_unit != "s":
            raise AdaptiveObservationError("duration_unit must be s")
        if self.availability_status is ObservationAvailability.RECORDED:
            if self.duration is None or isinstance(self.duration, bool) or not math.isfinite(self.duration) or self.duration < 0:
                raise AdaptiveObservationError("recorded duration must be finite and nonnegative")
        elif self.duration is not None:
            raise AdaptiveObservationError("unavailable duration must be None")
        if self.schema_version != OBSERVATION_SCHEMA_VERSION:
            raise AdaptiveObservationError("unsupported timing observation schema version")
        for name in ("timing_scope", "run_id", "process_identity", "backend", "backend_version", "operation_status"):
            value = getattr(self, name)
            if not value:
                raise AdaptiveObservationError(f"{name} must be explicit")
            _reject_path_text(value, name)
        if self.candidate_identity is not None:
            _require_digest(self.candidate_identity, "candidate_identity")
        if self.timing_id is None:
            object.__setattr__(self, "timing_id", _digest({
                "operation_name": self.operation_name,
                "role": self.role,
                "timing_scope": self.timing_scope,
                "run_id": self.run_id,
                "process_identity": self.process_identity,
                "backend": self.backend,
                "backend_version": self.backend_version,
                "candidate_identity": self.candidate_identity,
                "operation_status": self.operation_status,
            }))
        _require_digest(self.timing_id, "timing_id")

    def to_dict(self) -> dict[str, object]:
        """Return canonical timing payload; duration is not in numeric semantics."""
        return {
            "schema_version": self.schema_version,
            "operation_name": self.operation_name,
            "role": self.role,
            "duration": self.duration,
            "duration_unit": self.duration_unit,
            "timing_scope": self.timing_scope,
            "run_id": self.run_id,
            "process_identity": self.process_identity,
            "backend": self.backend,
            "backend_version": self.backend_version,
            "availability_status": self.availability_status.value,
            "candidate_identity": self.candidate_identity,
            "operation_status": self.operation_status,
            "timing_id": self.timing_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> PerformanceTimingObservation:
        """Restore one strict timing payload."""
        expected = {
            "schema_version", "operation_name", "role", "duration", "duration_unit",
            "timing_scope", "run_id", "process_identity", "backend", "backend_version",
            "availability_status", "candidate_identity", "operation_status",
            "timing_id",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise AdaptiveObservationError("timing observation fields do not match schema")
        try:
            availability = ObservationAvailability(payload["availability_status"])
        except (TypeError, ValueError) as exc:
            raise AdaptiveObservationError("unknown timing availability") from exc
        return cls(
            operation_name=payload["operation_name"],  # type: ignore[arg-type]
            role=payload["role"],  # type: ignore[arg-type]
            duration=payload["duration"],  # type: ignore[arg-type]
            duration_unit=payload["duration_unit"],  # type: ignore[arg-type]
            timing_scope=payload["timing_scope"],  # type: ignore[arg-type]
            run_id=payload["run_id"],  # type: ignore[arg-type]
            process_identity=payload["process_identity"],  # type: ignore[arg-type]
            backend=payload["backend"],  # type: ignore[arg-type]
            backend_version=payload["backend_version"],  # type: ignore[arg-type]
            availability_status=availability,
            candidate_identity=payload["candidate_identity"],  # type: ignore[arg-type]
            operation_status=payload["operation_status"],  # type: ignore[arg-type]
            schema_version=payload["schema_version"],  # type: ignore[arg-type]
            timing_id=payload["timing_id"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class AdaptiveEvaluationObservation:
    """Observation links from one controller incumbent or expansion candidate."""

    evaluation_id: str
    role: Literal["initial_candidate", "trial_candidate", "incumbent"]
    iteration_index: int
    candidate_identity: str | None
    build_attempt_identity: str | None
    reconstruction_status: str
    structural_status: str
    readiness_status: str
    pf_status: str
    fidelity_status: str
    numeric_observation_ids: tuple[str, ...] = ()
    timing_observation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate deterministic links and explicit stage states."""
        _require_digest(self.evaluation_id, "evaluation_id")
        for name in ("candidate_identity", "build_attempt_identity"):
            value = getattr(self, name)
            if value is not None:
                _require_digest(value, name)
        if self.role not in {"initial_candidate", "trial_candidate", "incumbent"}:
            raise AdaptiveObservationError("invalid adaptive evaluation role")
        if isinstance(self.iteration_index, bool) or self.iteration_index < 0:
            raise AdaptiveObservationError("iteration_index must be nonnegative")
        for name in ("reconstruction_status", "structural_status", "readiness_status", "pf_status", "fidelity_status"):
            if not getattr(self, name):
                raise AdaptiveObservationError(f"{name} must be explicit")
        for rows, name in ((self.numeric_observation_ids, "numeric_observation_ids"),
                           (self.timing_observation_ids, "timing_observation_ids")):
            if tuple(sorted(set(rows))) != rows:
                raise AdaptiveObservationError(f"{name} must be unique and sorted")
            for item in rows:
                _require_digest(item, name)
        if self.pf_status == "NOT_RUN" and self.numeric_observation_ids:
            raise AdaptiveObservationError("PF NOT_RUN cannot link a numeric PF observation")

    def to_dict(self) -> dict[str, object]:
        """Return an anonymous controller-stage link row."""
        return {
            "evaluation_id": self.evaluation_id,
            "role": self.role,
            "iteration_index": self.iteration_index,
            "candidate_identity": self.candidate_identity,
            "build_attempt_identity": self.build_attempt_identity,
            "reconstruction_status": self.reconstruction_status,
            "structural_status": self.structural_status,
            "readiness_status": self.readiness_status,
            "pf_status": self.pf_status,
            "fidelity_status": self.fidelity_status,
            "numeric_observation_ids": list(self.numeric_observation_ids),
            "timing_observation_ids": list(self.timing_observation_ids),
        }


@dataclass(frozen=True)
class NumericObservationBundle:
    """Versioned companion payload with separate numeric and timing digests."""

    run_id: str
    numeric_observations: tuple[NumericFidelityObservation, ...]
    performance_timing_observations: tuple[PerformanceTimingObservation, ...]
    adaptive_evaluations: tuple[AdaptiveEvaluationObservation, ...]

    def __post_init__(self) -> None:
        """Require unique, stable observation IDs and explicit run identity."""
        if not self.run_id:
            raise AdaptiveObservationError("observation bundle run_id must be explicit")
        for rows, name, id_attribute in (
            (self.numeric_observations, "numeric", "observation_id"),
            (self.performance_timing_observations, "timing", "timing_id"),
            (self.adaptive_evaluations, "adaptive evaluation", "evaluation_id"),
        ):
            ids = [getattr(row, id_attribute) for row in rows]
            if len(ids) != len(set(ids)):
                raise AdaptiveObservationError(f"{name} observation IDs must be unique")

    def to_payload(self) -> dict[str, object]:
        """Serialize with timing separated from the semantic numeric digest."""
        numeric = [item.to_dict() for item in self.numeric_observations]
        timing = [item.to_dict() for item in self.performance_timing_observations]
        evaluations = [item.to_dict() for item in self.adaptive_evaluations]
        return {
            "schema": NUMERIC_OBSERVATION_SCHEMA,
            "run_id": self.run_id,
            "numeric_observations": numeric,
            "performance_timing_observations": timing,
            "adaptive_evaluations": evaluations,
            "numeric_observations_digest": _digest(numeric),
            "timing_observations_digest": _digest(timing),
            "adaptive_evaluations_digest": _digest(evaluations),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> NumericObservationBundle:
        """Restore and verify all companion payload digests."""
        expected = {
            "schema", "run_id", "numeric_observations", "performance_timing_observations",
            "adaptive_evaluations", "numeric_observations_digest", "timing_observations_digest",
            "adaptive_evaluations_digest",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected or payload.get("schema") != NUMERIC_OBSERVATION_SCHEMA:
            raise AdaptiveObservationError("numeric observation bundle schema/fields are invalid")
        arrays = (payload["numeric_observations"], payload["performance_timing_observations"], payload["adaptive_evaluations"])
        if any(not isinstance(rows, (list, tuple)) for rows in arrays):
            raise AdaptiveObservationError("numeric observation bundle arrays are invalid")
        numeric, timing, evaluations = arrays
        if _digest(numeric) != payload["numeric_observations_digest"]:
            raise AdaptiveObservationError("numeric observations digest differs")
        if _digest(timing) != payload["timing_observations_digest"]:
            raise AdaptiveObservationError("timing observations digest differs")
        if _digest(evaluations) != payload["adaptive_evaluations_digest"]:
            raise AdaptiveObservationError("adaptive evaluation digest differs")
        return cls(
            run_id=payload["run_id"],  # type: ignore[arg-type]
            numeric_observations=tuple(NumericFidelityObservation.from_dict(row) for row in numeric),  # type: ignore[arg-type]
            performance_timing_observations=tuple(PerformanceTimingObservation.from_dict(row) for row in timing),  # type: ignore[arg-type]
            adaptive_evaluations=tuple(_evaluation_from_dict(row) for row in evaluations),  # type: ignore[arg-type]
        )


@dataclass
class AdaptiveObservationRecorder:
    """Collect observation-only evidence for one runtime execution."""

    enabled: bool = True
    run_id: str = "pending"
    process_identity: str = field(default_factory=lambda: f"pid:{os.getpid()}")
    _numeric: list[NumericFidelityObservation] = field(default_factory=list, repr=False)
    _timings: list[PerformanceTimingObservation] = field(default_factory=list, repr=False)
    _evaluations: list[AdaptiveEvaluationObservation] = field(default_factory=list, repr=False)
    _sequence_by_kind: dict[str, int] = field(default_factory=dict, repr=False)

    def bind_run_id(self, run_id: str) -> None:
        """Bind events captured before the controller report existed."""
        if not run_id:
            raise AdaptiveObservationError("run_id must be explicit")
        self.run_id = run_id
        self._timings = [replace(row, run_id=run_id) for row in self._timings]

    def next_id(self, event_kind: str, role: str, candidate_identity: str | None) -> str:
        """Create a deterministic local event identifier without timing inputs."""
        sequence = self._sequence_by_kind.get(event_kind, 0) + 1
        self._sequence_by_kind[event_kind] = sequence
        return _digest({"schema": NUMERIC_OBSERVATION_SCHEMA, "run_id": self.run_id,
                        "sequence": sequence, "kind": event_kind, "role": role,
                        "candidate_identity": candidate_identity})

    def add_numeric(self, observation: NumericFidelityObservation) -> None:
        """Append one validated numeric observation when enabled."""
        if self.enabled:
            if any(row.observation_id == observation.observation_id for row in self._numeric):
                raise AdaptiveObservationError("duplicate numeric observation ID")
            self._numeric.append(observation)

    def classify_numeric(self, observation_id: str, role: str) -> None:
        """Assign a PF event its controller role after the controller knows it."""
        if not self.enabled:
            return
        if role not in _ROLES:
            raise AdaptiveObservationError("invalid observation role")
        for index, row in enumerate(self._numeric):
            if row.observation_id == observation_id:
                self._numeric[index] = replace(row, role=role)
                return
        raise AdaptiveObservationError("cannot classify an unknown numeric observation")

    def add_timing(
        self, *, operation_name: str, role: str, duration_seconds: float | None,
        timing_scope: str, backend: str, backend_version: str,
        candidate_identity: str | None = None, operation_status: str = "COMPLETED",
        availability_status: ObservationAvailability = ObservationAvailability.RECORDED,
    ) -> str | None:
        """Append one timing event; its ID excludes duration and semantics."""
        if not self.enabled:
            return None
        timing_id = self.next_id("timing", role, candidate_identity)
        self._timings.append(PerformanceTimingObservation(
            operation_name=operation_name, role=role, duration=duration_seconds,
            duration_unit="s", timing_scope=timing_scope, run_id=self.run_id,
            process_identity=self.process_identity, backend=backend,
            backend_version=backend_version, candidate_identity=candidate_identity,
            operation_status=operation_status, availability_status=availability_status,
            timing_id=timing_id,
        ))
        return timing_id

    def add_evaluation(self, evaluation: AdaptiveEvaluationObservation) -> None:
        """Append one controller-stage record when instrumentation is enabled."""
        if not self.enabled:
            return
        if any(row.evaluation_id == evaluation.evaluation_id for row in self._evaluations):
            raise AdaptiveObservationError("duplicate adaptive evaluation ID")
        self._evaluations.append(evaluation)

    def replace_evaluation(self, evaluation: AdaptiveEvaluationObservation) -> None:
        """Replace one controller-stage record without changing its identity."""
        if not self.enabled:
            return
        for index, row in enumerate(self._evaluations):
            if row.evaluation_id == evaluation.evaluation_id:
                self._evaluations[index] = evaluation
                return
        raise AdaptiveObservationError("cannot update an unknown adaptive evaluation")

    def numeric(self) -> tuple[NumericFidelityObservation, ...]:
        """Return observations in their deterministic execution order."""
        return tuple(self._numeric)

    def timings(self) -> tuple[PerformanceTimingObservation, ...]:
        """Return timing observations in their execution order."""
        return tuple(self._timings)

    def evaluations(self) -> tuple[AdaptiveEvaluationObservation, ...]:
        """Return controller-stage links in execution order."""
        return tuple(self._evaluations)

    def bundle(self) -> NumericObservationBundle:
        """Return the complete companion payload for the currently bound run."""
        return NumericObservationBundle(
            run_id=self.run_id,
            numeric_observations=self.numeric(),
            performance_timing_observations=self.timings(),
            adaptive_evaluations=self.evaluations(),
        )


def power_flow_execution_observation(
    result: PowerFlowExecutionResult,
    *,
    observation_id: str,
    role: str,
    source_model_digest: str,
    candidate_model_digest: str | None = None,
    candidate_identity: str | None = None,
    source_pf_status: str | None = None,
    source_sidecar_digest: str | None = None,
    candidate_sidecar_digest: str | None = None,
    solver_contract_digest: str | None = None,
    operating_point_digest: str | None = None,
    threshold_set_id: str | None = None,
    threshold_set_digest: str | None = None,
    solver_contract_summary: Mapping[str, object] | None = None,
) -> NumericFidelityObservation:
    """Project authoritative PF status, snapshot, iterations, and mismatch values."""
    if not isinstance(result, PowerFlowExecutionResult):
        raise AdaptiveObservationError("PF result must be PowerFlowExecutionResult")
    unsupported = result.status is PowerFlowExecutionStatus.PF_EXECUTION_UNSUPPORTED
    subnetwork_rows = tuple(SubnetworkIterationObservation(
        subnetwork_ordinal=row.subnetwork_ordinal,
        converged=row.converged,
        iterations=_availability_value(row.iterations, not_applicable=unsupported),
        final_mismatch=_availability_value(row.error, not_applicable=unsupported),
    ) for row in result.subnetwork_status)
    snapshot = _snapshot_value(result.snapshot)
    if role == "source":
        return NumericFidelityObservation(
            observation_id=observation_id, observation_kind="pf_execution", role="source",
            snapshot_identity=snapshot, pf_status=result.status.value,
            source_pf_status=result.status.value, candidate_pf_status="NOT_APPLICABLE",
            subnetwork_observations=subnetwork_rows, metric_observations=(),
            metric_availability=ObservationAvailability.NOT_APPLICABLE,
            metric_set_digest=None, resolved_threshold_digest=None,
            source_model_digest=source_model_digest, candidate_model_digest=None,
            candidate_identity=None, data_provenance="PyPSA AC executor result",
            source_sidecar_digest=source_sidecar_digest,
            solver_contract_digest=solver_contract_digest,
            operating_point_digest=operating_point_digest,
            threshold_set_id=threshold_set_id,
            threshold_set_digest=threshold_set_digest,
            failure_reason=_safe_failure_reason(result.failure_reason),
            solver_contract_summary=_solver_summary(solver_contract_summary or result.solver_metadata),
        )
    if role not in {"initial_candidate", "trial_candidate", "incumbent"}:
        raise AdaptiveObservationError("candidate PF result requires a controller role")
    return NumericFidelityObservation(
        observation_id=observation_id, observation_kind="pf_execution", role=role,
        snapshot_identity=snapshot, pf_status=result.status.value,
        source_pf_status=source_pf_status or "NOT_RECORDED",
        candidate_pf_status=result.status.value,
        subnetwork_observations=subnetwork_rows, metric_observations=(),
        metric_availability=ObservationAvailability.NOT_APPLICABLE,
        metric_set_digest=None, resolved_threshold_digest=None,
        source_model_digest=source_model_digest, candidate_model_digest=candidate_model_digest,
        candidate_identity=candidate_identity, data_provenance="PyPSA AC executor result",
        source_sidecar_digest=source_sidecar_digest,
        candidate_sidecar_digest=candidate_sidecar_digest,
        solver_contract_digest=solver_contract_digest,
        operating_point_digest=operating_point_digest,
        threshold_set_id=threshold_set_id,
        threshold_set_digest=threshold_set_digest,
        failure_reason=_safe_failure_reason(result.failure_reason),
        solver_contract_summary=_solver_summary(solver_contract_summary or result.solver_metadata),
    )


def fidelity_comparison_observation(
    metrics: PowerFlowMetricSet,
    thresholds: ResolvedPowerFlowThresholds,
    evaluation: PowerFlowThresholdEvaluation,
    *,
    observation_id: str,
    role: str,
    source_model_digest: str,
    candidate_model_digest: str,
    candidate_identity: str,
    snapshot: object | None,
    source_pf_status: str,
    candidate_pf_status: str,
    source_sidecar_digest: str | None = None,
    candidate_sidecar_digest: str | None = None,
    solver_contract_digest: str | None = None,
    operating_point_digest: str | None = None,
    threshold_set_id: str | None = None,
    threshold_set_digest: str | None = None,
) -> NumericFidelityObservation:
    """Project authoritative metric values, limits, evaluator results, and digests."""
    if not isinstance(metrics, PowerFlowMetricSet):
        raise AdaptiveObservationError("metrics must be the existing PowerFlowMetricSet")
    if not isinstance(thresholds, ResolvedPowerFlowThresholds):
        raise AdaptiveObservationError("thresholds must be the existing resolved threshold object")
    if not isinstance(evaluation, PowerFlowThresholdEvaluation):
        raise AdaptiveObservationError("evaluation must be the existing threshold evaluation")
    if evaluation.metrics_digest != metric_set_digest(metrics):
        raise AdaptiveObservationError("authoritative metric-set digest differs")
    if evaluation.threshold_digest != thresholds.digest:
        raise AdaptiveObservationError("authoritative threshold digest differs")
    rows = _metric_projection(metrics, thresholds.thresholds, evaluation)
    if role not in {"initial_candidate", "trial_candidate", "incumbent"}:
        raise AdaptiveObservationError("comparison requires a candidate role")
    return NumericFidelityObservation(
        observation_id=observation_id, observation_kind="fidelity_comparison", role=role,
        snapshot_identity=_snapshot_value(snapshot), pf_status="NOT_APPLICABLE",
        source_pf_status=source_pf_status, candidate_pf_status=candidate_pf_status,
        subnetwork_observations=(), metric_observations=rows,
        metric_availability=ObservationAvailability.RECORDED,
        metric_set_digest=evaluation.metrics_digest,
        resolved_threshold_digest=thresholds.digest,
        source_model_digest=source_model_digest,
        candidate_model_digest=candidate_model_digest,
        candidate_identity=candidate_identity,
        data_provenance="PR-022 metric evaluator and frozen threshold resolver",
        source_sidecar_digest=source_sidecar_digest,
        candidate_sidecar_digest=candidate_sidecar_digest,
        solver_contract_digest=solver_contract_digest,
        operating_point_digest=operating_point_digest,
        threshold_set_id=threshold_set_id or thresholds.threshold_set_id,
        threshold_set_digest=threshold_set_digest or thresholds.threshold_set_sha256,
    )


def numeric_digest(observations: tuple[NumericFidelityObservation, ...]) -> str:
    """Hash only canonical numeric observations, never timing values."""
    return _digest([row.to_dict() for row in observations])


def _metric_projection(
    metrics: PowerFlowMetricSet,
    thresholds: PowerFlowMetricThresholds,
    evaluation: PowerFlowThresholdEvaluation,
) -> tuple[MetricValueObservation, ...]:
    """Map existing evaluator inputs to report rows without reevaluating status."""
    summaries = {
        "vm_max_pu": (metrics.bus_voltage.max_absolute, metrics.bus_voltage.sample_count, "pu", "bus_voltage.max_absolute"),
        "vm_rmse_pu": (metrics.bus_voltage.rmse, metrics.bus_voltage.sample_count, "pu", "bus_voltage.rmse"),
        "vm_p95_pu": (metrics.bus_voltage.p95_absolute, metrics.bus_voltage.sample_count, "pu", "bus_voltage.p95_absolute"),
        "angle_rmse_deg": (metrics.angle.rmse, metrics.angle.sample_count, "deg", "angle.rmse"),
        "angle_p95_deg": (metrics.angle.p95_absolute, metrics.angle.sample_count, "deg", "angle.p95_absolute"),
        "angle_max_deg": (metrics.angle.max_absolute, metrics.angle.sample_count, "deg", "angle.max_absolute"),
        "branch_p_rmse_mw": (metrics.branch_p.rmse, metrics.branch_p.sample_count, "MW", "branch_p.rmse"),
        "branch_p_p95_mw": (metrics.branch_p.p95_absolute, metrics.branch_p.sample_count, "MW", "branch_p.p95_absolute"),
        "branch_p_max_mw": (metrics.branch_p.max_absolute, metrics.branch_p.sample_count, "MW", "branch_p.max_absolute"),
        "branch_q_rmse_mvar": (metrics.branch_q.rmse, metrics.branch_q.sample_count, "Mvar", "branch_q.rmse"),
        "branch_q_p95_mvar": (metrics.branch_q.p95_absolute, metrics.branch_q.sample_count, "Mvar", "branch_q.p95_absolute"),
        "branch_q_max_mvar": (metrics.branch_q.max_absolute, metrics.branch_q.sample_count, "Mvar", "branch_q.max_absolute"),
        "cutset_p_max_mw": (metrics.cutset_p.max_absolute, metrics.cutset_p.sample_count, "MW", "cutset_p.max_absolute"),
        "cutset_p_rmse_mw": (metrics.cutset_p.rmse, metrics.cutset_p.sample_count, "MW", "cutset_p.rmse"),
        "cutset_q_max_mvar": (metrics.cutset_q.max_absolute, metrics.cutset_q.sample_count, "Mvar", "cutset_q.max_absolute"),
        "cutset_q_rmse_mvar": (metrics.cutset_q.rmse, metrics.cutset_q.sample_count, "Mvar", "cutset_q.rmse"),
        "injection_p_rmse_mw": (metrics.cluster_injection.p_mw.rmse, metrics.cluster_injection.p_mw.sample_count, "MW", "cluster_injection.p_mw.rmse"),
        "injection_p_max_mw": (metrics.cluster_injection.p_mw.max_absolute, metrics.cluster_injection.p_mw.sample_count, "MW", "cluster_injection.p_mw.max_absolute"),
        "injection_q_rmse_mvar": (metrics.cluster_injection.q_mvar.rmse, metrics.cluster_injection.q_mvar.sample_count, "Mvar", "cluster_injection.q_mvar.rmse"),
        "injection_q_max_mvar": (metrics.cluster_injection.q_mvar.max_absolute, metrics.cluster_injection.q_mvar.sample_count, "Mvar", "cluster_injection.q_mvar.max_absolute"),
        "loss_abs_max_mw": (metrics.passive_branch_losses.passive_branch_active_abs_diff_mw, None, "MW", "passive_branch_losses.passive_branch_active_abs_diff_mw"),
        "loss_rel_max": (metrics.passive_branch_losses.passive_branch_active_relative_diff, None, "1", "passive_branch_losses.passive_branch_active_relative_diff"),
    }
    if set(summaries) != POWER_FLOW_HARD_THRESHOLD_FIELDS | POWER_FLOW_DIAGNOSTIC_THRESHOLD_FIELDS:
        raise AdaptiveObservationError("metric projection differs from existing evaluator fields")
    failed = set(evaluation.hard_failures) | set(evaluation.diagnostic_failures)
    rows: list[MetricValueObservation] = []
    for name in sorted(summaries):
        observed, count, unit, provenance = summaries[name]
        threshold = getattr(thresholds, name)
        observed_value = _availability_value(observed)
        sample_count = _availability_value(count, not_applicable=count is None)
        if observed is None:
            threshold_value = _availability_value(None)
            margin = _availability_value(None)
            status: Literal["PASS", "FAIL", "NOT_APPLICABLE", "NOT_RECORDED"] = "NOT_RECORDED"
        elif threshold is None:
            threshold_value = _availability_value(None, not_applicable=True)
            margin = _availability_value(None, not_applicable=True)
            status = "NOT_APPLICABLE"
        else:
            threshold_value = _availability_value(threshold)
            margin = _availability_value(float(threshold) - float(observed))
            status = "FAIL" if name in failed else "PASS"
        severity: Literal["HARD", "DIAGNOSTIC"] = "HARD" if name in POWER_FLOW_HARD_THRESHOLD_FIELDS else "DIAGNOSTIC"
        rows.append(MetricValueObservation(
            metric_name=name, severity=severity, observed_value=observed_value,
            unit=unit, sample_count=sample_count, resolved_threshold=threshold_value,
            margin=margin, status=status,
            metric_provenance=f"PowerFlowMetricSet.{provenance}",
        ))
    return tuple(rows)


def _solver_summary(values: Mapping[str, object]) -> dict[str, NumericObservationValue]:
    """Convert existing executor metadata into a typed, path-free summary."""
    summary: dict[str, NumericObservationValue] = {}
    for key, value in sorted(values.items()):
        if isinstance(value, bool):
            scalar: int | float | str | bool | None = value
        elif value is None or isinstance(value, (int, float, str)):
            scalar = value
        else:
            continue
        if isinstance(scalar, str):
            _reject_path_text(scalar, "solver contract summary")
        summary[key] = NumericObservationValue(
            scalar,
            ObservationAvailability.RECORDED if scalar is not None
            else ObservationAvailability.NOT_RECORDED,
        )
    return summary


def _availability_value(value: int | float | str | bool | None, *,
                        not_applicable: bool = False) -> NumericObservationValue:
    """Create a measured or explicit missing/not-applicable scalar."""
    if value is not None:
        return NumericObservationValue(value, ObservationAvailability.RECORDED)
    state = (ObservationAvailability.NOT_APPLICABLE if not_applicable
             else ObservationAvailability.NOT_RECORDED)
    return NumericObservationValue(None, state)


def _snapshot_value(snapshot: object | None) -> NumericObservationValue:
    """Canonicalize the public snapshot label without object repr addresses."""
    if snapshot is None:
        return _availability_value(None)
    value = snapshot.isoformat() if hasattr(snapshot, "isoformat") else str(snapshot)
    return NumericObservationValue(value, ObservationAvailability.RECORDED)


def _safe_failure_reason(reason: str | None) -> str | None:
    """Retain a path-free executor reason while avoiding raw exception details."""
    if reason is None:
        return None
    lowered = reason.lower()
    if any(token in lowered for token in ("/users/", "/private/", "/tmp/", "\\users\\", "\\private\\")):
        return "failure reason redacted because it contained a local path"
    return reason


def _evaluation_from_dict(payload: Mapping[str, object]) -> AdaptiveEvaluationObservation:
    """Restore a strict adaptive evaluation link row."""
    expected = {
        "evaluation_id", "role", "iteration_index", "candidate_identity", "build_attempt_identity",
        "reconstruction_status", "structural_status", "readiness_status", "pf_status",
        "fidelity_status", "numeric_observation_ids", "timing_observation_ids",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise AdaptiveObservationError("adaptive evaluation fields do not match schema")
    numeric_ids, timing_ids = payload["numeric_observation_ids"], payload["timing_observation_ids"]
    if not isinstance(numeric_ids, (list, tuple)) or not isinstance(timing_ids, (list, tuple)):
        raise AdaptiveObservationError("adaptive observation links must be arrays")
    return AdaptiveEvaluationObservation(
        evaluation_id=payload["evaluation_id"],  # type: ignore[arg-type]
        role=payload["role"],  # type: ignore[arg-type]
        iteration_index=payload["iteration_index"],  # type: ignore[arg-type]
        candidate_identity=payload["candidate_identity"],  # type: ignore[arg-type]
        build_attempt_identity=payload["build_attempt_identity"],  # type: ignore[arg-type]
        reconstruction_status=payload["reconstruction_status"],  # type: ignore[arg-type]
        structural_status=payload["structural_status"],  # type: ignore[arg-type]
        readiness_status=payload["readiness_status"],  # type: ignore[arg-type]
        pf_status=payload["pf_status"],  # type: ignore[arg-type]
        fidelity_status=payload["fidelity_status"],  # type: ignore[arg-type]
        numeric_observation_ids=tuple(numeric_ids),
        timing_observation_ids=tuple(timing_ids),
    )


def _reject_path_text(value: str, name: str) -> None:
    """Reject filesystem paths and obvious private checkout fragments."""
    lowered = value.lower()
    if any(token in lowered for token in ("/users/", "/private/", "/tmp/", "\\users\\", "\\private\\")):
        raise AdaptiveObservationError(f"{name} must not contain a filesystem path")


def _require_digest(value: str, name: str) -> None:
    """Require lowercase SHA-256 hex."""
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise AdaptiveObservationError(f"{name} must be a lowercase SHA-256 digest")


def _canonical_json(value: object) -> str:
    """Return canonical JSON without NaN/Infinity or field-order sensitivity."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AdaptiveObservationError(f"cannot canonicalize observation: {exc}") from exc


def _digest(value: object) -> str:
    """Hash a canonical JSON-compatible payload."""
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
