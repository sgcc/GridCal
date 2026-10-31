"""Persist and replay anonymous AC power-flow fidelity evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from enum import Enum
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import re
from typing import Any, Mapping

import numpy as np
import pandas as pd
import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.fidelity.contracts import (
    PF_FIDELITY_SOURCE_STAGE,
    PF_FIDELITY_TARGET_STAGE,
    PF_FIDELITY_VALID_STRUCTURAL_STATUS,
    PfFidelityStatus,
    PfSolveStatus,
    PowerFlowCoverage,
    PowerFlowErrorSummary,
    PowerFlowFidelityReport,
    PowerFlowInjectionMetrics,
    PowerFlowLossMetrics,
    PowerFlowMetricPolicy,
    PowerFlowMetricThresholds,
    PowerFlowOperatingPointContract,
)
from psdcalc.reduction.fidelity.mapping import (
    PfBranchComparisonEntry,
    PfBusComparisonEntry,
    PfComparisonMapping,
    PfComponentCorrespondence,
    PfCutsetComparisonEntry,
)
from psdcalc.reduction.fidelity.metrics import (
    MetricError,
    PowerFlowComparison,
    compare_power_flow_results,
    evaluate_metric_thresholds_detailed,
    metric_policy_digest,
    operating_point_digest,
)
from psdcalc.reduction.fidelity.pypsa_executor import (
    PowerFlowExecutionResult,
    PowerFlowExecutionStatus,
    PypsaAcPowerFlowExecutor,
)


EVIDENCE_SCHEMA_VERSION = "1.0"
_MODEL_COMPONENTS = frozenset({"Bus", "Line", "Transformer", "Generator", "Load", "ShuntImpedance"})
_BUS_REFERENCE_FIELDS = frozenset({"bus", "bus0", "bus1", "bus2", "bus3"})


class PowerFlowPersistenceError(RuntimeError):
    """A persistence, integrity, or replay contract failed closed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ReplayNumericTolerance:
    """Numeric comparison tolerance for replay only, independent of fidelity thresholds."""

    absolute: float = 1e-12
    relative: float = 1e-10

    def __post_init__(self) -> None:
        for name in ("absolute", "relative"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PowerFlowPersistenceError("PF_REPLAY_TOLERANCE_INVALID", f"{name} must be numeric")
            if not math.isfinite(float(value)) or value < 0:
                raise PowerFlowPersistenceError("PF_REPLAY_TOLERANCE_INVALID", f"{name} must be finite and nonnegative")


@dataclass(frozen=True)
class ThresholdDefinition:
    """Describe one threshold field without assigning a production value."""

    name: str
    unit: str
    classification: str
    mandatory: bool


@dataclass(frozen=True)
class PowerFlowThresholdSchema:
    """Reviewed threshold vocabulary and severity classes, with no acceptance values."""

    threshold_set_id: str = "UNCONFIGURED"
    version: str = "1"

    @property
    def definitions(self) -> tuple[ThresholdDefinition, ...]:
        """Return the full proposed threshold schema and HARD/DIAGNOSTIC classification."""
        return (
            ThresholdDefinition("vm_rmse_pu", "pu", "HARD", True),
            ThresholdDefinition("vm_p95_pu", "pu", "DIAGNOSTIC", False),
            ThresholdDefinition("vm_max_pu", "pu", "HARD", True),
            ThresholdDefinition("angle_rmse_deg", "deg", "DIAGNOSTIC", False),
            ThresholdDefinition("angle_p95_deg", "deg", "DIAGNOSTIC", False),
            ThresholdDefinition("angle_max_deg", "deg", "DIAGNOSTIC", False),
            ThresholdDefinition("branch_p_rmse_mw", "MW", "DIAGNOSTIC", False),
            ThresholdDefinition("branch_p_p95_mw", "MW", "DIAGNOSTIC", False),
            ThresholdDefinition("branch_p_max_mw", "MW", "DIAGNOSTIC", False),
            ThresholdDefinition("branch_q_rmse_mvar", "Mvar", "DIAGNOSTIC", False),
            ThresholdDefinition("branch_q_p95_mvar", "Mvar", "DIAGNOSTIC", False),
            ThresholdDefinition("branch_q_max_mvar", "Mvar", "DIAGNOSTIC", False),
            ThresholdDefinition("cutset_p_rmse_mw", "MW", "DIAGNOSTIC", False),
            ThresholdDefinition("cutset_p_max_mw", "MW", "HARD", True),
            ThresholdDefinition("cutset_q_rmse_mvar", "Mvar", "DIAGNOSTIC", False),
            ThresholdDefinition("cutset_q_max_mvar", "Mvar", "HARD", True),
            ThresholdDefinition("injection_p_rmse_mw", "MW", "DIAGNOSTIC", False),
            ThresholdDefinition("injection_p_max_mw", "MW", "DIAGNOSTIC", False),
            ThresholdDefinition("injection_q_rmse_mvar", "Mvar", "DIAGNOSTIC", False),
            ThresholdDefinition("injection_q_max_mvar", "Mvar", "DIAGNOSTIC", False),
            ThresholdDefinition("loss_abs_mw", "MW", "HARD", True),
            ThresholdDefinition("loss_relative", "fraction", "HARD", True),
            ThresholdDefinition("target_pf_convergence", "status", "HARD", True),
            ThresholdDefinition("mapping_coverage", "fraction", "HARD", True),
        )


@dataclass(frozen=True)
class PowerFlowThresholdProvenance:
    """Identify a preregistered threshold set; production values are not supplied here."""

    threshold_set_id: str
    version: str
    rationale: str
    pre_registration_date: str

    def __post_init__(self) -> None:
        if not self.threshold_set_id.strip() or self.threshold_set_id == "UNCONFIGURED":
            raise PowerFlowPersistenceError("PF_THRESHOLD_PROVENANCE_INVALID", "configured thresholds need a stable set ID")
        if not self.version.strip() or not self.rationale.strip():
            raise PowerFlowPersistenceError("PF_THRESHOLD_PROVENANCE_INVALID", "threshold version and rationale are required")
        try:
            date.fromisoformat(self.pre_registration_date)
        except ValueError as exc:
            raise PowerFlowPersistenceError("PF_THRESHOLD_PROVENANCE_INVALID", "pre_registration_date must be ISO date") from exc


@dataclass(frozen=True)
class PowerFlowFidelityEvidenceBundle:
    """Typed persisted run evidence; report values remain aggregate and anonymous."""

    schema_version: str
    model_id: str
    run_id: str
    config_sha256: str
    source_model_digest: str
    target_model_digest: str
    structural_report_digest: str
    source_structural_report_digest: str
    mapping_digest: str
    operating_point_contract: PowerFlowOperatingPointContract
    operating_point_digest: str
    metric_policy: PowerFlowMetricPolicy
    metric_policy_digest: str
    thresholds: PowerFlowMetricThresholds | None
    threshold_provenance: PowerFlowThresholdProvenance | None
    threshold_digest: str | None
    threshold_set_id: str
    solver_provenance: Mapping[str, str]
    solver_provenance_digest: str
    source_execution_summary: Mapping[str, object]
    target_execution_summary: Mapping[str, object]
    metrics_digest: str
    report_digest: str
    fidelity_report: PowerFlowFidelityReport
    structural_status: str
    replay_tolerance: ReplayNumericTolerance
    replay_tolerance_digest: str


@dataclass(frozen=True)
class PowerFlowReplayResult:
    """Result of a clean on-disk replay, preserving structural status independently."""

    report: PowerFlowFidelityReport
    structural_status: str
    replay_verified: bool
    source_execution_status: str
    target_execution_status: str


class PowerFlowFidelityEvidenceStore:
    """Persist models and internal mapping, then recompute fidelity evidence on replay."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        *,
        executor: PypsaAcPowerFlowExecutor | None = None,
        replay_tolerance: ReplayNumericTolerance = ReplayNumericTolerance(),
    ) -> None:
        self.artifact_store = artifact_store
        self.executor = executor or PypsaAcPowerFlowExecutor()
        self.replay_tolerance = replay_tolerance
        self.directory = artifact_store.path("pf_fidelity")

    def persist(
        self,
        *,
        model_id: str,
        config_sha256: str,
        structural_report_digest: str,
        source_structural_report_digest: str,
        structural_status: str,
        source_network: pypsa.Network,
        source_sidecar: IdentitySidecar,
        target_network: pypsa.Network,
        target_sidecar: IdentitySidecar,
        comparison_mapping: PfComparisonMapping,
        operating_point_contract: PowerFlowOperatingPointContract,
        metric_policy: PowerFlowMetricPolicy,
        thresholds: PowerFlowMetricThresholds | None = None,
        threshold_provenance: PowerFlowThresholdProvenance | None = None,
    ) -> PowerFlowFidelityEvidenceBundle:
        """Export clean PF inputs, reload, execute and atomically register evidence artifacts."""
        _check_sha256(config_sha256, "config_sha256")
        _check_sha256(structural_report_digest, "structural_report_digest")
        _check_sha256(source_structural_report_digest, "source_structural_report_digest")
        if not model_id.strip():
            raise PowerFlowPersistenceError("PF_EVIDENCE_INVALID", "model_id must be non-empty")
        if structural_status not in {PF_FIDELITY_VALID_STRUCTURAL_STATUS, "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID"}:
            raise PowerFlowPersistenceError("PF_EVIDENCE_INVALID", "unsupported structural status")
        if (thresholds is None) != (threshold_provenance is None):
            raise PowerFlowPersistenceError(
                "PF_THRESHOLD_PROVENANCE_INVALID",
                "threshold values and provenance must be supplied together",
            )
        self._prepare_directory()
        adapter = PypsaAdapter()
        source_digest = canonical_model_digest(source_network, source_sidecar)
        target_digest = canonical_model_digest(target_network, target_sidecar)
        source_path = self.directory / "source_model.nc"
        target_path = self.directory / "target_model.nc"
        adapter.export(_structural_input_copy(source_network), source_path, "netcdf")
        adapter.export(_structural_input_copy(target_network), target_path, "netcdf")
        _write_sidecar_parquet(source_sidecar, self.directory / "source_sidecar.parquet")
        _write_sidecar_parquet(target_sidecar, self.directory / "target_sidecar.parquet")
        mapping_digest = _write_mapping_parquet(comparison_mapping, self.directory / "comparison_map.parquet")

        # PF always starts from newly loaded structural files, never caller objects.
        loaded_source = adapter.load(source_path, "netcdf")
        loaded_target = adapter.load(target_path, "netcdf")
        loaded_source_sidecar = _read_sidecar_parquet(self.directory / "source_sidecar.parquet")
        loaded_target_sidecar = _read_sidecar_parquet(self.directory / "target_sidecar.parquet")
        loaded_mapping, loaded_mapping_digest = _read_mapping_parquet(self.directory / "comparison_map.parquet")
        if loaded_mapping_digest != mapping_digest:
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "comparison map changed on readback")
        if canonical_model_digest(loaded_source, loaded_source_sidecar) != source_digest:
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "source model changed on NetCDF readback")
        if canonical_model_digest(loaded_target, loaded_target_sidecar) != target_digest:
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "target model changed on NetCDF readback")
        source_result, target_result = _execute_pair(
            self.executor, loaded_source, loaded_source_sidecar,
            loaded_target, loaded_target_sidecar, operating_point_contract,
        )
        op_digest = operating_point_digest(operating_point_contract)
        policy_digest = metric_policy_digest(metric_policy)
        threshold_digest = _threshold_evidence_digest(thresholds, threshold_provenance)
        solver_contract = _solver_contract_digest(operating_point_contract)
        report = _build_report(
            model_id=model_id,
            run_id=self.artifact_store.manifest.run_id,
            config_sha256=config_sha256,
            structural_report_digest=structural_report_digest,
            source_structural_report_digest=source_structural_report_digest,
            structural_status=structural_status,
            source_model_digest=source_digest,
            target_model_digest=target_digest,
            mapping_digest=mapping_digest,
            solver_contract_digest=solver_contract,
            metric_policy_digest_value=policy_digest,
            contract=operating_point_contract,
            mapping=loaded_mapping,
            source_result=source_result,
            target_result=target_result,
            metric_policy=metric_policy,
            thresholds=thresholds,
            threshold_digest_value=threshold_digest,
        )
        bundle = _make_bundle(
            model_id=model_id,
            run_id=self.artifact_store.manifest.run_id,
            source_digest=source_digest,
            target_digest=target_digest,
            structural_report_digest=structural_report_digest,
            source_structural_report_digest=source_structural_report_digest,
            mapping_digest=mapping_digest,
            contract=operating_point_contract,
            metric_policy=metric_policy,
            thresholds=thresholds,
            threshold_provenance=threshold_provenance,
            threshold_digest_value=threshold_digest,
            report=report,
            structural_status=structural_status,
            replay_tolerance=self.replay_tolerance,
            source_result=source_result,
            target_result=target_result,
        )
        bundle = self._write_bundle(bundle)
        return bundle

    def replay(self) -> PowerFlowReplayResult:
        """Reload every persisted input, verify its digest, rerun PF and compare evidence."""
        evidence_path = self.directory / "evidence.json"
        try:
            raw = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PowerFlowPersistenceError("PF_EVIDENCE_MISSING_OR_INVALID", str(exc)) from exc
        if raw.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
            raise PowerFlowPersistenceError("PF_EVIDENCE_SCHEMA_UNSUPPORTED", "unsupported evidence schema version")
        if _digest(raw.get("solver_provenance")) != raw.get("solver_provenance_digest"):
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "solver provenance digest mismatch")
        _require_supported_pypsa(raw.get("solver_provenance", {}).get("pypsa_version", ""))
        _require_supported_pypsa(importlib.metadata.version("pypsa"))
        adapter = PypsaAdapter()
        source_network = adapter.load(self.directory / "source_model.nc", "netcdf")
        target_network = adapter.load(self.directory / "target_model.nc", "netcdf")
        source_sidecar = _read_sidecar_parquet(self.directory / "source_sidecar.parquet")
        target_sidecar = _read_sidecar_parquet(self.directory / "target_sidecar.parquet")
        mapping, mapping_digest = _read_mapping_parquet(self.directory / "comparison_map.parquet")
        if mapping_digest != raw["mapping_digest"]:
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "mapping digest mismatch")
        source_digest = canonical_model_digest(source_network, source_sidecar)
        target_digest = canonical_model_digest(target_network, target_sidecar)
        if source_digest != raw["source_model_digest"]:
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "source model digest mismatch")
        if target_digest != raw["target_model_digest"]:
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "target model digest mismatch")
        contract = _contract_from_dict(raw["operating_point_contract"])
        if (raw["solver_provenance"].get("solver") != contract.solver or
                raw["solver_provenance"].get("solver_contract_digest") != _solver_contract_digest(contract)):
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "solver provenance does not match operating contract")
        op_digest = operating_point_digest(contract)
        if op_digest != raw["operating_point_digest"]:
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "operating point digest mismatch")
        policy = PowerFlowMetricPolicy(**raw["metric_policy"])
        policy_digest = metric_policy_digest(policy)
        if policy_digest != raw["metric_policy_digest"]:
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "metric policy digest mismatch")
        thresholds_raw = raw.get("thresholds")
        thresholds = PowerFlowMetricThresholds(**thresholds_raw) if thresholds_raw is not None else None
        provenance_raw = raw.get("threshold_provenance")
        provenance = (PowerFlowThresholdProvenance(**provenance_raw)
                      if provenance_raw is not None else None)
        if (thresholds is None) != (provenance is None):
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "threshold provenance presence mismatch")
        expected_threshold_set_id = provenance.threshold_set_id if provenance else "UNCONFIGURED"
        if raw.get("threshold_set_id") != expected_threshold_set_id:
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "threshold set identity mismatch")
        threshold_digest = _threshold_evidence_digest(thresholds, provenance)
        if threshold_digest != raw.get("threshold_digest"):
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "threshold digest mismatch")
        tolerance = ReplayNumericTolerance(**raw["replay_tolerance"])
        if _digest(asdict(tolerance)) != raw.get("replay_tolerance_digest"):
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "replay tolerance digest mismatch")
        report_file = json.loads((self.directory / "report.json").read_text(encoding="utf-8"))
        report_digest = _digest(report_file)
        if report_digest != raw["report_digest"] or report_file != raw["fidelity_report"]:
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "persisted report digest mismatch")
        metrics_rows = pd.read_parquet(self.directory / "metrics.parquet").to_dict(orient="records")
        if _digest(_canonical_metric_rows(metrics_rows)) != raw["metrics_digest"]:
            raise PowerFlowPersistenceError("PF_EVIDENCE_DIGEST_MISMATCH", "persisted metrics digest mismatch")
        expected_metric_rows = _metric_rows(report_file)
        if _canonical_metric_rows(metrics_rows) != _canonical_metric_rows(expected_metric_rows):
            raise PowerFlowPersistenceError(
                "PF_EVIDENCE_DIGEST_MISMATCH", "aggregate metrics artifact differs from the bound report")
        structural_digest = raw["structural_report_digest"]
        source_structural_digest = raw["source_structural_report_digest"]
        _check_sha256(structural_digest, "structural_report_digest")
        _check_sha256(source_structural_digest, "source_structural_report_digest")
        source_result, target_result = _execute_pair(
            self.executor, source_network, source_sidecar,
            target_network, target_sidecar, contract,
        )
        rebuilt = _build_report(
            model_id=raw["model_id"], run_id=raw["run_id"],
            config_sha256=raw["config_sha256"],
            structural_report_digest=structural_digest,
            source_structural_report_digest=source_structural_digest,
            structural_status=raw["structural_status"],
            source_model_digest=source_digest, target_model_digest=target_digest,
            mapping_digest=mapping_digest,
            solver_contract_digest=_solver_contract_digest(contract),
            metric_policy_digest_value=policy_digest,
            contract=contract, mapping=mapping,
            source_result=source_result, target_result=target_result,
            metric_policy=policy, thresholds=thresholds,
            threshold_digest_value=threshold_digest,
        )
        rebuilt_public = rebuilt.to_public_dict()
        if not _semantic_equal(report_file, rebuilt_public, tolerance):
            raise PowerFlowPersistenceError("PF_REPLAY_REPORT_MISMATCH", "replayed report differs from persisted evidence")
        replay_summary = _execution_summary(source_result, target_result, "source")
        target_summary = _execution_summary(source_result, target_result, "target")
        _semantic_assert(raw["source_execution_summary"], replay_summary, tolerance, "source execution summary")
        _semantic_assert(raw["target_execution_summary"], target_summary, tolerance, "target execution summary")
        return PowerFlowReplayResult(
            report=rebuilt,
            structural_status=raw["structural_status"],
            replay_verified=True,
            source_execution_status=replay_summary["status"],
            target_execution_status=target_summary["status"],
        )

    def _prepare_directory(self) -> None:
        """Create the fidelity subdirectory beneath the ArtifactStore run directory."""
        self.directory.mkdir(exist_ok=True)

    def _write_bundle(self, bundle: PowerFlowFidelityEvidenceBundle) -> PowerFlowFidelityEvidenceBundle:
        """Write evidence JSON, public report, aggregate metrics, and manifest references."""
        report_data = bundle.fidelity_report.to_public_dict()
        report_path = self.directory / "report.json"
        _write_json(report_path, report_data)
        metric_rows = _metric_rows(report_data)
        metrics_path = self.directory / "metrics.parquet"
        pd.DataFrame(metric_rows, columns=("metric", "field", "value")).to_parquet(metrics_path, index=False)
        persisted_metric_rows = pd.read_parquet(metrics_path).to_dict(orient="records")
        metrics_digest_value = _digest(_canonical_metric_rows(persisted_metric_rows))
        bundle = replace(bundle, metrics_digest=metrics_digest_value)
        evidence = _bundle_payload(bundle)
        evidence["fidelity_report"] = report_data
        evidence_path = self.directory / "evidence.json"
        _write_json(evidence_path, evidence)
        for name, path in (
            ("pf_fidelity_source_model", self.directory / "source_model.nc"),
            ("pf_fidelity_target_model", self.directory / "target_model.nc"),
            ("pf_fidelity_source_sidecar", self.directory / "source_sidecar.parquet"),
            ("pf_fidelity_target_sidecar", self.directory / "target_sidecar.parquet"),
            ("pf_fidelity_evidence", evidence_path),
            ("pf_fidelity_report", report_path),
            ("pf_fidelity_metrics", metrics_path),
            ("pf_fidelity_comparison_map", self.directory / "comparison_map.parquet"),
        ):
            relative = path.relative_to(self.artifact_store.run_dir).as_posix()
            self.artifact_store.record_artifact(name, relative)
        self.artifact_store.set_metadata(software_versions={
            "python": platform.python_version(),
            "pypsa": importlib.metadata.version("pypsa"),
            "pf_solver": bundle.solver_provenance["solver"],
        })
        return bundle


def canonical_model_digest(network: pypsa.Network, sidecar: IdentitySidecar) -> str:
    """Hash PF input semantics canonically, keyed by StableId and excluding solved outputs."""
    components: list[dict[str, object]] = []
    for component in sorted(network.components, key=lambda item: item.name):
        if component.name not in _MODEL_COMPONENTS:
            continue
        defaults = component.defaults
        if not isinstance(defaults, pd.DataFrame) or "status" not in defaults.columns:
            raise PowerFlowPersistenceError("PF_MODEL_DIGEST_UNSUPPORTED", f"missing input metadata for {component.name}")
        inputs = [str(attribute) for attribute, row in defaults.iterrows()
                  if str(row["status"]).startswith("Input")]
        static_rows: list[dict[str, object]] = []
        for backend_name, row in component.static.iterrows():
            stable_id = sidecar.source_id(component.name, str(backend_name))
            attributes: dict[str, object] = {}
            for attribute in inputs:
                if attribute not in row.index:
                    continue
                value = row[attribute]
                if attribute in _BUS_REFERENCE_FIELDS and not pd.isna(value) and str(value):
                    try:
                        value = sidecar.source_id("Bus", str(value)).encode()
                    except Exception as exc:
                        raise PowerFlowPersistenceError("PF_MODEL_DIGEST_INVALID_IDENTITY", str(exc)) from exc
                attributes[attribute] = _normalize(value)
            static_rows.append({"source_id": stable_id.encode(), "inputs": attributes})
        dynamic_rows: list[dict[str, object]] = []
        for attribute, frame in sorted(component.dynamic.items()):
            if attribute not in inputs:
                continue
            rows = []
            # PyPSA dynamic tables use snapshots as the row index and component
            # backend names as columns. Persist by StableId, never by row order.
            for backend_name in frame.columns:
                stable_id = sidecar.source_id(component.name, str(backend_name))
                rows.append({"source_id": stable_id.encode(),
                             "snapshots": [_normalize(value) for value in frame[backend_name].tolist()]})
            dynamic_rows.append({"attribute": attribute,
                                 "snapshots": [_normalize(value) for value in frame.index],
                                 "rows": sorted(rows, key=lambda row: row["source_id"])})
        components.append({"component": component.name,
                           "static": sorted(static_rows, key=lambda row: row["source_id"]),
                           "dynamic": dynamic_rows})
    payload = {
        "components": components,
        "snapshots": [_normalize(value) for value in network.snapshots],
        "snapshot_weightings": _frame_payload(network.snapshot_weightings),
        "investment_periods": [_normalize(value) for value in network.investment_periods],
        "investment_period_weightings": _frame_payload(network.investment_period_weightings),
    }
    return _digest(payload)


def _structural_input_copy(network: pypsa.Network) -> pypsa.Network:
    """Return a detached input-only copy without solved Output columns or frames."""
    result = network.copy()
    for component in result.components:
        defaults = component.defaults
        if not isinstance(defaults, pd.DataFrame) or "status" not in defaults.columns:
            continue
        output_attributes = [str(attribute) for attribute, row in defaults.iterrows()
                             if str(row["status"]).startswith("Output")]
        removable = [attribute for attribute in output_attributes
                     if attribute in component.static.columns]
        if removable:
            component.static = component.static.drop(columns=removable)
        for attribute in output_attributes:
            component.dynamic.pop(attribute, None)
    return result


def require_supported_pypsa_version(version: str) -> None:
    """Require the recorded/runtime PyPSA major/minor contract to be 1.2.x."""
    match = re.match(r"^(\d+)\.(\d+)\.", version)
    if match is None or tuple(map(int, match.groups())) != (1, 2):
        raise PowerFlowPersistenceError("PF_REPLAY_SOLVER_INCOMPATIBLE", f"PyPSA {version!r} is outside supported 1.2.x")


def _require_supported_pypsa(version: str) -> None:
    """Internal compatibility guard for persisted and current runtime versions."""
    require_supported_pypsa_version(version)


def _execute_pair(executor: PypsaAcPowerFlowExecutor, source_network: pypsa.Network,
                  source_sidecar: IdentitySidecar, target_network: pypsa.Network,
                  target_sidecar: IdentitySidecar,
                  contract: PowerFlowOperatingPointContract
                  ) -> tuple[PowerFlowExecutionResult, PowerFlowExecutionResult | None]:
    source = executor.solve(source_network, source_sidecar, contract)
    if source.status is not PowerFlowExecutionStatus.PF_CONVERGED:
        return source, None
    return source, executor.solve(target_network, target_sidecar, contract)


def _build_report(*, model_id: str, run_id: str, config_sha256: str,
                  structural_report_digest: str, source_structural_report_digest: str,
                  structural_status: str, source_model_digest: str, target_model_digest: str,
                  mapping_digest: str, solver_contract_digest: str,
                  metric_policy_digest_value: str, contract: PowerFlowOperatingPointContract,
                  mapping: PfComparisonMapping, source_result: PowerFlowExecutionResult,
                  target_result: PowerFlowExecutionResult | None,
                  metric_policy: PowerFlowMetricPolicy,
                  thresholds: PowerFlowMetricThresholds | None,
                  threshold_digest_value: str | None) -> PowerFlowFidelityReport:
    source_status = _solve_status(source_result)
    target_status = PfSolveStatus.NOT_RUN if target_result is None else _solve_status(target_result)
    comparison: PowerFlowComparison | None = None
    failure_reason: str | None = None
    hard_threshold_failures: tuple[str, ...] = ()
    diagnostic_threshold_failures: tuple[str, ...] = ()
    if source_status is PfSolveStatus.NOT_CONVERGED:
        fidelity_status = PfFidelityStatus.SOURCE_PF_NOT_CONVERGED
        failure_reason = source_result.failure_reason or "source PF did not converge"
    elif source_status is not PfSolveStatus.CONVERGED:
        fidelity_status = PfFidelityStatus.FAILED
        failure_reason = source_result.failure_reason or source_result.status.value
    elif target_result is None:
        fidelity_status = PfFidelityStatus.FAILED
        failure_reason = "target PF was not attempted"
    elif target_status is PfSolveStatus.NOT_CONVERGED:
        fidelity_status = PfFidelityStatus.TARGET_PF_NOT_CONVERGED
        failure_reason = target_result.failure_reason or "target PF did not converge"
    elif target_status is not PfSolveStatus.CONVERGED:
        fidelity_status = PfFidelityStatus.FAILED
        failure_reason = target_result.failure_reason or target_result.status.value
    else:
        comparison = compare_power_flow_results(source_result, target_result, mapping, metric_policy)
        if thresholds is not None:
            evaluation = evaluate_metric_thresholds_detailed(comparison.metrics, thresholds)
            fidelity_status = evaluation.fidelity_status
            hard_threshold_failures = evaluation.hard_failures
            diagnostic_threshold_failures = evaluation.diagnostic_failures
        else:
            fidelity_status = PfFidelityStatus.PF_FIDELITY_MEASURED
    metrics = comparison.metrics if comparison is not None else None
    coverage = comparison.coverage if comparison is not None else mapping.coverage
    return PowerFlowFidelityReport(
        model_id=model_id, run_id=run_id, config_sha256=config_sha256,
        structural_report_digest=structural_report_digest,
        source_structural_report_digest=source_structural_report_digest,
        mapping_digest=mapping_digest, solver_contract_digest=solver_contract_digest,
        metric_policy_digest=metric_policy_digest_value,
        s1_model_digest=source_model_digest, s4_model_digest=target_model_digest,
        source_stage=PF_FIDELITY_SOURCE_STAGE, target_stage=PF_FIDELITY_TARGET_STAGE,
        target_structural_status=structural_status, operating_point=contract,
        source_pf_status=source_status, target_pf_status=target_status,
        bus_voltage_metrics=metrics.bus_voltage if metrics else None,
        bus_voltage_preserved_metrics=metrics.bus_voltage_preserved if metrics else None,
        bus_voltage_aggregated_metrics=metrics.bus_voltage_aggregated if metrics else None,
        angle_metrics=metrics.angle if metrics else None,
        branch_p_metrics=metrics.branch_p if metrics else None,
        branch_q_metrics=metrics.branch_q if metrics else None,
        branch_p1_metrics=metrics.branch_p1 if metrics else None,
        branch_q1_metrics=metrics.branch_q1 if metrics else None,
        cutset_p_metrics=metrics.cutset_p if metrics else None,
        cutset_q_metrics=metrics.cutset_q if metrics else None,
        injection_metrics=metrics.cluster_injection if metrics else None,
        loss_metrics=metrics.passive_branch_losses if metrics else None,
        coverage=coverage, fidelity_status=fidelity_status,
        operating_point_digest=operating_point_digest(contract),
        thresholds_digest=threshold_digest_value,
        failure_reason=failure_reason,
        threshold_hard_failures=hard_threshold_failures,
        threshold_diagnostic_failures=diagnostic_threshold_failures,
    )


def _solve_status(result: PowerFlowExecutionResult) -> PfSolveStatus:
    """Map the executor's public result state into the independent report state."""
    if result.status is PowerFlowExecutionStatus.PF_CONVERGED:
        return PfSolveStatus.CONVERGED
    if result.status is PowerFlowExecutionStatus.PF_NOT_CONVERGED:
        return PfSolveStatus.NOT_CONVERGED
    return PfSolveStatus.NOT_RUN


def _make_bundle(*, model_id: str, run_id: str, source_digest: str, target_digest: str,
                 structural_report_digest: str, source_structural_report_digest: str,
                 mapping_digest: str, contract: PowerFlowOperatingPointContract,
                 metric_policy: PowerFlowMetricPolicy,
                 thresholds: PowerFlowMetricThresholds | None,
                 threshold_provenance: PowerFlowThresholdProvenance | None,
                 threshold_digest_value: str | None,
                 report: PowerFlowFidelityReport, structural_status: str,
                 replay_tolerance: ReplayNumericTolerance,
                 source_result: PowerFlowExecutionResult,
                 target_result: PowerFlowExecutionResult | None) -> PowerFlowFidelityEvidenceBundle:
    """Assemble the immutable typed evidence envelope."""
    solver_provenance = {
        "python_version": platform.python_version(),
        "pypsa_version": importlib.metadata.version("pypsa"),
        "solver": contract.solver,
        "solver_contract_digest": _solver_contract_digest(contract),
    }
    return PowerFlowFidelityEvidenceBundle(
        schema_version=EVIDENCE_SCHEMA_VERSION, model_id=model_id, run_id=run_id,
        config_sha256=report.config_sha256,
        source_model_digest=source_digest, target_model_digest=target_digest,
        structural_report_digest=structural_report_digest,
        source_structural_report_digest=source_structural_report_digest,
        mapping_digest=mapping_digest, operating_point_contract=contract,
        operating_point_digest=operating_point_digest(contract), metric_policy=metric_policy,
        metric_policy_digest=metric_policy_digest(metric_policy), thresholds=thresholds,
        threshold_provenance=threshold_provenance,
        threshold_digest=threshold_digest_value,
        threshold_set_id=(threshold_provenance.threshold_set_id if threshold_provenance
                          else "UNCONFIGURED"), solver_provenance=solver_provenance,
        solver_provenance_digest=_digest(solver_provenance),
        source_execution_summary=_execution_summary(source_result, target_result, "source"),
        target_execution_summary=_execution_summary(source_result, target_result, "target"),
        metrics_digest="", report_digest=_digest(report.to_public_dict()),
        fidelity_report=report, structural_status=structural_status,
        replay_tolerance=replay_tolerance,
        replay_tolerance_digest=_digest(asdict(replay_tolerance)),
    )


def _execution_summary(source: PowerFlowExecutionResult,
                       target: PowerFlowExecutionResult | None, which: str) -> dict[str, object]:
    """Return a compact execution record without solved arrays or object identities."""
    result = source if which == "source" else target
    if result is None:
        return {"status": "NOT_RUN", "converged": False, "subnetwork_count": 0,
                "nonconverged_subnetwork_count": 0}
    states = [item.converged for item in result.subnetwork_status]
    return {"status": result.status.value, "converged": result.converged,
            "subnetwork_count": len(states),
            "nonconverged_subnetwork_count": sum(not value for value in states)}


def _bundle_payload(bundle: PowerFlowFidelityEvidenceBundle) -> dict[str, object]:
    """Serialize contracts and anonymous summaries into canonical evidence JSON."""
    return {
        "schema_version": bundle.schema_version,
        "model_id": bundle.model_id,
        "run_id": bundle.run_id,
        "config_sha256": bundle.config_sha256,
        "source_model_digest": bundle.source_model_digest,
        "target_model_digest": bundle.target_model_digest,
        "structural_report_digest": bundle.structural_report_digest,
        "source_structural_report_digest": bundle.source_structural_report_digest,
        "mapping_digest": bundle.mapping_digest,
        "operating_point_contract": _contract_to_dict(bundle.operating_point_contract),
        "operating_point_digest": bundle.operating_point_digest,
        "metric_policy": asdict(bundle.metric_policy),
        "metric_policy_digest": bundle.metric_policy_digest,
        "thresholds": asdict(bundle.thresholds) if bundle.thresholds else None,
        "threshold_provenance": (asdict(bundle.threshold_provenance)
                                  if bundle.threshold_provenance else None),
        "threshold_digest": bundle.threshold_digest,
        "threshold_set_id": bundle.threshold_set_id,
        "solver_provenance": dict(bundle.solver_provenance),
        "solver_provenance_digest": bundle.solver_provenance_digest,
        "source_execution_summary": dict(bundle.source_execution_summary),
        "target_execution_summary": dict(bundle.target_execution_summary),
        "metrics_digest": bundle.metrics_digest,
        "report_digest": bundle.report_digest,
        "structural_status": bundle.structural_status,
        "replay_tolerance": asdict(bundle.replay_tolerance),
        "replay_tolerance_digest": bundle.replay_tolerance_digest,
    }


def _contract_to_dict(contract: PowerFlowOperatingPointContract) -> dict[str, object]:
    """Encode snapshot labels without losing timestamp or scalar type on reload."""
    payload = asdict(contract)
    payload["snapshots"] = [_encode_snapshot(value) for value in contract.snapshots]
    return payload


def _contract_from_dict(payload: Mapping[str, object]) -> PowerFlowOperatingPointContract:
    """Restore the typed operating contract from evidence JSON."""
    values = dict(payload)
    values["snapshots"] = tuple(_decode_snapshot(value) for value in values["snapshots"])
    return PowerFlowOperatingPointContract(**values)


def _encode_snapshot(value: object) -> dict[str, object]:
    """Tag supported immutable snapshot scalars and tuple labels."""
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return {"type": "timestamp", "value": pd.Timestamp(value).isoformat()}
    if isinstance(value, tuple):
        return {"type": "tuple", "value": [_encode_snapshot(item) for item in value]}
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (str, int, float, bool)):
        return {"type": type(value).__name__, "value": value}
    raise PowerFlowPersistenceError("PF_EVIDENCE_INVALID", f"unsupported snapshot type: {type(value).__name__}")


