"""First-batch configuration contract tests."""

from pathlib import Path

import pytest

from psdcalc.reduction.config.loader import load_config
from psdcalc.reduction.config.schema_export import export_schema
from psdcalc.reduction.cli import main
from psdcalc.reduction.exceptions import (
    ConfigSchemaError,
    ConfigSemanticError,
    UnsupportedFeatureError,
)


def minimal_config() -> dict:
    """Return a valid single-level PyPSA construction configuration."""
    return {
        "schema_version": "0.1.0",
        "run": {"id": "small", "seed": 42, "deterministic": True, "output_dir": "runs"},
        "input": {"backend": "pypsa", "path": "small.nc", "format": "netcdf",
                  "source_namespace": "test"},
        "task": {"type": "ac_pf", "profile": "ac_pf_strict",
                 "reduction_role": "warm_start",
                 "final_authority": "full_grid_independent_pf"},
        "partition": {"planner": "pypsa_kmeans", "target": {"n_clusters": 2},
                      "connectivity_policy": "reject"},
        "simplification": {"operations": [
            {"id": "selective", "type": "voltage_aware_topology", "enabled": True,
             "mode": "keep_transformer_interface"}
        ]},
        "aggregation": {"backend": "pypsa"},
    }


def test_voltage_policy_omission_matches_explicit_base() -> None:
    omitted = load_config(minimal_config())
    explicit_data = minimal_config()
    explicit_data["voltage_policy"] = {
        "enabled": False, "mode": "freeze_at_or_above", "cutoff_kv": None,
    }
    explicit = load_config(explicit_data)
    assert omitted.voltage_policy == explicit.voltage_policy


def test_voltage_policy_accepts_positive_enabled_cutoff() -> None:
    data = minimal_config()
    data["voltage_policy"] = {"enabled": True, "cutoff_kv": 110}
    assert load_config(data).voltage_policy.cutoff_kv == 110


@pytest.mark.parametrize("policy", [
    {"enabled": True},
    {"enabled": False, "cutoff_kv": 110},
    {"enabled": True, "cutoff_kv": 0},
    {"enabled": True, "cutoff_kv": -1},
    {"enabled": True, "cutoff_kv": float("nan")},
    {"enabled": True, "cutoff_kv": float("inf")},
    {"enabled": True, "cutoff_kv": True},
    {"enabled": False, "mode": "nearest_layer"},
    {"enabled": False, "surprise": 1},
])
def test_voltage_policy_rejects_invalid_contract(policy: dict) -> None:
    from pydantic import ValidationError
    from psdcalc.reduction.config.models import VoltagePolicyConfig

    with pytest.raises(ValidationError):
        VoltagePolicyConfig.model_validate(policy)


def test_unknown_nested_field_is_schema_error() -> None:
    data = minimal_config()
    data["partition"]["mystery"] = True

    with pytest.raises(ConfigSchemaError, match="mystery"):
        load_config(data)


def test_wrong_section_type_is_schema_error() -> None:
    data = minimal_config()
    data["task"] = "not-a-mapping"

    with pytest.raises(ConfigSchemaError, match="task"):
        load_config(data)


def test_disabled_future_fields_parse_but_enabled_future_feature_fails() -> None:
    data = minimal_config()
    data["equivalence"] = {"enabled": False, "backend": "veragrid", "method": "ward"}
    data["refinement"] = {"enabled": False, "policy": "rule_based", "max_rounds": 4}
    assert load_config(data).equivalence.enabled is False

    data["equivalence"]["enabled"] = True
    with pytest.raises(UnsupportedFeatureError, match="equivalence"):
        load_config(data)


def test_disabled_voltage_aware_transform_parses_but_enabled_one_fails() -> None:
    data = minimal_config()
    data["partition"]["planner"] = "custom_busmap"
    data["partition"]["custom_busmap"] = {"path": "map.csv"}
    data["simplification"] = {"operations": [
        {"id": "selective", "type": "voltage_aware_topology", "enabled": False,
         "mode": "keep_transformer_interface"}
    ]}
    assert not load_config(data).simplification.operations[0].enabled

    data["simplification"]["operations"][0]["enabled"] = True
    assert load_config(data).simplification.operations[0].enabled

    data["pipeline"] = {"materialization_family": "simplification"}
    assert load_config(data).simplification.operations[0].enabled


def test_declared_lifting_method_is_unsupported() -> None:
    data = minimal_config()
    data["lifting"] = {"state": {"method": "local_pf"}}

    with pytest.raises(UnsupportedFeatureError, match="lifting"):
        load_config(data)


def test_disabled_renewable_time_feature_parses() -> None:
    data = minimal_config()
    data["features"] = {"builders": [{"type": "renewable_time", "enabled": False}]}

    assert load_config(data).features.builders[0].type == "renewable_time"


def test_unknown_voltage_aware_mode_is_schema_error() -> None:
    data = minimal_config()
    data["simplification"] = {"operations": [
        {"id": "selective", "type": "voltage_aware_topology", "enabled": False,
         "mode": "flatten_everything"}
    ]}

    with pytest.raises(ConfigSchemaError, match="mode"):
        load_config(data)


def test_voltage_lift_is_planning_only_and_explicitly_unsupported() -> None:
    data = minimal_config()
    data["simplification"]["operations"][0]["mode"] = "lift_to_target_voltage"
    with pytest.raises(ConfigSemanticError, match="planning_aggregation"):
        load_config(data)
    data["task"]["type"] = "planning"
    data["task"]["profile"] = "planning_aggregation"
    with pytest.raises(UnsupportedFeatureError, match="voltage lift"):
        load_config(data)


def test_conflicting_partition_fields_are_semantic_error() -> None:
    data = minimal_config()
    data["partition"]["planner"] = "custom_busmap"

    with pytest.raises(ConfigSemanticError, match="custom_busmap.path"):
        load_config(data)


def test_required_evidence_cannot_be_disabled() -> None:
    data = minimal_config()
    data["output"] = {"save_maps": False}
    with pytest.raises(ConfigSemanticError, match="evidence are required"):
        load_config(data)


def test_count_target_rejects_ratio_field() -> None:
    data = minimal_config()
    data["partition"]["target"]["ratio"] = 0.5
    with pytest.raises(ConfigSemanticError, match="cannot also specify ratio"):
        load_config(data)


def test_schema_export_forbids_unknown_top_level_fields(tmp_path: Path) -> None:
    target = tmp_path / "schema.json"
    export_schema(target)

    import json
    schema = json.loads(target.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert "partition" in schema["properties"]


def test_validate_config_cli_reports_valid_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import yaml

    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(minimal_config()), encoding="utf-8")

    assert main(["validate-config", str(path)]) == 0
    assert "VALID" in capsys.readouterr().out
