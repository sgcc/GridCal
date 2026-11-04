"""Attempt-scoped retained-fidelity orchestration over fresh frozen-S1 candidates.

This is experiment tooling outside the installed reduction package. It never
accepts a target network or an earlier run as an input.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from typing import Iterator, Sequence

import pypsa
import yaml

from psdcalc.reduction.adaptive.candidates import _semantic_config_digest, _sidecar_digest
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import ReductionConfig
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.core.ids import IdentitySidecar
from psdcalc.reduction.fidelity.persistence import canonical_model_digest
from psdcalc.reduction.fidelity.persistence import FrozenThresholdSet
from psdcalc.reduction.fidelity.contracts import (
    PowerFlowMetricPolicy, PowerFlowOperatingPointContract,
)
from psdcalc.reduction.fidelity.mapping import derive_pf_comparison_mapping
from psdcalc.reduction.fidelity.metrics import (
    compare_power_flow_results, evaluate_metric_thresholds_detailed,
    metric_policy_digest, operating_point_digest, reference_cutset_magnitudes,
)
from psdcalc.reduction.fidelity.pypsa_executor import PypsaAcPowerFlowExecutor
from psdcalc.reduction.adaptive.pypsa_runtime import (
    _branch_endpoints, _bus_ids, _component_map,
)
from psdcalc.reduction.reporting.retained_fidelity import compare_retained_network_fidelity
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.pipelines.multi_voltage import MultiVoltageQuotientPipeline
from psdcalc.reduction.policies.voltage_cutoff import (
    VoltageCutoffPolicy, voltage_policy_protection,
)
from psdcalc.reduction.protection.effective import (
    EffectiveProtectionResolver, source_ledger_digest,
)
from psdcalc.reduction.protection.engine import ProtectionEngine
from psdcalc.reduction.topology.parallel_provenance import ParallelSourceLedger
from experiment_tools.retained_pf_evidence import (
    read_power_flow_result, write_power_flow_result,
)
from experiment_tools.retained_fidelity_outputs import (
    export_cross_policy_report, verify_cross_policy_report, verify_numeric_projection,
    verify_policy_outputs, write_policy_outputs,
)


SCHEMA = "retained-fidelity-candidate-provenance/v1"


class RetainedAttemptError(ValueError):
    """Raised when frozen inputs or durable candidate provenance do not bind."""


@dataclass(frozen=True)
class FrozenAttemptBindings:
    """Semantic identities that every policy must inherit from the frozen S1."""

    experiment_id: str
    attempt_id: str
    source_model_digest: str
    source_sidecar_digest: str
    source_ledger_digest: str
    required_policy_order: tuple[str, ...] = ()
    operating_point_digest: str | None = None
    threshold_set_sha256: str | None = None
    require_pf: bool = False


@dataclass(frozen=True)
class FrozenPolicyRun:
    """One requested policy and its independently frozen resolution identity."""

    policy_id: str
    config: ReductionConfig
    resolved_policy_fingerprint: str
    semantic_config_digest: str
    effective_protection_digest: str


@dataclass(frozen=True)
class CandidateProvenance:
    """Readback-verified identity of one newly materialized candidate."""

    policy_id: str
    run_dir: Path
    candidate_identity: str
    candidate_model_digest: str
    mapping_digest: str
    structural_status: str
    ac_readiness_status: str
    source_pf_status: str | None = None
    candidate_pf_status: str | None = None
    fidelity_status: str | None = None


def _digest(value: object) -> str:
    """Hash one canonical JSON payload without runtime locations."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                   allow_nan=False).encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    """Return the exact byte digest for one durable evidence file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_digest(value: str, expected: str, label: str) -> None:
    """Reject any identity drift before materialization or PF."""
    if not expected or value != expected:
        raise RetainedAttemptError(f"CANDIDATE_PROVENANCE_INVALID: {label}")


@contextmanager
def _stop_on_policy_failure(
    root: Path, policies: Sequence[FrozenPolicyRun],
    completed: Sequence[CandidateProvenance], policy_id: str,
) -> Iterator[None]:
    """Preserve a single ordered stop record for any policy-stage exception."""
    try:
        yield
    except Exception as exc:
        _write_stop(root, policies, completed, policy_id, type(exc).__name__)
        raise