def _decode_snapshot(value: Mapping[str, object]) -> object:
    """Decode a tagged snapshot scalar or tuple label."""
    kind, raw = value["type"], value["value"]
    if kind == "timestamp":
        return pd.Timestamp(str(raw))
    if kind == "tuple":
        if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
            raise PowerFlowPersistenceError("PF_EVIDENCE_INVALID", "tuple snapshot payload is invalid")
        return tuple(_decode_snapshot(item) for item in raw)
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    if kind == "bool":
        return bool(raw)
    if kind == "str":
        return str(raw)
    raise PowerFlowPersistenceError("PF_EVIDENCE_INVALID", f"unsupported snapshot encoding: {kind}")


def _write_sidecar_parquet(sidecar: IdentitySidecar, path: Path) -> None:
    """Persist stable identity bindings as an internal Parquet artifact."""
    pd.DataFrame(sidecar.to_rows(), columns=("component", "backend_name", "source_id")).to_parquet(path, index=False)


def _read_sidecar_parquet(path: Path) -> IdentitySidecar:
    """Reload a sidecar and reject duplicate or malformed identity bindings."""
    try:
        rows = pd.read_parquet(path).to_dict(orient="records")
        return IdentitySidecar.from_rows([{key: str(value) for key, value in row.items()} for row in rows])
    except Exception as exc:
        raise PowerFlowPersistenceError("PF_EVIDENCE_IDENTITY_INVALID", str(exc)) from exc


