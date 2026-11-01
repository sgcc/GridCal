"""Backend-neutral immutable contracts for adaptive reduction runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import math
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


class AttributionStatus(StrEnum):
    """Whether a fidelity violation maps to legal local expansion actions."""

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

    @classmethod
    def create(cls, *, region_type: str, source_bus_ids: tuple[StableId, ...],
               action_ids: tuple[str, ...], voltage_layer: str | None,
               component_id: str | None) -> "ErrorRegion":
        """Create a region with canonical ordering and a content fingerprint."""
        if not region_type:
            raise AdaptiveContractError("region_type must be non-empty")
        buses = tuple(sorted(set(source_bus_ids), key=StableId.encode))
        actions = tuple(sorted(set(action_ids)))
        payload = {"schema_version": SCHEMA_VERSION, "region_type": region_type,
                   "source_bus_ids": [item.encode() for item in buses],
                   "action_ids": actions, "voltage_layer": voltage_layer,
                   "component_id": component_id}
        return cls(region_type, buses, actions, voltage_layer, component_id, _digest(payload))


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
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.metric_name or self.severity not in {"HARD", "DIAGNOSTIC"}:
            raise AdaptiveContractError("attribution metric and severity are invalid")
        for name in ("observed_value", "resolved_threshold", "normalized_violation"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise AdaptiveContractError(f"{name} must be finite")
        if self.normalized_violation < 0:
            raise AdaptiveContractError("normalized_violation cannot be negative")
        if self.status is AttributionStatus.ATTRIBUTABLE and not self.candidate_action_ids:
            raise AdaptiveContractError("attributable violations must identify legal actions")
        if self.status is AttributionStatus.UNATTRIBUTABLE and not self.reason:
            raise AdaptiveContractError("unattributable violations need a reason")
        if tuple(sorted(set(self.candidate_action_ids))) != self.candidate_action_ids:
            raise AdaptiveContractError("candidate_action_ids must be unique and sorted")


@dataclass(frozen=True)
class ExpansionCandidate:
    """A legal dependency-closed action deactivation and its deterministic cost."""

    candidate_fingerprint: str
    region_fingerprint: str
    deactivated_action_ids: tuple[str, ...]
    resulting_active_action_ids: tuple[str, ...]
    added_bus_count: int
    added_branch_count: int
    expansion_cost: float

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
        if not math.isfinite(self.expansion_cost) or self.expansion_cost < 0:
            raise AdaptiveContractError("expansion_cost must be finite and non-negative")


@dataclass(frozen=True)
class CandidateEvaluation:
    """Per-candidate result preserving all prerequisite and scoring evidence."""

    candidate: ExpansionCandidate
    structural_status: str
    readiness_status: str
    pf_status: str
    fidelity_status: str
    model_digest: str
    mapping_digest: str
    rejection_reason: str | None = None
    normalized_hard_violation: float | None = None
    score: float | None = None

    def __post_init__(self) -> None:
        for name in ("model_digest", "mapping_digest"):
            _require_digest(getattr(self, name), name)
        for name in ("structural_status", "readiness_status", "pf_status", "fidelity_status"):
            if not getattr(self, name):
                raise AdaptiveContractError(f"{name} must be explicit, including NOT_RUN")
        for name in ("normalized_hard_violation", "score"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise AdaptiveContractError(f"{name} must be finite and non-negative")


@dataclass(frozen=True)
class ExpansionDecision:
    """Deterministic selection and complete ordered evaluation set."""

    strategy: str
    candidate_evaluations: tuple[CandidateEvaluation, ...]
    selected_candidate_fingerprint: str | None
    tie_break_trace: tuple[str, ...]
    reason: str

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
    model_digest: str
    mapping_digest: str
    structural_status: str
    readiness_status: str
    pf_status: str
    fidelity_status: str
    hard_violations: tuple[ErrorAttribution, ...] = ()
    expansion_decision: ExpansionDecision | None = None
    stopping_reason: AdaptiveStoppingReason | None = None
    diagnostics: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.iteration_index, bool) or self.iteration_index < 0:
            raise AdaptiveContractError("iteration_index must be a non-negative integer")
        for name in ("action_set_fingerprint", "model_digest", "mapping_digest"):
            _require_digest(getattr(self, name), name)
        for name in ("structural_status", "readiness_status", "pf_status", "fidelity_status"):
            if not getattr(self, name):
                raise AdaptiveContractError(f"{name} must be explicit, including NOT_RUN")


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

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION or self.claim_scope != ADAPTIVE_CLAIM_SCOPE:
            raise AdaptiveContractError("adaptive report schema or claim scope is unsupported")
        if not self.run_id or not self.evidence_reference:
            raise AdaptiveContractError("run_id and evidence_reference must be non-empty")
        for name in ("config_digest", "s1_model_digest", "s1_sidecar_digest",
                     "action_catalog_digest", "journal_digest"):
            _require_digest(getattr(self, name), name)
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
