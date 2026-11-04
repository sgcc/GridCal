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
from psdcalc.reduction.adaptive.observations import NumericObservationBundle
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
from psdcalc.reduction.fidelity.pypsa_executor import PypsaAcPowerFlowExecutor
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.parallel_provenance import (
    ParallelSourceLedger, SourceProfileContract,
)


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
    strict_middle: bool = False,
    failure_mode: str | None = None,
    config_path_prefix: str | None = None,
    observations_enabled: bool = True,
    executor: PypsaAcPowerFlowExecutor | None = None,
    initial_active_action_ids: tuple[str, ...] | None = None,
):
    network = pypsa.Network()
    network.add("Bus", "slack", v_nom=110.0)
    network.add("Bus", "middle", v_nom=110.0)
    network.add("Bus", "load", v_nom=110.0)
    network.add("Line", "slack-middle", bus0="slack", bus1="middle", r=0.0, x=0.1)
    network.add("Line", "line", bus0="middle", bus1="load", r=line_r, x=line_x)
    network.add("Generator", "source", bus="slack", control="Slack", p_nom=100.0)
    network.add("Load", "demand", bus="load", p_set=0.0, q_set=load_q)
    if failure_mode == "anchor_away":
        network.add("Bus", "low", v_nom=10.0)
        network.add("Transformer", "terminal", bus0="middle", bus1="low", x=0.1)
    network.set_snapshots(["snapshot-0"])
    network.loads_t.p_set["demand"] = [3.0]
    network.loads_t.q_set["demand"] = [load_q]
    sidecar = PypsaAdapter().identity_sidecar(network, "stage5-synthetic")
    reduction = load_config({
        "schema_version": "0.1.0",
        "run": {"id": "stage5", "seed": 7,
                "output_dir": str(Path(config_path_prefix) / "stage5-output")
                if config_path_prefix else "stage5-output"},
        "input": {"backend": "pypsa",
                  "path": str(Path(config_path_prefix) / "frozen-s1.nc")
                  if config_path_prefix else "frozen-s1.nc",
                  "format": "netcdf",
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
    if strict_middle:
        empty_protection = ProtectionSet(
            empty_protection.traced, empty_protection.preserved_components,
            empty_protection.terminal_buses, frozenset({bus_middle}),
            empty_protection.cannot_link,
        )
    if failure_mode == "cannot_link":
        empty_protection = ProtectionSet(
            empty_protection.traced, empty_protection.preserved_components,
            empty_protection.terminal_buses, empty_protection.singleton_buses,
            frozenset({frozenset({bus_middle, bus_load})}),
        )
    if failure_mode == "anchor_away":
        action = ReductionAction.create(
            action_id="merge-radial-bus", operation=ActionOperation.RADIAL_ABSORPTION,
            source_ids=(bus_middle, bus_load, line), target_ids=(bus_load,),
            mapping_delta=(
                MappingTransition(bus_middle, bus_load, "clustered"),
                MappingTransition(bus_load, bus_load, "preserved"),
                MappingTransition(line, None, "absorbed_internal"),
            ), voltage_layer="110", component_id="synthetic-component",
            region_fingerprint=_digest("synthetic-region"),
            audit_reason="mapped-away Transformer terminal anchor",
        )
        actions = (action,)
    if failure_mode == "unsupported_action":
        action = ReductionAction.create(
            action_id="merge-radial-bus",
            operation=ActionOperation.PARALLEL_CORRIDOR_AGGREGATION,
            source_ids=action.source_ids, target_ids=action.target_ids,
            mapping_delta=action.mapping_delta, voltage_layer=action.voltage_layer,
            component_id=action.component_id,
            region_fingerprint=action.region_fingerprint,
            audit_reason="unsupported action in initial catalog",
        )
        actions = (action,)
    if failure_mode == "dependency_closure":
        prerequisite = ReductionAction.create(
            action_id="inactive-prerequisite", operation=ActionOperation.BUS_CLUSTER,
            source_ids=action.source_ids, target_ids=action.target_ids,
            mapping_delta=action.mapping_delta, voltage_layer=action.voltage_layer,
            component_id=action.component_id,
            region_fingerprint=action.region_fingerprint,
            audit_reason="known but inactive prerequisite",
        )
        action = ReductionAction.create(
            action_id="merge-radial-bus", operation=action.operation,
            source_ids=action.source_ids, target_ids=action.target_ids,
            mapping_delta=action.mapping_delta, voltage_layer=action.voltage_layer,
            component_id=action.component_id,
            region_fingerprint=action.region_fingerprint,
            dependencies=(prerequisite.action_id,),
            audit_reason="initial action lacks active prerequisite",
        )
        actions = (prerequisite, action)
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=network, source_sidecar=sidecar, actions=actions,
        reduction_config=reduction, protection=empty_protection,
        source_ledger=ParallelSourceLedger(SourceProfileContract("programmatic_pypsa", "unavailable")),
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
        threshold_set=thresholds, observations_enabled=observations_enabled,
        executor=executor,
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
    initial_ids = ((action.action_id,) if initial_active_action_ids is None
                   else initial_active_action_ids)
    report = AdaptiveReductionController().run(
        runtime=runtime, actions=actions, initial_active_action_ids=initial_ids, config=config,
    )
    return network, sidecar, reduction, empty_protection, actions, runtime, config, report, initial_ids


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


def test_numeric_companion_persists_and_replays_without_timing_equality(tmp_path: Path) -> None:
    run_dir, case_data = _persist(tmp_path)
    companion_path = run_dir / "adaptive/numeric_observations.json"
    assert companion_path.is_file()
    bundle = NumericObservationBundle.from_payload(json.loads(companion_path.read_text()))
    assert bundle.run_id == case_data[7].run_id
    assert any(row.observation_kind == "pf_execution" and row.role == "source"
               for row in bundle.numeric_observations)
    source_pf = next(row for row in bundle.numeric_observations
                     if row.observation_kind == "pf_execution" and row.role == "source")
    assert source_pf.solver_contract_summary["solver"].value == "pypsa.newton_raphson"
    assert source_pf.solver_contract_digest == case_data[5].solver_contract_digest
    assert source_pf.source_sidecar_digest == case_data[5].source_sidecar_digest
    assert any(row.observation_kind == "fidelity_comparison"
               for row in bundle.numeric_observations)
    assert {row.operation_name for row in bundle.performance_timing_observations} >= {
        "source_pf_total", "source_pf_solver", "candidate_pf_total", "candidate_pf_solver",
        "candidate_reconstruction", "structural_ac_readiness", "fidelity_comparison",
        "attribution", "adaptive_controller_total", "evidence_persistence",
    }
    replay = AdaptiveReductionEvidenceStore.replay(run_dir)
    assert replay.verified
    assert replay.replay_timing_observation.operation_name == "fresh_process_replay_total"
    assert replay.replay_timing_observation.duration is not None


def _write_rebound_numeric_companion(run_dir: Path, payload: dict[str, object]) -> None:
    """Rebind syntactically valid tampering to isolate semantic replay checks."""
    import hashlib

    path = run_dir / "adaptive/numeric_observations.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    manifest_path = run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("artifact_sha256", {})["adaptive_numeric_observations"] = hashlib.sha256(
        path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")


@pytest.mark.parametrize("tamper", ["metric", "threshold"])
def test_numeric_companion_semantic_tampering_fails_closed(tmp_path: Path, tamper: str) -> None:
    run_dir, _ = _persist(tmp_path)
    path = run_dir / "adaptive/numeric_observations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if tamper in {"metric", "threshold"}:
        row = next(item for item in payload["numeric_observations"]
                   if item["observation_kind"] == "fidelity_comparison")
        metric = row["metric_observations"][0]
        field = "observed_value" if tamper == "metric" else "resolved_threshold"
        value = metric[field]["value"]
        metric[field]["value"] = float(value or 0.0) + 0.001
        payload["numeric_observations_digest"] = _digest(json.dumps(
            payload["numeric_observations"], sort_keys=True, separators=(",", ":"),
            ensure_ascii=False))
    _write_rebound_numeric_companion(run_dir, payload)
    with pytest.raises(AdaptiveEvidenceError):
        AdaptiveReductionEvidenceStore.replay(run_dir)


def test_timing_duration_is_not_part_of_numeric_replay_equality(tmp_path: Path) -> None:
    run_dir, _ = _persist(tmp_path)
    path = run_dir / "adaptive/numeric_observations.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["performance_timing_observations"][0]["duration"] += 0.001
    payload["timing_observations_digest"] = _digest(json.dumps(
        payload["performance_timing_observations"], sort_keys=True, separators=(",", ":"),
        ensure_ascii=False))
    _write_rebound_numeric_companion(run_dir, payload)
    assert AdaptiveReductionEvidenceStore.replay(run_dir).verified


def test_v2_without_numeric_companion_remains_replayable(tmp_path: Path) -> None:
    run_dir, _ = _persist(tmp_path)
    companion = run_dir / "adaptive/numeric_observations.json"
    if companion.exists():
        companion.unlink()
    manifest_path = run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.get("artifacts", {}).pop("adaptive_numeric_observations", None)
    manifest.get("artifact_sha256", {}).pop("adaptive_numeric_observations", None)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    assert AdaptiveReductionEvidenceStore.replay(run_dir).verified


def test_instrumentation_does_not_change_controller_or_pf_call_count(tmp_path: Path) -> None:
    class CountingExecutor(PypsaAcPowerFlowExecutor):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def solve(self, *args, **kwargs):
            self.calls += 1
            return super().solve(*args, **kwargs)

    enabled_executor = CountingExecutor()
    disabled_executor = CountingExecutor()
    enabled = _case(tmp_path / "enabled", executor=enabled_executor, observations_enabled=True)
    disabled = _case(tmp_path / "disabled", executor=disabled_executor, observations_enabled=False)
    assert enabled[7].selected_candidate_sequence == disabled[7].selected_candidate_sequence
    assert enabled[7].terminal_reason == disabled[7].terminal_reason
    assert [row.candidate_fingerprint for row in enabled[7].iterations] == [
        row.candidate_fingerprint for row in disabled[7].iterations]
    assert enabled_executor.calls == disabled_executor.calls
    assert enabled[5].source_model_digest == disabled[5].source_model_digest
    assert enabled[5].derived_closure_evidence() == disabled[5].derived_closure_evidence()
    assert len([row for row in enabled[5].observation_bundle().numeric_observations
                if row.observation_kind == "pf_execution"]) == enabled_executor.calls
    assert enabled_executor.calls == 4
    assert enabled[5].source_model_digest == (
        "6b7047d504d1737dd17985628816d8995d9bbf9f58c3592e98a7824d8db9df5c")
    assert enabled[7].selected_candidate_sequence == (
        "85ebfded1b4eb6a5bdc65f593e6e860623f0bd08819c7c48f4ade4cc2c927623",
    )
    assert enabled[7].terminal_reason.value == "FIDELITY_SATISFIED"
    assert tuple(row.candidate_fingerprint for row in enabled[7].iterations) == (
        "e50ca01c8c10b05946e0e706ab992ab14fd432bdaa1c6174db9a5d98027bd885",
        "9f7d89a087561ee6d68420c2425e03a13ba7c5b72f34413ba0b4345f1ccfb3dd",
    )
    assert tuple(row.mapping_digest for row in enabled[7].iterations) == (
        "41ec309f62d692edc9a645bda7c8d9f172d2b3ffc51ee32eb8707b71346a6266",
        "ec3ba94517bb430df2231b82b060d650d272f6e8f7f184c047f6193b86c175d6",
    )
    assert tuple(row.pf_status for row in enabled[7].iterations) == (
        "PF_CONVERGED", "PF_CONVERGED")
    assert tuple(row["actual_closure_digest"] for row in enabled[5].derived_closure_evidence()) == (
        "23dcb3e71faa981145dc9f33f58cf41e9eb00c0d3f4b59b896044add470b3922",
        "73accc71e4f7aec75108e8150249e4ef30a1a54a0d3b5bc072ed7ae0711793ed",
    )


@pytest.mark.parametrize("kwargs, failure_code", [
    ({"strict_middle": True}, "STRICT_SINGLETON_CONFLICT"),
    ({"failure_mode": "anchor_away"}, "ANCHOR_MAPPED_AWAY"),
    ({"failure_mode": "cannot_link"}, "CANNOT_LINK_CONFLICT"),
    ({"failure_mode": "unsupported_action"}, "UNSUPPORTED_ACTION_TYPE"),
    ({"failure_mode": "dependency_closure"}, "INVALID_DEPENDENCY_CLOSURE"),
])
def test_failed_initial_build_has_v2_identity_and_fresh_process_replay(
    tmp_path: Path, kwargs: dict[str, object], failure_code: str,
) -> None:
    """Five failed initial builds replay with stable attempts and null candidates."""
    run_dir, case_data = _persist(tmp_path, **kwargs)
    report = case_data[7]
    assert report.terminal_reason == AdaptiveStoppingReason.STRUCTURAL_INVALID
    summary = report.final_incumbent_summary
    assert summary["build_attempt_fingerprint"] is not None
    assert summary["materialized_candidate_fingerprint"] is None
    assert summary["failure_stage"] == "ACTION_CATALOG_READINESS"
    assert summary["failure_code"] == failure_code
    assert tuple(summary["offending_identity_fingerprints"]) == tuple(sorted(
        summary["offending_identity_fingerprints"]))
    evidence = json.loads((run_dir / "adaptive/evidence.json").read_text())
    assert evidence["schema"] == "adaptive-reduction-evidence/v2"
    assert evidence["digests"]["source_ledger_digest"]
    assert evidence["digests"]["effective_protection_digest"]
    code = (
        "from pathlib import Path; "
        "from psdcalc.reduction.adaptive.evidence import AdaptiveReductionEvidenceStore; "
        "r=AdaptiveReductionEvidenceStore.replay(Path(__import__('sys').argv[1])); "
        "s=r.report.final_incumbent_summary; "
        "print(s['build_attempt_fingerprint'], s['failure_code'], "
        "s['materialized_candidate_fingerprint'], "
        "','.join(s['offending_identity_fingerprints']), "
        "r.report.terminal_reason.value)"
    )
    for seed in ("1", "29"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
        result = subprocess.run([sys.executable, "-c", code, str(run_dir)],
                                env=env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == (
            f"{summary['build_attempt_fingerprint']} {failure_code} None "
            f"{','.join(summary['offending_identity_fingerprints'])} "
            "STRUCTURAL_INVALID"
        )


def test_failed_replay_is_path_independent_across_directories(tmp_path: Path) -> None:
    """Clean-process failed replay keeps semantic identity after artifact relocation."""
    first_dir, first = _persist(tmp_path / "first", strict_middle=True,
                                config_path_prefix="first")
    second_dir, second = _persist(tmp_path / "second", strict_middle=True,
                                  config_path_prefix="second")
    assert first[5].reduction_config_digest != second[5].reduction_config_digest
    summaries = [case[7].final_incumbent_summary for case in (first, second)]
    for key in ("build_attempt_fingerprint", "failure_code",
                "offending_identity_fingerprints"):
        assert summaries[0][key] == summaries[1][key]
    assert first[7].terminal_reason == second[7].terminal_reason
    assert summaries[0]["materialized_candidate_fingerprint"] is None
    code = (
        "import json, sys; "
        "from psdcalc.reduction.adaptive.evidence import AdaptiveReductionEvidenceStore; "
        "r=AdaptiveReductionEvidenceStore.replay(sys.argv[1]); "
        "s=r.report.final_incumbent_summary; "
        "print(json.dumps([s['build_attempt_fingerprint'], s['failure_code'], "
        "s['offending_identity_fingerprints'], r.report.terminal_reason.value]))"
    )
    observed = []
    for run_dir in (first_dir, second_dir):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
        result = subprocess.run([sys.executable, "-c", code, str(run_dir)],
                                cwd=run_dir, env=env, capture_output=True, text=True,
                                check=False)
        assert result.returncode == 0, result.stderr
        observed.append(json.loads(result.stdout))
    assert observed[0] == observed[1]
    assert observed[0][-1] == "STRUCTURAL_INVALID"


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
        source_ledger=ParallelSourceLedger(SourceProfileContract("programmatic_pypsa", "unavailable")),
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


@pytest.mark.parametrize("relative", [
    "adaptive/source_ledger/source_profile.json",
    "adaptive/source_ledger/line_provenance.parquet",
    "adaptive/source_ledger/l_plus_associations.parquet",
])
def test_source_ledger_artifact_tampering_fails_closed(
    tmp_path: Path, relative: str,
) -> None:
    """Replay binds all three frozen source-ledger artifacts."""
    run_dir, _ = _persist(tmp_path)
    artifact = run_dir / relative
    if artifact.suffix == ".json":
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        payload["tampered"] = True
        artifact.write_text(json.dumps(payload), encoding="utf-8")
    else:
        artifact.write_bytes(artifact.read_bytes() + b"tampered")
    with pytest.raises(AdaptiveEvidenceError):
        AdaptiveReductionEvidenceStore.replay(run_dir)


def test_replay_rejects_source_ledger_digest_mismatch(tmp_path: Path) -> None:
    """A well-formed but mismatched ledger digest cannot authorize replay."""
    run_dir, _ = _persist(tmp_path, strict_middle=True)
    evidence_path = run_dir / "adaptive/evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["digests"]["source_ledger_digest"] = "0" * 64
    evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
    with pytest.raises(AdaptiveEvidenceError, match="source ledger semantic digest differs"):
        AdaptiveReductionEvidenceStore.replay(run_dir)


def test_historical_v1_evidence_is_explicitly_rejected(tmp_path: Path) -> None:
    """The v2 replay path cannot reinterpret an old candidate fingerprint."""
    run_dir, _ = _persist(tmp_path)
    evidence_path = run_dir / "adaptive/evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["schema"] = "adaptive-reduction-evidence/v1"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(AdaptiveEvidenceError, match="v1|legacy|unsupported"):
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
