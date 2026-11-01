"""PyPSA adapter binding Stage 2, PR-022 fidelity, and Stage 3 attribution."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping

import networkx as nx
import pypsa

from psdcalc.reduction.adaptive.actions import ReductionAction
from psdcalc.reduction.adaptive.attribution import (
    ErrorAttributionEngine,
    Stage3AttributionInput,
)
from psdcalc.reduction.adaptive.candidates import (
    BuiltCandidate,
    CandidateReconstructionError,
    PypsaAdaptiveCandidateBuilder,
)
from psdcalc.reduction.adaptive.controller import (
    AdaptiveCandidateSnapshot,
    AdaptiveFidelityEvidence,
    RuntimePowerFlowOutcome,
)
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.config.models import ReductionConfig
from psdcalc.reduction.fidelity.contracts import (
    PowerFlowMetricPolicy,
    PowerFlowOperatingPointContract,
)
from psdcalc.reduction.fidelity.mapping import (
    PfBranchEndpoints,
    derive_pf_comparison_mapping,
)
from psdcalc.reduction.fidelity.metrics import (
    compare_power_flow_results,
    evaluate_metric_thresholds_detailed,
    metric_policy_digest,
    metric_set_digest,
    operating_point_digest,
    reference_cutset_magnitudes,
)
from psdcalc.reduction.fidelity.persistence import (
    FrozenThresholdSet,
    canonical_model_digest,
)
from psdcalc.reduction.fidelity.pypsa_executor import (
    PowerFlowExecutionStatus,
    PowerFlowExecutionResult,
    PypsaAcPowerFlowExecutor,
)
from psdcalc.reduction.protection.engine import ProtectionSet


class PypsaAdaptiveRuntime:
    """Run adaptive candidates through the existing PyPSA and PR-022 layers.

    This adapter owns no structural or fidelity rules. Stage-2 reconstruction
    returns preliminary structural and AC-readiness evidence; the existing
    PyPSA executor, mapping derivation, metric evaluator, frozen threshold
    artifact, and Stage-3 attribution engine provide the remaining outcomes.
    """

    rebuilds_from_s1 = True

    def __init__(
        self,
        *,
        source_network: pypsa.Network,
        source_sidecar: IdentitySidecar,
        builder: PypsaAdaptiveCandidateBuilder,
        actions: tuple[ReductionAction, ...],
        reduction_config_digest: str,
        operating_point: PowerFlowOperatingPointContract,
        metric_policy: PowerFlowMetricPolicy,
        threshold_set: FrozenThresholdSet | None = None,
        executor: PypsaAcPowerFlowExecutor | None = None,
        attribution_engine: ErrorAttributionEngine | None = None,
    ) -> None:
        """Bind immutable run inputs and optionally supplied existing components."""
        self._source = source_network
        self._source_sidecar = source_sidecar
        self._builder = builder
        self._actions = tuple(sorted(actions, key=lambda item: item.action_id))
        self._operating_point = operating_point
        self._metric_policy = metric_policy
        self._thresholds = threshold_set or FrozenThresholdSet.load()
        self._executor = executor or PypsaAcPowerFlowExecutor()
        self._attribution = attribution_engine or ErrorAttributionEngine()
        self.source_model_digest = canonical_model_digest(source_network, source_sidecar)
        self.source_sidecar_digest = builder.source_sidecar_digest
        self.action_catalog_digest = _action_catalog_digest(self._actions)
        self.reduction_config_digest = reduction_config_digest
        self.operating_point_digest = operating_point_digest(operating_point)
        self.solver_contract_digest = _digest({
            "solver": operating_point.solver,
            "tolerance": operating_point.tolerance,
            "max_iterations": operating_point.max_iterations,
            "distribute_slack": operating_point.distribute_slack,
            "slack_weights": operating_point.slack_weights,
            "use_seed": operating_point.use_seed,
            "control_policy": operating_point.control_policy,
            "tap_policy": operating_point.tap_policy,
            "phase_shift_policy": operating_point.phase_shift_policy,
        })
        self.metric_policy_digest = metric_policy_digest(metric_policy)
        self.threshold_set_id = str(self._thresholds.payload["threshold_set_id"])
        self.threshold_digest = self._thresholds.sha256
        self.source_bus_count = len(source_network.buses)
        self.source_branch_count = len(source_network.lines) + len(source_network.transformers) + len(source_network.links)
        if self.source_bus_count < 1:
            raise CandidateReconstructionError("adaptive source network must contain at least one Bus")
        if builder.source_model_digest != self.source_model_digest:
            raise CandidateReconstructionError("candidate builder is bound to a different S1 network")
        if builder.source_sidecar_digest != self.source_sidecar_digest:
            raise CandidateReconstructionError("candidate builder is bound to a different S1 sidecar")
        if builder.action_catalog_digest != self.action_catalog_digest:
            raise CandidateReconstructionError("candidate builder is bound to a different action catalog")
        if builder.reduction_config_digest != self.reduction_config_digest:
            raise CandidateReconstructionError("candidate builder is bound to a different reduction config")

    @property
    def reduction_config(self) -> ReductionConfig:
        """Expose the frozen resolved config for durable run evidence."""
        return self._builder.reduction_config

    @property
    def protection_set(self) -> ProtectionSet:
        """Expose the effective source-ID protection contract for replay."""
        return self._builder.protection_set

    @property
    def operating_point(self) -> PowerFlowOperatingPointContract:
        """Return the immutable source/target PF contract used by this runtime."""
        return self._operating_point

    @property
    def metric_policy(self) -> PowerFlowMetricPolicy:
        """Return the immutable PR-022 comparison metric policy."""
        return self._metric_policy

    @property
    def threshold_set(self) -> FrozenThresholdSet:
        """Return the frozen PR-022 threshold artifact and verified raw bytes."""
        return self._thresholds

    def build(self, active_action_ids: tuple[str, ...]) -> AdaptiveCandidateSnapshot:
        """Rebuild through Stage 2 and expose its existing structural/readiness Gates."""
        active = tuple(sorted(active_action_ids))
        try:
            built = self._builder.build(active)
        except CandidateReconstructionError as exc:
            action_set_fingerprint = _digest({"schema_version": "1.0", "active_action_ids": active})
            failure_digest = _digest({"active_action_ids": active, "failure": str(exc)})
            return AdaptiveCandidateSnapshot(
                candidate_fingerprint=failure_digest,
                active_action_ids=active,
                action_set_fingerprint=action_set_fingerprint,
                model_digest=None,
                mapping_digest=None,
                structural_status="STRUCTURAL_INVALID",
                structural_checks={"candidate_reconstruction": False},
                readiness_status="NOT_RUN",
                readiness_checks={},
                bus_count=None,
                branch_count=None,
                failure_reason=str(exc),
            )
        if built.identity.reduction_config_digest != self.reduction_config_digest:
            raise CandidateReconstructionError("Stage-2 candidate used a different frozen reduction config")
        structural_checks = dict(built.structural_result.checks)
        structural_status = (
            "STRUCTURALLY_VALID" if structural_checks and all(structural_checks.values())
            else "STRUCTURAL_INVALID"
        )
        readiness_checks = dict(built.ac_readiness_result.checks)
        return AdaptiveCandidateSnapshot(
            candidate_fingerprint=built.identity.candidate_fingerprint,
            active_action_ids=active,
            action_set_fingerprint=built.identity.action_set_fingerprint,
            model_digest=built.identity.model_digest,
            mapping_digest=built.identity.mapping_digest,
            structural_status=structural_status,
            structural_checks=structural_checks,
            readiness_status=built.ac_readiness_result.status.value,
            readiness_checks=readiness_checks,
            bus_count=len(built.network.buses),
            branch_count=len(built.network.lines) + len(built.network.transformers) + len(built.network.links),
            artifact=built,
            failure_reason=(None if structural_status == "STRUCTURALLY_VALID" else
                            "preliminary structural checks did not all pass"),
        )

    def solve_source(self) -> RuntimePowerFlowOutcome:
        """Run the existing AC PF executor on frozen S1."""
        return self._solve(self._source, self._source_sidecar)

    def solve_candidate(self, candidate: AdaptiveCandidateSnapshot) -> RuntimePowerFlowOutcome:
        """Run the existing AC PF executor on one gated Stage-2 candidate."""
        if not isinstance(candidate.artifact, BuiltCandidate):
            return RuntimePowerFlowOutcome(
                "PF_EXECUTION_FAILED", False, "candidate artifact is unavailable")
        return self._solve(candidate.artifact.network, candidate.artifact.sidecar)

    def assess(
        self,
        candidate: AdaptiveCandidateSnapshot,
        source_pf: RuntimePowerFlowOutcome,
        target_pf: RuntimePowerFlowOutcome,
    ) -> AdaptiveFidelityEvidence:
        """Reuse PR-022 comparison/thresholds and Stage-3 error attribution."""
        built = candidate.artifact
        source_result = source_pf.evidence
        target_result = target_pf.evidence
        if (not isinstance(built, BuiltCandidate)
                or not isinstance(source_result, PowerFlowExecutionResult)
                or not isinstance(target_result, PowerFlowExecutionResult)):
            raise CandidateReconstructionError("fidelity assessment lacks successful built/PF evidence")
        mapping = derive_pf_comparison_mapping(
            built.mappings,
            source_bus_ids=_bus_ids(self._source, self._source_sidecar),
            target_bus_ids=_bus_ids(built.network, built.sidecar),
            source_branch_endpoints=_branch_endpoints(self._source, self._source_sidecar),
            target_branch_endpoints=_branch_endpoints(built.network, built.sidecar),
            source_component_by_bus=_component_map(self._source, self._source_sidecar),
            target_component_by_bus=_component_map(built.network, built.sidecar),
        )
        comparison = compare_power_flow_results(
            source_result, target_result, mapping, self._metric_policy)
        reference_p, reference_q = reference_cutset_magnitudes(source_result, mapping)
        resolved = self._thresholds.resolve(reference_p, reference_q)
        threshold_evaluation = evaluate_metric_thresholds_detailed(
            comparison.metrics, resolved.thresholds, threshold_digest=resolved.digest)
        attribution = self._attribution.run(Stage3AttributionInput(
            comparison=comparison,
            threshold_evaluation=threshold_evaluation,
            comparison_mapping=mapping,
            mappings=built.mappings,
            actions=self._actions,
            active_action_ids=candidate.active_action_ids,
            source_comparison_identity=_digest({
                "model": self.source_model_digest,
                "sidecar": self.source_sidecar_digest,
                "operating_point": self.operating_point_digest,
            }),
            target_comparison_identity=built.identity.model_digest,
        ))
        return AdaptiveFidelityEvidence(
            fidelity_status=threshold_evaluation.fidelity_status.value,
            attribution=attribution,
            metrics_digest=metric_set_digest(comparison.metrics),
            threshold_digest=resolved.digest,
        )

    def _solve(self, network: pypsa.Network, sidecar: IdentitySidecar) -> RuntimePowerFlowOutcome:
        """Adapt one existing PyPSA executor result without translating PF metrics."""
        result = self._executor.solve(network, sidecar, self._operating_point)
        if result.status is PowerFlowExecutionStatus.PF_CONVERGED:
            return RuntimePowerFlowOutcome("PF_CONVERGED", True, evidence=result)
        return RuntimePowerFlowOutcome(
            result.status.value, False, result.failure_reason or result.status.value,
            evidence=result,
        )


def _bus_ids(network: pypsa.Network, sidecar: IdentitySidecar) -> tuple[StableId, ...]:
    """Return canonical Bus identities from the authoritative sidecar."""
    return tuple(sorted((sidecar.source_id("Bus", str(name)) for name in network.buses.index),
                        key=StableId.encode))


def _branch_endpoints(network: pypsa.Network,
                      sidecar: IdentitySidecar) -> tuple[PfBranchEndpoints, ...]:
    """Build ordered two-terminal branch views from public PyPSA static tables."""
    rows: list[PfBranchEndpoints] = []
    for component_name, frame in (("Line", network.lines),
                                  ("Transformer", network.transformers),
                                  ("Link", network.links)):
        for name, row in frame.iterrows():
            rows.append(PfBranchEndpoints(
                branch_id=sidecar.source_id(component_name, str(name)),
                component=component_name,
                bus0=sidecar.source_id("Bus", str(row.bus0)),
                bus1=sidecar.source_id("Bus", str(row.bus1)),
            ))
    return tuple(sorted(rows, key=lambda row: row.branch_id.encode()))


def _component_map(network: pypsa.Network,
                   sidecar: IdentitySidecar) -> Mapping[StableId, str]:
    """Label active AC subnetworks by a stable hash of their Bus identities."""
    graph = nx.Graph()
    name_to_id = {str(name): sidecar.source_id("Bus", str(name)) for name in network.buses.index}
    graph.add_nodes_from(name_to_id.values())
    for frame in (network.lines, network.transformers):
        for _, row in frame.iterrows():
            if bool(row.get("active", True)):
                graph.add_edge(name_to_id[str(row.bus0)], name_to_id[str(row.bus1)])
    result: dict[StableId, str] = {}
    for component in nx.connected_components(graph):
        fingerprint = _digest([item.encode() for item in sorted(component, key=StableId.encode)])
        result.update((bus_id, fingerprint) for bus_id in component)
    return result


def _action_catalog_digest(actions: tuple[ReductionAction, ...]) -> str:
    """Hash the ordered complete action catalog using Stage-4 canonical shape."""
    return _digest({
        "schema_version": "1.0",
        "actions": [row.canonical_payload() for row in sorted(actions, key=lambda item: item.action_id)],
    })


def _digest(value: object) -> str:
    """Return one deterministic JSON SHA-256 digest."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
