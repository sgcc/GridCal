"""Read-only experiment adapters for numeric and comprehensive evidence."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Mapping

import pypsa

from psdcalc.reduction.adaptive.observations import (
    NumericFidelityObservation, NumericObservationBundle, PerformanceTimingObservation,
    fidelity_comparison_observation, power_flow_execution_observation,
)
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.core.ids import IdentitySidecar
from psdcalc.reduction.fidelity.contracts import (
    PowerFlowMetricSet, PowerFlowOperatingPointContract,
)
from psdcalc.reduction.fidelity.metrics import (
    PowerFlowThresholdEvaluation, operating_point_digest,
)
from psdcalc.reduction.fidelity.persistence import (
    FrozenThresholdSet, ResolvedPowerFlowThresholds,
)
from psdcalc.reduction.fidelity.pypsa_executor import PowerFlowExecutionResult
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.pipelines.multi_voltage import MultiVoltagePipelineResult
from psdcalc.reduction.policies.voltage_cutoff import VoltageCutoffPolicy
from psdcalc.reduction.reporting.comprehensive import (
    ComprehensiveReductionReport, ComprehensiveRunReport, MetricGroup,
    ReportProvenance,
)
from psdcalc.reduction.reporting.comprehensive_builder import (
    FidelityGroupInput, build_comprehensive_run,
)
from psdcalc.reduction.reporting.comprehensive_export import (
    export_report_bundle, render_markdown, render_metrics_csv,
    render_policy_comparison_csv, render_retained_fidelity_csv,
    render_voltage_layers_csv,
)
from psdcalc.reduction.reporting.opportunity import CompressionOpportunityReport
from psdcalc.reduction.validation.multi_voltage_final import MultiVoltageStructuralResult


class RetainedOutputError(ValueError):
    """Raised when a persisted output differs from its authoritative inputs."""


def _digest(value: object) -> str:
    """Hash canonical JSON without locations or timings."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _report(
    *, policy_id: str, experiment_id: str, attempt_id: str,
    source: pypsa.Network, target: pypsa.Network,
    source_sidecar: IdentitySidecar, target_sidecar: IdentitySidecar,
    mappings: MappingBundle, opportunity: CompressionOpportunityReport,
    structural: MultiVoltageStructuralResult, source_model_digest: str,
    sidecar_digest: str, ledger_digest: str,
    source_pf: PowerFlowExecutionResult, candidate_pf: PowerFlowExecutionResult,
    contract: PowerFlowOperatingPointContract, threshold_set_id: str,
    threshold_sha256: str, metrics: PowerFlowMetricSet | None,
    evaluation: PowerFlowThresholdEvaluation | None,
    resolved_thresholds: ResolvedPowerFlowThresholds | None,
    timings: Mapping[str, float], cutoff_kv: float,
) -> ComprehensiveRunReport:
    """Build one existing report model from authoritative results only."""
    groups = () if metrics is None or evaluation is None or resolved_thresholds is None else (
        FidelityGroupInput(MetricGroup.WHOLE_NETWORK, metrics, evaluation,
                           resolved_thresholds),)
    provenance = ReportProvenance(
        study_id="retained-fidelity-v1",
        run_id=f"{attempt_id.lower()}-{policy_id.lower()}",
        experiment_id=experiment_id, package_version="0.8.0",
        pypsa_version=str(pypsa.__version__),
        source_model_sha256=source_model_digest,
        source_sidecar_sha256=sidecar_digest,
        source_ledger_sha256=ledger_digest,
        metric_digest=evaluation.metrics_digest if evaluation else None,
        threshold_set_id=threshold_set_id, threshold_sha256=threshold_sha256,
    )
    return build_comprehensive_run(
        provenance=provenance, source_network=source,
        source_sidecar=source_sidecar, target_network=target,
        target_sidecar=target_sidecar, mappings=mappings,
        opportunity=opportunity, structural=structural,
        fidelity_groups=groups, source_pf=source_pf, candidate_pf=candidate_pf,
        solver_contract_id=contract.solver,
        solver_contract_digest=operating_point_digest(contract), timings=timings,
        voltage_policies=(VoltageCutoffPolicy(True, "freeze_at_or_above", cutoff_kv),),
        retained_cutoffs_kv=(cutoff_kv,),
    )


