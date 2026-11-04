"""Versioned, immutable contracts for comprehensive reduction reports.

The models in this module are an evidence schema only. They do not execute
power flow, recalculate PR-022 metrics, or change any admission decision.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

REPORT_SCHEMA_VERSION = "psdcalc-comprehensive-reduction-report/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_PUBLIC_ID = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")


class EvidenceStatus(StrEnum):
    """Availability state for one report value."""

    AVAILABLE = "AVAILABLE"
    NOT_RECORDED = "NOT_RECORDED"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class MetricOutcome(StrEnum):
    """Reporting outcome, separate from the evidence availability state."""

    PASS = "PASS"
    FAIL = "FAIL"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"
    NOT_EVALUATED = "NOT_EVALUATED"
    NOT_RECORDED = "NOT_RECORDED"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class MetricGroup(StrEnum):
    """Network subset associated with an observed PF metric."""

    WHOLE_NETWORK = "WHOLE_NETWORK"
    RETAINED_BACKBONE = "RETAINED_BACKBONE"
    CROSS_VOLTAGE_INTERFACES = "CROSS_VOLTAGE_INTERFACES"
    AGGREGATED_REGION = "AGGREGATED_REGION"


class ReportModel(BaseModel):
    """Base class that rejects unrecognized report fields and mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EvidenceValue(ReportModel):
    """One scalar or small histogram plus its explicit evidence state."""

    value: int | float | str | bool | dict[str, int] | None = None
    state: EvidenceStatus = EvidenceStatus.NOT_RECORDED
    unit: str | None = None
    sample_count: int | None = None
    mapping_coverage: float | None = None
    source_identity_digest: str | None = None
    target_identity_digest: str | None = None
    threshold: float | None = None
    outcome: MetricOutcome = MetricOutcome.NOT_EVALUATED

    @model_validator(mode="after")
    def validate_evidence_state(self) -> "EvidenceValue":
        """Require values only when their evidence is explicitly available."""
        if self.state is EvidenceStatus.AVAILABLE and self.value is None:
            raise ValueError("available evidence requires a value")
        if self.state is not EvidenceStatus.AVAILABLE and self.value is not None:
            raise ValueError("unavailable evidence cannot contain a value")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("evidence values must be finite")
        if isinstance(self.value, dict) and any(
                isinstance(value, bool) or value < 0 for value in self.value.values()):
            raise ValueError("histogram counts must be nonnegative integers")
        if self.sample_count is not None and (
                isinstance(self.sample_count, bool) or self.sample_count < 0):
            raise ValueError("sample_count must be nonnegative or None")
        if self.mapping_coverage is not None and (
                not math.isfinite(self.mapping_coverage)
                or not 0.0 <= self.mapping_coverage <= 1.0):
            raise ValueError("mapping_coverage must be within [0, 1]")
        for name in ("source_identity_digest", "target_identity_digest"):
            digest = getattr(self, name)
            if digest is not None and _SHA256.fullmatch(digest) is None:
                raise ValueError(f"{name} must be SHA-256")
        if self.threshold is not None and not math.isfinite(self.threshold):
            raise ValueError("threshold must be finite")
        return self


def not_recorded() -> EvidenceValue:
    """Return a fresh explicit missing-evidence value."""
    return EvidenceValue(state=EvidenceStatus.NOT_RECORDED)


class EvidenceReference(ReportModel):
    """Repository-relative reference to a digest-bound evidence artifact."""

    path: str
    sha256: str

    @model_validator(mode="after")
    def validate_reference(self) -> "EvidenceReference":
        """Reject absolute paths, traversal, and malformed SHA-256 values."""
        pure = PurePosixPath(self.path)
        if (not self.path or pure.is_absolute() or ".." in pure.parts
                or "\\" in self.path or self.path.startswith("~")):
            raise ValueError("evidence path must be repository-relative")
        if _SHA256.fullmatch(self.sha256) is None:
            raise ValueError("evidence reference SHA-256 is invalid")
        return self


