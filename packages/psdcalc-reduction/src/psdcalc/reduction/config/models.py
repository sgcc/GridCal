"""Pydantic v2 models for the first-batch reduction configuration."""

from typing import Any, Literal, Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator
import math


class StrictModel(BaseModel):
    """Reject unknown fields at every declared model boundary."""

    model_config = ConfigDict(extra="forbid")


class RunConfig(StrictModel):
    """Run identity and reproducibility settings."""

    id: str
    seed: int
    deterministic: bool = True
    output_dir: str = "runs"


class InputConfig(StrictModel):
    """PyPSA input artifact and source namespace."""

    backend: Literal["pypsa"]
    path: str
    format: Literal["netcdf", "csv"]
    source_namespace: str


class TaskConfig(StrictModel):
    """Task context without granting physical acceptance authority."""

    type: Literal["ac_pf", "planning", "var_control", "ac_opf", "dc_security"]
    profile: Literal[
        "planning_aggregation", "ac_pf_strict", "var_control_strict",
        "ac_opf_warmstart", "dc_security_screening", "custom",
    ]
    reduction_role: str
    final_authority: str


class HierarchyLevel(StrictModel):
    """Known future level specification."""

    id: str
    ratio: float = Field(gt=0, le=1)


class HierarchyConfig(StrictModel):
    """Known future hierarchy settings."""

    nested: bool = False
    levels: list[HierarchyLevel] = Field(default_factory=list)


class PipelineConfig(StrictModel):
    """Construction family and future hierarchy placeholders."""

    materialization_family: Literal["aggregation", "simplification", "equivalencing", "hybrid", "multi_voltage_quotient"] = "aggregation"
    materialize_from: Literal["original", "parent"] = "original"
    hierarchy: HierarchyConfig = Field(default_factory=HierarchyConfig)


class SimplificationOperation(StrictModel):
    """Known but unsupported simplification operation."""

    id: str
    type: Literal["isolated_components", "remove_stubs", "map_voltage_layer", "simplify_dc_chains", "series_collapse", "parallel_aggregation", "voltage_aware_topology"]
    enabled: bool = False
    mode: Literal["keep_and_report", "keep_transformer_interface", "lift_to_target_voltage"] | None = None
    min_mean_power_mw: float | None = None
    across_regions: bool | None = None
    preserve_if_has_injection: bool | None = None
    preserve_if_has_control: bool | None = None
    preserve_if_protected: bool | None = None
    base_kv: float | None = None
    target_voltage_kv: float | None = None


class SimplificationConfig(StrictModel):
    """Future transforms are parsed but cannot execute in this batch."""

    operations: list[SimplificationOperation] = Field(default_factory=list)


class MatchConfig(StrictModel):
    """Declarative protection selector."""

    field: str
    op: Literal["eq", "ne", "in", "ge", "gt", "le", "lt"]
    value: Any


class ProtectionRule(StrictModel):
    """One protection rule with explicit action semantics."""

    id: str
    component: str
    match: MatchConfig | None = None
    ids_file: str | None = None
    action: Literal[
        "trace_only", "preserve_component_identity", "preserve_terminal_bus",
        "preserve_bus_as_singleton", "cannot_link",
    ]


class ProtectionConfig(StrictModel):
    """Protection rule collection."""

    rules: list[ProtectionRule] = Field(default_factory=list)


class FeatureBuilder(StrictModel):
    """Known feature builder declaration."""

    type: Literal["geographic_xy", "load_generation", "renewable_profile", "renewable_time", "electrical_distance", "qv_sensitivity"]
    enabled: bool = True
    weight: float | None = Field(default=None, ge=0)


class FeaturesConfig(StrictModel):
    """Planner feature declarations."""

    builders: list[FeatureBuilder] = Field(default_factory=list)


