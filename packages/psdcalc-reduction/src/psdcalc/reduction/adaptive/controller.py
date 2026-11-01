"""Deterministic in-memory adaptive reduction controller.

The controller owns state transitions, stopping semantics, scoring, and tie
breaking. A backend runtime rebuilds each candidate from frozen S1 and calls
the existing structural/readiness, PF, fidelity, and Stage-3 layers.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence

from psdcalc.reduction.adaptive.actions import ReductionAction
from psdcalc.reduction.adaptive.contracts import (
    AdaptiveReductionConfig,
    AdaptiveReductionIteration,
    AdaptiveReductionReport,
    AdaptiveStoppingReason,
    CandidateEvaluation,
    ExpansionCandidate,
    ExpansionDecision,
    SCHEMA_VERSION,
)
from psdcalc.reduction.adaptive.journal import JournalIntegrityError, ReductionActionJournal
from psdcalc.reduction.adaptive.attribution import Stage3AttributionResult


class AdaptiveControllerError(RuntimeError):
    """Raised when runtime evidence conflicts with the frozen Stage-4 contract."""


@dataclass(frozen=True)
class RuntimePowerFlowOutcome:
    """Backend-neutral summary of one PF execution stage."""

    status: str
    converged: bool
    failure_reason: str | None = None
    evidence: object | None = None

    def __post_init__(self) -> None:
        if not self.status:
            raise AdaptiveControllerError("PF status must be explicit")
        if not isinstance(self.converged, bool):
            raise AdaptiveControllerError("PF converged flag must be bool")
        if self.converged != (self.status == "PF_CONVERGED"):
            raise AdaptiveControllerError("PF status and converged flag conflict")
        if not self.converged and not self.failure_reason:
            raise AdaptiveControllerError("non-converged PF evidence needs a reason")


@dataclass(frozen=True)
class AdaptiveCandidateSnapshot:
    """One freshly rebuilt candidate and the existing Gate outcomes for it."""

    candidate_fingerprint: str
    active_action_ids: tuple[str, ...]
    action_set_fingerprint: str
    model_digest: str | None
    mapping_digest: str | None
    structural_status: str
    structural_checks: Mapping[str, bool]
    readiness_status: str
    readiness_checks: Mapping[str, bool]
    bus_count: int | None
    branch_count: int | None
    artifact: object | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        _require_digest(self.candidate_fingerprint, "candidate_fingerprint")
        _require_digest(self.action_set_fingerprint, "action_set_fingerprint")
        for name in ("model_digest", "mapping_digest"):
            value = getattr(self, name)
            if value is not None:
                _require_digest(value, name)
        if tuple(sorted(set(self.active_action_ids))) != self.active_action_ids:
            raise AdaptiveControllerError("candidate active action IDs must be unique and sorted")
        for name in ("structural_status", "readiness_status"):
            if not getattr(self, name):
                raise AdaptiveControllerError(f"{name} must be explicit")
        for name in ("bus_count", "branch_count"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise AdaptiveControllerError(f"{name} must be non-negative or None")
        for name in ("structural_checks", "readiness_checks"):
            checks = getattr(self, name)
            if not isinstance(checks, Mapping) or any(not isinstance(value, bool) for value in checks.values()):
                raise AdaptiveControllerError(f"{name} must map check names to bool values")
            object.__setattr__(self, name, MappingProxyType(dict(sorted(checks.items()))))


@dataclass(frozen=True)
class AdaptiveFidelityEvidence:
    """Existing PR-022 threshold result and Stage-3 attribution for a PF pair."""

    fidelity_status: str
    attribution: Stage3AttributionResult
    metrics_digest: str
    threshold_digest: str

    def __post_init__(self) -> None:
        if not self.fidelity_status:
            raise AdaptiveControllerError("fidelity status must be explicit")
        _require_digest(self.metrics_digest, "metrics_digest")
        _require_digest(self.threshold_digest, "threshold_digest")
        names = tuple(sorted(item.metric_name for item in self.attribution.attributions))
        if names != tuple(sorted(self.attribution.hard_failures)):
            raise AdaptiveControllerError("Stage-3 attributions differ from HARD violation names")
        if self.attribution.fidelity_satisfied != (not self.attribution.hard_failures):
            raise AdaptiveControllerError("Stage-3 fidelity state conflicts with HARD violations")


class AdaptiveRuntime(Protocol):
    """Backend boundary bound to frozen S1, never to a caller reduced network."""

    rebuilds_from_s1: bool
    source_model_digest: str
    source_sidecar_digest: str
    action_catalog_digest: str
    reduction_config_digest: str
    operating_point_digest: str
    solver_contract_digest: str
    metric_policy_digest: str
    threshold_set_id: str
    threshold_digest: str
    source_bus_count: int
    source_branch_count: int

    def build(self, active_action_ids: tuple[str, ...]) -> AdaptiveCandidateSnapshot:
        """Rebuild one candidate from the runtime's frozen S1 and action set."""
        ...

    def solve_source(self) -> RuntimePowerFlowOutcome:
        """Solve the frozen S1 reference once under the frozen PF contract."""
        ...

    def solve_candidate(self, candidate: AdaptiveCandidateSnapshot) -> RuntimePowerFlowOutcome:
        """Run the existing PF executor on one already-gated candidate."""
        ...

    def assess(
        self,
        candidate: AdaptiveCandidateSnapshot,
        source_pf: RuntimePowerFlowOutcome,
        target_pf: RuntimePowerFlowOutcome,
    ) -> AdaptiveFidelityEvidence:
        """Run the existing comparison, frozen thresholds, and Stage-3 attribution."""
        ...