def _write_mapping_parquet(mapping: PfComparisonMapping, path: Path) -> str:
    """Persist all derived StableId-based comparison-map rows in deterministic Parquet."""
    rows = _mapping_rows(mapping)
    pd.DataFrame(rows, columns=("kind", "key", "payload")).to_parquet(path, index=False)
    return _digest(rows)


def _read_mapping_parquet(path: Path) -> tuple[PfComparisonMapping, str]:
    """Reload the full comparison mapping and recompute its canonical digest."""
    try:
        frame = pd.read_parquet(path)
        rows = [{"kind": str(row.kind), "key": str(row.key), "payload": str(row.payload)}
                for row in frame.itertuples(index=False)]
        mapping = _mapping_from_rows(rows)
        return mapping, _digest(_mapping_rows(mapping))
    except PowerFlowPersistenceError:
        raise
    except Exception as exc:
        raise PowerFlowPersistenceError("PF_EVIDENCE_MAPPING_INVALID", str(exc)) from exc


def _mapping_rows(mapping: PfComparisonMapping) -> list[dict[str, str]]:
    """Flatten map classes into canonical JSON payload rows for Parquet."""
    rows: list[dict[str, str]] = []
    def add(kind: str, key: str, payload: object) -> None:
        rows.append({"kind": kind, "key": key, "payload": _canonical_json(payload)})
    for entry in mapping.bus_entries:
        add("bus", entry.source_bus_id.encode(), {
            "source_bus_id": entry.source_bus_id.encode(), "target_bus_id": entry.target_bus_id.encode(),
            "comparison_type": entry.comparison_type, "source_component_id": entry.source_component_id,
            "target_component_id": entry.target_component_id})
    for entry in mapping.branch_entries:
        add("branch", entry.source_branch_id.encode(), _branch_payload(entry))
    for item in mapping.component_correspondence:
        add("component", item.source_component_id, asdict(item))
    for cluster, entries in mapping.cutsets_by_cluster.items():
        add("cutset", cluster.encode(), [_cutset_payload(entry) for entry in entries])
    for target, entries in mapping.corridor_members.items():
        add("corridor", target.encode(), [_branch_payload(entry) for entry in entries])
    add("coverage", "coverage", asdict(mapping.coverage))
    return sorted(rows, key=lambda row: (row["kind"], row["key"], row["payload"]))


