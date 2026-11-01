"""Durable Stage-5 adaptive evidence and clean-process replay tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pypsa
import pytest

from psdcalc.reduction.adaptive.actions import (
    ActionOperation,
    MappingTransition,
    ReductionAction,
)
from psdcalc.reduction.adaptive.candidates import PypsaAdaptiveCandidateBuilder
from psdcalc.reduction.adaptive.controller import AdaptiveReductionController
from psdcalc.reduction.adaptive.contracts import (
    AdaptiveReductionConfig,
    AdaptiveStoppingReason,
    AttributionStatus,
    CandidateEvaluation,
    CompressionRequirement,
    ErrorAttribution,
    ExpansionCandidate,
    SCHEMA_VERSION,
    _digest as _contract_digest,
)
from psdcalc.reduction.adaptive.evidence import (
    AdaptiveEvidenceError,
    AdaptiveReductionEvidenceStore,
    _digest as _evidence_digest,
    _validate_candidate_evaluation_rows,
)
from psdcalc.reduction.adaptive.journal import ReductionActionJournal
from psdcalc.reduction.adaptive.pypsa_runtime import PypsaAdaptiveRuntime
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.loader import load_config
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.fidelity.contracts import (
    PowerFlowMetricPolicy,
    PowerFlowOperatingPointContract,
)
from psdcalc.reduction.fidelity.metrics import operating_point_digest
from psdcalc.reduction.fidelity.persistence import FrozenThresholdSet
from psdcalc.reduction.protection.engine import ProtectionSet


def _digest(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def _case(
    tmp_path: Path,
    *,
    max_bus_count: int | None = None,
    improvement_tolerance: float = 1e-12,
    reversible: bool = True,
    protected: bool = False,
    line_r: float = 0.0,
    line_x: float = 5.0,
    load_q: float = 50.0,
    max_iterations: int = 3,
):
    network = pypsa.Network()
    network.add("Bus", "slack", v_nom=110.0)
    network.add("Bus", "middle", v_nom=110.0)
    network.add("Bus", "load", v_nom=110.0)
    network.add("Line", "slack-middle", bus0="slack", bus1="middle", r=0.0, x=0.1)
    network.add("Line", "line", bus0="middle", bus1="load", r=line_r, x=line_x)
    network.add("Generator", "source", bus="slack", control="Slack", p_nom=100.0)
    network.add("Load", "demand", bus="load", p_set=0.0, q_set=load_q)
    network.set_snapshots(["snapshot-0"])
    network.loads_t.p_set["demand"] = [3.0]
    network.loads_t.q_set["demand"] = [load_q]
    sidecar = PypsaAdapter().identity_sidecar(network, "stage5-synthetic")
    reduction = load_config({
        "schema_version": "0.1.0",
        "run": {"id": "stage5", "seed": 7, "output_dir": "stage5-output"},
        "input": {"backend": "pypsa", "path": "frozen-s1.nc", "format": "netcdf",
                  "source_namespace": "stage5-synthetic"},
        "task": {"type": "planning", "profile": "planning_aggregation",
                 "reduction_role": "candidate", "final_authority": "original"},
        "pipeline": {"materialization_family": "multi_voltage_quotient"},
        "simplification": {"operations": [{
            "id": "topology", "type": "voltage_aware_topology", "enabled": True,
            "mode": "keep_transformer_interface",
        }]},
        "partition": {"planner": "pypsa_kmeans", "target": {"n_clusters": 1},
                       "connectivity_policy": "reject"},
        "aggregation": {"backend": "pypsa"},
    })
    empty_protection = ProtectionSet(frozenset(), frozenset(), frozenset(),
                                     frozenset(), frozenset())
    bus_slack = sidecar.source_id("Bus", "slack")
    bus_middle = sidecar.source_id("Bus", "middle")
    bus_load = sidecar.source_id("Bus", "load")
    line = sidecar.source_id("Line", "line")
    action = ReductionAction.create(
        action_id="merge-radial-bus", operation=ActionOperation.RADIAL_ABSORPTION,
        source_ids=(bus_middle, bus_load, line), target_ids=(bus_middle,),
        mapping_delta=(
            MappingTransition(bus_middle, bus_middle, "preserved"),
            MappingTransition(bus_load, bus_middle, "clustered"),
            MappingTransition(line, None, "absorbed_internal"),
        ), voltage_layer="110", component_id="synthetic-component",
        region_fingerprint=_digest("synthetic-region"),
        reversible=reversible,
        protected=protected,
        protection_reasons=("explicit protection",) if protected else (),
        inverse_semantics=("rebuild_from_s1_with_action_deactivated" if reversible else ""),
        audit_reason="synthetic Stage-5 radial contraction",
    )
    actions: tuple[ReductionAction, ...] = (action,)
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=network, source_sidecar=sidecar, actions=actions,
        reduction_config=reduction, protection=empty_protection,
    )
    thresholds = FrozenThresholdSet.load()
    operating_point = PowerFlowOperatingPointContract(
        snapshots=("snapshot-0",), solver="pypsa.newton_raphson", tolerance=1e-8, max_iterations=None,
        distribute_slack=False, slack_weights=None, use_seed=False,
        control_policy="preserve_source_controls", tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest=_digest("topology"), device_schedule_digest=_digest("schedule"),
    )
    metric_policy = PowerFlowMetricPolicy(1e-6, 1e-6, 1e-6)
    runtime = PypsaAdaptiveRuntime(
        source_network=network, source_sidecar=sidecar, builder=builder, actions=actions,
        reduction_config_digest=builder.reduction_config_digest,
        operating_point=operating_point, metric_policy=metric_policy,
        threshold_set=thresholds,
    )
    config = AdaptiveReductionConfig(
        schema_version=SCHEMA_VERSION,
        reduction_config_digest=runtime.reduction_config_digest,
        operating_point_digest=runtime.operating_point_digest,
        solver_contract_digest=runtime.solver_contract_digest,
        metric_policy_digest=runtime.metric_policy_digest,
        threshold_set_id=runtime.threshold_set_id, threshold_digest=runtime.threshold_digest,
        max_iterations=max_iterations, cost_bus_weight=1.0, cost_branch_weight=1.0,
        compression_requirement=(CompressionRequirement(max_bus_count=max_bus_count)
                                 if max_bus_count is not None else None),
        improvement_tolerance=improvement_tolerance,
    )
    report = AdaptiveReductionController().run(
        runtime=runtime, actions=actions, initial_active_action_ids=(action.action_id,), config=config,
    )
    return network, sidecar, reduction, empty_protection, actions, runtime, config, report, (action.action_id,)


def _persist(tmp_path: Path, *, run_id: str = "adaptive-stage5", **case_kwargs):
    case = _case(tmp_path, **case_kwargs)
    return _persist_case(tmp_path, run_id=run_id, case=case)


def _persist_case(tmp_path: Path, *, run_id: str, case):
    store = ArtifactStore(tmp_path / "runs", run_id)
    evidence_store = AdaptiveReductionEvidenceStore(store)
    evidence_store.persist(
        runtime=case[5], reduction_config=case[2], adaptive_config=case[6],
        initial_active_action_ids=case[8], report=case[7],
    )
    return store.run_dir, case


def test_persisted_adaptive_run_replays_in_fresh_process(tmp_path: Path) -> None:
    run_dir, case_data = _persist(tmp_path)
    assert case_data[7].selected_candidate_sequence
    assert case_data[7].iterations[0].expansion_decision.candidate_evaluations
    code = (
        "from pathlib import Path; "
        "from psdcalc.reduction.adaptive.evidence import AdaptiveReductionEvidenceStore; "
        "r = AdaptiveReductionEvidenceStore.replay(Path(__import__('sys').argv[1])); "
        "print(r.verified, r.report_digest)"
    )
    env = dict(os.environ)
    source_root = str(Path(__file__).parents[1] / "src")
    env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run([sys.executable, "-c", code, str(run_dir)], env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("True ")


def test_dependency_chain_candidate_journal_and_clean_replay_agree(tmp_path: Path) -> None:
    """Persisted expansion uses the same dependent closure as Stage 3 and replay."""
    original = _case(tmp_path)
    network, sidecar, reduction, protection, original_actions = original[:5]
    prerequisite = original_actions[0]
    dependent = ReductionAction.create(
        action_id="dependent-radial-action",
        operation=prerequisite.operation,
        source_ids=prerequisite.source_ids,
        target_ids=prerequisite.target_ids,
        mapping_delta=prerequisite.mapping_delta,
        voltage_layer=prerequisite.voltage_layer,
        component_id=prerequisite.component_id,
        region_fingerprint=prerequisite.region_fingerprint,
        dependencies=(prerequisite.action_id,),
        audit_reason="synthetic dependency-chain replay",
    )
    actions = (dependent, prerequisite)
    initial = tuple(sorted(action.action_id for action in actions))
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=network, source_sidecar=sidecar, actions=actions,
        reduction_config=reduction, protection=protection,
    )
    assert builder.build(initial).structural_result.checks
    runtime = PypsaAdaptiveRuntime(
        source_network=network, source_sidecar=sidecar, builder=builder, actions=actions,
        reduction_config_digest=builder.reduction_config_digest,
        operating_point=original[5].operating_point,
        metric_policy=original[5].metric_policy,
        threshold_set=original[5].threshold_set,
    )
    config = original[6]
    report = AdaptiveReductionController().run(
        runtime=runtime, actions=actions,
        initial_active_action_ids=initial, config=config,
    )
    decision = report.iterations[0].expansion_decision
    assert decision is not None
    candidate = next(row.candidate for row in decision.candidate_evaluations
                     if row.candidate.direct_action_ids == (prerequisite.action_id,))
    journal = ReductionActionJournal.start(actions).deactivate(
        candidate.direct_action_ids, reason="test replay closure")
    closure = tuple(sorted(set(initial) - set(journal.active_action_ids)))
    assert candidate.deactivated_action_ids == closure == initial
    assert candidate.resulting_active_action_ids == journal.active_action_ids == ()
    assert report.iterations[0].action_set_delta == closure

    run_dir, _ = _persist_case(
        tmp_path, run_id="adaptive-dependency-chain",
        case=(network, sidecar, reduction, protection, actions, runtime, config, report, initial),
    )
    code = (
        "import json, sys; "
        "from psdcalc.reduction.adaptive.evidence import AdaptiveReductionEvidenceStore; "
        "r = AdaptiveReductionEvidenceStore.replay(sys.argv[1]); "
        "print(json.dumps({'verified': r.verified, "
        "'delta': r.report.iterations[0].action_set_delta}))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src") + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.run([sys.executable, "-c", code, str(run_dir)], env=env,
                             capture_output=True, text=True, check=False)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {"verified": True, "delta": list(closure)}


@pytest.mark.parametrize("relative", [
    "adaptive/frozen_s1.nc",
    "adaptive/source_sidecar.json",
    "adaptive/reduction_config.json",
    "adaptive/adaptive_config.json",
    "adaptive/operating_point.json",
    "adaptive/metric_policy.json",
    "adaptive/protection.json",
    "adaptive/threshold_set.json",
    "adaptive/action_catalog.json",
    "adaptive/initial_action_set.json",
    "adaptive/journal.json",
    "adaptive/iterations.json",
    "adaptive/iteration_bindings.json",
    "adaptive/candidate_evaluations.json",
    "adaptive/report.json",
])
def test_tampered_adaptive_evidence_fails_closed(tmp_path: Path, relative: str) -> None:
    run_dir, _case_data = _persist(tmp_path)
    path = run_dir / relative
    if relative.endswith(".nc"):
        network = PypsaAdapter().load(path, "netcdf")
        network.buses.loc[network.buses.index[0], "v_nom"] = 220.0
        PypsaAdapter().export(network, path, "netcdf")
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            value["tampered"] = True
        else:
            value.append({"tampered": True})
        path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    with pytest.raises(AdaptiveEvidenceError):
        AdaptiveReductionEvidenceStore.replay(run_dir)


def test_semantic_digest_is_stable_across_run_directories(tmp_path: Path) -> None:
    first_dir, _ = _persist(tmp_path / "one", run_id="adaptive-one")
    second_dir, _ = _persist(tmp_path / "two", run_id="adaptive-two")
    first = AdaptiveReductionEvidenceStore.replay(first_dir)
    second = AdaptiveReductionEvidenceStore.replay(second_dir)
    assert first.semantic_digest == second.semantic_digest
    evidence = json.loads((first_dir / "adaptive/evidence.json").read_text(encoding="utf-8"))
    assert evidence["runtime_policy"]["excluded_from_semantic_digest"] is True
    assert "runtime_seconds" not in evidence["digests"]


@pytest.mark.parametrize("kwargs, expected_reason", [
    ({}, "FIDELITY_SATISFIED"),
    ({"max_bus_count": 1}, "FIDELITY_SATISFIED_BUT_COMPRESSION_BUDGET_NOT_MET"),
    ({"protected": True, "line_r": 1e-6, "line_x": 30.0, "load_q": 10.0},
     "NO_EXPANSION_CANDIDATE"),
    ({"line_r": 0.005, "line_x": 0.2, "load_q": 20.0}, "UNATTRIBUTABLE_HARD_VIOLATION"),
    ({"improvement_tolerance": 100.0}, "NO_FURTHER_COMPRESSION_BENEFIT"),
    ({"max_iterations": 1}, "MAX_ITERATIONS_REACHED"),
])
def test_terminal_reasons_survive_clean_adaptive_replay(
    tmp_path: Path, kwargs: dict[str, object], expected_reason: str,
) -> None:
    run_dir, case = _persist(tmp_path, run_id="adaptive-terminal", **kwargs)
    assert case[7].terminal_reason.value == expected_reason
    replay = AdaptiveReductionEvidenceStore.replay(run_dir)
    assert replay.report.terminal_reason.value == expected_reason


@pytest.mark.parametrize("field", [
    "action_id", "operation", "source_ids", "dependencies",
    "parent_action_set_fingerprint", "deactivated_action_ids", "active_action_ids",
    "mapping_delta", "mapping_fingerprint", "fingerprint", "journal_digest",
])
def test_action_journal_semantic_field_tampering_fails_closed(tmp_path: Path, field: str) -> None:
    run_dir, _ = _persist(tmp_path)
    path = run_dir / "adaptive/journal.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    journal = value["journal"]
    action_fields = {"action_id", "operation", "source_ids", "dependencies"}
    snapshot_fields = {
        "parent_action_set_fingerprint", "deactivated_action_ids", "active_action_ids",
        "mapping_delta", "mapping_fingerprint", "fingerprint",
    }
    if field in action_fields:
        action = journal["actions"][0]
        if field == "source_ids":
                action[field][0] = action[field][1]
        elif field == "dependencies":
            action[field] = ["missing-dependency"]
        else:
            action[field] = "tampered" if field != "operation" else "BUS_CLUSTER"
    elif field == "journal_digest":
        journal["digest"] = "0" * 64
    else:
        snapshot = journal["snapshots"][1]
        if field == "parent_action_set_fingerprint":
            snapshot[field] = "0" * 64
        elif field == "deactivated_action_ids":
            snapshot[field] = ["unknown-action"]
        elif field == "active_action_ids":
            snapshot[field] = ["tampered"]
        elif field == "mapping_delta":
            snapshot[field][0]["mapping_type"] = "changed"
        elif field in {"mapping_fingerprint", "fingerprint"}:
            snapshot[field] = "0" * 64
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    with pytest.raises(AdaptiveEvidenceError):
        AdaptiveReductionEvidenceStore.replay(run_dir)


def _rebind_report_artifacts(run_dir: Path, report: dict[str, object]) -> None:
    """Recompute stored content bindings so replay must compare semantics, not just syntax."""
    from psdcalc.reduction.adaptive.evidence import (
        _candidate_evaluation_rows, _digest, _iteration_bindings,
    )

    adaptive = run_dir / "adaptive"
    iterations = report["iterations"]
    candidate_rows = _candidate_evaluation_rows(report)
    bindings = _iteration_bindings(iterations)
    (adaptive / "iterations.json").write_text(json.dumps(iterations, sort_keys=True), encoding="utf-8")
    (adaptive / "candidate_evaluations.json").write_text(json.dumps(candidate_rows, sort_keys=True), encoding="utf-8")
    (adaptive / "iteration_bindings.json").write_text(json.dumps(bindings, sort_keys=True), encoding="utf-8")
    evidence_path = adaptive / "evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    digests = evidence["digests"]
    digests["final_report_digest"] = _digest(report)
    digests["ordered_iteration_evidence_digest"] = _digest(iterations)
    digests["candidate_evaluation_digest"] = _digest(candidate_rows)
    digests["iteration_bindings_digest"] = _digest(bindings)
    digests["terminal_reason"] = report["terminal_reason"]
    evidence["semantic_digest"] = _digest(digests)
    evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
    (adaptive / "report.json").write_text(json.dumps(report, sort_keys=True), encoding="utf-8")


@pytest.mark.parametrize("tamper", [
    "iteration_index", "candidate_model_digest", "mapping_digest", "structural_status",
    "readiness_status", "pf_status", "fidelity_status", "attribution_digest",
    "candidate_set_digest", "candidate_rejection_reason", "selected_candidate_fingerprint",
    "score", "tie_break_field", "terminal_reason",
])
def test_recomputed_report_tampering_fails_semantic_replay(tmp_path: Path, tamper: str) -> None:
    run_dir, _ = _persist(tmp_path)
    path = run_dir / "adaptive/report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    iteration = report["iterations"][0]
    decision = iteration["expansion_decision"]
    evaluation = decision["candidate_evaluations"][0]
    if tamper == "iteration_index":
        iteration["iteration_index"] += 1
    elif tamper == "candidate_model_digest":
        evaluation["model_digest"] = "0" * 64
    elif tamper == "mapping_digest":
        evaluation["mapping_digest"] = "0" * 64
    elif tamper == "structural_status":
        evaluation["structural_status"] = "STRUCTURAL_INVALID"
    elif tamper == "readiness_status":
        evaluation["readiness_status"] = "AC_MODEL_NOT_READY"
    elif tamper == "pf_status":
        evaluation["pf_status"] = "PF_NOT_CONVERGED"
    elif tamper == "fidelity_status":
        evaluation["fidelity_status"] = "PF_FIDELITY_OUTSIDE_TOLERANCE"
    elif tamper == "attribution_digest":
        iteration["hard_violations"][0]["observed_value"] += 1.0
    elif tamper == "candidate_set_digest":
        evaluation["candidate"]["candidate_fingerprint"] = "0" * 64
    elif tamper == "candidate_rejection_reason":
        evaluation["rejection_reason"] = "tampered-rejection"
    elif tamper == "selected_candidate_fingerprint":
        decision["selected_candidate_fingerprint"] = "0" * 64
    elif tamper == "score":
        evaluation["score"] += 1.0
    elif tamper == "tie_break_field":
        decision["tie_break_trace"][0] = "tampered-tie-break"
    elif tamper == "terminal_reason":
        report["terminal_reason"] = "MAX_ITERATIONS_REACHED"
        iteration["stopping_reason"] = "MAX_ITERATIONS_REACHED"
    _rebind_report_artifacts(run_dir, report)
    with pytest.raises(AdaptiveEvidenceError):
        AdaptiveReductionEvidenceStore.replay(run_dir)


def test_cross_run_report_journal_and_candidate_substitution_fails_closed(tmp_path: Path) -> None:
    left, _ = _persist(tmp_path / "left", run_id="adaptive-left", load_q=50.0)
    right, _ = _persist(tmp_path / "right", run_id="adaptive-right", load_q=51.0)
    for relative in ("report.json", "journal.json", "iterations.json", "candidate_evaluations.json"):
        left_path = left / "adaptive" / relative
        right_path = right / "adaptive" / relative
        original = left_path.read_bytes()
        left_path.write_bytes(right_path.read_bytes())
        with pytest.raises(AdaptiveEvidenceError):
            AdaptiveReductionEvidenceStore.replay(left)
        left_path.write_bytes(original)


def test_no_viable_defensive_candidate_failure_payload_round_trips_and_binds_digest() -> None:
    """Validate the defensive failure schema without fabricating a PyPSA E2E run."""
    candidate = ExpansionCandidate(
        candidate_fingerprint=_contract_digest({
            "schema_version": SCHEMA_VERSION,
            "deactivated_action_ids": ("collapse",),
        }),
        region_fingerprint="a" * 64,
        deactivated_action_ids=("collapse",),
        resulting_active_action_ids=(),
        added_bus_count=1,
        added_branch_count=1,
        direct_action_ids=("collapse",),
        dependency_closure_action_ids=("collapse",),
        region_fingerprints=("a" * 64,),
    )
    failure = ErrorAttribution(
        metric_name="vm_rmse_pu",
        severity="HARD",
        observed_value=0.02,
        resolved_threshold=0.01,
        normalized_violation=1.0,
        status=AttributionStatus.ATTRIBUTABLE,
        source_ids=(StableId("stage5", "Bus", "source-bus"),),
        candidate_action_ids=("collapse",),
        related_action_ids=("collapse",),
        violation_magnitude=0.01,
    )
    evaluation = CandidateEvaluation(
        candidate=candidate,
        structural_status="STRUCTURALLY_INVALID",
        readiness_status="NOT_RUN",
        pf_status="NOT_RUN",
        fidelity_status="NOT_RUN",
        model_digest=None,
        mapping_digest=None,
        rejection_reason="structural prerequisite failed",
        structural_checks={"structurally_valid": False},
        hard_violations=(failure,),
        diagnostic_failures=("structural gate rejected candidate",),
    )

    payload = evaluation.to_dict()
    restored = CandidateEvaluation.from_dict(json.loads(json.dumps(payload)))

    row = {"iteration_index": 0, **payload}
    _validate_candidate_evaluation_rows([row])
    assert restored == evaluation
    assert restored.structural_status == "STRUCTURALLY_INVALID"
    assert restored.rejection_reason == "structural prerequisite failed"
    original_digest = _evidence_digest([row])
    tampered = json.loads(json.dumps(payload))
    tampered["structural_checks"]["structurally_valid"] = True
    tampered["hard_violations"][0]["source_ids"][0] = StableId(
        "stage5", "Bus", "different-bus"
    ).encode()
    tampered_row = {"iteration_index": 0, **tampered}
    assert _evidence_digest([tampered_row]) != original_digest
    assert AdaptiveStoppingReason.NO_VIABLE_EXPANSION_CANDIDATE.value in {
        reason.value for reason in AdaptiveStoppingReason
    }


def test_forged_no_viable_terminal_reason_fails_clean_replay(tmp_path: Path) -> None:
    """A stored defensive terminal cannot override the terminal recomputed by replay."""
    run_dir, case = _persist(tmp_path, run_id="adaptive-terminal-binding")
    assert case[7].terminal_reason is not AdaptiveStoppingReason.NO_VIABLE_EXPANSION_CANDIDATE
    evidence_path = run_dir / "adaptive/evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["digests"]["terminal_reason"] = AdaptiveStoppingReason.NO_VIABLE_EXPANSION_CANDIDATE.value
    evidence["semantic_digest"] = _evidence_digest(evidence["digests"])
    evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")

    with pytest.raises(AdaptiveEvidenceError, match="TERMINAL_MISMATCH"):
        AdaptiveReductionEvidenceStore.replay(run_dir)


def test_candidate_evaluation_digest_rebinding_fails_clean_replay(tmp_path: Path) -> None:
    """Replay independently verifies the flattened candidate-evaluation artifact digest."""
    run_dir, _case = _persist(tmp_path, run_id="adaptive-candidate-digest")
    evidence_path = run_dir / "adaptive/evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["digests"]["candidate_evaluation_digest"] = "0" * 64
    evidence["semantic_digest"] = _evidence_digest(evidence["digests"])
    evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")

    with pytest.raises(AdaptiveEvidenceError, match="DIGEST_MISMATCH"):
        AdaptiveReductionEvidenceStore.replay(run_dir)