class AdaptiveReductionController:
    """Run a bounded, deterministic Stage-4 adaptive expansion state machine."""

    strategy_name = "HIERARCHICALLY_CONSTRAINED_GREEDY_LOCAL_EXPANSION"

    def run(
        self,
        *,
        runtime: AdaptiveRuntime,
        actions: tuple[ReductionAction, ...],
        initial_active_action_ids: tuple[str, ...],
        config: AdaptiveReductionConfig,
    ) -> AdaptiveReductionReport:
        """Evaluate immutable S1-derived incumbents until one terminal reason applies.

        ``max_iterations`` counts evaluated incumbent states, with the initial
        S1/action-set reconstruction at iteration 0. Candidate-level failures
        are retained and do not prevent evaluation of later candidates.
        """
        self._validate_runtime(runtime, actions, config)
        action_by_id = {item.action_id: item for item in actions}
        if len(action_by_id) != len(actions):
            raise AdaptiveControllerError("action catalog contains duplicate IDs")
        active = tuple(sorted(initial_active_action_ids))
        if len(active) != len(set(initial_active_action_ids)) or set(active) - set(action_by_id):
            raise AdaptiveControllerError("initial action set is duplicated or references unknown actions")
        if any(action_by_id[item].protected or not action_by_id[item].reversible for item in set(action_by_id) - set(active)):
            raise AdaptiveControllerError("initial action set omits a protected or irreversible action")
        journal = ReductionActionJournal.start(tuple(action_by_id[item] for item in active))
        if tuple(sorted(active)) != journal.active_action_ids:
            raise AdaptiveControllerError("initial action set could not be represented by the journal")

        catalog_digest = _action_catalog_digest(actions)
        if catalog_digest != runtime.action_catalog_digest:
            raise AdaptiveControllerError("runtime action catalog digest does not match the supplied catalog")
        initial_action_set_fingerprint = journal.action_set_fingerprint
        visited = {initial_action_set_fingerprint}
        iterations: list[AdaptiveReductionIteration] = []
        selected_sequence: list[str] = []
        current_parent_fingerprint: str | None = None
        expected_rebuild: tuple[str, str | None, str | None, str] | None = None
        current_summary: dict[str, object] | None = None
        source_pf: RuntimePowerFlowOutcome | None = None
        terminal: AdaptiveStoppingReason | None = None

        while len(iterations) < config.max_iterations:
            iteration_index = len(iterations)
            active = journal.active_action_ids
            candidate = runtime.build(active)
            self._validate_snapshot(candidate, active)
            if expected_rebuild is not None:
                expected_candidate, expected_model, expected_mapping, expected_set = expected_rebuild
                if (candidate.candidate_fingerprint != expected_candidate
                        or candidate.model_digest != expected_model
                        or candidate.mapping_digest != expected_mapping
                        or candidate.action_set_fingerprint != expected_set):
                    terminal = AdaptiveStoppingReason.CONFIGURATION_INVALID
                    iterations.append(self._iteration(
                        iteration_index, journal, candidate,
                        structural_status="CONFIGURATION_INVALID",
                        readiness_status=candidate.readiness_status,
                        pf_status="NOT_RUN", fidelity_status="NOT_RUN",
                        stopping_reason=terminal,
                        diagnostics={"rebuild_identity": "selected candidate differed from fresh S1 rebuild"},
                        parent_fingerprint=current_parent_fingerprint,
                    ))
                    break
                expected_rebuild = None

            current_parent_fingerprint = candidate.candidate_fingerprint
            if candidate.structural_status != "STRUCTURALLY_VALID":
                terminal = AdaptiveStoppingReason.STRUCTURAL_INVALID
                iterations.append(self._iteration(
                    iteration_index, journal, candidate,
                    structural_status=candidate.structural_status,
                    readiness_status=candidate.readiness_status,
                    pf_status="NOT_RUN", fidelity_status="NOT_RUN",
                    stopping_reason=terminal,
                    diagnostics=_failure_details(candidate),
                ))
                current_summary = _incumbent_summary(candidate, None)
                break
            if candidate.readiness_status != "AC_MODEL_READY":
                terminal = AdaptiveStoppingReason.AC_MODEL_NOT_READY
                iterations.append(self._iteration(
                    iteration_index, journal, candidate,
                    structural_status=candidate.structural_status,
                    readiness_status=candidate.readiness_status,
                    pf_status="NOT_RUN", fidelity_status="NOT_RUN",
                    stopping_reason=terminal,
                    diagnostics=_failure_details(candidate),
                ))
                current_summary = _incumbent_summary(candidate, None)
                break

            if source_pf is None:
                source_pf = runtime.solve_source()
                if not source_pf.converged:
                    terminal = _pf_terminal(source_pf)
                    iterations.append(self._iteration(
                        iteration_index, journal, candidate,
                        structural_status=candidate.structural_status,
                        readiness_status=candidate.readiness_status,
                        pf_status=source_pf.status, fidelity_status="NOT_RUN",
                        stopping_reason=terminal,
                        diagnostics={"pf_failure": source_pf.failure_reason or "source PF failed"},
                    ))
                    current_summary = _incumbent_summary(candidate, None)
                    break

            target_pf = runtime.solve_candidate(candidate)
            if not target_pf.converged:
                terminal = _pf_terminal(target_pf)
                iterations.append(self._iteration(
                    iteration_index, journal, candidate,
                    structural_status=candidate.structural_status,
                    readiness_status=candidate.readiness_status,
                    pf_status=target_pf.status, fidelity_status="NOT_RUN",
                    stopping_reason=terminal,
                    diagnostics={"pf_failure": target_pf.failure_reason or "current PF failed"},
                ))
                current_summary = _incumbent_summary(candidate, None)
                break

            try:
                assessment = runtime.assess(candidate, source_pf, target_pf)
            except Exception as exc:
                terminal = AdaptiveStoppingReason.PF_MAPPING_INVALID
                iterations.append(self._iteration(
                    iteration_index, journal, candidate,
                    structural_status=candidate.structural_status,
                    readiness_status=candidate.readiness_status,
                    pf_status=target_pf.status, fidelity_status="FAILED",
                    stopping_reason=terminal,
                    diagnostics={"fidelity_failure": f"{type(exc).__name__}: {exc}"},
                ))
                current_summary = _incumbent_summary(candidate, None)
                break

            current_violation = _normalized_hard_violation(assessment)
            current_summary = _incumbent_summary(candidate, assessment)
            if assessment.attribution.fidelity_satisfied:
                terminal = self._compression_terminal(candidate, runtime, config)
                iterations.append(self._iteration(
                    iteration_index, journal, candidate,
                    structural_status=candidate.structural_status,
                    readiness_status=candidate.readiness_status,
                    pf_status=target_pf.status,
                    fidelity_status=assessment.fidelity_status,
                    assessment=assessment, stopping_reason=terminal,
                ))
                break
            if any(row.status.value == "UNATTRIBUTABLE"
                   for row in assessment.attribution.attributions):
                terminal = AdaptiveStoppingReason.UNATTRIBUTABLE_HARD_VIOLATION
                iterations.append(self._iteration(
                    iteration_index, journal, candidate,
                    structural_status=candidate.structural_status,
                    readiness_status=candidate.readiness_status,
                    pf_status=target_pf.status,
                    fidelity_status=assessment.fidelity_status,
                    assessment=assessment, stopping_reason=terminal,
                ))
                break
            candidates = tuple(sorted(
                assessment.attribution.candidates,
                key=lambda item: (item.candidate_fingerprint, item.deactivated_action_ids),
            ))
            if not candidates:
                terminal = AdaptiveStoppingReason.NO_EXPANSION_CANDIDATE
                decision = self._empty_decision(assessment, "no legal candidate was produced")
                iterations.append(self._iteration(
                    iteration_index, journal, candidate,
                    structural_status=candidate.structural_status,
                    readiness_status=candidate.readiness_status,
                    pf_status=target_pf.status,
                    fidelity_status=assessment.fidelity_status,
                    assessment=assessment, decision=decision, stopping_reason=terminal,
                ))
                break

            eval_rows: list[CandidateEvaluation] = []
            eval_contexts: dict[str, tuple[AdaptiveCandidateSnapshot, AdaptiveFidelityEvidence,
                                           ReductionActionJournal, tuple[str, ...]]] = {}
            viable_count = 0
            for expansion in candidates:
                evaluation, context = self._evaluate_candidate(
                    runtime=runtime, source_pf=source_pf, actions=action_by_id,
                    journal=journal, current=candidate, expansion=expansion,
                    current_violation=current_violation, config=config,
                    visited=visited,
                )
                eval_rows.append(evaluation)
                if context is not None:
                    viable_count += 1
                    eval_contexts[expansion.candidate_fingerprint] = context

            if viable_count == 0:
                terminal = AdaptiveStoppingReason.NO_VIABLE_EXPANSION_CANDIDATE
                decision = self._decision(
                    tuple(eval_rows), None, "all legal candidates failed prerequisite Gates",
                    rejected=assessment.attribution.rejected_candidates,
                )
                iterations.append(self._iteration(
                    iteration_index, journal, candidate,
                    structural_status=candidate.structural_status,
                    readiness_status=candidate.readiness_status,
                    pf_status=target_pf.status,
                    fidelity_status=assessment.fidelity_status,
                    assessment=assessment, decision=decision, stopping_reason=terminal,
                ))
                break

            improving = [row for row in eval_rows if row.score is not None
                         and row.hard_violation_improvement is not None
                         and row.hard_violation_improvement > config.improvement_tolerance]
            if not improving:
                terminal = AdaptiveStoppingReason.NO_FURTHER_COMPRESSION_BENEFIT
                decision = self._decision(
                    tuple(eval_rows), None,
                    "no evaluated candidate improves aggregate HARD violation beyond the frozen tolerance",
                    rejected=assessment.attribution.rejected_candidates,
                )
                iterations.append(self._iteration(
                    iteration_index, journal, candidate,
                    structural_status=candidate.structural_status,
                    readiness_status=candidate.readiness_status,
                    pf_status=target_pf.status,
                    fidelity_status=assessment.fidelity_status,
                    assessment=assessment, decision=decision, stopping_reason=terminal,
                ))
                break
            if iteration_index + 1 >= config.max_iterations:
                terminal = AdaptiveStoppingReason.MAX_ITERATIONS_REACHED
                decision = self._decision(
                    tuple(eval_rows), None,
                    "maximum evaluated incumbent-state count reached before another transition",
                    rejected=assessment.attribution.rejected_candidates,
                )
                iterations.append(self._iteration(
                    iteration_index, journal, candidate,
                    structural_status=candidate.structural_status,
                    readiness_status=candidate.readiness_status,
                    pf_status=target_pf.status,
                    fidelity_status=assessment.fidelity_status,
                    assessment=assessment, decision=decision, stopping_reason=terminal,
                ))
                break

            winner = min(improving, key=lambda row: self._rank_key(row, action_by_id))
            context = eval_contexts[winner.candidate.candidate_fingerprint]
            next_snapshot, next_assessment, next_journal, deactivated = context
            next_action_fp = next_journal.action_set_fingerprint
            if next_action_fp in visited:
                terminal = AdaptiveStoppingReason.REPEATED_ACTION_SET
                decision = self._decision(
                    tuple(eval_rows), None, "candidate would repeat an already visited active action set",
                    rejected=assessment.attribution.rejected_candidates,
                )
                iterations.append(self._iteration(
                    iteration_index, journal, candidate,
                    structural_status=candidate.structural_status,
                    readiness_status=candidate.readiness_status,
                    pf_status=target_pf.status, fidelity_status=assessment.fidelity_status,
                    assessment=assessment, decision=decision, stopping_reason=terminal,
                ))
                break
            if not set(next_journal.active_action_ids) < set(journal.active_action_ids):
                terminal = AdaptiveStoppingReason.REPEATED_ACTION_SET
                decision = self._decision(
                    tuple(eval_rows), None, "adaptive action set did not decrease monotonically",
                    rejected=assessment.attribution.rejected_candidates,
                )
                iterations.append(self._iteration(
                    iteration_index, journal, candidate,
                    structural_status=candidate.structural_status,
                    readiness_status=candidate.readiness_status,
                    pf_status=target_pf.status, fidelity_status=assessment.fidelity_status,
                    assessment=assessment, decision=decision, stopping_reason=terminal,
                ))
                break
            decision = self._decision(
                tuple(eval_rows), winner.candidate.candidate_fingerprint,
                "selected highest-ranked viable HARD-violation improvement",
                rejected=assessment.attribution.rejected_candidates,
            )
            iterations.append(self._iteration(
                iteration_index, journal, candidate,
                structural_status=candidate.structural_status,
                readiness_status=candidate.readiness_status,
                pf_status=target_pf.status, fidelity_status=assessment.fidelity_status,
                assessment=assessment, decision=decision,
                stopping_reason=None,
                parent_fingerprint=current_parent_fingerprint,
                new_fingerprint=next_snapshot.candidate_fingerprint,
                action_delta=deactivated,
            ))
            visited.add(next_action_fp)
            selected_sequence.append(winner.candidate.candidate_fingerprint)
            current_parent_fingerprint = candidate.candidate_fingerprint
            journal = next_journal
            expected_rebuild = (
                next_snapshot.candidate_fingerprint, next_snapshot.model_digest,
                next_snapshot.mapping_digest, next_snapshot.action_set_fingerprint,
            )
        else:
            terminal = AdaptiveStoppingReason.MAX_ITERATIONS_REACHED

        if terminal is None:
            raise AdaptiveControllerError("controller exited without exactly one terminal reason")
        if not iterations or iterations[-1].stopping_reason is not terminal:
            last = iterations[-1]
            iterations[-1] = _replace_stopping_reason(last, terminal)
        return AdaptiveReductionReport(
            schema_version=SCHEMA_VERSION,
            run_id="adaptive-" + _digest({
                "config": config.digest,
                "s1": runtime.source_model_digest,
                "sidecar": runtime.source_sidecar_digest,
                "catalog": catalog_digest,
            })[:16],
            config_digest=config.digest,
            s1_model_digest=runtime.source_model_digest,
            s1_sidecar_digest=runtime.source_sidecar_digest,
            action_catalog_digest=catalog_digest,
            journal_digest=journal.digest,
            terminal_reason=terminal,
            iterations=tuple(iterations),
            evidence_reference="in-memory:stage4",
            initial_action_set_fingerprint=initial_action_set_fingerprint,
            initial_candidate_fingerprint=(iterations[0].candidate_fingerprint if iterations else None),
            selected_candidate_sequence=tuple(selected_sequence),
            final_incumbent_summary=current_summary or {},
        )

    @staticmethod
    def _validate_runtime(runtime: AdaptiveRuntime, actions: tuple[ReductionAction, ...],
                          config: AdaptiveReductionConfig) -> None:
        """Bind runtime source/config/action/threshold inputs to frozen config."""
        if runtime.rebuilds_from_s1 is not True:
            raise AdaptiveControllerError("runtime must rebuild every candidate from frozen S1")
        for name in ("source_model_digest", "source_sidecar_digest", "action_catalog_digest",
                     "reduction_config_digest", "operating_point_digest", "solver_contract_digest",
                     "metric_policy_digest", "threshold_digest"):
            _require_digest(getattr(runtime, name), name)
        if runtime.reduction_config_digest != config.reduction_config_digest:
            raise AdaptiveControllerError("runtime reduction config differs from frozen adaptive config")
        if runtime.operating_point_digest != config.operating_point_digest:
            raise AdaptiveControllerError("runtime operating point differs from frozen adaptive config")
        if runtime.solver_contract_digest != config.solver_contract_digest:
            raise AdaptiveControllerError("runtime solver contract differs from frozen adaptive config")
        if runtime.metric_policy_digest != config.metric_policy_digest:
            raise AdaptiveControllerError("runtime metric policy differs from frozen adaptive config")
        if runtime.threshold_set_id != config.threshold_set_id or runtime.threshold_digest != config.threshold_digest:
            raise AdaptiveControllerError("runtime frozen threshold binding differs from adaptive config")
        if runtime.action_catalog_digest != _action_catalog_digest(actions):
            raise AdaptiveControllerError("runtime action catalog differs from controller input")
        for name in ("source_bus_count", "source_branch_count"):
            value = getattr(runtime, name)
            minimum = 1 if name == "source_bus_count" else 0
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise AdaptiveControllerError(f"runtime {name} must be an integer >= {minimum}")

    @staticmethod
    def _validate_snapshot(candidate: AdaptiveCandidateSnapshot,
                           active_ids: tuple[str, ...]) -> None:
        """Ensure a backend candidate exactly reflects the requested active set."""
        if candidate.active_action_ids != active_ids:
            raise AdaptiveControllerError("runtime candidate was built from a different action set")

    @staticmethod
    def _evaluate_candidate(
        *, runtime: AdaptiveRuntime, source_pf: RuntimePowerFlowOutcome,
        actions: Mapping[str, ReductionAction], journal: ReductionActionJournal,
        current: AdaptiveCandidateSnapshot, expansion: ExpansionCandidate,
        current_violation: float, config: AdaptiveReductionConfig,
        visited: set[str],
    ) -> tuple[CandidateEvaluation, tuple[AdaptiveCandidateSnapshot, AdaptiveFidelityEvidence,
                                          ReductionActionJournal, tuple[str, ...]] | None]:
        """Rebuild, gate, solve, and compare one candidate without short-circuiting siblings."""
        candidate_fp = expansion.candidate_fingerprint
        proposed_fp = _digest({"schema_version": SCHEMA_VERSION,
                               "active_action_ids": expansion.resulting_active_action_ids})
        try:
            proposed_journal = journal.deactivate(
                expansion.deactivated_action_ids,
                reason="Stage-4 local expansion candidate",
                decision_reference=candidate_fp,
            )
        except (JournalIntegrityError, KeyError) as exc:
            return _failed_evaluation(
                expansion, proposed_fp, "ACTION_SET_INVALID", str(exc),
            ), None
        proposed_ids = proposed_journal.active_action_ids
        declared_deactivated = tuple(sorted(set(journal.active_action_ids) - set(proposed_ids)))
        if (proposed_ids != expansion.resulting_active_action_ids
                or declared_deactivated != expansion.deactivated_action_ids):
            return _failed_evaluation(
                expansion, proposed_journal.action_set_fingerprint, "ACTION_SET_INVALID",
                "candidate declared action-set transition differs from journal dependency closure",
            ), None
        proposed_fp = proposed_journal.action_set_fingerprint
        if proposed_fp in visited:
            return _failed_evaluation(
                expansion, proposed_fp, "REPEATED_ACTION_SET",
                "candidate action set was already visited",
            ), None
        if not set(proposed_ids) < set(journal.active_action_ids):
            return _failed_evaluation(
                expansion, proposed_fp, "REPEATED_ACTION_SET",
                "candidate does not monotonically deactivate active actions",
            ), None
        candidate = runtime.build(proposed_ids)
        AdaptiveReductionController._validate_snapshot(candidate, proposed_ids)
        if candidate.structural_status != "STRUCTURALLY_VALID":
            return _failed_evaluation(
                expansion, proposed_fp, "STRUCTURAL_INVALID", candidate.failure_reason,
                candidate=candidate,
            ), None
        if candidate.readiness_status != "AC_MODEL_READY":
            return _failed_evaluation(
                expansion, proposed_fp, "AC_MODEL_NOT_READY", candidate.failure_reason,
                candidate=candidate,
            ), None
        target_pf = runtime.solve_candidate(candidate)
        if not target_pf.converged:
            return _failed_evaluation(
                expansion, proposed_fp, target_pf.status,
                target_pf.failure_reason, candidate=candidate,
            ), None
        try:
            assessment = runtime.assess(candidate, source_pf, target_pf)
        except Exception as exc:
            return _failed_evaluation(
                expansion, proposed_fp, "PF_MAPPING_INVALID",
                f"{type(exc).__name__}: {exc}", candidate=candidate,
                pf_status=target_pf.status,
            ), None
        after = _normalized_hard_violation(assessment)
        improvement = current_violation - after
        normalized_improvement = improvement / current_violation if current_violation > 0.0 else 0.0
        deactivated = tuple(sorted(set(journal.active_action_ids) - set(proposed_ids)))
        added_buses, added_branches = _expanded_object_counts(deactivated, actions)
        cost = config.cost_bus_weight * added_buses + config.cost_branch_weight * added_branches
        score = None
        if improvement > config.improvement_tolerance:
            score = normalized_improvement if cost == 0.0 else normalized_improvement / cost
        evaluation = CandidateEvaluation(
            candidate=expansion,
            structural_status=candidate.structural_status,
            readiness_status=candidate.readiness_status,
            pf_status=target_pf.status,
            fidelity_status=assessment.fidelity_status,
            model_digest=candidate.model_digest,
            mapping_digest=candidate.mapping_digest,
            rejection_reason=None,
            normalized_hard_violation=after,
            score=score,
            proposed_active_action_set_fingerprint=proposed_fp,
            candidate_build_fingerprint=candidate.candidate_fingerprint,
            structural_checks=candidate.structural_checks,
            readiness_checks=candidate.readiness_checks,
            hard_violations=assessment.attribution.attributions,
            diagnostic_failures=assessment.attribution.diagnostic_failures,
            hard_violation_improvement=improvement,
            normalized_improvement=normalized_improvement,
            expansion_cost=cost,
            added_bus_count=added_buses,
            added_branch_count=added_branches,
            metrics_digest=assessment.metrics_digest,
            threshold_digest=assessment.threshold_digest,
            attribution_digest=assessment.attribution.candidate_set_digest,
            attributed_region_fingerprints=tuple(sorted(
                region.fingerprint for region in assessment.attribution.regions)),
            remaining_candidate_fingerprints=tuple(sorted(
                row.candidate_fingerprint for row in assessment.attribution.candidates)),
            rejected_candidate_fingerprints=tuple(sorted(
                row.candidate_fingerprint for row in assessment.attribution.rejected_candidates)),
        )
        return evaluation, (candidate, assessment, proposed_journal, deactivated)

    @staticmethod
    def _rank_key(evaluation: CandidateEvaluation,
                  actions: Mapping[str, ReductionAction]) -> tuple[object, ...]:
        """Return the frozen zero-cost override and deterministic tie order."""
        improvement = evaluation.hard_violation_improvement or 0.0
        zero_cost_improving = evaluation.expansion_cost == 0.0 and improvement > 0.0
        source_ids = tuple(sorted(
            identity.encode()
            for action_id in evaluation.candidate.deactivated_action_ids
            for identity in actions[action_id].source_ids
        ))
        return (
            0 if zero_cost_improving else 1,
            (-improvement if zero_cost_improving else
             -(evaluation.score if evaluation.score is not None else 0.0)),
            evaluation.added_bus_count if evaluation.added_bus_count is not None else math.inf,
            evaluation.added_branch_count if evaluation.added_branch_count is not None else math.inf,
            evaluation.candidate.region_fingerprint,
            evaluation.candidate.deactivated_action_ids,
            source_ids,
            evaluation.candidate.candidate_fingerprint,
        )

    def _compression_terminal(self, candidate: AdaptiveCandidateSnapshot,
                              runtime: AdaptiveRuntime,
                              config: AdaptiveReductionConfig) -> AdaptiveStoppingReason:
        """Evaluate size requirements independently from PF HARD thresholds."""
        requirement = config.compression_requirement
        if requirement is None:
            return AdaptiveStoppingReason.FIDELITY_SATISFIED
        if candidate.bus_count is None or candidate.branch_count is None:
            return AdaptiveStoppingReason.FIDELITY_SATISFIED_BUT_COMPRESSION_BUDGET_NOT_MET
        bus_fraction = 1.0 - candidate.bus_count / runtime.source_bus_count
        branch_fraction = 1.0 - candidate.branch_count / runtime.source_branch_count
        passes = (
            (requirement.max_bus_count is None or candidate.bus_count <= requirement.max_bus_count)
            and (requirement.min_bus_compression_fraction is None
                 or bus_fraction >= requirement.min_bus_compression_fraction)
            and (requirement.max_branch_count is None or candidate.branch_count <= requirement.max_branch_count)
            and (requirement.min_branch_compression_fraction is None
                 or branch_fraction >= requirement.min_branch_compression_fraction)
        )
        return (AdaptiveStoppingReason.FIDELITY_SATISFIED if passes else
                AdaptiveStoppingReason.FIDELITY_SATISFIED_BUT_COMPRESSION_BUDGET_NOT_MET)

    @staticmethod
    def _iteration(
        index: int, journal: ReductionActionJournal, candidate: AdaptiveCandidateSnapshot,
        *, structural_status: str, readiness_status: str, pf_status: str,
        fidelity_status: str, assessment: AdaptiveFidelityEvidence | None = None,
        decision: ExpansionDecision | None = None,
        stopping_reason: AdaptiveStoppingReason | None = None,
        diagnostics: Mapping[str, str] | None = None,
        parent_fingerprint: str | None = None,
        new_fingerprint: str | None = None,
        action_delta: tuple[str, ...] = (),
    ) -> AdaptiveReductionIteration:
        """Create a semantic iteration record without runtime PF arrays."""
        return AdaptiveReductionIteration(
            iteration_index=index,
            action_set_fingerprint=journal.action_set_fingerprint,
            model_digest=candidate.model_digest,
            mapping_digest=candidate.mapping_digest,
            structural_status=structural_status,
            readiness_status=readiness_status,
            pf_status=pf_status,
            fidelity_status=fidelity_status,
            hard_violations=(assessment.attribution.attributions if assessment else ()),
            diagnostic_failures=(assessment.attribution.diagnostic_failures if assessment else ()),
            expansion_decision=decision,
            stopping_reason=stopping_reason,
            diagnostics=dict(diagnostics or _failure_details(candidate)),
            candidate_fingerprint=candidate.candidate_fingerprint,
            parent_incumbent_fingerprint=parent_fingerprint,
            new_incumbent_fingerprint=new_fingerprint,
            action_set_delta=tuple(sorted(action_delta)),
            candidate_build_fingerprint=candidate.candidate_fingerprint,
            metrics_digest=assessment.metrics_digest if assessment else None,
            threshold_digest=assessment.threshold_digest if assessment else None,
            attribution_digest=(_canonical_attribution_digest(assessment.attribution)
                               if assessment else None),
            error_region_fingerprints=(tuple(sorted(
                row.fingerprint for row in assessment.attribution.regions)) if assessment else ()),
            expansion_candidate_fingerprints=(tuple(sorted(
                row.candidate_fingerprint for row in assessment.attribution.candidates))
                if assessment else ()),
            rejected_candidate_fingerprints=(tuple(sorted(
                row.candidate_fingerprint for row in assessment.attribution.rejected_candidates))
                if assessment else ()),
        )

    @staticmethod
    def _decision(evaluations: tuple[CandidateEvaluation, ...], selected: str | None,
                  reason: str, *, rejected: tuple[ExpansionCandidate, ...] = ()) -> ExpansionDecision:
        """Create a deterministically ordered Stage-4 selection record."""
        ordered = tuple(sorted(evaluations, key=lambda row: row.candidate.candidate_fingerprint))
        return ExpansionDecision(
            strategy=AdaptiveReductionController.strategy_name,
            candidate_evaluations=ordered,
            selected_candidate_fingerprint=selected,
            tie_break_trace=(
                "zero_cost_positive_improvement_first",
                "highest_score_for_positive_cost",
                "greatest_hard_violation_improvement_for_zero_cost",
                "lowest_added_bus_count",
                "lowest_added_branch_count",
                "lowest_error_region_fingerprint",
                "canonical_action_id_order",
                "canonical_stable_id_order",
                "lowest_expansion_candidate_fingerprint",
            ),
            reason=reason,
            rejected_candidates=tuple(sorted(rejected, key=lambda row: row.candidate_fingerprint)),
        )

    def _empty_decision(self, assessment: AdaptiveFidelityEvidence, reason: str) -> ExpansionDecision:
        """Preserve Stage-3 rejected-candidate evidence when no legal proposal exists."""
        return ExpansionDecision(
            strategy=self.strategy_name, candidate_evaluations=(),
            selected_candidate_fingerprint=None,
            tie_break_trace=("zero_cost_positive_improvement_first", "greatest_hard_violation_improvement"),
            reason=reason,
            rejected_candidates=assessment.attribution.rejected_candidates,
        )