class ReportProvenance(ReportModel):
    """Public run identity and source/software bindings."""

    study_id: str
    run_id: str
    experiment_id: str | None = None
    package_version: str | None = None
    pypsa_version: str | None = None
    implementation_commit: str | None = None
    implementation_tree: str | None = None
    source_model_sha256: str | None = None
    source_sidecar_sha256: str | None = None
    source_ledger_sha256: str | None = None
    config_sha256: str | None = None
    metric_digest: str | None = None
    threshold_set_id: str | None = None
    threshold_sha256: str | None = None
    software_versions: dict[str, str] = Field(default_factory=dict)
    evidence_sources: tuple[EvidenceReference, ...] = ()

    @model_validator(mode="after")
    def validate_provenance(self) -> "ReportProvenance":
        """Validate public identifiers and digest lengths without exposing paths."""
        for name in ("study_id", "run_id"):
            if _PUBLIC_ID.fullmatch(getattr(self, name)) is None:
                raise ValueError(f"{name} must be a public slug")
        for name in ("implementation_commit", "implementation_tree"):
            value = getattr(self, name)
            if value is not None and _SHA1.fullmatch(value) is None:
                raise ValueError(f"{name} must be a Git SHA-1")
        for name in ("source_model_sha256", "source_sidecar_sha256",
                     "source_ledger_sha256", "config_sha256", "metric_digest",
                     "threshold_sha256"):
            value = getattr(self, name)
            if value is not None and _SHA256.fullmatch(value) is None:
                raise ValueError(f"{name} must be SHA-256")
        return self


class NetworkInventory(ReportModel):
    """Component and voltage-layer inventory for one network stage."""

    bus_count: EvidenceValue = Field(default_factory=not_recorded)
    line_count: EvidenceValue = Field(default_factory=not_recorded)
    transformer_count: EvidenceValue = Field(default_factory=not_recorded)
    transformer_count_basis: Literal[
        "PHYSICAL_UNITS", "BACKEND_ROWS", "NOT_RECORDED"] = "NOT_RECORDED"
    transformer_3w_physical_count: EvidenceValue = Field(default_factory=not_recorded)
    transformer_3w_t_leg_projection_count: EvidenceValue = Field(default_factory=not_recorded)
    link_count: EvidenceValue = Field(default_factory=not_recorded)
    total_branch_count: EvidenceValue = Field(default_factory=not_recorded)
    generator_count: EvidenceValue = Field(default_factory=not_recorded)
    load_count: EvidenceValue = Field(default_factory=not_recorded)
    shunt_count: EvidenceValue = Field(default_factory=not_recorded)
    connected_component_count: EvidenceValue = Field(default_factory=not_recorded)
    voltage_layers_kv: tuple[float, ...] = ()

    @model_validator(mode="after")
    def validate_layers(self) -> "NetworkInventory":
        """Require exact, sorted, finite nominal-voltage values."""
        if tuple(sorted(set(self.voltage_layers_kv))) != self.voltage_layers_kv:
            raise ValueError("voltage_layers_kv must be sorted and unique")
        if any(not math.isfinite(value) or value <= 0.0
               for value in self.voltage_layers_kv):
            raise ValueError("voltage layers must be positive finite kV")
        return self


