"""Durable, fail-closed persistence and semantic replay for adaptive runs.

The evidence format stores one frozen PyPSA S1 plus source identity, resolved
configuration, actions, protection and controller inputs. Candidate networks are
intentionally rebuilt during replay instead of being copied into the run bundle.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass, replace
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping

import pypsa

from psdcalc.reduction.adaptive.actions import ReductionAction
from psdcalc.reduction.adaptive.candidates import PypsaAdaptiveCandidateBuilder
from psdcalc.reduction.adaptive.controller import AdaptiveReductionController
from psdcalc.reduction.adaptive.contracts import (
    AdaptiveContractError,
    AdaptiveReductionConfig,
    AdaptiveReductionReport,
    CandidateEvaluation,
)
from psdcalc.reduction.adaptive.journal import JournalIntegrityError, ReductionActionJournal
from psdcalc.reduction.adaptive.pypsa_runtime import PypsaAdaptiveRuntime
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import ReductionConfig
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.fidelity.contracts import PowerFlowMetricPolicy, PowerFlowOperatingPointContract
from psdcalc.reduction.fidelity.metrics import operating_point_digest
from psdcalc.reduction.fidelity.persistence import (
    FrozenThresholdSet,
    ReplayNumericTolerance,
    canonical_model_digest,
)
from psdcalc.reduction.protection.engine import ProtectionSet


EVIDENCE_SCHEMA = "adaptive-reduction-evidence/v1"
_ADAPTIVE_DIR = "adaptive"
_ACTION_JOURNAL_REASON = "Stage-4 local expansion candidate"


class AdaptiveEvidenceErrorCode(StrEnum):
    """Stable fail-closed error categories for adaptive evidence replay."""

    MISSING_OR_INVALID = "ADAPTIVE_EVIDENCE_MISSING_OR_INVALID"
    DIGEST_MISMATCH = "ADAPTIVE_EVIDENCE_DIGEST_MISMATCH"
    INPUT_MISMATCH = "ADAPTIVE_REPLAY_INPUT_MISMATCH"
    SEQUENCE_MISMATCH = "ADAPTIVE_REPLAY_SEQUENCE_MISMATCH"
    SELECTION_MISMATCH = "ADAPTIVE_REPLAY_SELECTION_MISMATCH"
    TERMINAL_MISMATCH = "ADAPTIVE_REPLAY_TERMINAL_MISMATCH"


class AdaptiveEvidenceError(RuntimeError):
    """Raised when persisted adaptive evidence is missing, changed, or unreplayable."""

    def __init__(self, code: AdaptiveEvidenceErrorCode, detail: str) -> None:
        """Keep a stable machine-readable code and concrete diagnostic detail."""
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")


@dataclass(frozen=True)
class AdaptiveReplayResult:
    """Result of cleanly rebuilding and rerunning one persisted adaptive run."""

    verified: bool
    report_digest: str
    semantic_digest: str
    report: AdaptiveReductionReport
    runtime_seconds: float


class AdaptiveReductionEvidenceStore:
    """Persist controller inputs/results and replay from disk in a fresh process."""

    def __init__(self, artifact_store: ArtifactStore) -> None:
        """Bind a writer to an existing ArtifactStore-owned run directory."""
        self.artifact_store = artifact_store
        self.directory = artifact_store.path(_ADAPTIVE_DIR)

    def persist(
        self,
        *,
        runtime: PypsaAdaptiveRuntime,
        reduction_config: ReductionConfig,
        adaptive_config: AdaptiveReductionConfig,
        initial_active_action_ids: tuple[str, ...],
        report: AdaptiveReductionReport,
    ) -> AdaptiveReductionReport:
        """Persist authoritative inputs, full controller evidence, and manifest refs.

        Candidate networks are not serialized: replay rebuilds each from S1 and
        the explicit action set through the Stage-2 builder and existing Gates.
        """
        try:
            if not isinstance(runtime, PypsaAdaptiveRuntime):
                raise TypeError("the initial durable replay adapter requires PypsaAdaptiveRuntime")
            if report.s1_model_digest != runtime.source_model_digest:
                raise ValueError("report is not bound to runtime S1")
            if report.s1_sidecar_digest != runtime.source_sidecar_digest:
                raise ValueError("report is not bound to runtime sidecar")
            if report.config_digest != adaptive_config.digest:
                raise ValueError("report is not bound to adaptive config")
            if report.action_catalog_digest != runtime.action_catalog_digest:
                raise ValueError("report is not bound to action catalog")
            self.directory.mkdir(exist_ok=False)
            input_dir = self.directory
            source_path = input_dir / "frozen_s1.nc"
            PypsaAdapter().export(runtime._source, source_path, "netcdf")
            sidecar_rows = runtime._source_sidecar.to_rows()
            actions = tuple(runtime._actions)
            action_payloads = [item.canonical_payload() for item in actions]
            _validate_path_privacy(action_payloads, "action_catalog")
            protection = runtime.protection_set
            protection_payload = _protection_payload(protection)
            _validate_path_privacy(reduction_config.model_dump(mode="json"))
            threshold_set = runtime.threshold_set
            threshold_path = input_dir / "threshold_set.json"
            threshold_path.write_bytes(threshold_set.raw_bytes)
            _write_json(input_dir / "source_sidecar.json", sidecar_rows)
            _write_json(input_dir / "reduction_config.json", reduction_config.model_dump(mode="json"))
            _write_json(input_dir / "adaptive_config.json", adaptive_config.to_dict())
            _write_json(input_dir / "operating_point.json", _canonical(asdict(runtime.operating_point)))
            _write_json(input_dir / "metric_policy.json", asdict(runtime.metric_policy))
            _write_json(input_dir / "protection.json", protection_payload)
            _write_json(input_dir / "action_catalog.json", action_payloads)
            _write_json(input_dir / "initial_action_set.json", list(sorted(initial_active_action_ids)))
            initial_journal = ReductionActionJournal.start(tuple(
                action for action in actions if action.action_id in set(initial_active_action_ids)
            ))
            if initial_journal.action_set_fingerprint != report.initial_action_set_fingerprint:
                raise ValueError("initial action set does not match report")
            durable_report = replace(report, evidence_reference="adaptive/evidence.json")
            report_payload = durable_report.to_dict()
            _validate_path_privacy(report_payload, "report")
            _write_json(input_dir / "report.json", report_payload)
            _write_json(input_dir / "iterations.json", report_payload["iterations"])
            iteration_bindings = _iteration_bindings(report_payload["iterations"])
            _write_json(input_dir / "iteration_bindings.json", iteration_bindings)
            candidate_rows = _candidate_evaluation_rows(report_payload)
            _validate_candidate_evaluation_rows(candidate_rows)
            _write_json(input_dir / "candidate_evaluations.json", candidate_rows)
            journal = _journal_from_report(actions, initial_active_action_ids, durable_report)
            if journal.digest != report.journal_digest:
                raise ValueError("reconstructed action journal does not match controller report")
            digests = {
                "s1_model_digest": canonical_model_digest(runtime._source, runtime._source_sidecar),
                "s1_sidecar_digest": _digest(sidecar_rows),
                "reduction_config_digest": _digest(reduction_config.model_dump(mode="json")),
                "adaptive_config_digest": adaptive_config.digest,
                "operating_point_digest": operating_point_digest(runtime._operating_point),
                "solver_contract_digest": runtime.solver_contract_digest,
                "metric_policy_digest": runtime.metric_policy_digest,
                "protection_digest": _digest(_protection_payload(protection)),
                "threshold_set_id": runtime.threshold_set_id,
                "threshold_set_version": str(threshold_set.payload.get("threshold_version")),
                "threshold_set_digest": threshold_set.sha256,
                "action_catalog_digest": runtime.action_catalog_digest,
                "initial_action_set_fingerprint": initial_journal.action_set_fingerprint,
                "journal_digest": journal.digest,
                "ordered_iteration_evidence_digest": _digest(report_payload["iterations"]),
                "iteration_bindings_digest": _digest(iteration_bindings),
                "candidate_evaluation_digest": _digest(candidate_rows),
                "final_report_digest": _digest(report_payload),
                "terminal_reason": durable_report.terminal_reason.value,
                "final_action_set_fingerprint": _final_action_fingerprint(durable_report),
                "final_candidate_model_digest": _final_value(durable_report, "model_digest"),
                "final_mapping_bundle_digest": _final_value(durable_report, "mapping_digest"),
            }
            run_identity = _digest({
                "s1": digests["s1_model_digest"],
                "sidecar": digests["s1_sidecar_digest"],
                "reduction": digests["reduction_config_digest"],
                "adaptive": digests["adaptive_config_digest"],
                "catalog": digests["action_catalog_digest"],
            })
            _write_json(input_dir / "journal.json", {
                "schema": "adaptive-action-journal-binding/v1",
                "run_identity": run_identity,
                "journal": journal.to_dict(),
            })
            evidence = {
                "schema": EVIDENCE_SCHEMA,
                "run_identity": run_identity,
                "semantic_digest": _digest(digests),
                "digests": digests,
                "artifacts": {
                    "frozen_s1": "adaptive/frozen_s1.nc",
                    "source_sidecar": "adaptive/source_sidecar.json",
                    "reduction_config": "adaptive/reduction_config.json",
                    "adaptive_config": "adaptive/adaptive_config.json",
                    "operating_point": "adaptive/operating_point.json",
                    "metric_policy": "adaptive/metric_policy.json",
                    "protection": "adaptive/protection.json",
                    "threshold_set": "adaptive/threshold_set.json",
                    "action_catalog": "adaptive/action_catalog.json",
                    "initial_action_set": "adaptive/initial_action_set.json",
                    "journal": "adaptive/journal.json",
                    "iterations": "adaptive/iterations.json",
                    "iteration_bindings": "adaptive/iteration_bindings.json",
                    "candidate_evaluations": "adaptive/candidate_evaluations.json",
                    "report": "adaptive/report.json",
                },
                "runtime_policy": {"excluded_from_semantic_digest": True},
            }
            _write_json(input_dir / "evidence.json", evidence)
            for name, relative in evidence["artifacts"].items():
                self.artifact_store.record_artifact(f"adaptive_{name}", relative)
            self.artifact_store.record_artifact("adaptive_evidence", "adaptive/evidence.json")
            self.artifact_store.set_metadata(input_hash=digests["s1_model_digest"],
                                             config_hash=digests["reduction_config_digest"])
            return durable_report
        except AdaptiveEvidenceError:
            raise
        except Exception as exc:
            try:
                self.artifact_store.fail("adaptive_persistence", exc)
            except Exception:
                pass
            raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.MISSING_OR_INVALID,
                                        f"cannot persist adaptive run: {exc}") from exc

    @classmethod
    def replay(
        cls,
        run_directory: str | Path,
        *,
        replay_tolerance: ReplayNumericTolerance = ReplayNumericTolerance(),
    ) -> AdaptiveReplayResult:
        """Reload persisted inputs, reconstruct PyPSA runtime, and rerun Stage 4."""
        started = time.perf_counter()
        directory = Path(run_directory).resolve()
        adaptive_dir = directory / _ADAPTIVE_DIR
        try:
            evidence = _read_json(adaptive_dir / "evidence.json")
            if evidence.get("schema") != EVIDENCE_SCHEMA:
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.MISSING_OR_INVALID,
                                            "unsupported adaptive evidence schema")
            artifacts = evidence.get("artifacts")
            if not isinstance(artifacts, dict) or set(artifacts) != {
                "frozen_s1", "source_sidecar", "reduction_config", "adaptive_config",
                "operating_point", "metric_policy", "protection", "threshold_set",
                "action_catalog", "initial_action_set", "journal", "iterations", "iteration_bindings",
                "candidate_evaluations", "report",
            }:
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.MISSING_OR_INVALID,
                                            "adaptive evidence artifact index is incomplete")
            paths = {name: _safe_artifact_path(directory, relative)
                     for name, relative in artifacts.items()}
            sidecar = IdentitySidecar.from_rows(_read_json(paths["source_sidecar"]))
            source = PypsaAdapter().load(paths["frozen_s1"], "netcdf")
            if canonical_model_digest(source, sidecar) != evidence["digests"]["s1_model_digest"]:
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "reloaded S1 semantic digest differs")
            if _digest(sidecar.to_rows()) != evidence["digests"]["s1_sidecar_digest"]:
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "reloaded S1 sidecar digest differs")
            reduction_raw = _read_json(paths["reduction_config"])
            reduction_config = ReductionConfig.model_validate(reduction_raw)
            reduction_digest = _digest(reduction_config.model_dump(mode="json"))
            if reduction_digest != evidence["digests"]["reduction_config_digest"]:
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "reduction config digest differs")
            adaptive_config = _adaptive_config(_read_json(paths["adaptive_config"]))
            if adaptive_config.digest != evidence["digests"]["adaptive_config_digest"]:
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "adaptive config digest differs")
            operating = _operating_point(_read_json(paths["operating_point"]))
            metric_policy = PowerFlowMetricPolicy(**_read_json(paths["metric_policy"]))
            actions = tuple(ReductionAction.from_payload(row)
                            for row in _read_json(paths["action_catalog"]))
            action_payloads = [action.canonical_payload() for action in sorted(
                actions, key=lambda item: item.action_id)]
            action_digest = _digest({"schema_version": "1.0", "actions": action_payloads})
            if action_digest != evidence["digests"]["action_catalog_digest"]:
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "action catalog digest differs")
            protection = _protection_from_payload(_read_json(paths["protection"]))
            if _digest(_protection_payload(protection)) != evidence["digests"].get("protection_digest"):
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "resolved protection digest differs")
            threshold_set = FrozenThresholdSet.load(paths["threshold_set"])
            if threshold_set.sha256 != evidence["digests"]["threshold_set_digest"]:
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "threshold artifact digest differs")
            if (str(threshold_set.payload.get("threshold_set_id")) != evidence["digests"]["threshold_set_id"]
                    or str(threshold_set.payload.get("threshold_version")) !=
                    evidence["digests"]["threshold_set_version"]):
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.INPUT_MISMATCH,
                                            "threshold identity/version differs")
            builder = PypsaAdaptiveCandidateBuilder(
                source_network=source, source_sidecar=sidecar, actions=actions,
                reduction_config=reduction_config, protection=protection,
                protection_base_dir=adaptive_dir, protection_is_resolved=True,
            )
            runtime = PypsaAdaptiveRuntime(
                source_network=source, source_sidecar=sidecar, builder=builder,
                actions=actions, reduction_config_digest=builder.reduction_config_digest,
                operating_point=operating, metric_policy=metric_policy,
                threshold_set=threshold_set,
            )
            _verify_runtime_bindings(runtime, evidence["digests"])
            initial = tuple(_read_json(paths["initial_action_set"]))
            initial_journal = ReductionActionJournal.start(tuple(
                action for action in actions if action.action_id in set(initial)
            ))
            if initial_journal.active_action_ids != tuple(sorted(initial)):
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.INPUT_MISMATCH,
                                            "initial action set contains unknown or duplicate IDs")
            if initial_journal.action_set_fingerprint != evidence["digests"]["initial_action_set_fingerprint"]:
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "initial action-set fingerprint differs")
            report_expected = _read_json(paths["report"])
            iterations_expected = _read_json(paths["iterations"])
            iteration_bindings = _read_json(paths["iteration_bindings"])
            candidates_expected = _read_json(paths["candidate_evaluations"])
            if iterations_expected != report_expected.get("iterations"):
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "iteration artifact differs from final report")
            if iteration_bindings != _iteration_bindings(iterations_expected):
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "iteration semantic bindings differ")
            if _digest(iteration_bindings) != evidence["digests"].get("iteration_bindings_digest"):
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "iteration binding digest differs")
            _validate_candidate_evaluation_rows(candidates_expected)
            if candidates_expected != _candidate_evaluation_rows(report_expected):
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "candidate evaluation artifact differs from report")
            if _digest(candidates_expected) != evidence["digests"].get("candidate_evaluation_digest"):
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "candidate evaluation artifact digest differs")
            journal_binding = _read_json(paths["journal"])
            if (set(journal_binding) != {"schema", "run_identity", "journal"}
                    or journal_binding.get("schema") != "adaptive-action-journal-binding/v1"
                    or journal_binding.get("run_identity") != evidence.get("run_identity")):
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.INPUT_MISMATCH,
                                            "action journal belongs to a different adaptive run")
            stored_journal = ReductionActionJournal.from_dict(journal_binding["journal"])
            if stored_journal.digest != evidence["digests"]["journal_digest"]:
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "action journal digest differs")
            expected_report_digest = _digest(report_expected)
            if expected_report_digest != evidence["digests"]["final_report_digest"]:
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "final report digest differs")
            computed = AdaptiveReductionController().run(
                runtime=runtime, actions=actions, initial_active_action_ids=initial,
                config=adaptive_config,
            )
            computed = replace(computed, evidence_reference="adaptive/evidence.json")
            _verify_reconstructed_journal(actions, initial, computed, stored_journal)
            _compare_report(report_expected, computed.to_dict(), replay_tolerance)
            if computed.terminal_reason.value != evidence["digests"]["terminal_reason"]:
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.TERMINAL_MISMATCH,
                                            "terminal reason changed on replay")
            semantic_digest = _digest(evidence["digests"])
            if semantic_digest != evidence.get("semantic_digest"):
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "semantic evidence digest differs")
            expected_identity = _digest({
                "s1": evidence["digests"]["s1_model_digest"],
                "sidecar": evidence["digests"]["s1_sidecar_digest"],
                "reduction": evidence["digests"]["reduction_config_digest"],
                "adaptive": evidence["digests"]["adaptive_config_digest"],
                "catalog": evidence["digests"]["action_catalog_digest"],
            })
            if expected_identity != evidence.get("run_identity"):
                raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                            "run identity binding differs")
            _verify_manifest_references(directory, artifacts)
            return AdaptiveReplayResult(
                verified=True, report_digest=expected_report_digest,
                semantic_digest=semantic_digest, report=computed,
                runtime_seconds=time.perf_counter() - started,
            )
        except AdaptiveEvidenceError:
            raise
        except JournalIntegrityError as exc:
            raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                        f"action journal validation failed: {exc}") from exc
        except Exception as exc:
            raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.MISSING_OR_INVALID,
                                        f"adaptive replay failed closed: {exc}") from exc


def _write_json(path: Path, value: object) -> None:
    """Write canonical, path-independent JSON with a trailing newline."""
    path.write_text(json.dumps(_canonical(value), ensure_ascii=False,
                               sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    """Read one JSON artifact and require an object or array root."""
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.MISSING_OR_INVALID,
                                    f"cannot read {path.name}: {exc}") from exc
    if not isinstance(result, (dict, list)):
        raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.MISSING_OR_INVALID,
                                    f"invalid JSON root in {path.name}")
    return result


def _digest(value: object) -> str:
    """Hash canonical JSON content independently of runtime and output paths."""
    payload = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical(value: object) -> object:
    """Convert immutable domain values into deterministic JSON primitives."""
    if isinstance(value, StableId):
        return value.encode()
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {key: _canonical(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported semantic evidence value: {type(value).__name__}")


def _protection_payload(protection: ProtectionSet) -> dict[str, object]:
    """Serialize every ProtectionSet dimension using StableIds, not backend names."""
    return {
        "traced": sorted(item.encode() for item in protection.traced),
        "preserved_components": sorted(item.encode() for item in protection.preserved_components),
        "terminal_buses": sorted(item.encode() for item in protection.terminal_buses),
        "singleton_buses": sorted(item.encode() for item in protection.singleton_buses),
        "cannot_link": sorted(sorted(item.encode() for item in group)
                              for group in protection.cannot_link),
    }


def _protection_from_payload(value: Mapping[str, object]) -> ProtectionSet:
    """Reload a resolved protection set and reject missing or extra dimensions."""
    names = {"traced", "preserved_components", "terminal_buses", "singleton_buses", "cannot_link"}
    if set(value) != names:
        raise ValueError("protection payload schema mismatch")
    decode = lambda rows: frozenset(StableId.decode(str(item)) for item in rows)
    pairs = frozenset(frozenset(StableId.decode(str(item)) for item in row)
                      for row in value["cannot_link"])
    return ProtectionSet(decode(value["traced"]), decode(value["preserved_components"]),
                         decode(value["terminal_buses"]), decode(value["singleton_buses"]), pairs)


def _adaptive_config(value: Mapping[str, object]) -> AdaptiveReductionConfig:
    """Restore the immutable adaptive config and its optional compression bounds."""
    from psdcalc.reduction.adaptive.contracts import (
        AdaptiveStrategy, CompressionRequirement,
    )

    raw = dict(value)
    requirement = raw.get("compression_requirement")
    if requirement is not None:
        raw["compression_requirement"] = CompressionRequirement(**requirement)
    raw["strategy"] = AdaptiveStrategy(raw["strategy"])
    return AdaptiveReductionConfig(**raw)


def _operating_point(value: Mapping[str, object]) -> PowerFlowOperatingPointContract:
    """Restore the frozen solver and operating-point contract."""
    raw = dict(value)
    raw["snapshots"] = tuple(raw["snapshots"])
    return PowerFlowOperatingPointContract(**raw)


def _candidate_evaluation_rows(report: Mapping[str, object]) -> list[dict[str, object]]:
    """Flatten every evaluated candidate, including prerequisite and PF failures."""
    rows: list[dict[str, object]] = []
    for iteration in report.get("iterations", []):
        decision = iteration.get("expansion_decision")
        if not decision:
            continue
        for evaluation in decision.get("candidate_evaluations", []):
            rows.append({"iteration_index": iteration["iteration_index"], **evaluation})
    return rows


def _validate_candidate_evaluation_rows(rows: object) -> None:
    """Validate persisted candidate rows against the typed candidate-evidence schema."""
    if not isinstance(rows, list):
        raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.MISSING_OR_INVALID,
                                    "candidate evaluations must be an array")
    expected_fields = set(CandidateEvaluation.__dataclass_fields__) | {"iteration_index"}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != expected_fields:
            raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.MISSING_OR_INVALID,
                                        "candidate evaluation row fields do not match the schema")
        iteration_index = row.get("iteration_index")
        if isinstance(iteration_index, bool) or not isinstance(iteration_index, int) or iteration_index < 0:
            raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.MISSING_OR_INVALID,
                                        "candidate evaluation iteration index is invalid")
        payload = {key: value for key, value in row.items() if key != "iteration_index"}
        try:
            CandidateEvaluation.from_dict(payload)
        except (AdaptiveContractError, TypeError, ValueError) as exc:
            raise AdaptiveEvidenceError(
                AdaptiveEvidenceErrorCode.MISSING_OR_INVALID,
                f"candidate evaluation payload is invalid: {exc}",
            ) from exc


def _iteration_bindings(iterations: object) -> list[dict[str, object]]:
    """Bind every gate, PF, attribution, candidate, and selection stage per iteration."""
    if not isinstance(iterations, list):
        raise ValueError("iterations must be an array")
    result: list[dict[str, object]] = []
    for iteration in iterations:
        decision = iteration.get("expansion_decision") or {}
        hard = iteration.get("hard_violations", [])
        evaluations = decision.get("candidate_evaluations", [])
        result.append({
            "iteration_index": iteration["iteration_index"],
            "incumbent_action_set_fingerprint": iteration["action_set_fingerprint"],
            "incumbent_model_digest": iteration.get("model_digest"),
            "incumbent_mapping_bundle_digest": iteration.get("mapping_digest"),
            "structural_status": iteration["structural_status"],
            "structural_report_digest": _digest({
                "status": iteration["structural_status"],
                "diagnostics": {key: value for key, value in iteration.get("diagnostics", {}).items()
                                if key.startswith("structural.")},
            }),
            "ac_readiness_status": iteration["readiness_status"],
            "pf_execution_status": iteration["pf_status"],
            "fidelity_report_digest": _digest({
                "status": iteration["fidelity_status"],
                "metrics_digest": iteration.get("metrics_digest"),
                "threshold_digest": iteration.get("threshold_digest"),
                "hard_violations": hard,
            }),
            "metrics_digest": iteration.get("metrics_digest"),
            "threshold_digest": iteration.get("threshold_digest"),
            "hard_violation_summary_digest": _digest(hard),
            "error_attribution_digest": iteration.get("attribution_digest"),
            "error_region_set_digest": _digest(iteration.get("error_region_fingerprints", [])),
            "expansion_candidate_set_digest": _digest({
                "generated": iteration.get("expansion_candidate_fingerprints", []),
                "rejected": iteration.get("rejected_candidate_fingerprints", []),
                "evaluated": [row.get("candidate", {}).get("candidate_fingerprint") for row in evaluations],
            }),
            "candidate_evaluations_digest": _digest(evaluations),
            "selected_candidate_fingerprint": decision.get("selected_candidate_fingerprint"),
            "selection_trace": decision.get("tie_break_trace", []),
            "resulting_action_set_fingerprint": iteration.get("new_incumbent_fingerprint"),
            "action_set_delta": iteration.get("action_set_delta", []),
            "terminal_reason": iteration.get("stopping_reason"),
        })
    return result


def _validate_path_privacy(value: object, path: str = "config") -> None:
    """Reject host-specific local paths from persisted synthetic config artifacts."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            _validate_path_privacy(item, f"{path}.{key}")
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _validate_path_privacy(item, f"{path}[{index}]")
    elif isinstance(value, str) and (value.startswith("/") or
            (len(value) > 2 and value[1:3] == ":\\")):
        raise ValueError(f"absolute local path is not allowed in adaptive evidence: {path}")


