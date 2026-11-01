"""Orchestrate existing structural and AC-readiness validation for candidates."""

from __future__ import annotations

from dataclasses import dataclass

import pypsa

from psdcalc.reduction.core.ids import IdentitySidecar
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.readiness.ac_model import AcModelReadinessResult, AcModelReadinessValidator
from psdcalc.reduction.validation.multi_voltage import PreliminaryStructuralEvidence


@dataclass(frozen=True)
class CandidateValidationResult:
    """Separate structural precheck and AC readiness outcomes for one build."""

    structural: PreliminaryStructuralEvidence
    ac_readiness: AcModelReadinessResult


class CandidateValidationOrchestrator:
    """Reuse the materializer's Structural Gate and the public AC readiness Gate."""

    def validate(
        self,
        *,
        source_network: pypsa.Network,
        source_sidecar: IdentitySidecar,
        candidate_network: pypsa.Network,
        candidate_sidecar: IdentitySidecar,
        mappings: MappingBundle,
        structural: PreliminaryStructuralEvidence,
    ) -> CandidateValidationResult:
        """Return existing Gate outputs without duplicating validation rules."""
        readiness = AcModelReadinessValidator().validate(
            source_network,
            source_sidecar,
            candidate_network,
            candidate_sidecar,
            mappings,
        )
        return CandidateValidationResult(structural, readiness)