class OpportunitySection(ReportModel):
    """Structural opportunities, planning candidates, and realized results."""

    protected_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    strict_singleton_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    anchor_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    radial_tree_eligible_count: EvidenceValue = Field(default_factory=not_recorded)
    radial_tree_rejected_count: EvidenceValue = Field(default_factory=not_recorded)
    radial_tree_candidate_count: EvidenceValue = Field(default_factory=not_recorded)
    series_chain_eligible_count: EvidenceValue = Field(default_factory=not_recorded)
    series_chain_rejected_count: EvidenceValue = Field(default_factory=not_recorded)
    series_chain_candidate_count: EvidenceValue = Field(default_factory=not_recorded)
    retained_cycle_rank: EvidenceValue = Field(default_factory=not_recorded)
    retained_parallel_corridor_count: EvidenceValue = Field(default_factory=not_recorded)
    eligible_count: EvidenceValue = Field(default_factory=not_recorded)
    rejected_count: EvidenceValue = Field(default_factory=not_recorded)
    rejection_histogram: EvidenceValue = Field(default_factory=not_recorded)
    minimum_cluster_count: EvidenceValue = Field(default_factory=not_recorded)
    structural_compression_ceiling_bus_fraction: EvidenceValue = Field(default_factory=not_recorded)
    structural_compression_ceiling_line_fraction: EvidenceValue = Field(default_factory=not_recorded)
    structural_compression_ceiling_total_branch_fraction: EvidenceValue = Field(
        default_factory=not_recorded)
    planning_candidate_bus_fraction: EvidenceValue = Field(default_factory=not_recorded)
    planning_candidate_line_fraction: EvidenceValue = Field(default_factory=not_recorded)
    planning_candidate_total_branch_fraction: EvidenceValue = Field(default_factory=not_recorded)
    actual_achieved_bus_fraction: EvidenceValue = Field(default_factory=not_recorded)
    actual_achieved_line_fraction: EvidenceValue = Field(default_factory=not_recorded)
    actual_achieved_total_branch_fraction: EvidenceValue = Field(default_factory=not_recorded)
    actual_accepted_bus_fraction: EvidenceValue = Field(default_factory=not_recorded)
    actual_accepted_line_fraction: EvidenceValue = Field(default_factory=not_recorded)
    actual_accepted_total_branch_fraction: EvidenceValue = Field(default_factory=not_recorded)
    ceiling_semantics: Literal["STRUCTURAL_BOUND_NOT_GLOBAL_OPTIMUM"] = (
        "STRUCTURAL_BOUND_NOT_GLOBAL_OPTIMUM")


class ComponentMappingSummary(ReportModel):
    """Anonymous mapping counts for one source component type."""

    component: str
    source_count: EvidenceValue = Field(default_factory=not_recorded)
    target_count: EvidenceValue = Field(default_factory=not_recorded)
    preserved_identity_count: EvidenceValue = Field(default_factory=not_recorded)
    aggregated_count: EvidenceValue = Field(default_factory=not_recorded)
    absorbed_internal_count: EvidenceValue = Field(default_factory=not_recorded)
    mapping_coverage: EvidenceValue = Field(default_factory=not_recorded)
    source_identity_digest: str | None = None
    target_identity_digest: str | None = None

    @model_validator(mode="after")
    def validate_identity_hashes(self) -> "ComponentMappingSummary":
        """Validate hashes used in place of raw StableIds."""
        for value in (self.source_identity_digest, self.target_identity_digest):
            if value is not None and _SHA256.fullmatch(value) is None:
                raise ValueError("mapping identity digest must be SHA-256")
        return self


class StructuralComparison(ReportModel):
    """Structural inventories, map transitions, and final structural Gate."""

    stages: tuple["StageInventorySummary", ...] = ()
    target_inventory: NetworkInventory = Field(default_factory=NetworkInventory)
    component_mappings: tuple[ComponentMappingSummary, ...] = ()
    absorbed_internal_branch_count: EvidenceValue = Field(default_factory=not_recorded)
    aggregated_parallel_corridor_count: EvidenceValue = Field(default_factory=not_recorded)
    cross_voltage_line_count: EvidenceValue = Field(default_factory=not_recorded)
    cross_voltage_link_count: EvidenceValue = Field(default_factory=not_recorded)
    preserved_identity_count: EvidenceValue = Field(default_factory=not_recorded)
    mapping_coverage: EvidenceValue = Field(default_factory=not_recorded)
    source_connected_components: EvidenceValue = Field(default_factory=not_recorded)
    target_connected_components: EvidenceValue = Field(default_factory=not_recorded)
    protected_cycle_preservation: EvidenceValue = Field(default_factory=not_recorded)
    structural_gate_status: EvidenceValue = Field(default_factory=not_recorded)
    line_compression_fraction: EvidenceValue = Field(default_factory=not_recorded)
    total_branch_compression_fraction: EvidenceValue = Field(default_factory=not_recorded)