def _verify_manifest_references(run_dir: Path, refs: Mapping[str, object]) -> None:
    """Require every evidence artifact to be registered by the ArtifactStore manifest."""
    manifest = _read_json(run_dir / "run_manifest.json")
    artifact_refs = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(artifact_refs, dict):
        raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.MISSING_OR_INVALID,
                                    "run manifest has no artifact registry")
    for name, relative in refs.items():
        if artifact_refs.get(f"adaptive_{name}") != relative:
            raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                        f"manifest reference mismatch for {name}")
    if artifact_refs.get("adaptive_evidence") != "adaptive/evidence.json":
        raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.DIGEST_MISMATCH,
                                    "adaptive evidence is not registered in manifest")


def _journal_from_report(
    actions: tuple[ReductionAction, ...], initial_ids: tuple[str, ...], report: AdaptiveReductionReport,
) -> ReductionActionJournal:
    """Rebuild the selected transition chain from exact action-set deltas."""
    action_by_id = {action.action_id: action for action in actions}
    journal = ReductionActionJournal.start(tuple(action_by_id[item] for item in sorted(initial_ids)))
    for iteration in report.iterations:
        if not iteration.action_set_delta:
            continue
        decision = iteration.expansion_decision
        if decision is None or decision.selected_candidate_fingerprint is None:
            raise ValueError("action transition is missing its selected candidate")
        journal = journal.deactivate(
            iteration.action_set_delta, reason=_ACTION_JOURNAL_REASON,
            decision_reference=decision.selected_candidate_fingerprint,
        )
    journal.verify()
    return journal