def _mapping_from_rows(rows: list[dict[str, str]]) -> PfComparisonMapping:
    """Rebuild a validated-shaped comparison map from persisted Parquet rows."""
    buses: list[PfBusComparisonEntry] = []
    branches: list[PfBranchComparisonEntry] = []
    components: list[PfComponentCorrespondence] = []
    cutsets: dict[StableId, tuple[PfCutsetComparisonEntry, ...]] = {}
    corridors: dict[StableId, tuple[PfBranchComparisonEntry, ...]] = {}
    coverage: PowerFlowCoverage | None = None
    for row in rows:
        payload = json.loads(row["payload"])
        if row["kind"] == "bus":
            buses.append(PfBusComparisonEntry(
                StableId.decode(payload["source_bus_id"]), StableId.decode(payload["target_bus_id"]),
                payload["comparison_type"], payload["source_component_id"], payload["target_component_id"]))
        elif row["kind"] == "branch":
            branches.append(_branch_from_payload(payload))
        elif row["kind"] == "component":
            components.append(PfComponentCorrespondence(**payload))
        elif row["kind"] == "cutset":
            cutsets[StableId.decode(row["key"])] = tuple(_cutset_from_payload(value) for value in payload)
        elif row["kind"] == "corridor":
            corridors[StableId.decode(row["key"])] = tuple(_branch_from_payload(value) for value in payload)
        elif row["kind"] == "coverage":
            coverage = PowerFlowCoverage(**payload)
    if coverage is None:
        raise PowerFlowPersistenceError("PF_EVIDENCE_MAPPING_INVALID", "mapping coverage row is absent")
    restored = PfComparisonMapping(tuple(buses), tuple(branches), tuple(components),
                                   cutsets, corridors, "", coverage)
    full_digest = _digest(_mapping_rows(restored))
    return replace(restored, mapping_digest=full_digest)