class StageInventorySummary(ReportModel):
    """Inventory at one named reduction stage."""

    stage: str
    inventory: NetworkInventory

    @model_validator(mode="after")
    def validate_stage(self) -> "StageInventorySummary":
        """Reject empty or private stage labels."""
        if not self.stage or not _PUBLIC_ID.fullmatch(self.stage):
            raise ValueError("stage must be a public identifier")
        return self


class VoltageLayerSummary(ReportModel):
    """Exact nominal-voltage layer counts and identity coverage."""

    voltage_kv: float
    policy_id: str | None = None
    source_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    source_line_count: EvidenceValue = Field(default_factory=not_recorded)
    retained_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    reduced_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    retained_line_count: EvidenceValue = Field(default_factory=not_recorded)
    reduced_line_count: EvidenceValue = Field(default_factory=not_recorded)
    protected_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    protected_line_count: EvidenceValue = Field(default_factory=not_recorded)
    rejected_count: EvidenceValue = Field(default_factory=not_recorded)
    radial_structure_count: EvidenceValue = Field(default_factory=not_recorded)
    meshed_structure_count: EvidenceValue = Field(default_factory=not_recorded)
    transformer_interface_count: EvidenceValue = Field(default_factory=not_recorded)
    bus_compression_fraction: EvidenceValue = Field(default_factory=not_recorded)
    line_compression_fraction: EvidenceValue = Field(default_factory=not_recorded)
    retained_identity_coverage: EvidenceValue = Field(default_factory=not_recorded)
    retained_line_identity_coverage: EvidenceValue = Field(default_factory=not_recorded)

    @model_validator(mode="after")
    def validate_voltage(self) -> "VoltageLayerSummary":
        """Keep actual voltage values positive and exact."""
        if not math.isfinite(self.voltage_kv) or self.voltage_kv <= 0.0:
            raise ValueError("voltage_kv must be positive and finite")
        return self


class VoltagePolicyResolution(ReportModel):
    """One requested cutoff and its actual retained/compressible layers."""

    policy_id: str
    requested_cutoff_kv: float | None
    source_voltage_layers_kv: tuple[float, ...]
    retained_voltage_layers_kv: tuple[float, ...]
    compressible_voltage_layers_kv: tuple[float, ...]
    equivalent_policy_ids: tuple[str, ...] = ()
    resolved_policy_fingerprint: str

    @model_validator(mode="after")
    def validate_resolution(self) -> "VoltagePolicyResolution":
        """Require a valid digest and a disjoint exact layer partition."""
        if _SHA256.fullmatch(self.resolved_policy_fingerprint) is None:
            raise ValueError("resolved policy fingerprint must be SHA-256")
        if tuple(sorted(set(self.source_voltage_layers_kv))) != self.source_voltage_layers_kv:
            raise ValueError("source voltage layers must be sorted and unique")
        if tuple(sorted(set(self.retained_voltage_layers_kv))) != self.retained_voltage_layers_kv:
            raise ValueError("retained voltage layers must be sorted and unique")
        if tuple(sorted(set(self.compressible_voltage_layers_kv))) != self.compressible_voltage_layers_kv:
            raise ValueError("compressible voltage layers must be sorted and unique")
        if set(self.retained_voltage_layers_kv) & set(self.compressible_voltage_layers_kv):
            raise ValueError("retained and compressible voltage layers overlap")
        if set(self.source_voltage_layers_kv) != (
                set(self.retained_voltage_layers_kv) | set(self.compressible_voltage_layers_kv)):
            raise ValueError("resolved layers do not cover source voltage layers")
        return self


