"""Deterministic extraction of local HARD violations and legal expansion actions.

This module consumes PR-022 comparison and threshold-evaluation outputs. It
does not solve power flow, recalculate aggregate metrics, score candidates, or
select a candidate.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
from typing import Iterable, Mapping

from psdcalc.reduction.adaptive.actions import ActionOperation, ReductionAction
from psdcalc.reduction.adaptive.contracts import (
    AttributionStatus,
    ErrorAttribution,
    ErrorRegion,
    ExpansionCandidate,
)
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.fidelity.contracts import PfFidelityStatus
from psdcalc.reduction.fidelity.mapping import PfComparisonMapping
from psdcalc.reduction.fidelity.metrics import (
    PowerFlowComparison,
    PowerFlowMetricViolation,
    PowerFlowThresholdEvaluation,
    metric_set_digest,
)
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry


class AttributionError(ValueError):
    """Raised when fidelity evidence, mappings, or the action graph conflict."""


class MetricFamily(StrEnum):
    """PR-022 metric families reviewed by the Stage-3 attribution boundary."""

    BUS_VM = "bus_vm"
    BUS_ANGLE = "bus_angle"
    BRANCH_P = "branch_p"
    BRANCH_Q = "branch_q"
    CUTSET_P = "cutset_p"
    CUTSET_Q = "cutset_q"
    INJECTION_PQ = "injection_pq"
    PASSIVE_LOSS = "passive_loss"


class MetricSupport(StrEnum):
    """How much object-level provenance Stage 3 can justify for a metric family."""

    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNATTRIBUTABLE_BY_STAGE3_DESIGN = "UNATTRIBUTABLE_BY_STAGE3_DESIGN"


@dataclass(frozen=True)
class Stage3AttributionInput:
    """Bound PR-022 results, authoritative maps, and the Stage-2 action catalog."""

    comparison: PowerFlowComparison
    threshold_evaluation: PowerFlowThresholdEvaluation
    comparison_mapping: PfComparisonMapping
    mappings: MappingBundle
    actions: tuple[ReductionAction, ...]
    active_action_ids: tuple[str, ...]
    source_comparison_identity: str
    target_comparison_identity: str


@dataclass(frozen=True)
class Stage3AttributionResult:
    """Canonical Stage-3 evidence; candidates are unevaluated and unscored."""

    fidelity_satisfied: bool
    hard_violation_present: bool
    hard_failures: tuple[str, ...]
    diagnostic_failures: tuple[str, ...]
    attributions: tuple[ErrorAttribution, ...]
    regions: tuple[ErrorRegion, ...]
    candidates: tuple[ExpansionCandidate, ...]
    rejected_candidates: tuple[ExpansionCandidate, ...]
    candidate_set_digest: str


_FAMILY_SUPPORT: Mapping[MetricFamily, MetricSupport] = {
    MetricFamily.BUS_VM: MetricSupport.PARTIALLY_SUPPORTED,
    MetricFamily.BUS_ANGLE: MetricSupport.PARTIALLY_SUPPORTED,
    MetricFamily.BRANCH_P: MetricSupport.PARTIALLY_SUPPORTED,
    MetricFamily.BRANCH_Q: MetricSupport.PARTIALLY_SUPPORTED,
    MetricFamily.CUTSET_P: MetricSupport.SUPPORTED,
    MetricFamily.CUTSET_Q: MetricSupport.SUPPORTED,
    MetricFamily.INJECTION_PQ: MetricSupport.PARTIALLY_SUPPORTED,
    MetricFamily.PASSIVE_LOSS: MetricSupport.UNATTRIBUTABLE_BY_STAGE3_DESIGN,
}

_STAGE2_OPERATIONS = frozenset({
    ActionOperation.BUS_CLUSTER,
    ActionOperation.RADIAL_ABSORPTION,
})


class ErrorAttributionEngine:
    """Attribute authoritative HARD failures and generate legal deactivation sets."""

    @staticmethod
    def metric_support(family: MetricFamily) -> MetricSupport:
        """Return the explicit object-level attribution boundary for one metric family."""
        try:
            return _FAMILY_SUPPORT[family]
        except KeyError as exc:
            raise AttributionError(f"unsupported metric family: {family}") from exc

    def run(self, evidence: Stage3AttributionInput) -> Stage3AttributionResult:
        """Validate evidence bindings, attribute HARD failures, and deduplicate candidates."""
        self._validate_evidence(evidence)
        actions, active_ids = self._validate_action_catalog(evidence)
        self._validate_mapping_authority(evidence, actions, active_ids)
        evaluation = evidence.threshold_evaluation
        hard_details = tuple(sorted(evaluation.hard_violations,
                                    key=lambda row: row.metric_name))
        diagnostic_details = tuple(sorted(evaluation.diagnostic_violations,
                                          key=lambda row: row.metric_name))

        regions_by_fingerprint: dict[str, ErrorRegion] = {}
        attributions: list[ErrorAttribution] = []
        legal_groups: dict[tuple[str, ...], dict[str, object]] = {}
        rejected: dict[tuple[str, ...], ExpansionCandidate] = {}
        for violation in hard_details:
            family = _metric_family(violation.metric_name)
            local = self._localize(evidence, violation, family)
            related = self._related_actions(
                actions, active_ids,
                (*local["source_ids"], *local["source_branch_ids"]),
            )
            regions = self._regions(local, related)
            regions_by_fingerprint.update((region.fingerprint, region) for region in regions)
            legal_direct: list[str] = []
            violation_rejections: list[ExpansionCandidate] = []
            candidate_group_keys: list[tuple[str, ...]] = []
            for action in related:
                if not self._ordinary_action_eligible(action):
                    continue
                closure = _dependency_closure(action.action_id, actions)
                closure_actions = tuple(actions[item] for item in closure)
                reason = self._closure_rejection(closure_actions)
                if reason is not None:
                    violation_rejections.append(self._rejected_candidate(
                        action.action_id, closure, active_ids,
                        regions, reason, closure_actions,
                    ))
                    continue
                legal_direct.append(action.action_id)
                key = closure
                group = legal_groups.setdefault(key, {
                    "direct": set(), "triggers": set(), "regions": set(),
                    "closure_actions": closure_actions,
                })
                group["direct"].add(action.action_id)
                group["regions"].update(region.fingerprint for region in regions)
                candidate_group_keys.append(key)
            region_ids = tuple(sorted(region.fingerprint for region in regions))
            source_ids = tuple(sorted(set(local["source_ids"]), key=StableId.encode))
            target_ids = tuple(sorted(set(local["target_ids"]), key=StableId.encode))
            normalized_ids = tuple(sorted(set(legal_direct)))
            status = (AttributionStatus.ATTRIBUTABLE if normalized_ids else
                      AttributionStatus.UNATTRIBUTABLE)
            attribution = ErrorAttribution(
                metric_name=violation.metric_name,
                severity="HARD",
                observed_value=violation.observed_value,
                resolved_threshold=violation.resolved_threshold,
                normalized_violation=violation.normalized_violation,
                status=status,
                source_ids=tuple(sorted(set((*source_ids, *local["source_branch_ids"])),
                                        key=StableId.encode)),
                target_ids=target_ids,
                region_fingerprints=region_ids,
                candidate_action_ids=normalized_ids,
                reason=None if normalized_ids else self._unattributable_reason(
                    family, related, local),
                violation_magnitude=violation.violation_magnitude,
                threshold_digest=evaluation.threshold_digest,
                source_comparison_identity=evidence.source_comparison_identity,
                target_comparison_identity=evidence.target_comparison_identity,
            )
            attributions.append(attribution)
            for key in candidate_group_keys:
                legal_groups[key]["triggers"].add(attribution.fingerprint)
            for item in violation_rejections:
                prior = rejected.get(item.deactivated_action_ids)
                if prior is None:
                    rejected[item.deactivated_action_ids] = replace(
                        item, triggering_attribution_ids=(attribution.fingerprint,))
                else:
                    rejected[item.deactivated_action_ids] = replace(
                        prior,
                        triggering_attribution_ids=tuple(sorted(
                            {*prior.triggering_attribution_ids, attribution.fingerprint})),
                        direct_action_ids=tuple(sorted(
                            {*prior.direct_action_ids, *item.direct_action_ids})),
                        region_fingerprints=tuple(sorted(
                            {*prior.region_fingerprints, *item.region_fingerprints})),
                    )

        attribution_rows = tuple(sorted(attributions, key=lambda row: row.fingerprint))
        region_rows = tuple(regions_by_fingerprint[key]
                            for key in sorted(regions_by_fingerprint))
        candidate_rows = self._build_candidates(legal_groups, active_ids)
        rejected_rows = tuple(sorted(rejected.values(), key=_candidate_order))
        if not evaluation.hard_failures and evaluation.fidelity_status is not (
                PfFidelityStatus.PF_FIDELITY_WITHIN_TOLERANCE):
            raise AttributionError("threshold status conflicts with an empty HARD violation set")
        payload = {
            "schema_version": "1.0",
            "fidelity_satisfied": not bool(evaluation.hard_failures),
            "hard_failures": tuple(sorted(evaluation.hard_failures)),
            "diagnostic_failures": tuple(sorted(evaluation.diagnostic_failures)),
            "attributions": [row.fingerprint for row in attribution_rows],
            "regions": [row.fingerprint for row in region_rows],
            "candidates": [_candidate_payload(row) for row in candidate_rows],
            "rejected_candidates": [_candidate_payload(row) for row in rejected_rows],
        }
        return Stage3AttributionResult(
            fidelity_satisfied=not bool(evaluation.hard_failures),
            hard_violation_present=bool(evaluation.hard_failures),
            hard_failures=tuple(sorted(evaluation.hard_failures)),
            diagnostic_failures=tuple(sorted(evaluation.diagnostic_failures)),
            attributions=attribution_rows,
            regions=region_rows,
            candidates=candidate_rows,
            rejected_candidates=rejected_rows,
            candidate_set_digest=_digest(payload),
        )

    @staticmethod
    def _validate_evidence(evidence: Stage3AttributionInput) -> None:
        """Require exact bindings between PR-022 metrics, thresholds, and identities."""
        evaluation = evidence.threshold_evaluation
        if evidence.comparison.residuals is None:
            raise AttributionError("PR-022 comparison lacks object-indexed residual evidence")
        if evaluation.metrics_digest != metric_set_digest(evidence.comparison.metrics):
            raise AttributionError("threshold evaluation is bound to different PR-022 metrics")
        _require_digest(evaluation.threshold_digest, "threshold digest")
        _require_digest(evidence.source_comparison_identity, "source comparison identity")
        _require_digest(evidence.target_comparison_identity, "target comparison identity")
        _require_digest(evidence.comparison_mapping.mapping_digest, "comparison mapping digest")
        hard_names = tuple(sorted(row.metric_name for row in evaluation.hard_violations))
        diagnostic_names = tuple(sorted(row.metric_name for row in evaluation.diagnostic_violations))
        if hard_names != tuple(sorted(evaluation.hard_failures)):
            raise AttributionError("HARD violation details differ from threshold evaluation")
        if diagnostic_names != tuple(sorted(evaluation.diagnostic_failures)):
            raise AttributionError("DIAGNOSTIC violation details differ from threshold evaluation")
        if any(row.severity != "HARD" for row in evaluation.hard_violations):
            raise AttributionError("HARD evaluation contains non-HARD details")
        if any(row.severity != "DIAGNOSTIC" for row in evaluation.diagnostic_violations):
            raise AttributionError("DIAGNOSTIC evaluation contains non-DIAGNOSTIC details")
        expected_status = (PfFidelityStatus.PF_FIDELITY_OUTSIDE_TOLERANCE
                           if evaluation.hard_failures else
                           PfFidelityStatus.PF_FIDELITY_WITHIN_TOLERANCE)
        if evaluation.fidelity_status is not expected_status:
            raise AttributionError("threshold result status conflicts with HARD failures")

    @staticmethod
    def _validate_action_catalog(
        evidence: Stage3AttributionInput,
    ) -> tuple[dict[str, ReductionAction], tuple[str, ...]]:
        """Validate action fingerprints, complete dependency DAG, and active closure."""
        actions = {action.action_id: action for action in evidence.actions}
        if len(actions) != len(evidence.actions):
            raise AttributionError("duplicate action ID in Stage-3 catalog")
        for action in evidence.actions:
            if not action.verify_fingerprint():
                raise AttributionError(f"invalid action fingerprint: {action.action_id}")
            missing = set(action.dependencies) - set(actions)
            if missing:
                raise AttributionError(
                    f"action dependency references missing action: {action.action_id}: "
                    + ", ".join(sorted(missing))
                )
        _check_dependency_cycles(actions)
        active = tuple(sorted(evidence.active_action_ids))
        if len(active) != len(set(active)):
            raise AttributionError("active action IDs must be unique")
        unknown = set(active) - set(actions)
        if unknown:
            raise AttributionError("unknown active action IDs: " + ", ".join(sorted(unknown)))
        active_set = set(active)
        for action_id in active:
            if not set(actions[action_id].dependencies) <= active_set:
                raise AttributionError(f"active action dependency closure is incomplete: {action_id}")
        return actions, active

    @staticmethod
    def _validate_mapping_authority(
        evidence: Stage3AttributionInput,
        actions: Mapping[str, ReductionAction],
        active_ids: tuple[str, ...],
    ) -> None:
        """Cross-check derived PF mappings and active deltas against MappingBundle."""
        bundle_bus_rows = {row.source_id: row for row in evidence.mappings.bus_map}
        if len(bundle_bus_rows) != len(evidence.mappings.bus_map):
            raise AttributionError("MappingBundle has duplicate source Bus rows")
        bundle_bus = {source_id: row.target_id
                      for source_id, row in bundle_bus_rows.items()}
        comparison_bus = {row.source_bus_id: row.target_bus_id
                          for row in evidence.comparison_mapping.bus_entries}
        if len(comparison_bus) != len(evidence.comparison_mapping.bus_entries):
            raise AttributionError("PfComparisonMapping has duplicate source Bus rows")
        if bundle_bus != comparison_bus:
            raise AttributionError("PfComparisonMapping conflicts with authoritative MappingBundle Bus map")
        bundle_branch_rows = {row.source_id: row for row in evidence.mappings.branch_map}
        if len(bundle_branch_rows) != len(evidence.mappings.branch_map):
            raise AttributionError("MappingBundle has duplicate source branch rows")
        for row in evidence.comparison_mapping.branch_entries:
            if (row.source_branch_id not in bundle_branch_rows
                    or bundle_branch_rows[row.source_branch_id].target_id != row.target_branch_id):
                raise AttributionError(
                    "PfComparisonMapping conflicts with authoritative MappingBundle branch map"
                )
        for action_id in active_ids:
            action = actions[action_id]
            for transition in action.mapping_delta:
                if transition.source_id.component == "Bus":
                    row = bundle_bus_rows.get(transition.source_id)
                elif transition.source_id.component in {"Line", "Transformer", "Link"}:
                    row = bundle_branch_rows.get(transition.source_id)
                else:
                    row = _device_row(evidence.mappings, transition.source_id)
                if (row is None or row.target_id != transition.target_id
                        or row.mapping_type != transition.mapping_type):
                    raise AttributionError(
                        f"active action mapping delta conflicts with MappingBundle: {action_id}"
                    )

    @staticmethod
    def _localize(
        evidence: Stage3AttributionInput,
        violation: PowerFlowMetricViolation,
        family: MetricFamily,
    ) -> dict[str, tuple[StableId, ...]]:
        """Extract only identity-supported error regions from PR-022 residual evidence."""
        mapping = evidence.comparison_mapping
        residuals = evidence.comparison.residuals
        assert residuals is not None
        source_ids: set[StableId] = set()
        target_ids: set[StableId] = set()
        source_branches: set[StableId] = set()
        target_branches: set[StableId] = set()
        if violation.metric_name in {"vm_max_pu", "vm_rmse_pu"}:
            residual_map = residuals.bus_voltage_by_source_id
            if not residual_map:
                return _empty_local()
            for source_id, signed_error in residual_map.items():
                magnitude = abs(signed_error)
                selected = (magnitude == violation.observed_value
                            if violation.metric_name == "vm_max_pu" else magnitude > 0.0)
                if selected:
                    source_ids.add(source_id)
                    target = _bus_target(mapping, source_id)
                    if target is not None:
                        target_ids.add(target)
            return _local_result(source_ids, target_ids, source_branches, target_branches)
        if violation.metric_name.startswith("angle_"):
            residual_map = residuals.angle_by_source_id
            select_max = violation.metric_name == "angle_max_deg"
            for source_id, signed_error in residual_map.items():
                magnitude = abs(signed_error)
                if (magnitude == violation.observed_value if select_max else magnitude > 0.0):
                    source_ids.add(source_id)
                    target = _bus_target(mapping, source_id)
                    if target is not None:
                        target_ids.add(target)
            return _local_result(source_ids, target_ids, source_branches, target_branches)
        if violation.metric_name.startswith(("branch_p_", "branch_q_")):
            values = (residuals.branch_p_by_target_id
                      if family is MetricFamily.BRANCH_P else
                      residuals.branch_q_by_target_id)
            select_max = violation.metric_name.endswith("_max_mw") or violation.metric_name.endswith("_max_mvar")
            for target_branch, signed_error in values.items():
                magnitude = abs(signed_error)
                if not (magnitude == violation.observed_value if select_max else magnitude > 0.0):
                    continue
                target_branches.add(target_branch)
                for member in mapping.corridor_members.get(target_branch, ()):
                    source_branches.add(member.source_branch_id)
                    source_ids.update((member.source_bus0, member.source_bus1))
                    target_ids.update(member.endpoint_target_buses)
                target_ids.add(target_branch)
            return _local_result(source_ids, target_ids, source_branches, target_branches)
        if violation.metric_name in {"cutset_p_max_mw", "cutset_q_max_mvar"}:
            values = (residuals.cutset_p_by_target_bus_id
                      if family is MetricFamily.CUTSET_P else
                      residuals.cutset_q_by_target_bus_id)
            selected_clusters = {cluster for cluster, value in values.items()
                                 if abs(value) == violation.observed_value}
            if not selected_clusters:
                return _empty_local()
            for bus in mapping.bus_entries:
                if bus.target_bus_id in selected_clusters:
                    source_ids.add(bus.source_bus_id)
                    target_ids.add(bus.target_bus_id)
            for cluster in selected_clusters:
                for entry in mapping.cutsets_by_cluster.get(cluster, ()):
                    source_branches.add(entry.source_branch_id)
                    target_branches.add(entry.target_branch_id)
            return _local_result(source_ids, target_ids | target_branches,
                                 source_branches, target_branches)
        if violation.metric_name.startswith("injection_"):
            is_active = violation.metric_name.startswith("injection_p_")
            values = (residuals.injection_p_by_target_bus_id if is_active else
                      residuals.injection_q_by_target_bus_id)
            select_max = violation.metric_name.endswith("_max_mw") or violation.metric_name.endswith("_max_mvar")
            for target_bus, signed_error in values.items():
                magnitude = abs(signed_error)
                if not (magnitude == violation.observed_value if select_max else magnitude > 0.0):
                    continue
                target_ids.add(target_bus)
                source_ids.update(entry.source_bus_id for entry in mapping.bus_entries
                                  if entry.target_bus_id == target_bus)
            return _local_result(source_ids, target_ids, source_branches, target_branches)
        # Passive losses and all current DIAGNOSTIC-only families remain evidence,
        # but aggregate summaries alone do not justify a local expansion region.
        return _empty_local()

    @staticmethod
    def _related_actions(
        actions: Mapping[str, ReductionAction], active_ids: tuple[str, ...],
        affected_source_ids: tuple[StableId, ...],
    ) -> tuple[ReductionAction, ...]:
        """Find active actions intersecting identity-supported error objects."""
        affected = set(affected_source_ids)
        return tuple(actions[action_id] for action_id in active_ids
                     if affected & set(actions[action_id].source_ids))

    @staticmethod
    def _ordinary_action_eligible(action: ReductionAction) -> bool:
        """Exclude protected, irreversible, and unsupported actions from legal candidates."""
        return action.expansion_eligible and action.operation in _STAGE2_OPERATIONS

    @staticmethod
    def _closure_rejection(actions: tuple[ReductionAction, ...]) -> str | None:
        """Return an explicit reason if a prerequisite action cannot be reversed."""
        blocked = tuple(action.action_id for action in actions
                        if not ErrorAttributionEngine._ordinary_action_eligible(action))
        if blocked:
            return "dependency closure contains protected, irreversible, or unsupported actions: " + ", ".join(blocked)
        return None

    @staticmethod
    def _regions(
        local: Mapping[str, tuple[StableId, ...]],
        related: tuple[ReductionAction, ...],
    ) -> tuple[ErrorRegion, ...]:
        """Group local evidence by stable voltage-layer/component action semantics."""
        groups: dict[tuple[str | None, str | None], list[ReductionAction]] = defaultdict(list)
        for action in related:
            groups[(action.voltage_layer, action.component_id)].append(action)
        if not groups:
            groups[(None, None)] = []
        regions: list[ErrorRegion] = []
        for (layer, component), group in sorted(groups.items(), key=lambda item: item[0]):
            regions.append(ErrorRegion.create(
                region_type="cutset_corridor" if local["source_branch_ids"] else "bus_cluster",
                source_bus_ids=local["source_ids"],
                source_branch_ids=local["source_branch_ids"],
                target_bus_ids=tuple(item for item in local["target_ids"]
                                      if item.component == "Bus"),
                target_branch_ids=local["target_branch_ids"],
                action_ids=tuple(action.action_id for action in group),
                voltage_layer=layer,
                component_id=component,
            ))
        return tuple(sorted(regions, key=lambda item: item.fingerprint))

    @staticmethod
    def _unattributable_reason(
        family: MetricFamily, related: tuple[ReductionAction, ...],
        local: Mapping[str, tuple[StableId, ...]],
    ) -> str:
        """Explain why one HARD violation has no legal local action."""
        if _FAMILY_SUPPORT[family] is MetricSupport.UNATTRIBUTABLE_BY_STAGE3_DESIGN:
            return "aggregate metric has no justified object-level localization in Stage 3"
        if not local["source_ids"] and not local["source_branch_ids"]:
            return "PR-022 evidence lacks sufficient StableId provenance for localization"
        if related:
            return "all related actions are protected, irreversible, unsupported, or have blocked dependencies"
        return "no active supported reduction action intersects the attributable region"

    @staticmethod
    def _rejected_candidate(
        direct_id: str, closure: tuple[str, ...], active_ids: tuple[str, ...],
        regions: tuple[ErrorRegion, ...], reason: str,
        closure_actions: tuple[ReductionAction, ...],
    ) -> ExpansionCandidate:
        """Retain deterministic evidence for a dependency-blocked reversal proposal."""
        added_buses, added_branches = _added_objects(closure_actions)
        return ExpansionCandidate(
            candidate_fingerprint=_candidate_fingerprint(closure),
            region_fingerprint=regions[0].fingerprint if regions else _digest({"empty": True}),
            deactivated_action_ids=closure,
            resulting_active_action_ids=tuple(sorted(set(active_ids) - set(closure))),
            added_bus_count=added_buses,
            added_branch_count=added_branches,
            triggering_attribution_ids=(),
            direct_action_ids=(direct_id,),
            dependency_closure_action_ids=closure,
            region_fingerprints=tuple(sorted(row.fingerprint for row in regions)),
            eligible=False,
            rejection_reason=reason,
        )

    @staticmethod
    def _build_candidates(
        groups: Mapping[tuple[str, ...], Mapping[str, object]],
        active_ids: tuple[str, ...],
    ) -> tuple[ExpansionCandidate, ...]:
        """Merge candidates by canonical dependency-closed action set."""
        output: list[ExpansionCandidate] = []
        for closure, group in sorted(groups.items()):
            closure_actions = group["closure_actions"]
            assert isinstance(closure_actions, tuple)
            added_buses, added_branches = _added_objects(closure_actions)
            fingerprints = tuple(sorted(group["regions"]))
            if not fingerprints:
                raise AttributionError("legal expansion candidate has no ErrorRegion")
            output.append(ExpansionCandidate(
                candidate_fingerprint=_candidate_fingerprint(closure),
                region_fingerprint=fingerprints[0],
                deactivated_action_ids=closure,
                resulting_active_action_ids=tuple(sorted(set(active_ids) - set(closure))),
                added_bus_count=added_buses,
                added_branch_count=added_branches,
                triggering_attribution_ids=tuple(sorted(group["triggers"])),
                direct_action_ids=tuple(sorted(group["direct"])),
                dependency_closure_action_ids=closure,
                region_fingerprints=fingerprints,
                eligible=True,
            ))
        return tuple(sorted(output, key=_candidate_order))


def _metric_family(metric_name: str) -> MetricFamily:
    """Map the frozen PR-022 metric vocabulary to its explicit family."""
    if metric_name.startswith("vm_"):
        return MetricFamily.BUS_VM
    if metric_name.startswith("angle_"):
        return MetricFamily.BUS_ANGLE
    if metric_name.startswith("branch_p_"):
        return MetricFamily.BRANCH_P
    if metric_name.startswith("branch_q_"):
        return MetricFamily.BRANCH_Q
    if metric_name.startswith("cutset_p_"):
        return MetricFamily.CUTSET_P
    if metric_name.startswith("cutset_q_"):
        return MetricFamily.CUTSET_Q
    if metric_name.startswith("injection_"):
        return MetricFamily.INJECTION_PQ
    if metric_name.startswith("loss_"):
        return MetricFamily.PASSIVE_LOSS
    raise AttributionError(f"PR-022 metric has no Stage-3 family mapping: {metric_name}")


def _bus_target(mapping: PfComparisonMapping, source_id: StableId) -> StableId | None:
    """Return a target Bus only when exactly one authoritative mapping row exists."""
    matches = [entry.target_bus_id for entry in mapping.bus_entries
               if entry.source_bus_id == source_id]
    if len(matches) != 1:
        return None
    return matches[0]


def _local_result(
    source_ids: Iterable[StableId], target_ids: Iterable[StableId],
    source_branches: Iterable[StableId], target_branches: Iterable[StableId],
) -> dict[str, tuple[StableId, ...]]:
    """Return sorted unique identities for the localized region contract."""
    return {
        "source_ids": tuple(sorted(set(source_ids), key=StableId.encode)),
        "target_ids": tuple(sorted(set(target_ids), key=StableId.encode)),
        "source_branch_ids": tuple(sorted(set(source_branches), key=StableId.encode)),
        "target_branch_ids": tuple(sorted(set(target_branches), key=StableId.encode)),
    }


def _empty_local() -> dict[str, tuple[StableId, ...]]:
    """Return an empty, canonical localization result."""
    return _local_result((), (), (), ())


def _dependency_closure(
    action_id: str, actions: Mapping[str, ReductionAction],
) -> tuple[str, ...]:
    """Return one action and all active prerequisite actions in canonical order."""
    output: set[str] = set()
    pending = [action_id]
    while pending:
        current = pending.pop()
        if current in output:
            continue
        output.add(current)
        pending.extend(actions[current].dependencies)
    return tuple(sorted(output))


def _check_dependency_cycles(actions: Mapping[str, ReductionAction]) -> None:
    """Reject cyclic dependency graphs independently of active-action order."""
    state: dict[str, int] = {}

    def visit(action_id: str) -> None:
        mark = state.get(action_id, 0)
        if mark == 1:
            raise AttributionError(f"action dependency cycle includes {action_id}")
        if mark == 2:
            return
        state[action_id] = 1
        for dependency in actions[action_id].dependencies:
            visit(dependency)
        state[action_id] = 2

    for action_id in sorted(actions):
        visit(action_id)


def _device_row(mappings: MappingBundle, source_id: StableId) -> MapEntry | None:
    """Return one unique authoritative device row, or None when absent/ambiguous."""
    rows = [row for row in mappings.device_map if row.source_id == source_id]
    if len(rows) != 1:
        return None
    return rows[0]


def _added_objects(actions: tuple[ReductionAction, ...]) -> tuple[int, int]:
    """Count unique source buses and branches reintroduced by action reversal."""
    buses: set[StableId] = set()
    branches: set[StableId] = set()
    for action in actions:
        for transition in action.mapping_delta:
            if transition.source_id.component == "Bus" and transition.target_id != transition.source_id:
                buses.add(transition.source_id)
            elif transition.source_id.component in {"Line", "Transformer", "Link"} and (
                    transition.target_id is None or transition.target_id != transition.source_id):
                branches.add(transition.source_id)
    return len(buses), len(branches)


def _candidate_fingerprint(action_ids: tuple[str, ...]) -> str:
    """Fingerprint a deactivation set independent of trigger or processing order."""
    return _digest({"schema_version": "1.0", "deactivated_action_ids": tuple(sorted(action_ids))})


def _candidate_order(candidate: ExpansionCandidate) -> tuple[str, str, tuple[str, ...]]:
    """Return stable evidence ordering without implying candidate selection."""
    return (candidate.candidate_fingerprint, candidate.region_fingerprint,
            candidate.deactivated_action_ids)


def _candidate_payload(candidate: ExpansionCandidate) -> dict[str, object]:
    """Return canonical Stage-3 candidate evidence without runtime information."""
    return {
        "candidate_fingerprint": candidate.candidate_fingerprint,
        "triggering_attribution_ids": candidate.triggering_attribution_ids,
        "direct_action_ids": candidate.direct_action_ids,
        "dependency_closure_action_ids": candidate.dependency_closure_action_ids,
        "deactivated_action_ids": candidate.deactivated_action_ids,
        "region_fingerprints": candidate.region_fingerprints,
        "added_bus_count": candidate.added_bus_count,
        "added_branch_count": candidate.added_branch_count,
        "eligible": candidate.eligible,
        "rejection_reason": candidate.rejection_reason,
    }


def _require_digest(value: str | None, label: str) -> None:
    """Require a lowercase SHA-256 string for every compared evidence identity."""
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise AttributionError(f"{label} must be a lowercase SHA-256 digest")


def _digest(payload: object) -> str:
    """Hash a canonical JSON payload without runtime or row-order data."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
