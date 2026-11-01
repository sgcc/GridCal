"""Backend-neutral immutable contracts for adaptive reduction runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import math
from types import MappingProxyType
from typing import Mapping

from psdcalc.reduction.core.ids import StableId


SCHEMA_VERSION = "1.0"
ADAPTIVE_CLAIM_SCOPE = "REDUCTION_FIDELITY"
ADAPTIVE_STRATEGY = "HIERARCHICALLY_CONSTRAINED_GREEDY_LOCAL_EXPANSION"


class AdaptiveContractError(ValueError):
    """Raised when an adaptive-reduction contract is invalid or inconsistent."""


class AdaptiveStoppingReason(StrEnum):
    """Mutually exclusive terminal results for one adaptive run."""

    FIDELITY_SATISFIED = "FIDELITY_SATISFIED"
    FIDELITY_SATISFIED_BUT_COMPRESSION_BUDGET_NOT_MET = "FIDELITY_SATISFIED_BUT_COMPRESSION_BUDGET_NOT_MET"
    NO_EXPANSION_CANDIDATE = "NO_EXPANSION_CANDIDATE"
    NO_VIABLE_EXPANSION_CANDIDATE = "NO_VIABLE_EXPANSION_CANDIDATE"
    NO_FURTHER_COMPRESSION_BENEFIT = "NO_FURTHER_COMPRESSION_BENEFIT"
    UNATTRIBUTABLE_HARD_VIOLATION = "UNATTRIBUTABLE_HARD_VIOLATION"
    STRUCTURAL_INVALID = "STRUCTURAL_INVALID"
    AC_MODEL_NOT_READY = "AC_MODEL_NOT_READY"
    PF_NOT_CONVERGED = "PF_NOT_CONVERGED"
    MAX_ITERATIONS_REACHED = "MAX_ITERATIONS_REACHED"
    CONFIGURATION_INVALID = "CONFIGURATION_INVALID"
    PF_EXECUTION_UNSUPPORTED = "PF_EXECUTION_UNSUPPORTED"
    PF_EXECUTION_FAILED = "PF_EXECUTION_FAILED"
    PF_MAPPING_INVALID = "PF_MAPPING_INVALID"
    REPEATED_ACTION_SET = "REPEATED_ACTION_SET"


class AttributionStatus(StrEnum):
    """Whether provenance supports a deterministic reduction relationship."""

    ATTRIBUTABLE = "ATTRIBUTABLE"
    UNATTRIBUTABLE = "UNATTRIBUTABLE"


class AdaptiveStrategy(StrEnum):
    """Deterministic selection strategy frozen for the first adaptive release."""

    HIERARCHICALLY_CONSTRAINED_GREEDY_LOCAL_EXPANSION = ADAPTIVE_STRATEGY


@dataclass(frozen=True)
class CompressionRequirement:
    """Optional size limits, independent of PF fidelity thresholds."""

    max_bus_count: int | None = None
    min_bus_compression_fraction: float | None = None
    max_branch_count: int | None = None
    min_branch_compression_fraction: float | None = None

    def __post_init__(self) -> None:
        for name in ("max_bus_count", "max_branch_count"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise AdaptiveContractError(f"{name} must be a non-negative integer or None")
        for name in ("min_bus_compression_fraction", "min_branch_compression_fraction"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(float(value)) or not 0 <= value <= 1):
                raise AdaptiveContractError(f"{name} must be finite and in [0, 1]")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible frozen requirement payload."""
        return {name: getattr(self, name) for name in (
            "max_bus_count", "min_bus_compression_fraction", "max_branch_count",
            "min_branch_compression_fraction")}