class FidelityObservation(ReportModel):
    """One existing PF/Fidelity measurement, optionally grouped by topology."""

    group: MetricGroup
    metric_name: str
    severity: Literal["HARD", "DIAGNOSTIC"] | None = None
    observed: EvidenceValue = Field(default_factory=not_recorded)
    unit: str | None = None
    sample_count: int | None = None
    sample_count_status: EvidenceStatus = EvidenceStatus.NOT_RECORDED
    mapping_coverage: float | None = None
    mapping_coverage_status: EvidenceStatus = EvidenceStatus.NOT_RECORDED
    source_identity_digest: str | None = None
    target_identity_digest: str | None = None
    identity_binding_status: EvidenceStatus = EvidenceStatus.NOT_RECORDED
    threshold: EvidenceValue = Field(default_factory=not_recorded)
    outcome: MetricOutcome = MetricOutcome.NOT_RECORDED
    metric_digest: str | None = None
    threshold_digest: str | None = None

    @model_validator(mode="after")
    def validate_observation(self) -> "FidelityObservation":
        """Validate metric identity, counts, coverage, and digest bindings."""
        if not self.metric_name or not _PUBLIC_ID.fullmatch(self.metric_name):
            raise ValueError("metric_name must be a public identifier")
        if self.sample_count is not None and (
                isinstance(self.sample_count, bool) or self.sample_count < 0):
            raise ValueError("sample_count must be nonnegative or None")
        if self.mapping_coverage is not None and (
                not math.isfinite(self.mapping_coverage)
                or not 0.0 <= self.mapping_coverage <= 1.0):
            raise ValueError("mapping_coverage must be within [0, 1]")
        for digest in (self.source_identity_digest, self.target_identity_digest,
                       self.metric_digest, self.threshold_digest):
            if digest is not None and _SHA256.fullmatch(digest) is None:
                raise ValueError("fidelity identity and evidence digests must be SHA-256")
        return self


class ErrorStatistics(ReportModel):
    """Diagnostic absolute-error summary with an explicit sample count."""

    maximum: EvidenceValue = Field(default_factory=not_recorded)
    rmse: EvidenceValue = Field(default_factory=not_recorded)
    p95: EvidenceValue = Field(default_factory=not_recorded)
    sample_count: EvidenceValue = Field(default_factory=not_recorded)


class ValueStatistics(ReportModel):
    """Signed value summary for directional P/Q observations."""

    minimum: EvidenceValue = Field(default_factory=not_recorded)
    maximum: EvidenceValue = Field(default_factory=not_recorded)
    mean: EvidenceValue = Field(default_factory=not_recorded)
    max_absolute: EvidenceValue = Field(default_factory=not_recorded)
    sample_count: EvidenceValue = Field(default_factory=not_recorded)


class RetainedGroupComparison(ReportModel):
    """Identity coverage and numerical summaries for one network group."""

    group: MetricGroup
    status: EvidenceStatus = EvidenceStatus.NOT_RECORDED
    source_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    retained_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    compared_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    missing_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    unmatched_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    source_branch_count: EvidenceValue = Field(default_factory=not_recorded)
    retained_branch_count: EvidenceValue = Field(default_factory=not_recorded)
    compared_branch_count: EvidenceValue = Field(default_factory=not_recorded)
    missing_branch_count: EvidenceValue = Field(default_factory=not_recorded)
    unmatched_branch_count: EvidenceValue = Field(default_factory=not_recorded)
    mapping_coverage: EvidenceValue = Field(default_factory=not_recorded)
    bus_vm: ErrorStatistics = Field(default_factory=ErrorStatistics)
    bus_va: ErrorStatistics = Field(default_factory=ErrorStatistics)
    line_p: ErrorStatistics = Field(default_factory=ErrorStatistics)
    line_q: ErrorStatistics = Field(default_factory=ErrorStatistics)
    transformer_p: ErrorStatistics = Field(default_factory=ErrorStatistics)
    transformer_q: ErrorStatistics = Field(default_factory=ErrorStatistics)
    injection_p: ErrorStatistics = Field(default_factory=ErrorStatistics)
    injection_q: ErrorStatistics = Field(default_factory=ErrorStatistics)


