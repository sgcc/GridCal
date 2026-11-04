"""Contract tests for the versioned comprehensive reduction report."""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path

import pytest


def _api():
    """Load the report API only after asserting that the feature exists."""
    module_name = "psdcalc.reduction.reporting.comprehensive"
    assert importlib.util.find_spec(module_name) is not None
    return importlib.import_module(module_name)


def _run_report(api):
    """Build one minimal report with all optional source evidence omitted."""
    evidence = api.EvidenceValue
    status = api.EvidenceStatus
    run = api.ComprehensiveRunReport(
        provenance=api.ReportProvenance(
            study_id="synthetic-study",
            run_id="synthetic-run",
            package_version="0.8.0",
        ),
        source_inventory=api.NetworkInventory(
            bus_count=evidence(value=3, state=status.AVAILABLE, unit="count"),
        ),
        opportunity=api.OpportunitySection(),
        structural=api.StructuralComparison(),
        voltage_layers=(),
        fidelity=api.FidelitySection(),
        performance=api.PerformanceSection(),
    )
    return api.ComprehensiveReductionReport(report_id="synthetic-report", runs=(run,))


def test_contract_module_and_public_models_exist():
    api = _api()
    assert api.REPORT_SCHEMA_VERSION == "psdcalc-comprehensive-reduction-report/v1"
    assert api.ComprehensiveReductionReport is not None


def test_missing_values_are_explicit_and_available_values_are_bound():
    api = _api()
    missing = api.EvidenceValue(state=api.EvidenceStatus.NOT_RECORDED)
    assert missing.value is None
    with pytest.raises(ValueError, match="available evidence requires a value"):
        api.EvidenceValue(state=api.EvidenceStatus.AVAILABLE)
    with pytest.raises(ValueError, match="unavailable evidence cannot contain a value"):
        api.EvidenceValue(value=0, state=api.EvidenceStatus.NOT_RECORDED)


def test_contract_rejects_unknown_fields_and_invalid_mapping_coverage():
    api = _api()
    with pytest.raises(ValueError):
        api.EvidenceValue(state=api.EvidenceStatus.NOT_RECORDED, inferred_zero=True)
    with pytest.raises(ValueError):
        api.EvidenceValue(
            value=1.2,
            state=api.EvidenceStatus.AVAILABLE,
            mapping_coverage=1.2,
        )


def test_provenance_rejects_absolute_paths_and_non_sha_digests():
    api = _api()
    with pytest.raises(ValueError, match="repository-relative"):
        api.EvidenceReference(
            path=str(Path("synthetic/case/report.json").resolve()), sha256="a" * 64)
    with pytest.raises(ValueError, match="SHA-256"):
        api.EvidenceReference(path="docs/validation/report.json", sha256="bad")


def test_report_digest_is_stable_and_serialization_has_version():
    api = _api()
    first = _run_report(api)
    second = _run_report(api)
    assert first.digest_sha256 == second.digest_sha256
    assert json.loads(first.to_json())["schema_version"] == api.REPORT_SCHEMA_VERSION


def test_packaged_json_schema_matches_authoritative_model(tmp_path: Path):
    api = _api()
    expected = api.ComprehensiveReductionReport.model_json_schema()
    resource = Path(api.__file__).parent.parent / "resources" / "comprehensive-report.schema.json"
    assert json.loads(resource.read_text(encoding="utf-8")) == expected
    assert tmp_path is not None