def numeric_rows(
    *, run_id: str,
    source_model_digest: str, sidecar_digest: str,
    candidate_model_digest: str, candidate_identity: str,
    source_pf: PowerFlowExecutionResult, candidate_pf: PowerFlowExecutionResult,
    operating_digest: str, threshold_set: FrozenThresholdSet,
    metrics: PowerFlowMetricSet | None,
    evaluation: PowerFlowThresholdEvaluation | None,
    resolved_thresholds: ResolvedPowerFlowThresholds | None,
) -> tuple[NumericFidelityObservation, ...]:
    """Project authoritative PF/fidelity results for write and independent replay."""
    # The durable PF adapter stores a string snapshot label. Match that exact
    # representation before projecting observations so a fresh reader sees the
    # same identity; solved values and solver results are untouched.
    source_observed = replace(
        source_pf, snapshot=None if source_pf.snapshot is None else str(source_pf.snapshot))
    candidate_observed = replace(
        candidate_pf,
        snapshot=None if candidate_pf.snapshot is None else str(candidate_pf.snapshot))
    common = dict(source_model_digest=source_model_digest,
                  source_sidecar_digest=sidecar_digest,
                  solver_contract_digest=operating_digest,
                  operating_point_digest=operating_digest,
                  threshold_set_id=threshold_set.payload["threshold_set_id"],
                  threshold_set_digest=threshold_set.sha256)
    rows = [power_flow_execution_observation(
        source_observed, observation_id=_digest([run_id, "source_pf"]), role="source",
        **common), power_flow_execution_observation(
        candidate_observed, observation_id=_digest([run_id, "candidate_pf"]),
        role="initial_candidate", candidate_model_digest=candidate_model_digest,
        candidate_identity=candidate_identity,
        source_pf_status=source_pf.status.value, **common)]
    if metrics is not None and evaluation is not None and resolved_thresholds is not None:
        rows.append(fidelity_comparison_observation(
            metrics, resolved_thresholds, evaluation,
            observation_id=_digest([run_id, "fidelity"]),
            role="initial_candidate", candidate_model_digest=candidate_model_digest,
            candidate_identity=candidate_identity, snapshot=source_observed.snapshot,
            source_pf_status=source_pf.status.value,
            candidate_pf_status=candidate_pf.status.value, **common))
    return tuple(rows)


def write_policy_outputs(
    *, store: ArtifactStore, policy_id: str, experiment_id: str,
    attempt_id: str, source: pypsa.Network, target: pypsa.Network,
    source_sidecar: IdentitySidecar, target_sidecar: IdentitySidecar,
    mappings: MappingBundle, pipeline: MultiVoltagePipelineResult,
    source_model_digest: str, sidecar_digest: str, ledger_digest: str,
    candidate_model_digest: str, candidate_identity: str,
    source_pf: PowerFlowExecutionResult, candidate_pf: PowerFlowExecutionResult,
    contract: PowerFlowOperatingPointContract, threshold_set: FrozenThresholdSet,
    metrics: PowerFlowMetricSet | None,
    evaluation: PowerFlowThresholdEvaluation | None,
    resolved_thresholds: ResolvedPowerFlowThresholds | None,
    timings: Mapping[str, float], cutoff_kv: float,
) -> ComprehensiveRunReport:
    """Write one policy's report and non-controller numeric companion."""
    run_id = f"{attempt_id.lower()}-{policy_id.lower()}"
    rows = numeric_rows(
        run_id=run_id, source_model_digest=source_model_digest,
        sidecar_digest=sidecar_digest,
        candidate_model_digest=candidate_model_digest,
        candidate_identity=candidate_identity, source_pf=source_pf,
        candidate_pf=candidate_pf, operating_digest=operating_point_digest(contract),
        threshold_set=threshold_set, metrics=metrics, evaluation=evaluation,
        resolved_thresholds=resolved_thresholds,
    )
    timing_specs = (
        ("source_pf_end_to_end", "source_pf_total", "source", "executor call including setup and copy"),
        ("source_pf_solver", "source_pf_solver", "source", "solver invocation only"),
        ("candidate_pf_end_to_end", "candidate_pf_total", "initial_candidate", "executor call including setup and copy"),
        ("candidate_pf_solver", "candidate_pf_solver", "initial_candidate", "solver invocation only"),
        ("candidate_reconstruction", "candidate_reconstruction", "initial_candidate", "full pipeline including persistence and structural/readiness Gates"),
        ("fidelity_evaluation", "fidelity_comparison", "initial_candidate", "metric mapping comparison and threshold evaluation"),
    )
    timing_rows = tuple(PerformanceTimingObservation(
        operation_name=operation, role=role, duration=timings[key],
        duration_unit="s", timing_scope=scope, run_id=run_id,
        process_identity=f"pid:{os.getpid()}", backend="pypsa",
        backend_version=str(pypsa.__version__),
        candidate_identity=candidate_identity if role != "source" else None,
    ) for key, operation, role, scope in timing_specs if key in timings)
    # This is an experiment projection, not an adaptive controller journal.
    bundle = NumericObservationBundle(run_id, rows, timing_rows, ())
    store.write_json("metrics/numeric_observations.json", bundle.to_payload())
    store.record_artifact("numeric_observations", "metrics/numeric_observations.json",
                          bind_sha256=True)
    report = _report(
        policy_id=policy_id, experiment_id=experiment_id, attempt_id=attempt_id,
        source=source, target=target, source_sidecar=source_sidecar,
        target_sidecar=target_sidecar, mappings=mappings,
        opportunity=pipeline.opportunity_report, structural=pipeline.structural,
        source_model_digest=source_model_digest, sidecar_digest=sidecar_digest,
        ledger_digest=ledger_digest, source_pf=source_pf, candidate_pf=candidate_pf,
        contract=contract, threshold_set_id=threshold_set.payload["threshold_set_id"],
        threshold_sha256=threshold_set.sha256, metrics=metrics,
        evaluation=evaluation, resolved_thresholds=resolved_thresholds,
        timings=timings, cutoff_kv=cutoff_kv)
    exported = export_report_bundle(
        ComprehensiveReductionReport(report_id="comprehensive", runs=(report,)),
        store.path("metrics"))
    for name, path in exported.items():
        store.record_artifact("comprehensive_" + name,
                              str(path.relative_to(store.run_dir)), bind_sha256=True)
    return report