class CrossVoltageInterfaceReport(ReportModel):
    """Direction-normalized AC boundary flow and region-injection diagnostics."""

    interface_count: EvidenceValue = Field(default_factory=not_recorded)
    source_branch_member_count: EvidenceValue = Field(default_factory=not_recorded)
    target_branch_count: EvidenceValue = Field(default_factory=not_recorded)
    matched_interface_count: EvidenceValue = Field(default_factory=not_recorded)
    unmatched_interface_count: EvidenceValue = Field(default_factory=not_recorded)
    mapping_coverage: EvidenceValue = Field(default_factory=not_recorded)
    source_p_mw: ValueStatistics = Field(default_factory=ValueStatistics)
    target_p_mw: ValueStatistics = Field(default_factory=ValueStatistics)
    p_error_mw: ErrorStatistics = Field(default_factory=ErrorStatistics)
    source_q_mvar: ValueStatistics = Field(default_factory=ValueStatistics)
    target_q_mvar: ValueStatistics = Field(default_factory=ValueStatistics)
    q_error_mvar: ErrorStatistics = Field(default_factory=ErrorStatistics)
    region_net_injection_p_error_mw: ErrorStatistics = Field(default_factory=ErrorStatistics)
    region_net_injection_q_error_mvar: ErrorStatistics = Field(default_factory=ErrorStatistics)


class RetainedVoltageLayerComparison(ReportModel):
    """Per-cutoff counts and preserved-object AC errors on one actual layer."""

    cutoff_kv: float
    voltage_kv: float
    transformer_count_basis: Literal["BACKEND_ROWS"] = "BACKEND_ROWS"
    source_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    policy_protected_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    retained_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    reduced_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    target_bus_count: EvidenceValue = Field(default_factory=not_recorded)
    source_line_count: EvidenceValue = Field(default_factory=not_recorded)
    policy_protected_line_count: EvidenceValue = Field(default_factory=not_recorded)
    retained_line_count: EvidenceValue = Field(default_factory=not_recorded)
    reduced_line_count: EvidenceValue = Field(default_factory=not_recorded)
    target_line_count: EvidenceValue = Field(default_factory=not_recorded)
    source_transformer_count: EvidenceValue = Field(default_factory=not_recorded)
    retained_transformer_count: EvidenceValue = Field(default_factory=not_recorded)
    reduced_transformer_count: EvidenceValue = Field(default_factory=not_recorded)
    target_transformer_count: EvidenceValue = Field(default_factory=not_recorded)
    preserved_identity_coverage: EvidenceValue = Field(default_factory=not_recorded)
    preserved_bus_identity_coverage: EvidenceValue = Field(default_factory=not_recorded)
    preserved_line_identity_coverage: EvidenceValue = Field(default_factory=not_recorded)
    preserved_transformer_identity_coverage: EvidenceValue = Field(default_factory=not_recorded)
    bus_vm: ErrorStatistics = Field(default_factory=ErrorStatistics)
    bus_va: ErrorStatistics = Field(default_factory=ErrorStatistics)
    line_p: ErrorStatistics = Field(default_factory=ErrorStatistics)
    line_q: ErrorStatistics = Field(default_factory=ErrorStatistics)
    transformer_p: ErrorStatistics = Field(default_factory=ErrorStatistics)
    transformer_q: ErrorStatistics = Field(default_factory=ErrorStatistics)
    evaluation_status: EvidenceStatus = EvidenceStatus.NOT_RECORDED

    @model_validator(mode="after")
    def validate_layer(self) -> "RetainedVoltageLayerComparison":
        """Reject nonfinite voltage policy values."""
        if not math.isfinite(self.cutoff_kv) or self.cutoff_kv <= 0.0:
            raise ValueError("cutoff_kv must be positive and finite")
        if not math.isfinite(self.voltage_kv) or self.voltage_kv <= 0.0:
            raise ValueError("voltage_kv must be positive and finite")
        return self


