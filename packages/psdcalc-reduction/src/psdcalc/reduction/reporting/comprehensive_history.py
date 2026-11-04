"""Read-only, allowlisted adapters for already-published validation evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HistoricalEvidenceError(ValueError):
    """Raised when a historical evidence source is malformed or re-bound."""


class HistoricalObservation(BaseModel):
    """One allowlisted, source-preserving historical scalar observation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str
    value: int | float | str | bool | None
    unit: str | None = None
    status: Literal["AVAILABLE", "NOT_RECORDED"]


class HistoricalScenarioEvidence(BaseModel):
    """Anonymous evidence row with its original experiment and file binding."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_name: str
    source_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    declared_schema: str
    experiment_id: str
    scenario_id: str
    case_label: Literal["BEIJING", "JILIN", "NOT_RECORDED"]
    package_version: str | None
    software_versions: dict[str, str] = Field(default_factory=dict)
    source_model_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    implementation_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    implementation_tree: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    evaluation_convention: str
    threshold_set_id: str | None = None
    threshold_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    observations: tuple[HistoricalObservation, ...] = ()

    def value_for(self, name: str) -> int | float | str | bool | None:
        """Return an observation value by its unique source field name."""
        matches = [item.value for item in self.observations if item.name == name]
        if len(matches) > 1:
            raise HistoricalEvidenceError(f"ambiguous historical observation: {name}")
        return matches[0] if matches else None


class HistoricalEvidenceBundle(BaseModel):
    """Versioned archive schema for anonymous historical report evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["psdcalc-comprehensive-historical-evidence/v1"]
    records: tuple[HistoricalScenarioEvidence, ...]

    @model_validator(mode="after")
    def validate_records(self) -> "HistoricalEvidenceBundle":
        """Require unique experiment/case/scenario identities in the archive."""
        identities = [(row.experiment_id, row.case_label, row.scenario_id)
                      for row in self.records]
        if not identities or len(set(identities)) != len(identities):
            raise ValueError("historical evidence bundle requires unique nonempty records")
        return self

    @classmethod
    def write_json_schema(cls, path: str | Path) -> None:
        """Write the historical archive schema from its Pydantic source model."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(cls.model_json_schema(), sort_keys=True, indent=2) + "\n",
                          encoding="utf-8")


_SOURCE_CONTRACTS = {
    "pr020-real-case-summary.json": ("PR020_REAL_CASE_STRUCTURE", "UNVERSIONED_LEGACY"),
    "pr021-beijing-voltage-policy-summary.json": (
        "PR021_BEIJING_VOLTAGE_POLICY_MATRIX", "pr021-beijing-voltage-policy-summary/v1"),
    "pr022-beijing-ac-pf-fidelity-summary.json": (
        "PR022_BEIJING_STATIC_SNAPSHOT_AC_PF", "integer-schema-version"),
    "pr025-stage2c-execution.json": (
        "PR025_STAGE2C_EXECUTION", "adaptive-reduction-evidence/v2"),
}


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """JSON object hook that rejects duplicate keys before data is interpreted."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise HistoricalEvidenceError(f"duplicate JSON key in historical input: {key}")
        result[key] = value
    return result


