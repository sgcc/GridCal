"""Cross-format export and privacy tests for comprehensive reports."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from psdcalc.reduction.reporting.comprehensive_export import (
    export_report_bundle,
    render_markdown,
    render_metrics_csv,
    render_policy_comparison_csv,
    render_voltage_layers_csv,
)
from psdcalc.reduction.reporting.comprehensive import (
    ComprehensiveReductionReport,
    ComprehensiveRunReport,
    EvidenceStatus,
    EvidenceValue,
    FidelityObservation,
    FidelitySection,
    MetricGroup,
    MetricOutcome,
    NetworkInventory,
    ReportProvenance,
    VoltageLayerSummary,
    VoltagePolicyResolution,
)


def _report() -> ComprehensiveReductionReport:
    available = lambda value: EvidenceValue(
        value=value, state=EvidenceStatus.AVAILABLE,
        outcome=MetricOutcome.NOT_EVALUATED,
    )
    run = ComprehensiveRunReport(
        provenance=ReportProvenance(study_id="synthetic", run_id="run-1", package_version="0.8.0"),
        source_inventory=NetworkInventory(
            bus_count=available(4),
            line_count=available(3),
            transformer_count=available(1),
            transformer_count_basis="BACKEND_ROWS",
            link_count=available(0),
            total_branch_count=available(4),
            generator_count=available(1),
            load_count=available(1),
            shunt_count=available(0),
            connected_component_count=available(1),
            voltage_layers_kv=(115.0, 230.0),
        ),
        voltage_layers=(
            VoltageLayerSummary(
                voltage_kv=115.0,
                policy_id="cut-200",
                source_bus_count=available(2),
                source_line_count=available(1),
                retained_bus_count=available(0),
                reduced_bus_count=available(2),
            ),
            VoltageLayerSummary(
                voltage_kv=230.0,
                policy_id="cut-200",
                source_bus_count=available(2),
                source_line_count=available(2),
                retained_bus_count=available(2),
                reduced_bus_count=available(0),
            ),
        ),
        voltage_policies=(VoltagePolicyResolution(
            policy_id="cut-200",
            requested_cutoff_kv=200.0,
            source_voltage_layers_kv=(115.0, 230.0),
            retained_voltage_layers_kv=(230.0,),
            compressible_voltage_layers_kv=(115.0,),
            resolved_policy_fingerprint="0" * 64,
        ),),
    )
    return ComprehensiveReductionReport(report_id="synthetic-report", runs=(run,))


def test_exports_have_required_sections_and_consistent_scalar_values():
    report = _report()
    markdown = render_markdown(report)
    assert all(f"## {letter}. " in markdown for letter in "ABCDEF")

    metrics = list(csv.DictReader(io.StringIO(render_metrics_csv(report))))
    bus_row = next(row for row in metrics if row["metric"] == "bus_count")
    assert bus_row["value"] == str(report.runs[0].source_inventory.bus_count.value)

    policy_rows = list(csv.DictReader(io.StringIO(render_policy_comparison_csv(report))))
    assert policy_rows
    layer_rows = list(csv.DictReader(io.StringIO(render_voltage_layers_csv(report))))
    assert {float(row["voltage_kv"]) for row in layer_rows} == {115.0, 230.0}


def test_report_bundle_is_deterministic_and_privacy_checked(tmp_path):
    report = _report()
    first = export_report_bundle(report, tmp_path / "one")
    second = export_report_bundle(report, tmp_path / "two")
    assert {name: path.read_bytes() for name, path in first.items()} == {
        name: path.read_bytes() for name, path in second.items()
    }
    payload = b"\n".join(path.read_bytes() for path in first.values())
    assert b"/Users/" not in payload
    assert b"synthetic-private:" not in payload
    assert json.loads(first["json"].read_text()) == report.to_dict()


def test_nonempty_typed_fidelity_rows_export_without_changing_numeric_json(tmp_path):
    """A real model row has str severity, int N, float coverage, and enum status."""
    base = _report()
    rows = tuple(FidelityObservation(
        group=MetricGroup.WHOLE_NETWORK,
        metric_name=f"vm_rmse_pu_{index}",
        severity="HARD" if index < 2 else "DIAGNOSTIC",
        observed=EvidenceValue(value=value, state=EvidenceStatus.AVAILABLE, unit="pu"),
        sample_count=count, mapping_coverage=coverage, unit="pu",
        threshold=EvidenceValue(value=0.01, state=EvidenceStatus.AVAILABLE, unit="pu")
        if index < 2 else EvidenceValue(state=EvidenceStatus.NOT_RECORDED),
        outcome=MetricOutcome.PASS if index < 2 else MetricOutcome.DIAGNOSTIC_ONLY,
    ) for index, (value, count, coverage) in enumerate(
        ((0.0, 0, 0.0), (-0.01, 1, 0.5), (0.02, 3, 1.0))))
    run = base.runs[0].model_copy(update={
        "fidelity": FidelitySection(observations=rows),
    })
    report = ComprehensiveReductionReport(report_id=base.report_id, runs=(run,))
    exports = export_report_bundle(report, tmp_path)
    assert "| HARD |" in exports["markdown"].read_text()
    assert "| DIAGNOSTIC |" in exports["markdown"].read_text()
    saved = json.loads(exports["json"].read_text())
    assert saved["runs"][0]["fidelity"]["observations"][0]["observed"]["value"] == 0.0
    assert saved["runs"][0]["fidelity"]["observations"][1]["sample_count"] == 1
    assert saved["runs"][0]["fidelity"]["observations"][1]["mapping_coverage"] == 0.5


def test_exporter_rejects_forbidden_private_material():
    from psdcalc.reduction.reporting.comprehensive_export import ComprehensiveExportError, validate_public_export_text

    try:
        validate_public_export_text(str(Path("synthetic/grid.nc").resolve()))
    except ComprehensiveExportError:
        pass
    else:
        raise AssertionError("private absolute path should be rejected")