def _branch_payload(entry: PfBranchComparisonEntry) -> dict[str, object]:
    """Serialize one source branch comparison row."""
    return {"source_branch_id": entry.source_branch_id.encode(),
            "target_branch_id": entry.target_branch_id.encode() if entry.target_branch_id else None,
            "component": entry.component, "source_bus0": entry.source_bus0.encode(),
            "source_bus1": entry.source_bus1.encode(),
            "mapped_source_bus0": entry.mapped_source_bus0.encode(),
            "mapped_source_bus1": entry.mapped_source_bus1.encode(),
            "orientation_sign": entry.orientation_sign,
            "comparison_type": entry.comparison_type}


def _branch_from_payload(value: Mapping[str, object]) -> PfBranchComparisonEntry:
    """Restore a branch mapping row with stable IDs."""
    return PfBranchComparisonEntry(
        StableId.decode(str(value["source_branch_id"])),
        StableId.decode(str(value["target_branch_id"])) if value["target_branch_id"] else None,
        value["component"], StableId.decode(str(value["source_bus0"])),
        StableId.decode(str(value["source_bus1"])), StableId.decode(str(value["mapped_source_bus0"])),
        StableId.decode(str(value["mapped_source_bus1"])), value["orientation_sign"], value["comparison_type"])


def _cutset_payload(entry: PfCutsetComparisonEntry) -> dict[str, object]:
    """Serialize one cutset terminal row."""
    return {"cluster_bus_id": entry.cluster_bus_id.encode(),
            "source_branch_id": entry.source_branch_id.encode(),
            "target_branch_id": entry.target_branch_id.encode(),
            "orientation_sign": entry.orientation_sign,
            "source_terminal_index": entry.source_terminal_index,
            "target_terminal_index": entry.target_terminal_index}