def _sha(path: Path) -> str:
    """Hash the exact historical artifact bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_case(raw: object) -> Literal["BEIJING", "JILIN", "NOT_RECORDED"]:
    """Map private source case tokens to the approved anonymous labels."""
    token = str(raw or "")
    if token == "BPA10056814" or "beijing" in token.lower():
        return "BEIJING"
    if token == "BPA10056819" or "jilin" in token.lower():
        return "JILIN"
    if token.lower() in {"beijing", "jilin"}:
        return token.upper()  # type: ignore[return-value]
    return "NOT_RECORDED"


def _safe_scenario(raw: object, fallback: str) -> str:
    """Retain only short policy slugs; never export a raw source label."""
    token = str(raw or "").strip().lower().replace(" ", "-")
    return token if re.fullmatch(r"[a-z0-9_-]{1,40}", token) else fallback


def _observation(name: str, value: object, unit: str | None = None) -> HistoricalObservation:
    """Create an observation only for supported JSON scalar types."""
    if isinstance(value, list):
        if any(not isinstance(item, (int, float, str, bool)) for item in value):
            raise HistoricalEvidenceError(f"historical list contains unsupported values: {name}")
        value = json.dumps(value, separators=(",", ":"))
    if isinstance(value, dict):
        if any(not isinstance(key, str) or not isinstance(item, (int, float, str, bool, type(None)))
               for key, item in value.items()):
            raise HistoricalEvidenceError(f"historical mapping contains unsupported values: {name}")
        value = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if value is not None and not isinstance(value, (int, float, str, bool)):
        raise HistoricalEvidenceError(f"historical field is not scalar: {name}")
    return HistoricalObservation(
        name=name,
        value=value,
        unit=unit,
        status="AVAILABLE" if value is not None else "NOT_RECORDED",
    )


def _selected(source: Mapping[str, object], names: Sequence[str], units: Mapping[str, str] | None = None) -> tuple[HistoricalObservation, ...]:
    """Copy a fixed allowlist of scalar evidence fields, including absence."""
    unit_map = units or {}
    return tuple(_observation(name, source.get(name), unit_map.get(name)) for name in names)


def _record(
    *,
    source_name: str,
    source_sha: str,
    declared_schema: str,
    experiment_id: str,
    scenario_id: str,
    case_label: Literal["BEIJING", "JILIN", "NOT_RECORDED"],
    package_version: object,
    model_sha: object,
    software_versions: Mapping[str, object] | None = None,
    implementation_commit: object = None,
    implementation_tree: object = None,
    convention: str,
    observations: tuple[HistoricalObservation, ...],
    threshold_id: object = None,
    threshold_sha: object = None,
) -> HistoricalScenarioEvidence:
    """Validate common provenance and digest values without inference."""
    for name, value in (("package_version", package_version),
                        ("source_model_sha256", model_sha),
                        ("implementation_commit", implementation_commit),
                        ("implementation_tree", implementation_tree),
                        ("threshold_set_id", threshold_id),
                        ("threshold_sha256", threshold_sha)):
        if value is not None and not isinstance(value, str):
            raise HistoricalEvidenceError(f"invalid {name} in historical source")
    return HistoricalScenarioEvidence(
        source_name=source_name,
        source_document_sha256=source_sha,
        declared_schema=declared_schema,
        experiment_id=experiment_id,
        scenario_id=scenario_id,
        case_label=case_label,
        package_version=package_version,
        software_versions={key: value for key, value in (software_versions or {}).items()
                           if isinstance(value, str)},
        source_model_sha256=model_sha,
        implementation_commit=implementation_commit,
        implementation_tree=implementation_tree,
        evaluation_convention=convention,
        threshold_set_id=threshold_id,
        threshold_sha256=threshold_sha,
        observations=observations,
    )


def _parse_pr020(data: Mapping[str, object], source: str, digest: str) -> list[HistoricalScenarioEvidence]:
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list):
        raise HistoricalEvidenceError("PR-020 historical source lacks scenarios array")
    names = (
        "s1_bus_count", "s1_line_count", "accepted_bus_count", "candidate_bus_count",
        "accepted_compression_fraction", "candidate_compression_fraction",
        "accepted_line_count",
        "l0_source_line_count", "l1_bus_count", "l1_line_count",
        "l0_to_l1_preserved_line_count", "l0_to_l1_absorbed_internal_count",
        "l0_to_l1_aggregated_count", "l1_derived_line_count", "protected_l0_line_count",
        "protected_l1_preserved_count", "protected_l1_lost_count", "l_plus_association_count",
        "l_plus_base_preserved_count", "l_plus_shunt_preserved_count",
        "l_plus_terminal_preserved_count", "final_structural_status",
    )
    result: list[HistoricalScenarioEvidence] = []
    for index, item in enumerate(scenarios):
        if not isinstance(item, dict):
            raise HistoricalEvidenceError("PR-020 scenario row is not an object")
        case_label = _safe_case(item.get("case"))
        raw_scenario = _safe_scenario(item.get("scenario"), f"scenario-{index + 1}")
        model_digest_map = data.get("source_dat_sha256")
        model_digest = (model_digest_map.get(item.get("case"))
                        if isinstance(model_digest_map, dict) else model_digest_map)
        result.append(_record(
            source_name=source,
            source_sha=digest,
            declared_schema="NOT_RECORDED",
            experiment_id="PR020_REAL_CASE_STRUCTURE",
            scenario_id=f"{case_label.lower()}-{raw_scenario}",
            case_label=case_label,
            package_version=data.get("package_version"),
            model_sha=model_digest,
            implementation_tree=data.get("tested_implementation_tree_sha"),
            convention="Structural final Gate; physical fidelity not evaluated.",
            observations=_selected(item, names),
        ))
    return result


def _parse_pr021(data: Mapping[str, object], source: str, digest: str) -> list[HistoricalScenarioEvidence]:
    rows = data.get("scenarios")
    binding = data.get("evidence_binding")
    source_evidence = data.get("source_evidence")
    environment = data.get("real_case_environment")
    if (not isinstance(rows, list) or not isinstance(binding, dict)
            or not isinstance(source_evidence, dict) or not isinstance(environment, dict)):
        raise HistoricalEvidenceError("PR-021 historical source is incomplete")
    declared = data.get("evidence_schema")
    if not isinstance(declared, str):
        raise HistoricalEvidenceError("PR-021 declared schema missing")
    model_sha = source_evidence.get("dat_sha256")
    source_inventory = source_evidence.get("source_inventory")
    if not isinstance(source_inventory, dict):
        raise HistoricalEvidenceError("PR-021 source inventory is missing")
    equivalence_rows = data.get("cutoff_equivalence")
    policy_equivalence: dict[str, tuple[str, ...]] = {}
    if isinstance(equivalence_rows, list):
        for equivalence in equivalence_rows:
            if isinstance(equivalence, dict) and equivalence.get("all_equal") is True:
                members = equivalence.get("policies")
                if isinstance(members, list) and all(isinstance(value, str) for value in members):
                    for member in members:
                        policy_equivalence[member] = tuple(sorted(set(members) - {member}))
    result: list[HistoricalScenarioEvidence] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise HistoricalEvidenceError("PR-021 policy row is not an object")
        accepted = row.get("accepted") if isinstance(row.get("accepted"), dict) else {}
        candidate = row.get("candidate") if isinstance(row.get("candidate"), dict) else {}
        l1 = row.get("l1") if isinstance(row.get("l1"), dict) else {}
        fields = ("policy", "requested_cutoff_kv", "retained_voltage_layers_kv",
                  "resolved_policy_fingerprint", "final_status", "accepted_compression_fraction",
                  "retained_identity_set_digest", "mapping_semantic_sha256")
        obs = list(_selected(row, fields))
        obs.append(_observation(
            "equivalent_policy_ids",
            ";".join(policy_equivalence.get(str(row.get("policy")), ())) or None,
        ))
        for key in ("bus", "line", "projected_transformer_rows", "generator", "load",
                    "shunt_impedance", "link", "storage_unit", "store"):
            obs.append(_observation(f"source.{key}", source_inventory.get(key)))
        obs.append(_observation(
            "actual_source_voltage_layers_kv",
            json.dumps(source_evidence.get("actual_voltage_layers_kv"), separators=(",", ":")),
        ))
        obs.append(_observation(
            "source_absent_voltage_layers_kv",
            json.dumps(source_evidence.get("absent_voltage_layers_kv"), separators=(",", ":")),
        ))
        for prefix, record in (("candidate", candidate), ("accepted", accepted), ("l1", l1)):
            for key in ("bus_count", "line_count", "compression_fraction"):
                obs.append(_observation(f"{prefix}.{key}", record.get(key)))
        result.append(_record(
            source_name=source,
            source_sha=digest,
            declared_schema=declared,
            experiment_id="PR021_BEIJING_VOLTAGE_POLICY_MATRIX",
            scenario_id=_safe_scenario(row.get("policy"), f"policy-{index + 1}"),
            case_label="BEIJING",
            package_version=binding.get("package_version"),
            software_versions={key: environment[key] for key in
                               ("python_version", "pypsa_version", "highspy_version")
                               if isinstance(environment.get(key), str)},
            model_sha=model_sha,
            implementation_commit=binding.get("tested_implementation_commit"),
            implementation_tree=binding.get("tested_implementation_tree"),
            convention="Voltage cutoff structural matrix; exact source layers; no PF fidelity claim.",
            observations=tuple(obs),
        ))
    return result


def _parse_pr022(data: Mapping[str, object], source: str, digest: str) -> list[HistoricalScenarioEvidence]:
    policies = data.get("policies")
    threshold = data.get("threshold_provenance")
    inventory = data.get("source_inventory")
    package_gate = data.get("package_gate")
    implementation = data.get("implementation")
    if (not isinstance(policies, list) or not isinstance(threshold, dict)
            or not isinstance(inventory, dict) or not isinstance(package_gate, dict)
            or not isinstance(implementation, dict)):
        raise HistoricalEvidenceError("PR-022 historical source is incomplete")
    threshold_id = threshold.get("threshold_set_id")
    threshold_sha = threshold.get("threshold_digest")
    result: list[HistoricalScenarioEvidence] = []
    names = (
        "policy", "s4_bus_count", "s4_line_count", "bus_compression_fraction",
        "line_compression_fraction", "s4_pf_converged", "vm_rmse_pu", "vm_max_pu",
        "cutset_p_max_mw", "cutset_q_max_mvar", "loss_relative_error",
        "post_fix_s4_pf_runtime_seconds_diagnostic_only", "structural_status",
        "ac_readiness_status", "pf_fidelity_status",
    )
    units = {"vm_rmse_pu": "pu", "vm_max_pu": "pu", "cutset_p_max_mw": "MW",
             "cutset_q_max_mvar": "Mvar", "loss_relative_error": "relative",
             "post_fix_s4_pf_runtime_seconds_diagnostic_only": "s (diagnostic only)"}
    for index, row in enumerate(policies):
        if not isinstance(row, dict):
            raise HistoricalEvidenceError("PR-022 policy row is not an object")
        observations = list(_selected(row, names, units))
        observations.extend((
            _observation("source.bus_count", inventory.get("bus_count")),
            _observation("source.line_count", inventory.get("line_count")),
        ))
        result.append(_record(
            source_name=source,
            source_sha=digest,
            declared_schema=f"pr022-schema-{data.get('schema_version')}",
            experiment_id="PR022_BEIJING_STATIC_SNAPSHOT_AC_PF",
            scenario_id=_safe_scenario(row.get("policy"), f"policy-{index + 1}"),
            case_label="BEIJING",
            package_version=package_gate.get("package_version"),
            software_versions={key: package_gate[key] for key in ("installed_python", "installed_pypsa")
                               if isinstance(package_gate.get(key), str)},
            # PR-022's public summary does not record an exact source-model digest.
            model_sha=None,
            implementation_commit=implementation.get("tested_implementation_commit"),
            implementation_tree=implementation.get("tested_implementation_tree"),
            convention="Static-snapshot AC PF fidelity; preregistered thresholds; no physical-equivalence claim.",
            observations=tuple(observations),
            threshold_id=threshold_id,
            threshold_sha=threshold_sha,
        ))
    return result


def _parse_stage2c(data: Mapping[str, object], source: str, digest: str) -> list[HistoricalScenarioEvidence]:
    experiment = data.get("experiment")
    package = data.get("package_version")
    initial = data.get("initial_candidate")
    freeze = data.get("freeze_binding")
    if not isinstance(experiment, str) or not isinstance(initial, dict) or not isinstance(freeze, dict):
        raise HistoricalEvidenceError("Stage2C evidence is incomplete")
    names = (
        "active_action_count", "bus_compression_fraction", "branch_compression_fraction",
        "candidate_fingerprint", "fidelity_status", "fidelity_metric_values",
        "metrics_digest", "pf_status", "pf_iterations", "pf_final_mismatch",
        "overall_admission", "actual_derived_closure_digest", "expected_derived_closure_digest",
    )
    observations = list(_selected(initial, names))
    reference_pf = data.get("s1_reference_pf")
    reference_inventory = reference_pf.get("inventory") if isinstance(reference_pf, dict) else None
    if isinstance(reference_inventory, dict):
        for key in ("Bus", "Line", "Transformer", "Link"):
            observations.append(_observation(f"source.{key.lower()}", reference_inventory.get(key)))
    observations.extend((
        _observation("projection_fidelity", data.get("evidence_boundary", {}).get("projection_fidelity")
                     if isinstance(data.get("evidence_boundary"), dict) else None),
        _observation("physical_equivalence", data.get("evidence_boundary", {}).get("physical_equivalence")
                     if isinstance(data.get("evidence_boundary"), dict) else None),
        _observation("experiment_outcome", data.get("experiment_outcome")),
        _observation("terminal_reason", data.get("terminal", {}).get("reason")
                     if isinstance(data.get("terminal"), dict) else None),
    ))
    return [_record(
        source_name=source,
        source_sha=digest,
        declared_schema=str(data.get("schema") or freeze.get("evidence_schema") or "adaptive-reduction-evidence/v2"),
        experiment_id=experiment,
        scenario_id="initial-candidate",
        case_label="BEIJING",
        package_version=package if isinstance(package, str) else None,
        implementation_commit=data.get("implementation_commit"),
        implementation_tree=data.get("implementation_tree"),
        model_sha=freeze.get("s1_digest"),
        convention="Adaptive Stage2C initial candidate; candidate metric values absent in source; not cross-filled.",
        observations=tuple(observations),
        threshold_id=freeze.get("threshold_set_id"),
        threshold_sha=freeze.get("threshold_artifact_sha256"),
    )]


def load_historical_evidence(
    paths: Sequence[str | Path],
    *,
    expected_digests: Mapping[str, str] | None = None,
) -> tuple[HistoricalScenarioEvidence, ...]:
    """Load known historical summaries with exact file hashing and fail-closed validation.

    The adapters use a fixed field allowlist and replace raw case tokens with
    approved anonymous labels. Source documents are only read and never edited.
    """
    if not paths:
        raise HistoricalEvidenceError("at least one historical source is required")
    expected = expected_digests or {}
    output: list[HistoricalScenarioEvidence] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path)
        source_name = path.name
        if source_name not in _SOURCE_CONTRACTS:
            raise HistoricalEvidenceError("unapproved historical evidence source")
        if source_name in seen:
            raise HistoricalEvidenceError("duplicate historical source document")
        seen.add(source_name)
        try:
            digest = _sha(path)
            bound = expected.get(source_name)
            if bound is not None and bound != digest:
                raise HistoricalEvidenceError("historical source digest mismatch")
            data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        except HistoricalEvidenceError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise HistoricalEvidenceError("historical evidence could not be parsed") from exc
        if not isinstance(data, dict):
            raise HistoricalEvidenceError("historical evidence root must be an object")
        experiment, declared = _SOURCE_CONTRACTS[source_name]
        if source_name == "pr020-real-case-summary.json":
            rows = _parse_pr020(data, source_name, digest)
        elif source_name == "pr021-beijing-voltage-policy-summary.json":
            rows = _parse_pr021(data, source_name, digest)
        elif source_name == "pr022-beijing-ac-pf-fidelity-summary.json":
            rows = _parse_pr022(data, source_name, digest)
        else:
            rows = _parse_stage2c(data, source_name, digest)
        if not rows or any(row.experiment_id != experiment for row in rows):
            raise HistoricalEvidenceError("historical experiment identity mismatch")
        output.extend(rows)
    return tuple(output)


def historical_evidence_json(records: Sequence[HistoricalScenarioEvidence]) -> str:
    """Serialize validated historical evidence with deterministic ordering."""
    if not records:
        raise HistoricalEvidenceError("historical evidence bundle cannot be empty")
    payload = HistoricalEvidenceBundle(
        schema_version="psdcalc-comprehensive-historical-evidence/v1",
        records=tuple(records),
    )
    return json.dumps(payload.model_dump(mode="json"), sort_keys=True, indent=2,
                       allow_nan=False) + "\n"


def historical_evidence_markdown(records: Sequence[HistoricalScenarioEvidence]) -> str:
    """Render anonymous historical cases while keeping experiments separated."""
    if not records:
        raise HistoricalEvidenceError("historical evidence bundle cannot be empty")
    lines = [
        "# Historical Comprehensive Report Evidence",
        "",
        "Historical artifacts are read-only inputs. Their experiment identity, software version, model digest, and evaluation convention remain separate. PR-022 values are never copied into Stage2C evidence.",
        "",
        "Runtime values from different package or software versions are diagnostic only; no cross-version speedup is computed.",
        "",
        "| Experiment | Case | Scenario | Package | Model digest | Source artifact SHA-256 | Evaluation convention |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in records:
        fields = (record.experiment_id, record.case_label, record.scenario_id,
                  record.package_version or "NOT_RECORDED",
                  record.source_model_sha256 or "NOT_RECORDED",
                  record.source_document_sha256, record.evaluation_convention)
        lines.append("| " + " | ".join(str(value).replace("|", "\\|") for value in fields) + " |")
    lines += ["", "## Recorded observations", ""]
    for record in records:
        lines += [f"### {record.experiment_id} / {record.case_label} / {record.scenario_id}", "",
                  f"Declared source schema: `{record.declared_schema}`; package: `{record.package_version or 'NOT_RECORDED'}`.", "",
                  "| Observation | Value | Unit | Status |", "| --- | --- | --- | --- |"]
        for observation in record.observations:
            value = "NOT_RECORDED" if observation.value is None else str(observation.value)
            escaped_value = value.replace("|", "\\|")
            lines.append(
                f"| {observation.name} | {escaped_value} | "
                f"{observation.unit or 'NOT_RECORDED'} | {observation.status} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def historical_evidence_csv(records: Sequence[HistoricalScenarioEvidence]) -> str:
    """Render the same historical observations in long-form CSV."""
    import csv
    import io

    if not records:
        raise HistoricalEvidenceError("historical evidence bundle cannot be empty")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(("experiment_id", "case_label", "scenario_id", "package_version",
                     "source_model_sha256", "source_document_sha256", "metric", "value",
                     "unit", "status", "evaluation_convention"))
    for record in records:
        for observation in record.observations:
            writer.writerow((record.experiment_id, record.case_label, record.scenario_id,
                             record.package_version or "NOT_RECORDED",
                             record.source_model_sha256 or "NOT_RECORDED",
                             record.source_document_sha256, observation.name,
                             "NOT_RECORDED" if observation.value is None else observation.value,
                             observation.unit or "NOT_RECORDED", observation.status,
                             record.evaluation_convention))
    return stream.getvalue()


def historical_policy_comparison_csv(records: Sequence[HistoricalScenarioEvidence]) -> str:
    """Create one anonymous row per historical scenario with explicit missing cells."""
    import csv
    import io

    if not records:
        raise HistoricalEvidenceError("historical evidence bundle cannot be empty")

    def value(record: HistoricalScenarioEvidence, *names: str) -> object:
        for name in names:
            found = record.value_for(name)
            if found is not None:
                return found
        return "NOT_RECORDED"

    def compression_fraction(record: HistoricalScenarioEvidence, field: str,
                             component: str) -> object:
        raw = record.value_for(field)
        if isinstance(raw, (int, float)):
            return raw if component == "bus" else "NOT_RECORDED"
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return "NOT_RECORDED"
            if isinstance(parsed, dict):
                return parsed.get(component, "NOT_RECORDED")
        return "NOT_RECORDED"

    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow((
        "experiment_id", "case_label", "scenario_id", "package_version",
        "source_model_sha256", "source_document_sha256", "requested_cutoff_kv",
        "retained_voltage_layers_kv", "equivalent_policy_ids", "source_bus_count",
        "source_line_count", "accepted_bus_count", "accepted_line_count",
        "bus_compression_fraction", "line_compression_fraction",
        "structural_status", "ac_readiness_status", "pf_converged",
        "pf_fidelity_status", "vm_rmse_pu", "vm_max_pu", "cutset_p_max_mw",
        "cutset_q_max_mvar", "loss_relative_error", "diagnostic_runtime_seconds",
        "threshold_set_id", "threshold_sha256", "evaluation_convention",
    ))
    for record in records:
        accepted_bus = value(record, "accepted_bus_count", "accepted.bus_count", "s4_bus_count")
        accepted_line = value(record, "accepted_line_count", "accepted.line_count", "s4_line_count")
        writer.writerow((
            record.experiment_id,
            record.case_label,
            record.scenario_id,
            record.package_version or "NOT_RECORDED",
            record.source_model_sha256 or "NOT_RECORDED",
            record.source_document_sha256,
            value(record, "requested_cutoff_kv"),
            value(record, "retained_voltage_layers_kv"),
            value(record, "equivalent_policy_ids"),
            value(record, "s1_bus_count", "source_bus_count", "source.bus", "source.bus_count"),
            value(record, "s1_line_count", "source_line_count", "source.line", "source.line_count"),
            accepted_bus,
            accepted_line,
            compression_fraction(record, "accepted_compression_fraction", "bus")
            if record.experiment_id.startswith("PR021")
            else value(record, "bus_compression_fraction", "accepted_compression_fraction"),
            compression_fraction(record, "accepted_compression_fraction", "line")
            if record.experiment_id.startswith("PR021")
            else value(record, "line_compression_fraction", "branch_compression_fraction"),
            value(record, "final_structural_status", "structural_status"),
            value(record, "ac_readiness_status"),
            value(record, "s4_pf_converged"),
            value(record, "pf_fidelity_status", "fidelity_status"),
            value(record, "vm_rmse_pu"),
            value(record, "vm_max_pu"),
            value(record, "cutset_p_max_mw"),
            value(record, "cutset_q_max_mvar"),
            value(record, "loss_relative_error"),
            value(record, "post_fix_s4_pf_runtime_seconds_diagnostic_only",
                  "runtime_seconds"),
            record.threshold_set_id or "NOT_RECORDED",
            record.threshold_sha256 or "NOT_RECORDED",
            record.evaluation_convention,
        ))
    return stream.getvalue()


def export_historical_evidence_bundle(
    records: Sequence[HistoricalScenarioEvidence],
    output_directory: str | Path,
    *,
    stem: str = "comprehensive-report-v1-historical-evidence",
) -> Mapping[str, Path]:
    """Write JSON, Markdown, and CSV historical evidence artifacts."""
    from psdcalc.reduction.reporting.comprehensive_export import validate_public_export_text

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    contents = {
        "json": historical_evidence_json(records),
        "markdown": historical_evidence_markdown(records),
        "csv": historical_evidence_csv(records),
        "policy_comparison_csv": historical_policy_comparison_csv(records),
    }
    suffixes = {"json": ".json", "markdown": ".md", "csv": ".csv",
                "policy_comparison_csv": "-policy-comparison.csv"}
    result: dict[str, Path] = {}
    for name, content in contents.items():
        validate_public_export_text(content)
        path = output / f"{stem}{suffixes[name]}"
        path.write_text(content, encoding="utf-8", newline="\n")
        result[name] = path
    return result