def _failed_evaluation(
    expansion: ExpansionCandidate, proposed_fingerprint: str, reason: str,
    detail: str | None, *, candidate: AdaptiveCandidateSnapshot | None = None,
    pf_status: str = "NOT_RUN",
) -> CandidateEvaluation:
    """Preserve a rejected proposal with every stage reached before its failure."""
    return CandidateEvaluation(
        candidate=expansion,
        structural_status=candidate.structural_status if candidate else "NOT_RUN",
        readiness_status=candidate.readiness_status if candidate else "NOT_RUN",
        pf_status=pf_status,
        fidelity_status="NOT_RUN",
        model_digest=candidate.model_digest if candidate else None,
        mapping_digest=candidate.mapping_digest if candidate else None,
        rejection_reason=reason + (f": {detail}" if detail else ""),
        proposed_active_action_set_fingerprint=proposed_fingerprint,
        candidate_build_fingerprint=candidate.candidate_fingerprint if candidate else None,
        structural_checks=candidate.structural_checks if candidate else {},
        readiness_checks=candidate.readiness_checks if candidate else {},
        added_bus_count=expansion.added_bus_count,
        added_branch_count=expansion.added_branch_count,
    )


def _normalized_hard_violation(evidence: AdaptiveFidelityEvidence) -> float:
    """Aggregate only Stage-3 normalized HARD violation details by deterministic sum."""
    value = math.fsum(item.normalized_violation for item in evidence.attribution.attributions)
    if not math.isfinite(value) or value < 0.0:
        raise AdaptiveControllerError("aggregate normalized HARD violation is invalid")
    return value