def _effective_digest(
    network: pypsa.Network, sidecar: IdentitySidecar,
    ledger: ParallelSourceLedger, config: ReductionConfig,
) -> str:
    """Resolve the frozen policy's complete protection without changing it."""
    adapter = PypsaAdapter()
    base = ProtectionEngine().compile(
        adapter.inventory(network, sidecar), config.protection.rules,
        profile=config.task.profile, base_dir=Path(config.input.path).resolve().parent,
    )
    policy = VoltageCutoffPolicy(
        config.voltage_policy.enabled, config.voltage_policy.mode,
        config.voltage_policy.cutoff_kv,
    ).resolve(network, sidecar)
    protection = base.union(voltage_policy_protection(policy))
    return EffectiveProtectionResolver().resolve(
        network=network, sidecar=sidecar, config=config,
        protection=protection, source_ledger=ledger,
    ).digest


def run_frozen_policies(
    *, source_network: pypsa.Network, source_path: Path,
    source_sidecar: IdentitySidecar, source_ledger: ParallelSourceLedger,
    policies: Sequence[FrozenPolicyRun], bindings: FrozenAttemptBindings,
    output_root: Path,
    pf_contract: PowerFlowOperatingPointContract | None = None,
    metric_policy: PowerFlowMetricPolicy | None = None,
    threshold_set: FrozenThresholdSet | None = None,
) -> tuple[CandidateProvenance, ...]:
    """Build every policy independently from S1, stopping on the first failed Gate."""
    if not isinstance(source_ledger, ParallelSourceLedger):
        raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: explicit source ledger required")
    if not policies or len({p.policy_id for p in policies}) != len(policies):
        raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: policy sequence")
    if bindings.required_policy_order and tuple(p.policy_id for p in policies) != bindings.required_policy_order:
        raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: policy order")
    if bindings.require_pf and (pf_contract is None or metric_policy is None or threshold_set is None):
        raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: PF contract is required")
    if pf_contract is not None and (metric_policy is None or threshold_set is None):
        raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: incomplete PF contract")
    if pf_contract is not None and bindings.operating_point_digest is not None:
        _require_digest(operating_point_digest(pf_contract),
                        bindings.operating_point_digest, "operating point")
    if threshold_set is not None and bindings.threshold_set_sha256 is not None:
        _require_digest(threshold_set.sha256, bindings.threshold_set_sha256,
                        "threshold artifact")
    _require_digest(canonical_model_digest(source_network, source_sidecar),
                    bindings.source_model_digest, "source model")
    _require_digest(_sidecar_digest(source_sidecar),
                    bindings.source_sidecar_digest, "source sidecar")
    _require_digest(source_ledger_digest(source_ledger),
                    bindings.source_ledger_digest, "source ledger")
    source_ledger.validate(source_network, source_sidecar)
    original_source_digest = canonical_model_digest(source_network, source_sidecar)
    output_root = Path(output_root)
    results: list[CandidateProvenance] = []
    reports = []
    source_pf = None
    source_pf_times: dict[str, float] = {}
    if pf_contract is not None:
        source_pf_started = perf_counter()
        try:
            source_pf = PypsaAcPowerFlowExecutor().solve(
                source_network, source_sidecar, pf_contract,
                timing_observer=lambda duration, status: source_pf_times.update(
                    solver=duration))
        except Exception as exc:
            _write_stop(output_root, policies, results, policies[0].policy_id,
                        "SOURCE_PF_EXECUTION_FAILED")
            raise RetainedAttemptError("SOURCE_PF_EXECUTION_FAILED: candidate PF NOT_RUN") from exc
        source_pf_times["end_to_end"] = perf_counter() - source_pf_started
        output_root.mkdir(parents=True, exist_ok=True)
        write_power_flow_result(output_root / "source-pf.json", source_pf)
        readback_source = read_power_flow_result(output_root / "source-pf.json")
        if (readback_source.status != source_pf.status or
                readback_source.converged != source_pf.converged or
                readback_source.subnetwork_status != source_pf.subnetwork_status):
            raise RetainedAttemptError("SOURCE_PF_EVIDENCE_INVALID")
        if not source_pf.converged:
            _write_stop(output_root, policies, results, policies[0].policy_id,
                        "SOURCE_PF_NOT_CONVERGED")
            raise RetainedAttemptError("SOURCE_PF_NOT_CONVERGED: candidate PF NOT_RUN")
    for policy in policies:
        with _stop_on_policy_failure(output_root, policies, results, policy.policy_id):
            config = policy.config
            if Path(config.input.path).resolve() != Path(source_path).resolve():
                raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: source path")
            resolved = VoltageCutoffPolicy(
                config.voltage_policy.enabled, config.voltage_policy.mode,
                config.voltage_policy.cutoff_kv,
            ).resolve(source_network, source_sidecar)
            try:
                label_cutoff = float(policy.policy_id.removeprefix("BACKBONE_"))
            except ValueError as exc:
                raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: policy ID/cutoff") from exc
            if (not policy.policy_id.startswith("BACKBONE_") or
                    label_cutoff != resolved.requested_cutoff_kv):
                raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: policy ID/cutoff")
            _require_digest(resolved.resolved_policy_fingerprint,
                            policy.resolved_policy_fingerprint, "resolved policy")
            semantic_digest = _semantic_config_digest(config)
            _require_digest(semantic_digest, policy.semantic_config_digest, "semantic config")
            protection_digest = _effective_digest(source_network, source_sidecar,
                                                  source_ledger, config)
            _require_digest(protection_digest, policy.effective_protection_digest,
                            "effective protection")
            run_id = f"{bindings.attempt_id.lower()}-{policy.policy_id.lower()}"
            store = ArtifactStore(output_root, run_id)
            ledger_dir = store.path("input/source_ledger_binding")
            for path in source_ledger.save_parquet(ledger_dir):
                store.record_artifact("bound_" + path.stem,
                                      str(path.relative_to(store.run_dir)), bind_sha256=True)
            reconstruction_started = perf_counter()
            result = MultiVoltageQuotientPipeline(source_ledger=source_ledger).run(
                original_network=PypsaAdapter().clone(source_network),
                source_sidecar=source_sidecar, config=config, store=store,
            )
            reconstruction_seconds = perf_counter() - reconstruction_started
            if result.structural.status != "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID":
                raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: structural Gate")
            if result.ac_readiness is None or result.ac_readiness.status.value != "AC_MODEL_READY":
                raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: AC readiness")
            target_network = PypsaAdapter().load(result.final_network_path, "netcdf")
            target_sidecar = IdentitySidecar.from_rows(json.loads(
                result.target_sidecar_path.read_text(encoding="utf-8")))
            mapping_digest = result.composed_mappings.semantic_digest()
            model_digest = canonical_model_digest(target_network, target_sidecar)
            identity_payload = {
                "experiment_id": bindings.experiment_id,
                "attempt_id": bindings.attempt_id,
                "source_model_digest": bindings.source_model_digest,
                "source_input_sha256": _file_digest(Path(source_path)),
                "source_sidecar_digest": bindings.source_sidecar_digest,
                "source_ledger_digest": bindings.source_ledger_digest,
                "policy_id": policy.policy_id,
                "resolved_policy_fingerprint": resolved.resolved_policy_fingerprint,
                "semantic_config_digest": semantic_digest,
                "effective_protection_digest": protection_digest,
                "planner": config.partition.planner,
                "seed": config.run.seed,
                "candidate_model_digest": model_digest,
                "mapping_digest": mapping_digest,
                "partition_plan_sha256": _file_digest(store.path("input/plan.json")),
                "structural_gate_sha256": _file_digest(
                    store.path("validation/multi_voltage_structural.json")),
                "ac_readiness_sha256": _file_digest(
                    store.path("validation/ac_model_readiness.json")),
                "structural_status": result.structural.status,
                "ac_readiness_status": result.ac_readiness.status.value,
            }
            candidate_identity = _digest(identity_payload)
            store.write_json("validation/candidate_provenance.json", {
                "schema": SCHEMA, **identity_payload, "candidate_identity": candidate_identity,
            })
            store.record_artifact("candidate_provenance", "validation/candidate_provenance.json",
                                  bind_sha256=True)
            if pf_contract is not None and metric_policy is not None and threshold_set is not None:
                store.path("pf").mkdir(exist_ok=True)
                source_pf_sha = write_power_flow_result(store.path("pf/source.json"), source_pf)
                store.record_artifact("source_pf", "pf/source.json", bind_sha256=True)
                if not source_pf.converged:
                    raise RetainedAttemptError("SOURCE_PF_NOT_CONVERGED: candidate PF NOT_RUN")
                target_pf_times: dict[str, float] = {}
                target_pf_started = perf_counter()
                target_pf = PypsaAcPowerFlowExecutor().solve(
                    target_network, target_sidecar, pf_contract,
                    timing_observer=lambda duration, status: target_pf_times.update(
                        solver=duration))
                target_pf_times["end_to_end"] = perf_counter() - target_pf_started
                target_pf_sha = write_power_flow_result(store.path("pf/candidate.json"), target_pf)
                store.record_artifact("candidate_pf", "pf/candidate.json", bind_sha256=True)
                fidelity_status = None
                retained_started = perf_counter()
                retained = compare_retained_network_fidelity(
                    source_network=source_network, target_network=target_network,
                    source_sidecar=source_sidecar, target_sidecar=target_sidecar,
                    mappings=result.composed_mappings, source_pf=source_pf,
                    target_pf=target_pf, cutoff_kv=(float(config.voltage_policy.cutoff_kv),),
                )
                retained_seconds = perf_counter() - retained_started
                store.write_json("metrics/retained_fidelity.json", retained.model_dump(mode="json"))
                store.record_artifact("retained_fidelity", "metrics/retained_fidelity.json",
                                      bind_sha256=True)
                metrics = evaluation = resolved_thresholds = None
                fidelity_started = perf_counter()
                if source_pf.converged and target_pf.converged:
                    mapping = derive_pf_comparison_mapping(
                        result.composed_mappings,
                        source_bus_ids=_bus_ids(source_network, source_sidecar),
                        target_bus_ids=_bus_ids(target_network, target_sidecar),
                        source_branch_endpoints=_branch_endpoints(source_network, source_sidecar),
                        target_branch_endpoints=_branch_endpoints(target_network, target_sidecar),
                        source_component_by_bus=_component_map(source_network, source_sidecar),
                        target_component_by_bus=_component_map(target_network, target_sidecar),
                    )
                    comparison = compare_power_flow_results(
                        source_pf, target_pf, mapping, metric_policy)
                    reference_p, reference_q = reference_cutset_magnitudes(source_pf, mapping)
                    resolved_thresholds = threshold_set.resolve(reference_p, reference_q)
                    evaluation = evaluate_metric_thresholds_detailed(
                        comparison.metrics, resolved_thresholds.thresholds,
                        threshold_digest=resolved_thresholds.digest)
                    metrics = comparison.metrics
                    fidelity_status = evaluation.fidelity_status.value
                    store.write_json("metrics/fidelity.json", {
                        "metrics": asdict(comparison.metrics),
                        "metric_digest": evaluation.metrics_digest,
                        "threshold_digest": resolved_thresholds.digest,
                        "thresholds": asdict(resolved_thresholds.thresholds),
                        "status": fidelity_status,
                    })
                    store.record_artifact("fidelity", "metrics/fidelity.json",
                                          bind_sha256=True)
                fidelity_seconds = perf_counter() - fidelity_started
                store.write_json("pf/binding.json", {
                    "source_result_sha256": source_pf_sha,
                    "candidate_result_sha256": target_pf_sha,
                    "candidate_identity": candidate_identity,
                    "source_model_digest": bindings.source_model_digest,
                    "candidate_model_digest": model_digest,
                    "mapping_digest": mapping_digest,
                    "operating_point_digest": operating_point_digest(pf_contract),
                    "metric_policy_digest": metric_policy_digest(metric_policy),
                    "threshold_set_sha256": threshold_set.sha256,
                    "source_status": source_pf.status.value,
                    "candidate_status": target_pf.status.value,
                    "fidelity_status": fidelity_status,
                    "retained_report_sha256": _file_digest(
                        store.path("metrics/retained_fidelity.json")),
                    "fidelity_report_sha256": (
                        _file_digest(store.path("metrics/fidelity.json"))
                        if fidelity_status is not None else None),
                })
                store.record_artifact("pf_binding", "pf/binding.json", bind_sha256=True)
                report_timings = {
                    "candidate_reconstruction": reconstruction_seconds,
                    "source_pf_end_to_end": source_pf_times["end_to_end"],
                    "candidate_pf_end_to_end": target_pf_times["end_to_end"],
                    "fidelity_evaluation": fidelity_seconds,
                }
                if "solver" in source_pf_times:
                    report_timings["source_pf_solver"] = source_pf_times["solver"]
                if "solver" in target_pf_times:
                    report_timings["candidate_pf_solver"] = target_pf_times["solver"]
                report_started = perf_counter()
                report = write_policy_outputs(
                    store=store, policy_id=policy.policy_id,
                    experiment_id=bindings.experiment_id,
                    attempt_id=bindings.attempt_id, source=source_network,
                    target=target_network, source_sidecar=source_sidecar,
                    target_sidecar=target_sidecar, mappings=result.composed_mappings,
                    pipeline=result, source_model_digest=bindings.source_model_digest,
                    sidecar_digest=bindings.source_sidecar_digest,
                    ledger_digest=bindings.source_ledger_digest,
                    candidate_model_digest=model_digest,
                    candidate_identity=candidate_identity, source_pf=source_pf,
                    candidate_pf=target_pf, contract=pf_contract,
                    threshold_set=threshold_set,
                    metrics=metrics, evaluation=evaluation,
                    resolved_thresholds=resolved_thresholds, timings=report_timings,
                    cutoff_kv=float(config.voltage_policy.cutoff_kv),
                )
                reports.append(report)
                stage_timing = {
                    "schema": "retained-fidelity-stage-timings/v1",
                    "run_id": run_id, "candidate_identity": candidate_identity,
                    "timings_seconds": {
                        **report_timings,
                        "retained_fidelity": retained_seconds,
                        "report_generation": perf_counter() - report_started,
                    },
                    "stage_status": {
                        "structural_gate": "NOT_RECORDED",
                        "ac_readiness_gate": "NOT_RECORDED",
                        "fresh_replay": "NOT_RUN",
                    },
                    "timing_scopes": {
                        "candidate_reconstruction": "full pipeline including materialization, persistence and Gates",
                        "source_pf_end_to_end": "executor call including setup and copy",
                        "source_pf_solver": "solver invocation only",
                        "candidate_pf_end_to_end": "executor call including setup and copy",
                        "candidate_pf_solver": "solver invocation only",
                        "fidelity_evaluation": "metric mapping comparison and threshold evaluation",
                        "retained_fidelity": "read-only grouped diagnostics",
                        "report_generation": "numeric observations and all comprehensive exports",
                    },
                    "admission_impact": "NONE",
                }
                store.write_json("metrics/stage_timings.json", stage_timing)
                store.record_artifact("stage_timings", "metrics/stage_timings.json",
                                      bind_sha256=True)
                replay_started = perf_counter()
                package_dir = Path(__file__).resolve().parents[1]
                replay_env = dict(os.environ)
                replay_env["PYTHONPATH"] = os.pathsep.join((
                    str(package_dir / "src"), str(package_dir),
                    replay_env.get("PYTHONPATH", "")))
                replay = subprocess.run(
                    [sys.executable, "-c", "from pathlib import Path; from "
                     "experiment_tools.retained_fidelity_driver import "
                     "verify_policy_artifacts; verify_policy_artifacts("
                     "Path(__import__('sys').argv[1]), require_fresh_replay=False)",
                     str(store.run_dir)],
                    cwd=package_dir, env=replay_env, capture_output=True, text=True,
                    check=False,
                )
                if replay.returncode:
                    raise RetainedAttemptError("FRESH_PROCESS_REPLAY_INVALID")
                stage_timing["timings_seconds"]["fresh_replay"] = perf_counter() - replay_started
                stage_timing["timing_scopes"]["fresh_replay"] = "independent process durable replay without PF"
                stage_timing["stage_status"]["fresh_replay"] = "RECORDED"
                store.write_json("metrics/stage_timings.json", stage_timing)
                store.record_artifact("stage_timings", "metrics/stage_timings.json",
                                      bind_sha256=True)
            verified = verify_policy_artifacts(store.run_dir)
            if verified.candidate_identity != candidate_identity:
                raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: readback")
            results.append(verified)
            _require_digest(canonical_model_digest(source_network, source_sidecar),
                            original_source_digest, "source mutation")
    if reports:
        export_cross_policy_report(output_root, tuple(reports))
        verify_cross_policy_report(output_root, tuple(row.run_dir for row in results))
    return tuple(results)