@dataclass(frozen=True)
class AdaptiveReductionConfig:
    """Bind frozen reduction, operating-point, solver, metric and threshold inputs."""

    schema_version: str
    reduction_config_digest: str
    operating_point_digest: str
    solver_contract_digest: str
    metric_policy_digest: str
    threshold_set_id: str
    threshold_digest: str
    max_iterations: int
    cost_bus_weight: float
    cost_branch_weight: float
    strategy: AdaptiveStrategy = AdaptiveStrategy.HIERARCHICALLY_CONSTRAINED_GREEDY_LOCAL_EXPANSION
    compression_requirement: CompressionRequirement | None = None
    improvement_tolerance: float = 1e-12

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise AdaptiveContractError("unsupported adaptive schema_version")
        for name in ("reduction_config_digest", "operating_point_digest", "solver_contract_digest",
                     "metric_policy_digest", "threshold_digest"):
            _require_digest(getattr(self, name), name)
        if not self.threshold_set_id.strip():
            raise AdaptiveContractError("threshold_set_id must be non-empty")
        if self.max_iterations < 1 or isinstance(self.max_iterations, bool):
            raise AdaptiveContractError("max_iterations must be a positive integer")
        for name in ("cost_bus_weight", "cost_branch_weight"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(float(value)) or value <= 0:
                raise AdaptiveContractError(f"{name} must be finite and positive")
        tolerance = self.improvement_tolerance
        if (isinstance(tolerance, bool) or not isinstance(tolerance, (float, int))
                or not math.isfinite(float(tolerance)) or tolerance < 0.0):
            raise AdaptiveContractError("improvement_tolerance must be finite and non-negative")
        object.__setattr__(self, "improvement_tolerance", float(tolerance))
        if self.strategy is not AdaptiveStrategy.HIERARCHICALLY_CONSTRAINED_GREEDY_LOCAL_EXPANSION:
            raise AdaptiveContractError("unsupported adaptive strategy")

    @property
    def digest(self) -> str:
        """Return the canonical digest of all immutable adaptive settings."""
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible config payload."""
        return {
            "schema_version": self.schema_version,
            "reduction_config_digest": self.reduction_config_digest,
            "operating_point_digest": self.operating_point_digest,
            "solver_contract_digest": self.solver_contract_digest,
            "metric_policy_digest": self.metric_policy_digest,
            "threshold_set_id": self.threshold_set_id,
            "threshold_digest": self.threshold_digest,
            "max_iterations": self.max_iterations,
            "cost_bus_weight": float(self.cost_bus_weight),
            "cost_branch_weight": float(self.cost_branch_weight),
            "strategy": self.strategy.value,
            "compression_requirement": (self.compression_requirement.to_dict()
                                         if self.compression_requirement else None),
            "improvement_tolerance": float(self.improvement_tolerance),
        }


@dataclass(frozen=True)
class AdaptiveReductionState:
    """Immutable controller state; network objects are held by the runtime only."""

    iteration_index: int
    active_action_ids: tuple[str, ...]
    action_set_fingerprint: str
    incumbent_candidate_fingerprint: str | None = None
    visited_action_set_fingerprints: tuple[str, ...] = ()
    terminal_reason: AdaptiveStoppingReason | None = None

    def __post_init__(self) -> None:
        if isinstance(self.iteration_index, bool) or self.iteration_index < 0:
            raise AdaptiveContractError("iteration_index must be a non-negative integer")
        if tuple(sorted(set(self.active_action_ids))) != self.active_action_ids:
            raise AdaptiveContractError("active_action_ids must be unique and sorted")
        _require_digest(self.action_set_fingerprint, "action_set_fingerprint")
        for fingerprint in self.visited_action_set_fingerprints:
            _require_digest(fingerprint, "visited_action_set_fingerprint")


@dataclass(frozen=True)
class ErrorRegion:
    """Deterministic identity/topology grouping used for violation attribution."""

    region_type: str
    source_bus_ids: tuple[StableId, ...]
    action_ids: tuple[str, ...]
    voltage_layer: str | None
    component_id: str | None
    fingerprint: str
    source_branch_ids: tuple[StableId, ...] = ()
    target_bus_ids: tuple[StableId, ...] = ()
    target_branch_ids: tuple[StableId, ...] = ()

    @classmethod
    def create(cls, *, region_type: str, source_bus_ids: tuple[StableId, ...],
               action_ids: tuple[str, ...], voltage_layer: str | None,
               component_id: str | None,
               source_branch_ids: tuple[StableId, ...] = (),
               target_bus_ids: tuple[StableId, ...] = (),
               target_branch_ids: tuple[StableId, ...] = ()) -> "ErrorRegion":
        """Create a region with canonical ordering and a content fingerprint."""
        if not region_type:
            raise AdaptiveContractError("region_type must be non-empty")
        buses = tuple(sorted(set(source_bus_ids), key=StableId.encode))
        branches = tuple(sorted(set(source_branch_ids), key=StableId.encode))
        target_buses = tuple(sorted(set(target_bus_ids), key=StableId.encode))
        target_branches = tuple(sorted(set(target_branch_ids), key=StableId.encode))
        actions = tuple(sorted(set(action_ids)))
        payload = {"schema_version": SCHEMA_VERSION, "region_type": region_type,
                   "source_bus_ids": [item.encode() for item in buses],
                   "source_branch_ids": [item.encode() for item in branches],
                   "target_bus_ids": [item.encode() for item in target_buses],
                   "target_branch_ids": [item.encode() for item in target_branches],
                   "action_ids": actions, "voltage_layer": voltage_layer,
                   "component_id": component_id}
        return cls(region_type, buses, actions, voltage_layer, component_id,
                   _digest(payload), branches, target_buses, target_branches)


@dataclass(frozen=True)
class ErrorAttribution:
    """One existing PR-022 fidelity violation linked to an explicit local region."""

    metric_name: str
    severity: str
    observed_value: float
    resolved_threshold: float
    normalized_violation: float
    status: AttributionStatus
    source_ids: tuple[StableId, ...] = ()
    target_ids: tuple[StableId, ...] = ()
    region_fingerprints: tuple[str, ...] = ()
    candidate_action_ids: tuple[str, ...] = ()
    related_action_ids: tuple[str, ...] = ()
    reason: str | None = None
    violation_magnitude: float = 0.0
    threshold_digest: str | None = None
    source_comparison_identity: str = ""
    target_comparison_identity: str = ""

    def __post_init__(self) -> None:
        if not self.metric_name or self.severity not in {"HARD", "DIAGNOSTIC"}:
            raise AdaptiveContractError("attribution metric and severity are invalid")
        for name in ("observed_value", "resolved_threshold", "normalized_violation",
                     "violation_magnitude"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise AdaptiveContractError(f"{name} must be finite")
        if self.normalized_violation < 0:
            raise AdaptiveContractError("normalized_violation cannot be negative")
        if self.status is AttributionStatus.ATTRIBUTABLE and not self.related_action_ids:
            raise AdaptiveContractError("attributable violations must identify supported related actions")
        if self.status is AttributionStatus.UNATTRIBUTABLE and not self.reason:
            raise AdaptiveContractError("unattributable violations need a reason")
        if tuple(sorted(set(self.candidate_action_ids))) != self.candidate_action_ids:
            raise AdaptiveContractError("candidate_action_ids must be unique and sorted")
        if tuple(sorted(set(self.related_action_ids))) != self.related_action_ids:
            raise AdaptiveContractError("related_action_ids must be unique and sorted")
        if not set(self.candidate_action_ids) <= set(self.related_action_ids):
            raise AdaptiveContractError("candidate_action_ids must be a subset of related_action_ids")
        if tuple(sorted(set(self.region_fingerprints))) != self.region_fingerprints:
            raise AdaptiveContractError("region_fingerprints must be unique and sorted")
        if self.threshold_digest is not None:
            _require_digest(self.threshold_digest, "threshold_digest")
        for name in ("source_comparison_identity", "target_comparison_identity"):
            if getattr(self, name):
                _require_digest(getattr(self, name), name)
        if self.status is AttributionStatus.UNATTRIBUTABLE and (
                self.candidate_action_ids or self.related_action_ids):
            raise AdaptiveContractError("unattributable violations cannot name supported related actions")

    @property
    def fingerprint(self) -> str:
        """Return the canonical attribution fingerprint, excluding runtime state."""
        return _digest({
            "schema_version": SCHEMA_VERSION,
            "metric_name": self.metric_name,
            "severity": self.severity,
            "observed_value": float(self.observed_value),
            "resolved_threshold": float(self.resolved_threshold),
            "violation_magnitude": float(self.violation_magnitude),
            "normalized_violation": float(self.normalized_violation),
            "threshold_digest": self.threshold_digest,
            "status": self.status.value,
            "source_ids": [item.encode() for item in sorted(self.source_ids, key=StableId.encode)],
            "target_ids": [item.encode() for item in sorted(self.target_ids, key=StableId.encode)],
            "region_fingerprints": self.region_fingerprints,
            "candidate_action_ids": self.candidate_action_ids,
            "related_action_ids": self.related_action_ids,
            "reason": self.reason,
            "source_comparison_identity": self.source_comparison_identity,
            "target_comparison_identity": self.target_comparison_identity,
        })


@dataclass(frozen=True)
class ExpansionCandidate:
    """A legal dependency-closed action deactivation and its deterministic cost."""

    candidate_fingerprint: str
    region_fingerprint: str
    deactivated_action_ids: tuple[str, ...]
    resulting_active_action_ids: tuple[str, ...]
    added_bus_count: int
    added_branch_count: int
    expansion_cost: float | None = None
    triggering_attribution_ids: tuple[str, ...] = ()
    direct_action_ids: tuple[str, ...] = ()
    dependency_closure_action_ids: tuple[str, ...] = ()
    region_fingerprints: tuple[str, ...] = ()
    eligible: bool = True
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        _require_digest(self.candidate_fingerprint, "candidate_fingerprint")
        _require_digest(self.region_fingerprint, "region_fingerprint")
        if tuple(sorted(set(self.deactivated_action_ids))) != self.deactivated_action_ids:
            raise AdaptiveContractError("deactivated_action_ids must be unique and sorted")
        if tuple(sorted(set(self.resulting_active_action_ids))) != self.resulting_active_action_ids:
            raise AdaptiveContractError("resulting_active_action_ids must be unique and sorted")
        for name in ("added_bus_count", "added_branch_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise AdaptiveContractError(f"{name} must be a non-negative integer")
        for name in ("triggering_attribution_ids", "direct_action_ids",
                     "dependency_closure_action_ids", "region_fingerprints"):
            values = getattr(self, name)
            if tuple(sorted(set(values))) != values:
                raise AdaptiveContractError(f"{name} must be unique and sorted")
        if self.expansion_cost is not None and (
                not math.isfinite(self.expansion_cost) or self.expansion_cost < 0):
            raise AdaptiveContractError("expansion_cost must be finite and non-negative or None")
        if not isinstance(self.eligible, bool):
            raise AdaptiveContractError("eligible must be a bool")
        if self.eligible and self.rejection_reason is not None:
            raise AdaptiveContractError("eligible candidates cannot have rejection_reason")
        if not self.eligible and not self.rejection_reason:
            raise AdaptiveContractError("ineligible candidates need rejection_reason")
        expected = _digest({
            "schema_version": SCHEMA_VERSION,
            "deactivated_action_ids": self.deactivated_action_ids,
        })
        if self.candidate_fingerprint != expected:
            raise AdaptiveContractError("candidate fingerprint must bind the canonical deactivation set")


@dataclass(frozen=True)
class CandidateEvaluation:
    """Per-candidate result preserving all prerequisite and scoring evidence."""

    candidate: ExpansionCandidate
    structural_status: str
    readiness_status: str
    pf_status: str
    fidelity_status: str
    model_digest: str | None
    mapping_digest: str | None
    rejection_reason: str | None = None
    normalized_hard_violation: float | None = None
    score: float | None = None
    proposed_active_action_set_fingerprint: str | None = None
    candidate_build_fingerprint: str | None = None
    structural_checks: Mapping[str, bool] = field(default_factory=dict)
    readiness_checks: Mapping[str, bool] = field(default_factory=dict)
    hard_violations: tuple[ErrorAttribution, ...] = ()
    diagnostic_failures: tuple[str, ...] = ()
    hard_violation_improvement: float | None = None
    normalized_improvement: float | None = None
    expansion_cost: float | None = None
    added_bus_count: int | None = None
    added_branch_count: int | None = None
    metrics_digest: str | None = None
    threshold_digest: str | None = None
    attribution_digest: str | None = None
    attributed_region_fingerprints: tuple[str, ...] = ()
    remaining_candidate_fingerprints: tuple[str, ...] = ()
    rejected_candidate_fingerprints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("model_digest", "mapping_digest", "proposed_active_action_set_fingerprint",
                     "candidate_build_fingerprint", "metrics_digest", "threshold_digest",
                     "attribution_digest"):
            value = getattr(self, name)
            if value is not None:
                _require_digest(value, name)
        for name in ("structural_status", "readiness_status", "pf_status", "fidelity_status"):
            if not getattr(self, name):
                raise AdaptiveContractError(f"{name} must be explicit, including NOT_RUN")
        for name in ("normalized_hard_violation", "score", "expansion_cost"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise AdaptiveContractError(f"{name} must be finite and non-negative")
        for name in ("hard_violation_improvement", "normalized_improvement"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise AdaptiveContractError(f"{name} must be finite")
        for name in ("added_bus_count", "added_branch_count"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise AdaptiveContractError(f"{name} must be a non-negative integer or None")
        for name in ("structural_checks", "readiness_checks"):
            checks = getattr(self, name)
            if not isinstance(checks, Mapping) or any(not isinstance(value, bool) for value in checks.values()):
                raise AdaptiveContractError(f"{name} must map check names to bool values")
            object.__setattr__(self, name, MappingProxyType(dict(sorted(checks.items()))))
        if tuple(sorted(self.diagnostic_failures)) != self.diagnostic_failures:
            raise AdaptiveContractError("diagnostic_failures must be unique and sorted")
        for name in ("attributed_region_fingerprints", "remaining_candidate_fingerprints",
                     "rejected_candidate_fingerprints"):
            values = getattr(self, name)
            if tuple(sorted(set(values))) != values:
                raise AdaptiveContractError(f"{name} must be unique and sorted")
            for value in values:
                _require_digest(value, name)


@dataclass(frozen=True)
class ExpansionDecision:
    """Deterministic selection and complete ordered evaluation set."""

    strategy: str
    candidate_evaluations: tuple[CandidateEvaluation, ...]
    selected_candidate_fingerprint: str | None
    tie_break_trace: tuple[str, ...]
    reason: str
    rejected_candidates: tuple[ExpansionCandidate, ...] = ()

    def __post_init__(self) -> None:
        if self.strategy != ADAPTIVE_STRATEGY or not self.reason:
            raise AdaptiveContractError("decision strategy or reason is invalid")
        fingerprints = tuple(item.candidate.candidate_fingerprint for item in self.candidate_evaluations)
        if tuple(sorted(fingerprints)) != fingerprints or len(set(fingerprints)) != len(fingerprints):
            raise AdaptiveContractError("candidate evaluations must be unique and fingerprint-sorted")
        if self.selected_candidate_fingerprint is not None and self.selected_candidate_fingerprint not in fingerprints:
            raise AdaptiveContractError("selected candidate must occur in evaluations")


@dataclass(frozen=True)
class AdaptiveReductionIteration:
    """Immutable record of one current-candidate evaluation and expansion decision."""

    iteration_index: int
    action_set_fingerprint: str
    model_digest: str | None
    mapping_digest: str | None
    structural_status: str
    readiness_status: str
    pf_status: str
    fidelity_status: str
    hard_violations: tuple[ErrorAttribution, ...] = ()
    expansion_decision: ExpansionDecision | None = None
    stopping_reason: AdaptiveStoppingReason | None = None
    diagnostics: Mapping[str, str] = field(default_factory=dict)
    diagnostic_failures: tuple[str, ...] = ()
    candidate_fingerprint: str | None = None
    parent_incumbent_fingerprint: str | None = None
    new_incumbent_fingerprint: str | None = None
    action_set_delta: tuple[str, ...] = ()
    candidate_build_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.iteration_index, bool) or self.iteration_index < 0:
            raise AdaptiveContractError("iteration_index must be a non-negative integer")
        for name in ("action_set_fingerprint", "model_digest", "mapping_digest",
                     "candidate_fingerprint", "parent_incumbent_fingerprint",
                     "new_incumbent_fingerprint", "candidate_build_fingerprint"):
            value = getattr(self, name)
            if value is not None:
                _require_digest(value, name)
        for name in ("structural_status", "readiness_status", "pf_status", "fidelity_status"):
            if not getattr(self, name):
                raise AdaptiveContractError(f"{name} must be explicit, including NOT_RUN")
        if tuple(sorted(self.diagnostic_failures)) != self.diagnostic_failures:
            raise AdaptiveContractError("diagnostic_failures must be unique and sorted")
        if tuple(sorted(set(self.action_set_delta))) != self.action_set_delta:
            raise AdaptiveContractError("action_set_delta must be unique and sorted")


@dataclass(frozen=True)
class AdaptiveReductionReport:
    """Serializable run-level summary confined to REDUCTION_FIDELITY."""

    schema_version: str
    run_id: str
    config_digest: str
    s1_model_digest: str
    s1_sidecar_digest: str
    action_catalog_digest: str
    journal_digest: str
    terminal_reason: AdaptiveStoppingReason
    iterations: tuple[AdaptiveReductionIteration, ...]
    evidence_reference: str
    claim_scope: str = ADAPTIVE_CLAIM_SCOPE
    initial_action_set_fingerprint: str | None = None
    initial_candidate_fingerprint: str | None = None
    selected_candidate_sequence: tuple[str, ...] = ()
    final_incumbent_summary: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.claim_scope != ADAPTIVE_CLAIM_SCOPE:
            raise AdaptiveContractError("adaptive report schema or claim scope is unsupported")
        if not self.run_id or not self.evidence_reference:
            raise AdaptiveContractError("run_id and evidence_reference must be non-empty")
        for name in ("config_digest", "s1_model_digest", "s1_sidecar_digest",
                     "action_catalog_digest", "journal_digest"):
            _require_digest(getattr(self, name), name)
        for name in ("initial_action_set_fingerprint", "initial_candidate_fingerprint"):
            value = getattr(self, name)
            if value is not None:
                _require_digest(value, name)
        if any(not isinstance(item, str) or len(item) != 64 for item in self.selected_candidate_sequence):
            raise AdaptiveContractError("selected_candidate_sequence must contain SHA-256 fingerprints")
        if not isinstance(self.final_incumbent_summary, Mapping):
            raise AdaptiveContractError("final_incumbent_summary must be a mapping")
        object.__setattr__(self, "final_incumbent_summary", MappingProxyType(
            {str(key): self.final_incumbent_summary[key] for key in sorted(self.final_incumbent_summary)}))
        if tuple(item.iteration_index for item in self.iterations) != tuple(range(len(self.iterations))):
            raise AdaptiveContractError("iteration indexes must be contiguous and start at zero")

    def to_dict(self) -> dict[str, object]:
        """Return deterministic JSON-compatible report data without runtime objects."""
        return _canonicalize(self)


def _require_digest(value: str, field_name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise AdaptiveContractError(f"{field_name} must be a lowercase SHA-256 hex digest")


def _canonicalize(value: object) -> object:
    if isinstance(value, StableId):
        return value.encode()
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {name: _canonicalize(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_canonicalize(item) for item in value]
    return value


def _digest(payload: object) -> str:
    encoded = json.dumps(_canonicalize(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
