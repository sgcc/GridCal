"""Safe YAML loading, profile resolution, and first-batch gates."""

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import ValidationError
import yaml

from psdcalc.reduction.config.models import ReductionConfig
from psdcalc.reduction.config.profiles import profile_defaults
from psdcalc.reduction.exceptions import (
    ConfigSchemaError,
    ConfigSemanticError,
    UnsupportedFeatureError,
)


def _merge(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge configuration mappings without mutating inputs."""
    result = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _source_mapping(source: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    """Read a mapping or YAML file without executing custom YAML tags."""
    if isinstance(source, Mapping):
        return deepcopy(dict(source))
    try:
        raw = yaml.safe_load(Path(source).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigSchemaError(f"Cannot read configuration: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigSchemaError("Configuration root must be a mapping")
    return raw


def _gate_features(config: ReductionConfig) -> None:
    """Reject enabled later-stage capabilities without swallowing failures."""
    if config.pipeline.materialization_family not in {"aggregation", "simplification", "multi_voltage_quotient"}:
        raise UnsupportedFeatureError("pipeline.materialization_family is not implemented")
    if config.pipeline.materialize_from != "original":
        raise UnsupportedFeatureError("pipeline.materialize_from=parent requires hierarchy")
    if config.pipeline.hierarchy.nested or config.pipeline.hierarchy.levels:
        raise UnsupportedFeatureError("pipeline.hierarchy is not implemented")
    enabled = [op for op in config.simplification.operations if op.enabled]
    if any(op.type != "voltage_aware_topology" for op in enabled):
        raise UnsupportedFeatureError("requested simplification operation is not implemented")
    if len(enabled) > 1:
        raise UnsupportedFeatureError("multiple simplification operations are not implemented")
    if enabled and enabled[0].mode == "lift_to_target_voltage":
        if config.task.profile != "planning_aggregation":
            raise ConfigSemanticError("voltage lift is restricted to planning_aggregation")
        raise UnsupportedFeatureError("planning voltage lift is not implemented")
    if config.equivalence.enabled:
        raise UnsupportedFeatureError("equivalence is not implemented")
    if (config.lifting.state.method is not None or
            config.lifting.control.method is not None):
        raise UnsupportedFeatureError("lifting is not implemented")
    if config.refinement.enabled:
        raise UnsupportedFeatureError("refinement is not implemented")
    if config.validation.full_grid.enabled:
        raise UnsupportedFeatureError("validation.full_grid is not implemented")
    if (config.validation.acceptance.require_convergence or
            config.validation.acceptance.require_hard_constraints or
            config.validation.acceptance.max_mismatch_pu is not None):
        raise UnsupportedFeatureError("validation.acceptance is not implemented")
    if config.output.save_all_levels:
        raise UnsupportedFeatureError(
            "output.save_all_levels controls optional hierarchy levels; "
            "multi-voltage L1/L2 are mandatory audit artifacts")
    if config.output.save_metrics_parquet:
        raise UnsupportedFeatureError("metrics Parquet output is not implemented")
    if config.plugins.allow_external or config.plugins.modules:
        raise UnsupportedFeatureError("external plugins are not implemented")
    if config.partition is not None and config.partition.target.type == "from_hierarchy_level":
        raise UnsupportedFeatureError("hierarchy target is not implemented")
    if any(b.enabled and b.type not in {"geographic_xy", "load_generation", "renewable_time"}
           for b in config.features.builders):
        raise UnsupportedFeatureError("requested feature builder is not implemented")
    if any(getattr(config.cost_accounting, name) for name in (
        "include_solve", "include_lifting", "include_correction", "include_refinement",
        "include_validation", "report_amortization",
    )):
        raise UnsupportedFeatureError("full-chain cost accounting is not implemented")
    if config.task.profile in {"var_control_strict", "ac_opf_warmstart", "dc_security_screening"}:
        raise UnsupportedFeatureError(f"task.profile={config.task.profile} is not implemented")


def _check_semantics(config: ReductionConfig) -> None:
    """Reject well-formed but inconsistent first-batch settings."""
    if not config.run.deterministic:
        raise ConfigSemanticError("run.deterministic must be true in the first phase")
    if not config.validation.structural.enabled:
        raise ConfigSemanticError("validation.structural.enabled is required")
    if not (config.mapping.require_complete_bus_map and
            config.mapping.preserve_source_ids and
            config.mapping.record_device_ownership and
            config.mapping.record_control_ownership):
        raise ConfigSemanticError("complete source-ID mapping is required")
    if not (config.output.save_maps and config.output.save_records and
            config.output.save_validation_evidence):
        raise ConfigSemanticError("mapping, records, and structural evidence are required")
    enabled = [op for op in config.simplification.operations if op.enabled]
    if config.pipeline.materialization_family == "simplification":
        if len(enabled) != 1 or enabled[0].type != "voltage_aware_topology":
            raise ConfigSemanticError("simplification pipeline requires one voltage_aware_topology operation")
    if config.pipeline.materialization_family == "simplification":
        return
    if config.partition is None:
        raise ConfigSemanticError("partition is required for aggregation")
    target = config.partition.target
    if config.pipeline.materialization_family == "multi_voltage_quotient":
        if (len(enabled) != 1 or enabled[0].type != "voltage_aware_topology" or
                enabled[0].mode != "keep_transformer_interface"):
            raise ConfigSemanticError("multi_voltage_quotient requires voltage_aware_topology with keep_transformer_interface")
        if target.type != "count" or target.n_clusters is None:
            raise ConfigSemanticError("multi_voltage_quotient requires a count target")
        if config.partition.planner == "custom_busmap":
            raise ConfigSemanticError("multi_voltage_quotient requires an energy planner")
        if sum(target.per_layer.values()) > target.n_clusters:
            raise ConfigSemanticError("per_layer targets exceed global target")
    elif target.per_layer:
        raise ConfigSemanticError("per_layer targets require multi_voltage_quotient")
    if target.type == "count" and target.ratio is not None:
        raise ConfigSemanticError("count target cannot also specify ratio")
    if target.type == "ratio" and target.n_clusters is not None:
        raise ConfigSemanticError("ratio target cannot also specify n_clusters")
    if (config.partition.planner != "custom_busmap" and
            not any(op.type == "voltage_aware_topology" for op in enabled)):
        raise ConfigSemanticError("energy planners require preceding voltage_aware_topology")
    if config.partition.planner == "custom_busmap":
        if not config.partition.custom_busmap.path:
            raise ConfigSemanticError("custom_busmap.path is required for custom_busmap planner")
    elif target.type == "count" and target.n_clusters is None:
        raise ConfigSemanticError("partition.target.n_clusters is required")
    elif target.type == "ratio" and target.ratio is None:
        raise ConfigSemanticError("partition.target.ratio is required")
    maximum = config.partition.max_cluster_size
    if maximum is not None and maximum < config.partition.min_cluster_size:
        raise ConfigSemanticError("max_cluster_size must be >= min_cluster_size")


def load_config(
    source: Mapping[str, Any] | str | Path,
    *,
    scenario: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> ReductionConfig:
    """Resolve and validate a first-batch configuration.

    Raises ConfigSchemaError, UnsupportedFeatureError, or ConfigSemanticError.
    """
    project = _source_mapping(source)
    task = project.get("task")
    if not isinstance(task, Mapping):
        raise ConfigSchemaError("task must be a mapping with a profile")
    profile = task.get("profile")
    try:
        base = profile_defaults(profile)
    except (KeyError, TypeError) as exc:
        raise ConfigSchemaError(f"Unknown or missing task.profile: {profile}") from exc
    resolved = _merge(base, project)
    if scenario:
        resolved = _merge(resolved, scenario)
    if overrides:
        resolved = _merge(resolved, overrides)
    try:
        config = ReductionConfig.model_validate(resolved)
    except ValidationError as exc:
        raise ConfigSchemaError(str(exc)) from exc
    _gate_features(config)
    _check_semantics(config)
    return config