def _write_stop(
    root: Path, policies: Sequence[FrozenPolicyRun],
    completed: Sequence[CandidateProvenance], failed_policy: str, failure_code: str,
) -> None:
    """Write a single fail-closed stop record without overwriting old attempts."""
    states = {policy.policy_id: "NOT_RUN" for policy in policies}
    for row in completed:
        states[row.policy_id] = "COMPLETED"
    states[failed_policy] = "FAILED"
    payload = {
        "schema": "retained-fidelity-attempt-stop/v1",
        "failed_policy": failed_policy,
        "failure_code": failure_code,
        "policy_states": states,
    }
    root.mkdir(parents=True, exist_ok=True)
    with (root / "attempt-stop.json").open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, indent=2)
        stream.write("\n")


def verify_policy_artifacts(
    run_dir: Path, *, require_fresh_replay: bool = True,
) -> CandidateProvenance:
    """Reload target, maps, sidecar and stored identities without running PF."""
    run_dir = Path(run_dir)
    try:
        payload = json.loads((run_dir / "validation/candidate_provenance.json").read_text(
            encoding="utf-8"))
        if payload.pop("schema") != SCHEMA:
            raise ValueError("schema")
        candidate_identity = payload.pop("candidate_identity")
        if _digest(payload) != candidate_identity:
            raise ValueError("identity")
        target = PypsaAdapter().load(run_dir / "levels/L2.nc", "netcdf")
        source_config = ReductionConfig.model_validate(yaml.safe_load(
            (run_dir / "resolved_config.yaml").read_text(encoding="utf-8")))
        source_path = Path(source_config.input.path)
        if _file_digest(source_path) != payload["source_input_sha256"]:
            raise ValueError("source input bytes")
        source_sidecar = IdentitySidecar.from_rows(json.loads(
            (run_dir / "input/source_ids.json").read_text(encoding="utf-8")))
        ledger = ParallelSourceLedger.load_parquet(run_dir / "input/source_ledger_binding")
        if (_sidecar_digest(source_sidecar) != payload["source_sidecar_digest"] or
                source_ledger_digest(ledger) != payload["source_ledger_digest"]):
            raise ValueError("source bindings")
        sidecar = IdentitySidecar.from_rows(json.loads(
            (run_dir / "input/target_ids.json").read_text(encoding="utf-8")))
        maps = MappingBundle.load_parquet(run_dir / "maps")
        if canonical_model_digest(target, sidecar) != payload["candidate_model_digest"]:
            raise ValueError("target model")
        if maps.semantic_digest() != payload["mapping_digest"]:
            raise ValueError("maps")
        if (_file_digest(run_dir / "input/plan.json") != payload["partition_plan_sha256"] or
                _file_digest(run_dir / "validation/multi_voltage_structural.json") !=
                payload["structural_gate_sha256"] or
                _file_digest(run_dir / "validation/ac_model_readiness.json") !=
                payload["ac_readiness_sha256"]):
            raise ValueError("plan or Gate bytes")
        config = source_config
        if _semantic_config_digest(config) != payload["semantic_config_digest"]:
            raise ValueError("config")
        policy = json.loads((run_dir / "voltage_policy/resolved_policy.json").read_text(
            encoding="utf-8"))
        if policy["resolved"]["resolved_policy_fingerprint"] != payload["resolved_policy_fingerprint"]:
            raise ValueError("policy")
        structural = json.loads((run_dir / "validation/multi_voltage_structural.json").read_text(
            encoding="utf-8"))
        readiness = json.loads((run_dir / "validation/ac_model_readiness.json").read_text(
            encoding="utf-8"))
        if structural["status"] != payload["structural_status"] or readiness["status"] != payload["ac_readiness_status"]:
            raise ValueError("Gates")
        source_pf_status = candidate_pf_status = fidelity_status = None
        pf_binding_path = run_dir / "pf/binding.json"
        if pf_binding_path.exists():
            verify_policy_outputs(run_dir)
            timing = json.loads((run_dir / "metrics/stage_timings.json").read_text(
                encoding="utf-8"))
            if require_fresh_replay and (
                    timing["stage_status"]["fresh_replay"] != "RECORDED" or
                    "fresh_replay" not in timing["timings_seconds"]):
                raise ValueError("fresh replay evidence")
            binding = json.loads(pf_binding_path.read_text(encoding="utf-8"))
            source_pf = read_power_flow_result(run_dir / "pf/source.json")
            candidate_pf = read_power_flow_result(run_dir / "pf/candidate.json")
            frozen = FrozenThresholdSet.load()
            frozen_metric_policy = PowerFlowMetricPolicy(**frozen.payload["metric_policy"])
            source_payload = json.loads((run_dir / "pf/source.json").read_text(encoding="utf-8"))
            candidate_payload = json.loads((run_dir / "pf/candidate.json").read_text(encoding="utf-8"))
            if (binding["candidate_identity"] != candidate_identity or
                    binding["source_model_digest"] != payload["source_model_digest"] or
                    binding["candidate_model_digest"] != payload["candidate_model_digest"] or
                    binding["mapping_digest"] != payload["mapping_digest"] or
                    binding["source_result_sha256"] != source_payload["result_sha256"] or
                    binding["candidate_result_sha256"] != candidate_payload["result_sha256"] or
                    binding["source_status"] != source_pf.status.value or
                    binding["candidate_status"] != candidate_pf.status.value or
                    binding["threshold_set_sha256"] != frozen.sha256 or
                    binding["metric_policy_digest"] != metric_policy_digest(
                        frozen_metric_policy) or
                    binding["retained_report_sha256"] != _file_digest(
                        run_dir / "metrics/retained_fidelity.json") or
                    binding["fidelity_report_sha256"] != (
                        _file_digest(run_dir / "metrics/fidelity.json")
                        if binding["fidelity_status"] is not None else None)):
                raise ValueError("PF binding")
            source = PypsaAdapter().load(source_path, source_config.input.format)
            retained = compare_retained_network_fidelity(
                source_network=source, target_network=target,
                source_sidecar=source_sidecar, target_sidecar=sidecar,
                mappings=maps, source_pf=source_pf, target_pf=candidate_pf,
                cutoff_kv=(float(config.voltage_policy.cutoff_kv),),
            )
            persisted_retained = json.loads((run_dir / "metrics/retained_fidelity.json")
                                            .read_text(encoding="utf-8"))
            if retained.model_dump(mode="json") != persisted_retained:
                raise ValueError("retained metric replay")
            comparison = evaluation = resolved = None
            if source_pf.converged and candidate_pf.converged:
                mapping = derive_pf_comparison_mapping(
                    maps, source_bus_ids=_bus_ids(source, source_sidecar),
                    target_bus_ids=_bus_ids(target, sidecar),
                    source_branch_endpoints=_branch_endpoints(source, source_sidecar),
                    target_branch_endpoints=_branch_endpoints(target, sidecar),
                    source_component_by_bus=_component_map(source, source_sidecar),
                    target_component_by_bus=_component_map(target, sidecar),
                )
                comparison = compare_power_flow_results(
                    source_pf, candidate_pf, mapping, frozen_metric_policy)
                reference_p, reference_q = reference_cutset_magnitudes(source_pf, mapping)
                resolved = frozen.resolve(reference_p, reference_q)
                evaluation = evaluate_metric_thresholds_detailed(
                    comparison.metrics, resolved.thresholds,
                    threshold_digest=resolved.digest)
                fidelity = json.loads((run_dir / "metrics/fidelity.json").read_text(
                    encoding="utf-8"))
                if (fidelity["metric_digest"] != evaluation.metrics_digest or
                        fidelity["threshold_digest"] != resolved.digest or
                        fidelity["status"] != evaluation.fidelity_status.value or
                        fidelity["metrics"] != asdict(comparison.metrics) or
                        fidelity["thresholds"] != asdict(resolved.thresholds)):
                    raise ValueError("fidelity replay")
            verify_numeric_projection(
                run_dir=run_dir, source_pf=source_pf, candidate_pf=candidate_pf,
                source_model_digest=payload["source_model_digest"],
                sidecar_digest=payload["source_sidecar_digest"],
                candidate_model_digest=payload["candidate_model_digest"],
                candidate_identity=candidate_identity,
                operating_digest=binding["operating_point_digest"],
                threshold_set=frozen,
                metrics=comparison.metrics if comparison is not None else None,
                evaluation=evaluation, resolved_thresholds=resolved,
            )
            source_pf_status = source_pf.status.value
            candidate_pf_status = candidate_pf.status.value
            fidelity_status = binding["fidelity_status"]
        return CandidateProvenance(
            payload["policy_id"], run_dir, candidate_identity,
            payload["candidate_model_digest"], payload["mapping_digest"],
            payload["structural_status"], payload["ac_readiness_status"],
            source_pf_status, candidate_pf_status, fidelity_status,
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: durable readback") from exc