def _canonical_attribution_digest(attribution: Stage3AttributionResult) -> str:
    """Digest attribution evidence in canonical order for report determinism."""
    def candidate_payload(candidate: ExpansionCandidate) -> dict[str, object]:
        return {
            "candidate_fingerprint": candidate.candidate_fingerprint,
            "region_fingerprint": candidate.region_fingerprint,
            "deactivated_action_ids": candidate.deactivated_action_ids,
            "resulting_active_action_ids": candidate.resulting_active_action_ids,
            "added_bus_count": candidate.added_bus_count,
            "added_branch_count": candidate.added_branch_count,
            "expansion_cost": candidate.expansion_cost,
            "triggering_attribution_ids": candidate.triggering_attribution_ids,
            "direct_action_ids": candidate.direct_action_ids,
            "dependency_closure_action_ids": candidate.dependency_closure_action_ids,
            "region_fingerprints": candidate.region_fingerprints,
            "eligible": candidate.eligible,
            "rejection_reason": candidate.rejection_reason,
        }

    return _digest({
        "schema_version": SCHEMA_VERSION,
        "fidelity_satisfied": attribution.fidelity_satisfied,
        "hard_violation_present": attribution.hard_violation_present,
        "hard_failures": tuple(sorted(attribution.hard_failures)),
        "diagnostic_failures": tuple(sorted(attribution.diagnostic_failures)),
        "attributions": tuple(sorted(row.fingerprint for row in attribution.attributions)),
        "regions": tuple(sorted(row.fingerprint for row in attribution.regions)),
        "candidates": tuple(sorted((candidate_payload(row) for row in attribution.candidates),
                                   key=lambda row: row["candidate_fingerprint"])),
        "rejected_candidates": tuple(sorted(
            (candidate_payload(row) for row in attribution.rejected_candidates),
            key=lambda row: row["candidate_fingerprint"])),
    })