def _verify_reconstructed_journal(
    actions: tuple[ReductionAction, ...], initial_ids: tuple[str, ...],
    report: AdaptiveReductionReport, stored: ReductionActionJournal,
) -> None:
    """Recompute every transition and require full journal payload equality."""
    recomputed = _journal_from_report(actions, initial_ids, report)
    if recomputed.to_dict() != stored.to_dict() or recomputed.digest != report.journal_digest:
        raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.SEQUENCE_MISMATCH,
                                    "action journal chain differs from rerun controller sequence")


def _verify_runtime_bindings(runtime: PypsaAdaptiveRuntime, expected: Mapping[str, object]) -> None:
    """Check reused PR-022 runtime digests against the frozen evidence envelope."""
    values = {
        "s1_model_digest": runtime.source_model_digest,
        "s1_sidecar_digest": runtime.source_sidecar_digest,
        "operating_point_digest": runtime.operating_point_digest,
        "solver_contract_digest": runtime.solver_contract_digest,
        "metric_policy_digest": runtime.metric_policy_digest,
        "threshold_set_id": runtime.threshold_set_id,
        "action_catalog_digest": runtime.action_catalog_digest,
    }
    for key, actual in values.items():
        if expected.get(key) != actual:
            raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.INPUT_MISMATCH,
                                        f"reconstructed runtime differs for {key}")