class PartitionTarget(StrictModel):
    """Single-level target cluster count or ratio."""

    type: Literal["count", "ratio", "from_hierarchy_level"] = "count"
    n_clusters: int | None = Field(default=None, gt=0)
    ratio: float | None = Field(default=None, gt=0, le=1)
    per_layer: dict[str, Annotated[int, Field(strict=True, gt=0)]] = Field(default_factory=dict)

    @field_validator("per_layer")
    @classmethod
    def valid_layers(cls, value):
        for key in value:
            try:
                voltage = float(key)
            except ValueError as exc:
                raise ValueError("layer keys must be positive finite voltages") from exc
            if not math.isfinite(voltage) or voltage <= 0:
                raise ValueError("layer keys must be positive finite voltages")
        return value


class CustomBusmapConfig(StrictModel):
    """Custom busmap artifact path."""

    path: str | None = None


class PartitionOptions(StrictModel):
    """Allowlisted PyPSA clustering options."""

    linkage: str = "ward"
    affinity: str = "euclidean"
    n_init: int = Field(default=10, gt=0)


class PartitionConfig(StrictModel):
    """Planner selection and connectivity policy."""

    planner: Literal["custom_busmap", "pypsa_kmeans", "pypsa_hac", "pypsa_modularity"]
    target: PartitionTarget
    connectivity_policy: Literal["reject", "split_disconnected"] = "reject"
    min_cluster_size: int = Field(default=1, gt=0)
    max_cluster_size: int | None = Field(default=None, gt=0)
    custom_busmap: CustomBusmapConfig = Field(default_factory=CustomBusmapConfig)
    options: PartitionOptions = Field(default_factory=PartitionOptions)


class GeneratorStrategies(StrictModel):
    """Allowlisted generator aggregation strategy names."""

    p_nom: str | None = None
    p_nom_max: str | None = None
    p_nom_min: str | None = None
    marginal_cost: str | None = None
    committable: str | None = None


class LoadStrategies(StrictModel):
    """Allowlisted load aggregation strategy names."""

    p_set: str | None = None
    q_set: str | None = None


class OnePortStrategies(StrictModel):
    """Known one-port strategy groups."""

    Load: LoadStrategies | None = None


class AggregationStrategies(StrictModel):
    """Known PyPSA aggregation strategy groups."""

    generators: GeneratorStrategies = Field(default_factory=GeneratorStrategies)
    one_ports: OnePortStrategies = Field(default_factory=OnePortStrategies)


class AggregationInvariantConfig(StrictModel):
    """Explicit supported invariant for final structural validation."""
    component: str = Field(min_length=1)
    attribute: str = Field(min_length=1)
    strategy: Literal["sum", "preserve_exact"]
    dynamic: bool = False
    scope: Literal["target", "source_connected_component", "global"] = "target"
    snapshots: tuple[Any, ...] | None = None
    atol: float = Field(default=1e-9, ge=0, allow_inf_nan=False)


class VoltageLayersConfig(StrictModel):
    """Exact positive nominal-voltage aliases."""
    aliases: dict[float, float] = Field(default_factory=dict)

    @field_validator("aliases", mode="before")
    @classmethod
    def valid_aliases(cls, value):
        if not isinstance(value, dict):
            raise ValueError("aliases must be a mapping")
        for source, target in value.items():
            for item in (source, target):
                if isinstance(item, bool):
                    raise ValueError("aliases must contain positive finite voltages")
                try:
                    number = float(item)
                except (ValueError, TypeError) as exc:
                    raise ValueError("aliases must contain positive finite voltages") from exc
                if not math.isfinite(number) or number <= 0:
                    raise ValueError("aliases must contain positive finite voltages")
        return value


class ParallelCorridorConfig(StrictModel):
    """Opt-in parallel Line identity policy."""

    policy: Literal[
        "aggregate_corridor", "preserve_protected_members",
        "preserve_all_members",
    ] = "aggregate_corridor"


class AggregationConfig(StrictModel):
    """PyPSA quotient options."""

    backend: Literal["pypsa"] = "pypsa"
    aggregate_one_ports: list[str] = Field(default_factory=list)
    preserve_components: list[str] = Field(default_factory=list)
    line_length_factor: float = Field(default=1.0, gt=0)
    strategies: AggregationStrategies = Field(default_factory=AggregationStrategies)
    invariants: list[AggregationInvariantConfig] = Field(default_factory=list)
    parallel_corridors: ParallelCorridorConfig = Field(
        default_factory=ParallelCorridorConfig)