def _expanded_object_counts(
    deactivated_action_ids: tuple[str, ...], actions: Mapping[str, ReductionAction],
) -> tuple[int, int]:
    """Count unique Bus and branch identities restored by an action-set delta."""
    buses: set[object] = set()
    branches: set[object] = set()
    for action_id in deactivated_action_ids:
        for transition in actions[action_id].mapping_delta:
            if transition.source_id.component == "Bus" and transition.target_id != transition.source_id:
                buses.add(transition.source_id)
            elif transition.source_id.component in {"Line", "Transformer", "Link"} and (
                    transition.target_id is None or transition.target_id != transition.source_id):
                branches.add(transition.source_id)
    return len(buses), len(branches)


def _action_catalog_digest(actions: Sequence[ReductionAction]) -> str:
    """Hash a complete StableId action catalog in canonical action-ID order."""
    rows = [action.canonical_payload() for action in sorted(actions, key=lambda item: item.action_id)]
    return _digest({"schema_version": SCHEMA_VERSION, "actions": rows})


def _incumbent_summary(candidate: AdaptiveCandidateSnapshot,
                       assessment: AdaptiveFidelityEvidence | None) -> dict[str, object]:
    """Return the final in-memory candidate summary without carrying a network object."""
    return {
        "candidate_fingerprint": candidate.candidate_fingerprint,
        "action_set_fingerprint": candidate.action_set_fingerprint,
        "active_action_ids": candidate.active_action_ids,
        "model_digest": candidate.model_digest,
        "mapping_digest": candidate.mapping_digest,
        "structural_status": candidate.structural_status,
        "readiness_status": candidate.readiness_status,
        "fidelity_status": assessment.fidelity_status if assessment else "NOT_RUN",
        "normalized_hard_violation": (_normalized_hard_violation(assessment)
                                      if assessment else None),
        "bus_count": candidate.bus_count,
        "branch_count": candidate.branch_count,
    }