def verify_policy_outputs(run_dir: Path) -> ComprehensiveRunReport:
    """Verify byte-bound files and independently regenerate each export."""
    root = Path(run_dir)
    manifest = json.loads((root / "run_manifest.json").read_text())
    keys = ("numeric_observations", "stage_timings", "comprehensive_json",
            "comprehensive_markdown", "comprehensive_metrics_csv",
            "comprehensive_voltage_layers_csv", "comprehensive_retained_fidelity_csv",
            "comprehensive_policy_comparison_csv")
    for name in keys:
        relative = manifest["artifacts"][name]
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        if actual != manifest["artifact_sha256"][name]:
            raise RetainedOutputError("report or observation artifact digest differs")
    obs = NumericObservationBundle.from_payload(json.loads(
        (root / "metrics/numeric_observations.json").read_text()))
    report = ComprehensiveReductionReport.model_validate_json(
        (root / "metrics/comprehensive.json").read_text())
    if (len(report.runs) != 1 or obs.run_id != report.runs[0].provenance.run_id
            or obs.adaptive_evaluations or len(obs.numeric_observations) < 2
            or not obs.performance_timing_observations):
        raise RetainedOutputError("observation role or report identity differs")
    expected = {
        "comprehensive_markdown": render_markdown(report),
        "comprehensive_metrics_csv": render_metrics_csv(report),
        "comprehensive_voltage_layers_csv": render_voltage_layers_csv(report),
        "comprehensive_retained_fidelity_csv": render_retained_fidelity_csv(report),
        "comprehensive_policy_comparison_csv": render_policy_comparison_csv(report),
    }
    for name, content in expected.items():
        if (root / manifest["artifacts"][name]).read_text() != content:
            raise RetainedOutputError("report exporters differ from canonical JSON")
    timing = json.loads((root / "metrics/stage_timings.json").read_text())
    if (timing.get("schema") != "retained-fidelity-stage-timings/v1" or
            timing.get("run_id") != obs.run_id):
        raise RetainedOutputError("stage timing identity differs")
    for value in timing["timings_seconds"].values():
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0):
            raise RetainedOutputError("invalid stage timing")
    if timing.get("stage_status", {}).get("structural_gate") != "NOT_RECORDED":
        raise RetainedOutputError("structural timing scope differs")
    for row in obs.performance_timing_observations:
        key = {"source_pf_total": "source_pf_end_to_end",
               "candidate_pf_total": "candidate_pf_end_to_end",
               "fidelity_comparison": "fidelity_evaluation"}.get(
                   row.operation_name, row.operation_name)
        if row.duration != timing["timings_seconds"][key]:
            raise RetainedOutputError("numeric timing and stage timing differ")
    return report.runs[0]


