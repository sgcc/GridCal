"""Adaptive, deterministic expansion for synthetic reduction-fidelity runs."""

from psdcalc.reduction.adaptive.actions import (
    ActionContractError,
    ActionOperation,
    MappingTransition,
    ReductionAction,
)
from psdcalc.reduction.adaptive.attribution import (
    AttributionError,
    ErrorAttributionEngine,
    MetricFamily,
    MetricSupport,
    Stage3AttributionInput,
    Stage3AttributionResult,
)
from psdcalc.reduction.adaptive.contracts import (
    ADAPTIVE_CLAIM_SCOPE,
    ADAPTIVE_STRATEGY,
    AdaptiveContractError,
    AdaptiveReductionConfig,
    AdaptiveReductionIteration,
    AdaptiveReductionReport,
    AdaptiveReductionState,
    AdaptiveStrategy,
    AdaptiveStoppingReason,
    AttributionStatus,
    CandidateEvaluation,
    CompressionRequirement,
    ErrorAttribution,
    ErrorRegion,
    ExpansionCandidate,
    ExpansionDecision,
)
from psdcalc.reduction.adaptive.candidates import (
    ActionCatalogNotReady,
    BuiltCandidate,
    CandidateBuildIdentity,
    CandidateReconstructionError,
    PypsaAdaptiveCandidateBuilder,
)
from psdcalc.reduction.adaptive.readiness import (
    ActionCatalogReadinessResult,
    ActionCatalogReadinessValidator,
    ActionReadinessRejection,
)
from psdcalc.reduction.adaptive.journal import (
    ActionSetSnapshot,
    JournalIntegrityError,
    ReductionActionJournal,
)
from psdcalc.reduction.adaptive.controller import (
    AdaptiveCandidateSnapshot,
    AdaptiveControllerError,
    AdaptiveFidelityEvidence,
    AdaptiveReductionController,
    AdaptiveRuntime,
    RuntimePowerFlowOutcome,
)
from psdcalc.reduction.adaptive.pypsa_runtime import PypsaAdaptiveRuntime
from psdcalc.reduction.adaptive.evidence import (
    AdaptiveEvidenceError,
    AdaptiveEvidenceErrorCode,
    AdaptiveReductionEvidenceStore,
    AdaptiveReplayResult,
)

__all__ = [
    "ADAPTIVE_CLAIM_SCOPE", "ADAPTIVE_STRATEGY", "ActionContractError",
    "AttributionError", "ErrorAttributionEngine", "MetricFamily", "MetricSupport",
    "ActionOperation", "ActionSetSnapshot", "ActionCatalogNotReady",
    "ActionCatalogReadinessResult", "ActionCatalogReadinessValidator",
    "ActionReadinessRejection", "AdaptiveContractError",
    "AdaptiveReductionConfig", "AdaptiveReductionIteration", "AdaptiveReductionReport",
    "AdaptiveReductionState", "AdaptiveStoppingReason", "AdaptiveStrategy", "AttributionStatus",
    "BuiltCandidate", "CandidateBuildIdentity", "CandidateEvaluation",
    "CandidateReconstructionError", "CompressionRequirement",
    "ErrorAttribution", "ErrorRegion",
    "ExpansionCandidate", "ExpansionDecision", "JournalIntegrityError", "MappingTransition",
    "ReductionAction", "ReductionActionJournal", "PypsaAdaptiveCandidateBuilder",
    "Stage3AttributionInput", "Stage3AttributionResult",
    "AdaptiveCandidateSnapshot", "AdaptiveControllerError", "AdaptiveFidelityEvidence",
    "AdaptiveReductionController", "AdaptiveRuntime", "RuntimePowerFlowOutcome",
    "PypsaAdaptiveRuntime",
    "AdaptiveEvidenceError", "AdaptiveEvidenceErrorCode",
    "AdaptiveReductionEvidenceStore", "AdaptiveReplayResult",
]