def _failure_details(candidate: AdaptiveCandidateSnapshot) -> Mapping[str, str]:
    """Combine Gate checks and a concrete failure reason for in-memory evidence."""
    details = {f"structural.{key}": str(value) for key, value in candidate.structural_checks.items()}
    details.update({f"readiness.{key}": str(value) for key, value in candidate.readiness_checks.items()})
    if candidate.failure_reason:
        details["failure_reason"] = candidate.failure_reason
    return details


def _pf_terminal(outcome: RuntimePowerFlowOutcome) -> AdaptiveStoppingReason:
    """Map a current-required PF outcome without conflating unsupported and failed states."""
    if outcome.status == "PF_NOT_CONVERGED":
        return AdaptiveStoppingReason.PF_NOT_CONVERGED
    if outcome.status == "PF_EXECUTION_UNSUPPORTED":
        return AdaptiveStoppingReason.PF_EXECUTION_UNSUPPORTED
    return AdaptiveStoppingReason.PF_EXECUTION_FAILED


def _require_digest(value: object, name: str) -> None:
    """Require one lowercase SHA-256 digest at the runtime/controller boundary."""
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise AdaptiveControllerError(f"{name} must be a lowercase SHA-256 digest")


def _digest(value: object) -> str:
    """Return the Stage-4 canonical JSON SHA-256 fingerprint."""
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def _replace_stopping_reason(
    iteration: AdaptiveReductionIteration, reason: AdaptiveStoppingReason,
) -> AdaptiveReductionIteration:
    """Copy one immutable iteration with its unique terminal reason."""
    from dataclasses import replace

    return replace(iteration, stopping_reason=reason)
