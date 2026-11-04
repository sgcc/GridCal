"""Historical evidence ingestion must preserve source identity and boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from psdcalc.reduction.reporting.comprehensive_history import (
    HistoricalEvidenceBundle,
    HistoricalEvidenceError,
    export_historical_evidence_bundle,
    historical_evidence_csv,
    load_historical_evidence,
)


ROOT = Path(__file__).parents[3] / "docs" / "validation"
HISTORICAL_SOURCE_DIGESTS = {
    "pr020-real-case-summary.json": "fc46af8c4ab223ec83a6f04fe73426136a79dc2e25d75f6b3d72da240d1cdbda",
    "pr021-beijing-voltage-policy-summary.json": "99ba97ab4df6dffcb7d78bcc47877e47a657fbfd0b649f969d4b8d203a87a450",
    "pr022-beijing-ac-pf-fidelity-summary.json": "c0efc9669ddcfd26c7dd72492878254c6b39b3c914422320efe5f228b6e1b436",
    "pr025-stage2c-execution.json": "cecd31a1f4f793dd5eb40430da3dfebcf7a6b9ebee7522f424121c0bd760e5f8",
}


def test_historical_sources_are_bound_and_kept_separate():
    records = load_historical_evidence(
        (
            ROOT / "pr020-real-case-summary.json",
            ROOT / "pr021-beijing-voltage-policy-summary.json",
            ROOT / "pr022-beijing-ac-pf-fidelity-summary.json",
            ROOT / "pr025-stage2c-execution.json",
        ),
        expected_digests=HISTORICAL_SOURCE_DIGESTS,
    )
    assert {record.source_name for record in records} == {
        "pr020-real-case-summary.json",
        "pr021-beijing-voltage-policy-summary.json",
        "pr022-beijing-ac-pf-fidelity-summary.json",
        "pr025-stage2c-execution.json",
    }
    stage2c = next(record for record in records if record.experiment_id == "PR025_STAGE2C_EXECUTION")
    assert stage2c.package_version == "0.8.0"
    assert stage2c.value_for("fidelity_metric_values") is None
    assert stage2c.value_for("projection_fidelity") == "NOT VALIDATED"
    pr022 = next(record for record in records if record.experiment_id == "PR022_BEIJING_STATIC_SNAPSHOT_AC_PF")
    assert pr022.threshold_set_id == "PR022_AC_PF_FIDELITY_V1"
    assert pr022.threshold_sha256 == "4688f0f021f5aaabbfcb75bd1dcd8a1228753669dd415761d2d1b5471dac3f24"
    assert "BPA10056814" not in json.dumps([record.model_dump(mode="json") for record in records])


def test_policy_comparison_includes_historical_source_inventory_counts():
    """Known source inventories remain visible in the cross-policy CSV."""
    import csv
    import io

    from psdcalc.reduction.reporting.comprehensive_history import historical_policy_comparison_csv

    records = load_historical_evidence(
        (ROOT / "pr020-real-case-summary.json", ROOT / "pr021-beijing-voltage-policy-summary.json",
         ROOT / "pr022-beijing-ac-pf-fidelity-summary.json", ROOT / "pr025-stage2c-execution.json"),
        expected_digests=HISTORICAL_SOURCE_DIGESTS,
    )
    rows = list(csv.DictReader(io.StringIO(historical_policy_comparison_csv(records))))
    for experiment_id in (
        "PR021_BEIJING_VOLTAGE_POLICY_MATRIX",
        "PR022_BEIJING_STATIC_SNAPSHOT_AC_PF",
        "PR025_STAGE2C_EXECUTION",
    ):
        row = next(item for item in rows if item["experiment_id"] == experiment_id)
        assert row["source_bus_count"] == "1527"
        assert row["source_line_count"] == "639"


def test_historical_digest_mismatch_fails_closed(tmp_path):
    path = tmp_path / "pr020-real-case-summary.json"
    path.write_text(json.dumps({"scenarios": []}), encoding="utf-8")
    with pytest.raises(HistoricalEvidenceError):
        load_historical_evidence((path,), expected_digests={path.name: "0" * 64})


def test_historical_unknown_file_and_duplicate_json_key_fail_closed(tmp_path):
    unknown = tmp_path / "other.json"
    unknown.write_text("{}", encoding="utf-8")
    with pytest.raises(HistoricalEvidenceError):
        load_historical_evidence((unknown,))

    known = tmp_path / "pr020-real-case-summary.json"
    known.write_text('{"scenarios": [], "scenarios": []}', encoding="utf-8")
    with pytest.raises(HistoricalEvidenceError):
        load_historical_evidence((known,))


def test_historical_bundle_exports_are_anonymous_and_consistent(tmp_path):
    records = load_historical_evidence(
        (
            ROOT / "pr020-real-case-summary.json",
            ROOT / "pr021-beijing-voltage-policy-summary.json",
            ROOT / "pr022-beijing-ac-pf-fidelity-summary.json",
            ROOT / "pr025-stage2c-execution.json",
        ),
        expected_digests=HISTORICAL_SOURCE_DIGESTS,
    )
    written = export_historical_evidence_bundle(records, tmp_path)
    csv_rows = list(__import__("csv").DictReader(written["csv"].open(encoding="utf-8")))
    assert len(csv_rows) == sum(len(record.observations) for record in records)
    policy_rows = list(__import__("csv").DictReader(
        written["policy_comparison_csv"].open(encoding="utf-8")
    ))
    assert len(policy_rows) == len(records)
    serialized = b"\n".join(path.read_bytes() for path in written.values())
    assert b"BPA10056814" not in serialized
    assert b"BPA10056819" not in serialized
    assert b"/Users/" not in serialized
    assert historical_evidence_csv(records) == written["csv"].read_text(encoding="utf-8")


def test_historical_archive_schema_matches_model_generated_resource():
    resource = (
        Path(__file__).parents[1]
        / "src"
        / "psdcalc"
        / "reduction"
        / "resources"
        / "comprehensive-history.schema.json"
    )
    assert json.loads(resource.read_text(encoding="utf-8")) == HistoricalEvidenceBundle.model_json_schema()
