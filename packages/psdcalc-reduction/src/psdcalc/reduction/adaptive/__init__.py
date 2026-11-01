"""Adaptive, deterministic expansion for synthetic reduction-fidelity runs."""

from psdcalc.reduction.adaptive.actions import (
    ActionContractError,
    ActionOperation,
    MappingTransition,
    ReductionAction,
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
    BuiltCandidate,
    CandidateBuildIdentity,
    CandidateReconstructionError,
    PypsaAdaptiveCandidateBuilder,
)
from psdcalc.reduction.adaptive.journal import (
    ActionSetSnapshot,
    JournalIntegrityError,
    ReductionActionJournal,
)

__all__ = [
    "ADAPTIVE_CLAIM_SCOPE", "ADAPTIVE_STRATEGY", "ActionContractError",
    "ActionOperation", "ActionSetSnapshot", "AdaptiveContractError",
    "AdaptiveReductionConfig", "AdaptiveReductionIteration", "AdaptiveReductionReport",
    "AdaptiveReductionState", "AdaptiveStoppingReason", "AdaptiveStrategy", "AttributionStatus",
    "BuiltCandidate", "CandidateBuildIdentity", "CandidateEvaluation",
    "CandidateReconstructionError", "CompressionRequirement",
    "ErrorAttribution", "ErrorRegion",
    "ExpansionCandidate", "ExpansionDecision", "JournalIntegrityError", "MappingTransition",
    "ReductionAction", "ReductionActionJournal", "PypsaAdaptiveCandidateBuilder",
]