def _cutset_from_payload(value: Mapping[str, object]) -> PfCutsetComparisonEntry:
    """Restore one cutset terminal row."""
    return PfCutsetComparisonEntry(
        StableId.decode(str(value["cluster_bus_id"])),
        StableId.decode(str(value["source_branch_id"])),
        StableId.decode(str(value["target_branch_id"])), value["orientation_sign"],
        value["source_terminal_index"], value["target_terminal_index"])


def _metric_rows(report: Mapping[str, object]) -> list[dict[str, object]]:
    """Flatten aggregate report metrics only, never per-object solved arrays."""
    rows: list[dict[str, object]] = []
    metrics = report.get("metrics", {})
    for metric, payload in metrics.items():
        if isinstance(payload, dict):
            rows.extend({"metric": metric, "field": key, "value": value}
                        for key, value in payload.items())
        else:
            rows.append({"metric": metric, "field": "value", "value": payload})
    return sorted(rows, key=lambda row: (str(row["metric"]), str(row["field"])))


def _canonical_metric_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Canonicalize Parquet metric rows before digest comparison."""
    return sorted(({"metric": str(row["metric"]), "field": str(row["field"]),
                   "value": _normalize(row["value"])} for row in rows),
                  key=lambda row: (row["metric"], row["field"]))


def _solver_contract_digest(contract: PowerFlowOperatingPointContract) -> str:
    """Digest solver selection and numeric execution options separately from the operating point."""
    return _digest({key: getattr(contract, key) for key in (
        "solver", "tolerance", "max_iterations", "distribute_slack", "slack_weights", "use_seed")})


def _threshold_evidence_digest(
    thresholds: PowerFlowMetricThresholds | None,
    provenance: PowerFlowThresholdProvenance | None,
) -> str | None:
    """Bind configured threshold values to their preregistration provenance."""
    if thresholds is None and provenance is None:
        return None
    if thresholds is None or provenance is None:
        raise PowerFlowPersistenceError(
            "PF_THRESHOLD_PROVENANCE_INVALID", "threshold values and provenance must be paired")
    return _digest({"thresholds": asdict(thresholds), "provenance": asdict(provenance)})


def _frame_payload(frame: pd.DataFrame) -> dict[str, object]:
    """Canonicalize a PyPSA weighting table by typed index, columns and values."""
    return {"index": [_normalize(value) for value in frame.index],
            "columns": [_normalize(value) for value in frame.columns],
            "values": [[_normalize(value) for value in row] for row in frame.to_numpy().tolist()]}


def _normalize(value: object) -> object:
    """Convert common PyPSA scalar values into deterministic JSON-compatible values."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return {"timestamp": pd.Timestamp(value).isoformat()}
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if not math.isfinite(value):
            return {"float": repr(value)}
        return value
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, StableId):
        return value.encode()
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, np.ndarray, pd.Index)):
        return [_normalize(item) for item in list(value)]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    raise PowerFlowPersistenceError(
        "PF_MODEL_DIGEST_UNSUPPORTED_VALUE",
        f"unsupported canonical value type: {type(value).__name__}",
    )