class RetainedNetworkFidelityReport(ReportModel):
    """Read-only per-object fidelity summaries derived from existing PF results."""

    source_pf_status: EvidenceValue = Field(default_factory=not_recorded)
    target_pf_status: EvidenceValue = Field(default_factory=not_recorded)
    mapping_digest: str | None = None
    source_pf_digest: str | None = None
    target_pf_digest: str | None = None
    groups: tuple[RetainedGroupComparison, ...] = ()
    interfaces: CrossVoltageInterfaceReport = Field(default_factory=CrossVoltageInterfaceReport)
    layers: tuple[RetainedVoltageLayerComparison, ...] = ()

    @model_validator(mode="after")
    def validate_digests_and_groups(self) -> "RetainedNetworkFidelityReport":
        """Validate digests and require unique group/cutoff/layer keys."""
        for digest in (self.mapping_digest, self.source_pf_digest, self.target_pf_digest):
            if digest is not None and _SHA256.fullmatch(digest) is None:
                raise ValueError("retained fidelity digest must be SHA-256")
        group_names = tuple(row.group for row in self.groups)
        if len(set(group_names)) != len(group_names):
            raise ValueError("duplicate retained fidelity group")
        layer_keys = tuple((row.cutoff_kv, row.voltage_kv) for row in self.layers)
        if len(set(layer_keys)) != len(layer_keys):
            raise ValueError("duplicate retained fidelity cutoff/layer key")
        return self


class FidelitySection(ReportModel):
    """PF/Fidelity evidence grouped without altering PR-022 admission."""

    pf_source_status: EvidenceValue = Field(default_factory=not_recorded)
    pf_candidate_status: EvidenceValue = Field(default_factory=not_recorded)
    fidelity_status: EvidenceValue = Field(default_factory=not_recorded)
    metric_digest: str | None = None
    threshold_digest: str | None = None
    solver_contract_id: str | None = None
    solver_contract_digest: str | None = None
    source_failure_code: str | None = None
    candidate_failure_code: str | None = None
    source_subnetworks: tuple["PFSubnetworkObservation", ...] = ()
    candidate_subnetworks: tuple["PFSubnetworkObservation", ...] = ()
    observations: tuple[FidelityObservation, ...] = ()
    additional_groups_are_diagnostic_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_digests(self) -> "FidelitySection":
        """Validate optional section-level evidence digests."""
        for digest in (self.metric_digest, self.threshold_digest):
            if digest is not None and _SHA256.fullmatch(digest) is None:
                raise ValueError("Fidelity section digest must be SHA-256")
        if self.solver_contract_digest is not None and _SHA256.fullmatch(
                self.solver_contract_digest) is None:
            raise ValueError("solver contract digest must be SHA-256")
        for failure_code in (self.source_failure_code, self.candidate_failure_code):
            if failure_code is not None and _PUBLIC_ID.fullmatch(failure_code) is None:
                raise ValueError("PF failure code must be a public identifier")
        return self


class PFSubnetworkObservation(ReportModel):
    """Solver iterations and terminal error for one anonymous subnetwork."""

    side: Literal["SOURCE", "CANDIDATE"]
    ordinal: int
    converged: bool
    iterations: int | None
    final_error: float | None

    @model_validator(mode="after")
    def validate_subnetwork(self) -> "PFSubnetworkObservation":
        """Validate ordinal, iteration, and finite nonnegative error."""
        if isinstance(self.ordinal, bool) or self.ordinal < 0:
            raise ValueError("subnetwork ordinal must be nonnegative")
        if self.iterations is not None and (
                isinstance(self.iterations, bool) or self.iterations < 0):
            raise ValueError("subnetwork iterations must be nonnegative")
        if self.final_error is not None and (
                not math.isfinite(self.final_error) or self.final_error < 0.0):
            raise ValueError("subnetwork final error must be finite and nonnegative")
        return self


