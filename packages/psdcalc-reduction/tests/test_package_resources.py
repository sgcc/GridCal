"""Installed package resources must match the maintained source artifacts."""

import json
import pytest
from importlib.resources import files
from pathlib import Path

from psdcalc.reduction.config.models import ReductionConfig


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_schema_resource_matches_model_and_source() -> None:
    """Ship the exact Pydantic schema in the importable package."""
    resource = files("psdcalc.reduction.resources").joinpath("reduction.schema.json")
    payload = resource.read_text(encoding="utf-8")
    assert json.loads(payload) == ReductionConfig.model_json_schema()
    assert payload == (PACKAGE_ROOT / "schema/reduction.schema.json").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("name", ["reduction.first-batch.yaml",
                                   "reduction.three-voltage.yaml"])
def test_example_resource_matches_source(name: str) -> None:
    """Ship the maintained YAML example in the importable package."""
    resource = files("psdcalc.reduction.resources").joinpath(
        name
    )
    assert resource.read_text(encoding="utf-8") == (
        PACKAGE_ROOT / "examples" / name
    ).read_text(encoding="utf-8")