def verify_numeric_projection(
    *, run_dir: Path, source_pf: PowerFlowExecutionResult,
    candidate_pf: PowerFlowExecutionResult, source_model_digest: str,
    sidecar_digest: str, candidate_model_digest: str,
    candidate_identity: str, operating_digest: str,
    threshold_set: FrozenThresholdSet, metrics: PowerFlowMetricSet | None,
    evaluation: PowerFlowThresholdEvaluation | None,
    resolved_thresholds: ResolvedPowerFlowThresholds | None,
) -> None:
    """Compare durable observations and report cells with replayed PF metrics."""
    root = Path(run_dir)
    bundle = NumericObservationBundle.from_payload(json.loads(
        (root / "metrics/numeric_observations.json").read_text()))
    expected = numeric_rows(
        run_id=bundle.run_id, source_model_digest=source_model_digest,
        sidecar_digest=sidecar_digest, candidate_model_digest=candidate_model_digest,
        candidate_identity=candidate_identity, source_pf=source_pf,
        candidate_pf=candidate_pf, operating_digest=operating_digest,
        threshold_set=threshold_set, metrics=metrics, evaluation=evaluation,
        resolved_thresholds=resolved_thresholds)
    if bundle.numeric_observations != expected:
        raise RetainedOutputError("numeric observations differ from replayed PF results")
    report = ComprehensiveReductionReport.model_validate_json(
        (root / "metrics/comprehensive.json").read_text()).runs[0]
    fidelity = report.fidelity
    if (fidelity.pf_source_status.value != source_pf.status.value or
            fidelity.pf_candidate_status.value != candidate_pf.status.value or
            fidelity.metric_digest != (evaluation.metrics_digest if evaluation else None) or
            fidelity.threshold_digest != (resolved_thresholds.digest if resolved_thresholds else None) or
            fidelity.fidelity_status.value != (
                evaluation.fidelity_status.value if evaluation else None)):
        raise RetainedOutputError("comprehensive fidelity header differs from replay")
    expected_metrics = {
        row.metric_name: row for observation in expected
        for row in observation.metric_observations
    }
    report_metrics = {row.metric_name: row for row in fidelity.observations}
    if set(report_metrics) != set(expected_metrics):
        raise RetainedOutputError("comprehensive fidelity metric names differ")
    for name, row in report_metrics.items():
        authoritative = expected_metrics[name]
        if (row.observed.value != authoritative.observed_value.value or
                row.threshold.value != authoritative.resolved_threshold.value or
                row.severity != authoritative.severity or
                row.sample_count != authoritative.sample_count.value):
            raise RetainedOutputError("comprehensive fidelity metric differs from replay")


def export_cross_policy_report(root: Path, runs: tuple[ComprehensiveRunReport, ...]) -> None:
    """Export one canonical comparison of completed policy reports."""
    if not runs or len({run.provenance.run_id for run in runs}) != len(runs):
        raise RetainedOutputError("cross-policy run identity is invalid")
    output = Path(root) / "cross_policy"
    files = export_report_bundle(
        ComprehensiveReductionReport(report_id="comprehensive", runs=runs), output)
    manifest = {name: {"file": path.name,
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for name, path in files.items()}
    (output / "manifest.json").write_text(
        json.dumps({"schema": "retained-fidelity-cross-policy/v1",
                    "artifacts": manifest}, sort_keys=True, indent=2) + "\n")


def verify_cross_policy_report(root: Path, run_dirs: tuple[Path, ...]) -> None:
    """Reload all policy reports and verify the comparison in a fresh reader."""
    output = Path(root) / "cross_policy"
    manifest = json.loads((output / "manifest.json").read_text())
    if manifest.get("schema") != "retained-fidelity-cross-policy/v1":
        raise RetainedOutputError("cross-policy manifest schema differs")
    expected_runs = tuple(verify_policy_outputs(path) for path in run_dirs)
    report = ComprehensiveReductionReport.model_validate_json(
        (output / "comprehensive.json").read_text())
    if report.runs != expected_runs:
        raise RetainedOutputError("cross-policy reports differ from individual runs")
    expected = {
        "json": report.to_json(),
        "markdown": render_markdown(report),
        "metrics_csv": render_metrics_csv(report),
        "voltage_layers_csv": render_voltage_layers_csv(report),
        "retained_fidelity_csv": render_retained_fidelity_csv(report),
        "policy_comparison_csv": render_policy_comparison_csv(report),
    }
    if set(manifest.get("artifacts", {})) != set(expected):
        raise RetainedOutputError("cross-policy artifact set differs")
    for name, content in expected.items():
        item = manifest["artifacts"][name]
        path = output / item["file"]
        if (path.name != item["file"] or path.read_text() != content or
                hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]):
            raise RetainedOutputError("cross-policy export or digest differs")