class PerformanceSection(ReportModel):
    """Runtime, replay, and software context; timing is diagnostic only."""

    source_pf_solver_seconds: EvidenceValue = Field(default_factory=not_recorded)
    source_pf_end_to_end_seconds: EvidenceValue = Field(default_factory=not_recorded)
    candidate_pf_solver_seconds: EvidenceValue = Field(default_factory=not_recorded)
    candidate_pf_end_to_end_seconds: EvidenceValue = Field(default_factory=not_recorded)
    candidate_reconstruction_seconds: EvidenceValue = Field(default_factory=not_recorded)
    structural_ac_readiness_seconds: EvidenceValue = Field(default_factory=not_recorded)
    fidelity_evaluation_seconds: EvidenceValue = Field(default_factory=not_recorded)
    attribution_seconds: EvidenceValue = Field(default_factory=not_recorded)
    controller_total_seconds: EvidenceValue = Field(default_factory=not_recorded)
    evidence_persistence_seconds: EvidenceValue = Field(default_factory=not_recorded)
    fresh_replay_seconds: EvidenceValue = Field(default_factory=not_recorded)
    reported_total_seconds: EvidenceValue = Field(default_factory=not_recorded)
    replay_status: EvidenceValue = Field(default_factory=not_recorded)
    software_versions: dict[str, str] = Field(default_factory=dict)
    runtime_comparison_scope: Literal["WITHIN_RUN_DIAGNOSTIC_ONLY"] = (
        "WITHIN_RUN_DIAGNOSTIC_ONLY")


class ComprehensiveRunReport(ReportModel):
    """Complete A–F report sections for one immutable experiment identity."""

    provenance: ReportProvenance
    source_inventory: NetworkInventory
    opportunity: OpportunitySection = Field(default_factory=OpportunitySection)
    structural: StructuralComparison = Field(default_factory=StructuralComparison)
    voltage_layers: tuple[VoltageLayerSummary, ...] = ()
    voltage_policies: tuple[VoltagePolicyResolution, ...] = ()
    fidelity: FidelitySection = Field(default_factory=FidelitySection)
    retained_network_fidelity: RetainedNetworkFidelityReport = Field(
        default_factory=RetainedNetworkFidelityReport)
    performance: PerformanceSection = Field(default_factory=PerformanceSection)

    @model_validator(mode="after")
    def validate_run(self) -> "ComprehensiveRunReport":
        """Require unique, exact layer rows when the source inventory lists layers."""
        values = tuple((row.policy_id, row.voltage_kv) for row in self.voltage_layers)
        if len(set(values)) != len(values):
            raise ValueError("duplicate policy/voltage layer summaries")
        expected = set(self.source_inventory.voltage_layers_kv)
        if values and {voltage for _, voltage in values} != expected:
            raise ValueError("voltage layer summaries differ from source inventory")
        policy_ids = [policy.policy_id for policy in self.voltage_policies]
        if len(set(policy_ids)) != len(policy_ids):
            raise ValueError("duplicate voltage policy IDs")
        return self


class ComprehensiveReductionReport(ReportModel):
    """Canonical machine-readable report shared by every exporter."""

    schema_version: Literal["psdcalc-comprehensive-reduction-report/v1"] = (
        REPORT_SCHEMA_VERSION)
    report_id: str
    runs: tuple[ComprehensiveRunReport, ...]

    @model_validator(mode="after")
    def validate_report(self) -> "ComprehensiveReductionReport":
        """Require a public report ID and unique run identities."""
        if _PUBLIC_ID.fullmatch(self.report_id) is None:
            raise ValueError("report_id must be a public slug")
        run_ids = [run.provenance.run_id for run in self.runs]
        if not self.runs or len(set(run_ids)) != len(run_ids):
            raise ValueError("report requires nonempty runs with unique run IDs")
        return self

    def to_dict(self) -> dict[str, object]:
        """Return JSON-compatible immutable evidence as a plain dictionary."""
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        """Return deterministic canonical JSON with sorted object keys."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2, allow_nan=False) + "\n"

    @property
    def digest_sha256(self) -> str:
        """Return SHA-256 of canonical JSON, excluding runtime formatting."""
        canonical = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def write_json_schema(cls, path: str) -> None:
        """Write the model-generated schema resource deterministically."""
        from pathlib import Path

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(cls.model_json_schema(), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