def _canonical_json(value: object) -> str:
    """Serialize an object with sorted keys and normalized scalar forms."""
    return json.dumps(_normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    """Return SHA-256 over canonical UTF-8 JSON."""
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _check_sha256(value: str, name: str) -> None:
    """Validate a lowercase SHA-256 string."""
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise PowerFlowPersistenceError("PF_EVIDENCE_INVALID", f"{name} must be lowercase SHA-256")


def _write_json(path: Path, payload: object) -> None:
    """Write canonical JSON with atomic replacement."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _semantic_assert(expected: object, actual: object, tolerance: ReplayNumericTolerance, label: str) -> None:
    """Raise a fail-closed replay mismatch with the relevant contract label."""
    if not _semantic_equal(expected, actual, tolerance):
        raise PowerFlowPersistenceError("PF_REPLAY_REPORT_MISMATCH", f"{label} differs on replay")


def _semantic_equal(expected: object, actual: object, tolerance: ReplayNumericTolerance) -> bool:
    """Compare nested semantic values, applying replay-only tolerance to numeric leaves."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(expected), float(actual), rel_tol=tolerance.relative,
                            abs_tol=tolerance.absolute)
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(
            _semantic_equal(expected[key], actual[key], tolerance) for key in expected)
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(
            _semantic_equal(left, right, tolerance) for left, right in zip(expected, actual))
    return expected == actual