class EquivalenceConfig(StrictModel):
    """Known future electrical equivalence settings."""

    enabled: bool = False
    backend: str | None = None
    method: str | None = None
    base_operating_point: str | None = None


class MappingConfig(StrictModel):
    """Persistent mapping artifact settings."""

    storage_format: Literal["parquet"] = "parquet"
    require_complete_bus_map: bool = True
    preserve_source_ids: bool = True
    record_device_ownership: bool = True
    record_control_ownership: bool = True


class StateLiftingConfig(StrictModel):
    """Known future state lifting settings."""

    method: str | None = None
    boundary_state: str | None = None


class ControlLiftingConfig(StrictModel):
    """Known future control lifting settings."""

    method: str | None = None
    allow_discrete_aggregation: bool = False
    allow_unit_aggregation: bool = False


class LiftingConfig(StrictModel):
    """Disabled future lifting declarations."""

    state: StateLiftingConfig = Field(default_factory=StateLiftingConfig)
    control: ControlLiftingConfig = Field(default_factory=ControlLiftingConfig)


class RefinementConfig(StrictModel):
    """Disabled future refinement settings."""

    enabled: bool = False
    policy: str | None = None
    r_states: list[str] = Field(default_factory=list)
    max_rounds: int | None = None


class StructuralValidationConfig(StrictModel):
    """Structural validation toggles."""

    enabled: bool = True
    require_connected_retained_network: bool = True
    reject_dangling_devices: bool = True
    reject_missing_protected_objects: bool = True


class FullGridValidationConfig(StrictModel):
    """Known future full-grid validation settings."""

    enabled: bool = False
    backend: str | None = None
    solver: str | None = None
    independent_solver: str | None = None
    require_candidate_materialization: bool | None = None


class AcceptanceConfig(StrictModel):
    """Known future physical acceptance settings."""

    require_convergence: bool = False
    require_hard_constraints: bool = False
    max_mismatch_pu: float | None = None


class ValidationConfig(StrictModel):
    """Structural checks and disabled physical validation declarations."""

    structural: StructuralValidationConfig = Field(default_factory=StructuralValidationConfig)
    full_grid: FullGridValidationConfig = Field(default_factory=FullGridValidationConfig)
    acceptance: AcceptanceConfig = Field(default_factory=AcceptanceConfig)


class CostAccountingConfig(StrictModel):
    """Known future full-chain cost settings."""

    include_planning: bool = False
    include_construction: bool = False
    include_solve: bool = False
    include_lifting: bool = False
    include_correction: bool = False
    include_refinement: bool = False
    include_validation: bool = False
    report_amortization: bool = False


class OutputConfig(StrictModel):
    """Optional output toggles; MV L1/L2 are mandatory audit artifacts."""

    save_original_snapshot: bool = True
    save_all_levels: bool = False  # Optional hierarchy, never MV L1/L2.
    save_maps: bool = True
    save_records: bool = True
    save_validation_evidence: bool = True
    save_metrics_parquet: bool = False


class PluginsConfig(StrictModel):
    """Disabled external plugin configuration."""

    allow_external: bool = False
    modules: list[str] = Field(default_factory=list)


class ReductionConfig(StrictModel):
    """Resolved first-batch reduction configuration."""

    schema_version: Literal["0.1.0"]
    run: RunConfig
    input: InputConfig
    task: TaskConfig
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    voltage_layers: VoltageLayersConfig = Field(default_factory=VoltageLayersConfig)
    simplification: SimplificationConfig = Field(default_factory=SimplificationConfig)
    protection: ProtectionConfig = Field(default_factory=ProtectionConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    partition: PartitionConfig | None = None
    aggregation: AggregationConfig
    equivalence: EquivalenceConfig = Field(default_factory=EquivalenceConfig)
    mapping: MappingConfig = Field(default_factory=MappingConfig)
    lifting: LiftingConfig = Field(default_factory=LiftingConfig)
    refinement: RefinementConfig = Field(default_factory=RefinementConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    cost_accounting: CostAccountingConfig = Field(default_factory=CostAccountingConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
