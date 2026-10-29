"""Artifact and failure-recording contract tests."""

import json
from pathlib import Path

import pytest

from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.core.records import ReductionRecord
from psdcalc.reduction.exceptions import ArtifactError


def test_store_creates_run_layout_and_manifest(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, "run-1")

    assert store.path("maps").is_dir()
    assert store.path("records").is_dir()
    manifest = json.loads(store.path("run_manifest.json").read_text())
    assert manifest["run_id"] == "run-1"
    assert manifest["status"] == "RUNNING"


def test_capture_persists_failure_and_reraises(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, "run-2")

    with pytest.raises(ValueError, match="bad map"):
        with store.capture("plan"):
            raise ValueError("bad map")

    manifest = json.loads(store.path("run_manifest.json").read_text())
    evidence = json.loads(store.path("validation/failure.json").read_text())
    assert manifest["status"] == "FAILED"
    assert evidence["stage"] == "plan"
    assert evidence["error_type"] == "ValueError"


def test_store_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError):
        ArtifactStore(tmp_path, "../escape")


def test_reduction_record_is_immutable() -> None:
    record = ReductionRecord(
        operation_id="cluster-1", operation_type="aggregation",
        parent_grid_hash="abc", child_grid_hash="def",
        input_signature="i", output_signature="o",
    )

    with pytest.raises(Exception):
        record.operation_id = "different"