def _safe_artifact_path(run_dir: Path, relative: object) -> Path:
    """Resolve a manifest artifact path while refusing path traversal."""
    if not isinstance(relative, str):
        raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.MISSING_OR_INVALID,
                                    "artifact path must be a string")
    target = (run_dir / relative).resolve()
    if not target.is_relative_to(run_dir):
        raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.MISSING_OR_INVALID,
                                    "artifact path escapes the run directory")
    return target


def _compare_report(
    expected: object, actual: object, tolerance: ReplayNumericTolerance, path: str = "$",
) -> None:
    """Compare exact semantics and apply PR-022 replay tolerance to numbers only."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        if expected != actual:
            raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.SEQUENCE_MISMATCH,
                                        "boolean semantic field changed")
        return
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if abs(float(expected) - float(actual)) > max(
            tolerance.absolute, tolerance.relative * max(abs(float(expected)), abs(float(actual)))
        ):
            raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.SEQUENCE_MISMATCH,
                                        "numeric evidence differs beyond PR-022 replay tolerance")
        return
    if type(expected) is not type(actual):
        raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.SEQUENCE_MISMATCH,
                                    "report field type changed")
    if isinstance(expected, dict):
        if set(expected) != set(actual):
            raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.SEQUENCE_MISMATCH,
                                        "report field set changed")
        for key in expected:
            try:
                _compare_report(expected[key], actual[key], tolerance, f"{path}.{key}")
            except AdaptiveEvidenceError as exc:
                raise AdaptiveEvidenceError(exc.code, f"{key}: {exc.detail}") from exc
        return
    if isinstance(expected, list):
        if len(expected) != len(actual):
            raise AdaptiveEvidenceError(AdaptiveEvidenceErrorCode.SEQUENCE_MISMATCH,
                                        "report sequence length changed")
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            _compare_report(left, right, tolerance, f"{path}[{index}]")
        return
    if expected != actual:
        if "terminal_reason" in path:
            code = AdaptiveEvidenceErrorCode.TERMINAL_MISMATCH
        elif any(token in path for token in ("selected_candidate", ".score", "tie_break_trace")):
            code = AdaptiveEvidenceErrorCode.SELECTION_MISMATCH
        else:
            code = AdaptiveEvidenceErrorCode.SEQUENCE_MISMATCH
        raise AdaptiveEvidenceError(code, "exact report field changed")


def _final_value(report: AdaptiveReductionReport, key: str) -> object:
    """Return a final incumbent digest from the report summary when present."""
    return report.final_incumbent_summary.get(key)


def _final_action_fingerprint(report: AdaptiveReductionReport) -> str:
    """Resolve final active action-set identity from the last iteration."""
    if report.iterations:
        return report.iterations[-1].action_set_fingerprint
    return report.initial_action_set_fingerprint or ""
