"""Read-only, privacy-safe aggregation of canonical voltage-policy reports."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re

import pyarrow as pa
import pyarrow.parquet as pq

from psdcalc.reduction.metrics.stage_inventory import CompressionStage
from psdcalc.reduction.reporting.comparison import CompressionComparisonReport


_VALID = "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"
_INVALID = "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID"
_PHYSICAL = "PHYSICAL_FIDELITY_NOT_YET_EVALUATED"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TOPOLOGY_FIELDS = (
    "bridge_count", "articulation_count", "two_core_bus_count",
    "cycle_edge_count", "simple_cycle_rank", "multigraph_cycle_rank",
)
_RUNTIME_PHASES = (
    "t_topology", "t_planning", "t_builder", "t_persist_reload_gate",
    "t_report", "t_total", "peak_rss",
)


@dataclass(frozen=True)
class VoltagePolicyScenarioSummary:
    """One scenario row derived only from its canonical single-run report."""

    policy_id: str
    requested_cutoff_kv: float | None
    retained_voltage_layers: tuple[float, ...]
    compressible_voltage_layers: tuple[float, ...]
    resolved_policy_fingerprint: str
    s1_bus_count: int
    s1_line_count: int
    l1_bus_count: int
    l1_line_count: int
    candidate_bus_count: int
    candidate_line_count: int
    accepted_bus_count: int | None
    accepted_line_count: int | None
    candidate_bus_compression_fraction: float
    candidate_line_compression_fraction: float
    accepted_bus_compression_fraction: float | None
    accepted_line_compression_fraction: float | None
    cutoff_retained_bus_count: int
    cutoff_retained_line_count: int
    simple_cycle_rank: int | None
    multigraph_cycle_rank: int | None
    total_runtime_seconds: float | None
    final_structural_status: str
    mapping_semantic_sha256: str
    failed_gates: tuple[str, ...]
    unique_protected_bus_count: int | None
    unique_protected_line_count: int | None
    delta_l1_bus_vs_base: int | None = None
    delta_l1_line_vs_base: int | None = None
    delta_candidate_bus_vs_base: int | None = None
    delta_candidate_line_vs_base: int | None = None
    delta_accepted_bus_vs_base: int | None = None
    delta_accepted_line_vs_base: int | None = None
    delta_bus_compression_fraction_vs_base: float | None = None
    delta_line_compression_fraction_vs_base: float | None = None
    delta_candidate_bus_compression_fraction_vs_base: float | None = None
    delta_candidate_line_compression_fraction_vs_base: float | None = None
    delta_accepted_bus_compression_fraction_vs_base: float | None = None
    delta_accepted_line_compression_fraction_vs_base: float | None = None
    delta_cutoff_retained_bus_count_vs_base: int | None = None
    delta_cutoff_retained_line_count_vs_base: int | None = None
    delta_runtime_vs_base: float | None = None
    delta_singletons_vs_base: int | None = None
    delta_protected_lines_vs_base: int | None = None


@dataclass(frozen=True)
class VoltageLayerMatrixRow:
    """One anonymized scenario and voltage-layer row."""

    policy_id: str
    requested_cutoff_kv: float | None
    resolved_policy_fingerprint: str
    voltage_kv: float
    source_bus_count: int | None
    projected_bus_count: int
    simplified_bus_count: int
    candidate_bus_count: int
    final_bus_count: int
    accepted_bus_count: int | None
    mandatory_singleton_count: int
    cutoff_retained_bus_count: int
    ring_endpoint_count: int
    transformer_endpoint_count: int
    shunt_endpoint_count: int
    control_endpoint_count: int
    parallel_singleton_endpoint_count: int
    cutoff_identity_preserved_line_count: int
    minimum_required_bus_count: int
    candidate_compression_fraction: float
    accepted_compression_fraction: float | None


@dataclass(frozen=True)
class TopologyMatrixRow:
    """Topology evidence for one scenario and structural stage."""

    policy_id: str
    stage: str
    bridge_count: int | None
    articulation_count: int | None
    two_core_bus_count: int | None
    cycle_edge_count: int | None
    simple_cycle_rank: int | None
    multigraph_cycle_rank: int | None


@dataclass(frozen=True)
class DeviceTransitionMatrixRow:
    """Device mapping counts for one scenario and component class."""

    policy_id: str
    component: str
    source_count: int
    target_count: int
    preserved_count: int
    aggregated_count: int
    absorbed_internal_count: int
    moved_count: int
    mapping_coverage_fraction: float


@dataclass(frozen=True)
class GateMatrixRow:
    """Named structural Gate result for one scenario."""

    policy_id: str
    gate_name: str
    passed: bool


@dataclass(frozen=True)
class RuntimeMatrixRow:
    """Observed runtime field or an explicit unavailable marker."""

    policy_id: str
    phase: str
    seconds: float | None
    availability: str


@dataclass(frozen=True)
class CrossScenarioCompressionReport:
    """One canonical cross-scenario object backing JSON, Markdown, and Parquet."""

    model_id: str
    source_sha256: str
    scenarios: tuple[VoltagePolicyScenarioSummary, ...]
    voltage_layer_matrix: tuple[VoltageLayerMatrixRow, ...]
    topology_matrix: tuple[TopologyMatrixRow, ...]
    device_transition_matrix: tuple[DeviceTransitionMatrixRow, ...]
    gate_matrix: tuple[GateMatrixRow, ...]
    runtime_matrix: tuple[RuntimeMatrixRow, ...]
    physical_fidelity_status: str = _PHYSICAL

    def __post_init__(self) -> None:
        """Enforce the report's structural-only evidence boundary."""
        if self.physical_fidelity_status != _PHYSICAL:
            raise ValueError("Cross-scenario report cannot assert physical fidelity")

    @classmethod
    def create(
        cls,
        reports: Sequence[CompressionComparisonReport],
    ) -> "CrossScenarioCompressionReport":
        """Aggregate single-run evidence without accepting networks or raw mappings."""
        if not reports:
            raise ValueError("At least one CompressionComparisonReport is required")
        models = {report.model_id for report in reports}
        sources = {report.source_sha256 for report in reports}
        if len(models) != 1 or len(sources) != 1:
            raise ValueError("Cross-scenario reports must share model and source SHA")
        source_sha = next(iter(sources))
        if source_sha is None or not _SHA256.fullmatch(source_sha):
            raise ValueError("Cross-scenario reports require source SHA-256 evidence")
        if any(report.physical_fidelity_status != _PHYSICAL for report in reports):
            raise ValueError("Unsupported physical-fidelity status")
        if any(report.final_structural_status not in {_VALID, _INVALID}
               for report in reports):
            raise ValueError("Unsupported structural status")
        if len({report.voltage_policy.policy_id for report in reports}) != len(reports):
            raise ValueError("Scenario policy_id values must be unique")
        base_reports = [report for report in reports
                        if report.voltage_policy.policy_id == "BASE"]
        if len(base_reports) != 1:
            raise ValueError("Exactly one BASE scenario is required")
        for report in reports:
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", report.model_id):
                raise ValueError("Cross-scenario model_id must be a public identifier")
            if not _SHA256.fullmatch(
                    report.voltage_policy.resolved_policy_fingerprint):
                raise ValueError("Invalid resolved policy fingerprint")
            if not report.voltage_policy.round_trip_validated:
                raise ValueError("Scenario policy lacks final round-trip evidence")
            digest = report.mappings.get("semantic_sha256")
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                raise ValueError("Missing mapping semantic digest")
            if report.final_structural_status == _VALID:
                if (report.accepted_final_bus_count is None
                        or report.accepted_final_line_count is None):
                    raise ValueError("VALID scenario is missing accepted counts")
            elif (report.accepted_final_bus_count is not None
                  or report.accepted_final_line_count is not None
                  or report.accepted_bus_compression_fraction is not None
                  or report.accepted_line_compression_fraction is not None):
                raise ValueError("INVALID scenario cannot contain accepted results")

        fingerprint_groups: dict[str, list[CompressionComparisonReport]] = defaultdict(list)
        for report in reports:
            fingerprint_groups[
                report.voltage_policy.resolved_policy_fingerprint].append(report)
        for grouped in fingerprint_groups.values():
            if len(grouped) < 2:
                continue
            semantics = {
                (
                    report.final_structural_status,
                    report.candidate_bus_count,
                    report.candidate_line_count,
                    report.accepted_final_bus_count,
                    report.accepted_final_line_count,
                    report.mappings["semantic_sha256"],
                )
                for report in grouped
            }
            if len(semantics) != 1:
                raise ValueError("RESOLVED_POLICY_NONDETERMINISM")

        ordered = tuple(sorted(
            reports,
            key=lambda report: (
                report.voltage_policy.policy_id != "BASE",
                report.voltage_policy.requested_cutoff_kv is not None,
                report.voltage_policy.requested_cutoff_kv or 0.0,
                report.voltage_policy.policy_id,
            ),
        ))
        base = next(report for report in ordered
                    if report.voltage_policy.policy_id == "BASE")
        summaries = tuple(_scenario_summary(report, base) for report in ordered)
        layer_rows: list[VoltageLayerMatrixRow] = []
        topology_rows: list[TopologyMatrixRow] = []
        device_rows: list[DeviceTransitionMatrixRow] = []
        gate_rows: list[GateMatrixRow] = []
        runtime_rows: list[RuntimeMatrixRow] = []
        stage_names = tuple(stage.value for stage in list(CompressionStage)[1:])
        for report in ordered:
            policy = report.voltage_policy
            valid = report.final_structural_status == _VALID
            for layer in report.voltage_layers:
                layer_rows.append(VoltageLayerMatrixRow(
                    policy.policy_id, policy.requested_cutoff_kv,
                    policy.resolved_policy_fingerprint, layer.voltage_kv,
                    layer.source_bus_count, layer.projected_bus_count,
                    layer.simplified_bus_count, layer.candidate_bus_count,
                    layer.final_bus_count,
                    layer.final_bus_count if valid else None,
                    layer.mandatory_singleton_count,
                    layer.cutoff_retained_bus_count,
                    layer.ring_endpoint_count, layer.transformer_endpoint_count,
                    layer.shunt_endpoint_count, layer.control_endpoint_count,
                    layer.parallel_singleton_endpoint_count,
                    layer.cutoff_identity_preserved_line_count,
                    layer.minimum_required_bus_count,
                    layer.candidate_compression_fraction,
                    layer.accepted_compression_fraction if valid else None,
                ))
            for stage in stage_names:
                topology_rows.append(TopologyMatrixRow(
                    policy.policy_id, stage,
                    *(_optional_int(report.topology.get(stage + "_" + field))
                      for field in _TOPOLOGY_FIELDS),
                ))
            for transition in report.device_transitions:
                device_rows.append(DeviceTransitionMatrixRow(
                    policy.policy_id, transition.component,
                    transition.source_count, transition.target_count,
                    transition.preserved_count, transition.aggregated_count,
                    transition.absorbed_internal_count, transition.moved_count,
                    transition.mapping_coverage_fraction,
                ))
            gate_rows.extend(GateMatrixRow(policy.policy_id, gate, passed)
                             for gate, passed in sorted(report.named_gates.items()))
            runtime_values = {phase: report.runtime.get(phase)
                              for phase in _RUNTIME_PHASES}
            runtime_values.update(report.runtime)
            for phase, raw_value in sorted(runtime_values.items()):
                if isinstance(raw_value, bool):
                    runtime_rows.append(RuntimeMatrixRow(
                        policy.policy_id, phase, None, "NOT_AVAILABLE"))
                elif isinstance(raw_value, (int, float)) and math.isfinite(float(raw_value)):
                    runtime_rows.append(RuntimeMatrixRow(
                        policy.policy_id, phase, float(raw_value), "AVAILABLE"))
                else:
                    runtime_rows.append(RuntimeMatrixRow(
                        policy.policy_id, phase, None, "NOT_AVAILABLE"))
        return cls(
            model_id=next(iter(models)), source_sha256=source_sha,
            scenarios=summaries, voltage_layer_matrix=tuple(layer_rows),
            topology_matrix=tuple(topology_rows),
            device_transition_matrix=tuple(device_rows),
            gate_matrix=tuple(gate_rows), runtime_matrix=tuple(runtime_rows),
            physical_fidelity_status=_PHYSICAL,
        )

    def to_dict(self) -> dict[str, object]:
        """Return deterministic public aggregate evidence with no source identities."""
        return asdict(self)

    def to_json(self) -> str:
        """Render deterministic JSON from this canonical cross-scenario object."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"

    def to_markdown(self) -> str:
        """Render an overview and detail matrices from the same report object."""
        lines = [
            f"# Voltage compression matrix: {self.model_id}", "",
            f"Source SHA-256: `{self.source_sha256}`", "",
            f"Physical fidelity: `{self.physical_fidelity_status}`", "",
            "## Scenario summary", "",
            "| Policy | Cutoff | Retained layers | Accepted Bus | Bus compression | Accepted Line | Line compression | Protected Bus | Protected Line | Runtime | Status |",
            "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for row in self.scenarios:
            accepted_bus = _display(row.accepted_bus_count)
            accepted_bus_fraction = _fraction(row.accepted_bus_compression_fraction)
            accepted_line = _display(row.accepted_line_count)
            accepted_line_fraction = _fraction(row.accepted_line_compression_fraction)
            runtime = _display(row.total_runtime_seconds)
            layers = ", ".join(_voltage(value) for value in row.retained_voltage_layers) or "—"
            lines.append(
                f"| {row.policy_id} | {_display(row.requested_cutoff_kv)} | {layers} | "
                f"{accepted_bus} | {accepted_bus_fraction} | {accepted_line} | "
                f"{accepted_line_fraction} | {_display(row.unique_protected_bus_count)} | "
                f"{_display(row.unique_protected_line_count)} | {runtime} | "
                f"{_status_label(row.final_structural_status)} |"
            )
        lines += [
            "", "## Candidate counts", "",
            "| Policy | Candidate Bus | Candidate Bus compression | Candidate Line | Candidate Line compression | Failed Gates |",
            "|---|---:|---:|---:|---:|---|",
        ]
        for row in self.scenarios:
            failed = ", ".join(row.failed_gates) or "—"
            lines.append(
                f"| {row.policy_id} | {row.candidate_bus_count} | "
                f"{_fraction(row.candidate_bus_compression_fraction)} | "
                f"{row.candidate_line_count} | "
                f"{_fraction(row.candidate_line_compression_fraction)} | {failed} |"
            )
        _append_matrix(lines, "Voltage layers", self.voltage_layer_matrix)
        _append_matrix(lines, "Topology", self.topology_matrix)
        _append_matrix(lines, "Device transitions", self.device_transition_matrix)
        _append_matrix(lines, "Named Gates", self.gate_matrix)
        _append_matrix(lines, "Runtime", self.runtime_matrix)
        lines += ["", "STRUCTURAL COMPRESSION ONLY; PHYSICAL FIDELITY NOT EVALUATED", ""]
        return "\n".join(lines)

    def save_parquet(self, directory: str | Path) -> dict[str, Path]:
        """Write each matrix as Parquet from this report object's immutable rows."""
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        delta_fields = (
            "policy_id", "delta_l1_bus_vs_base", "delta_l1_line_vs_base",
            "delta_candidate_bus_vs_base", "delta_candidate_line_vs_base",
            "delta_accepted_bus_vs_base", "delta_accepted_line_vs_base",
            "delta_bus_compression_fraction_vs_base",
            "delta_line_compression_fraction_vs_base",
            "delta_candidate_bus_compression_fraction_vs_base",
            "delta_candidate_line_compression_fraction_vs_base",
            "delta_accepted_bus_compression_fraction_vs_base",
            "delta_accepted_line_compression_fraction_vs_base",
            "delta_cutoff_retained_bus_count_vs_base",
            "delta_cutoff_retained_line_count_vs_base", "delta_runtime_vs_base",
            "delta_singletons_vs_base", "delta_protected_lines_vs_base",
        )
        tables: dict[str, tuple[object, ...]] = {
            "scenarios": self.scenarios,
            "voltage_layers": self.voltage_layer_matrix,
            "topology": self.topology_matrix,
            "device_transitions": self.device_transition_matrix,
            "gates": self.gate_matrix,
            "runtime": self.runtime_matrix,
        }
        paths: dict[str, Path] = {}
        for name, rows in tables.items():
            records = [asdict(row) for row in rows]
            path = root / f"{name}.parquet"
            table = (pa.Table.from_pylist(records) if records else
                     pa.table({"_empty": pa.array([], type=pa.string())}))
            pq.write_table(table, path)
            paths[name] = path
        delta_path = root / "deltas.parquet"
        pq.write_table(pa.Table.from_pylist([
            {field: getattr(row, field) for field in delta_fields}
            for row in self.scenarios
        ]), delta_path)
        paths["deltas"] = delta_path
        return paths


def _scenario_summary(
    report: CompressionComparisonReport,
    base: CompressionComparisonReport,
) -> VoltagePolicyScenarioSummary:
    """Project one report and its relative values into a summary row."""
    policy = report.voltage_policy
    stages = report.stages
    s1 = stages[CompressionStage.PYPSA_FULL_PROJECTION.value]
    l1 = stages[CompressionStage.TOPOLOGY_SIMPLIFIED.value]
    topology = report.topology
    valid = report.final_structural_status == _VALID
    base_valid = base.final_structural_status == _VALID
    runtime = _runtime_total(report.runtime)
    base_runtime = _runtime_total(base.runtime)
    unique_buses = _optional_int(report.protection_costs.get(
        "unique_protected_bus_count"))
    unique_lines = _optional_int(report.protection_costs.get(
        "unique_protected_line_count"))
    base_buses = _optional_int(base.protection_costs.get(
        "unique_protected_bus_count"))
    base_lines = _optional_int(base.protection_costs.get(
        "unique_protected_line_count"))
    return VoltagePolicyScenarioSummary(
        policy_id=policy.policy_id,
        requested_cutoff_kv=policy.requested_cutoff_kv,
        retained_voltage_layers=policy.retained_voltage_layers,
        compressible_voltage_layers=policy.compressible_voltage_layers,
        resolved_policy_fingerprint=policy.resolved_policy_fingerprint,
        s1_bus_count=_required_count(s1.bus_count, "S1 Bus"),
        s1_line_count=_required_count(s1.line_count, "S1 Line"),
        l1_bus_count=_required_count(l1.bus_count, "L1 Bus"),
        l1_line_count=_required_count(l1.line_count, "L1 Line"),
        candidate_bus_count=_required_count(report.candidate_bus_count, "candidate Bus"),
        candidate_line_count=_required_count(report.candidate_line_count, "candidate Line"),
        accepted_bus_count=report.accepted_final_bus_count if valid else None,
        accepted_line_count=report.accepted_final_line_count if valid else None,
        candidate_bus_compression_fraction=_required_float(
            report.candidate_bus_compression_fraction, "candidate Bus fraction"),
        candidate_line_compression_fraction=_required_float(
            report.candidate_line_compression_fraction, "candidate Line fraction"),
        accepted_bus_compression_fraction=(
            report.accepted_bus_compression_fraction if valid else None),
        accepted_line_compression_fraction=(
            report.accepted_line_compression_fraction if valid else None),
        cutoff_retained_bus_count=policy.retained_bus_count,
        cutoff_retained_line_count=policy.retained_line_count,
        simple_cycle_rank=_optional_int(topology.get(
            "S4_FINAL_RELOADED_simple_cycle_rank")),
        multigraph_cycle_rank=_optional_int(topology.get(
            "S4_FINAL_RELOADED_multigraph_cycle_rank")),
        total_runtime_seconds=runtime,
        final_structural_status=report.final_structural_status,
        mapping_semantic_sha256=str(report.mappings["semantic_sha256"]),
        failed_gates=tuple(sorted(
            gate for gate, passed in report.named_gates.items() if not passed)),
        unique_protected_bus_count=unique_buses,
        unique_protected_line_count=unique_lines,
        delta_l1_bus_vs_base=_delta(l1.bus_count, base.stages[
            CompressionStage.TOPOLOGY_SIMPLIFIED.value].bus_count),
        delta_l1_line_vs_base=_delta(l1.line_count, base.stages[
            CompressionStage.TOPOLOGY_SIMPLIFIED.value].line_count),
        delta_candidate_bus_vs_base=_delta(report.candidate_bus_count,
                                           base.candidate_bus_count),
        delta_candidate_line_vs_base=_delta(report.candidate_line_count,
                                            base.candidate_line_count),
        delta_accepted_bus_vs_base=(
            _delta(report.accepted_final_bus_count, base.accepted_final_bus_count)
            if valid and base_valid else None),
        delta_accepted_line_vs_base=(
            _delta(report.accepted_final_line_count, base.accepted_final_line_count)
            if valid and base_valid else None),
        delta_bus_compression_fraction_vs_base=(
            _delta_float(report.accepted_bus_compression_fraction,
                         base.accepted_bus_compression_fraction)
            if valid and base_valid else None),
        delta_line_compression_fraction_vs_base=(
            _delta_float(report.accepted_line_compression_fraction,
                         base.accepted_line_compression_fraction)
            if valid and base_valid else None),
        delta_candidate_bus_compression_fraction_vs_base=_delta_float(
            report.candidate_bus_compression_fraction,
            base.candidate_bus_compression_fraction),
        delta_candidate_line_compression_fraction_vs_base=_delta_float(
            report.candidate_line_compression_fraction,
            base.candidate_line_compression_fraction),
        delta_accepted_bus_compression_fraction_vs_base=(
            _delta_float(report.accepted_bus_compression_fraction,
                         base.accepted_bus_compression_fraction)
            if valid and base_valid else None),
        delta_accepted_line_compression_fraction_vs_base=(
            _delta_float(report.accepted_line_compression_fraction,
                         base.accepted_line_compression_fraction)
            if valid and base_valid else None),
        delta_cutoff_retained_bus_count_vs_base=(
            policy.retained_bus_count - base.voltage_policy.retained_bus_count),
        delta_cutoff_retained_line_count_vs_base=(
            policy.retained_line_count - base.voltage_policy.retained_line_count),
        delta_runtime_vs_base=(runtime - base_runtime
                               if runtime is not None and base_runtime is not None else None),
        delta_singletons_vs_base=(unique_buses - base_buses
                                  if unique_buses is not None and base_buses is not None
                                  else None),
        delta_protected_lines_vs_base=(unique_lines - base_lines
                                       if unique_lines is not None and base_lines is not None
                                       else None),
    )


def _required_count(value: int | None, label: str) -> int:
    if value is None:
        raise ValueError(f"Missing {label} count")
    return value


def _required_float(value: float | None, label: str) -> float:
    if value is None:
        raise ValueError(f"Missing {label}")
    return float(value)


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return int(value)


def _delta(value: int | None, baseline: int | None) -> int | None:
    return value - baseline if value is not None and baseline is not None else None


def _delta_float(value: float | None, baseline: float | None) -> float | None:
    return value - baseline if value is not None and baseline is not None else None


def _runtime_total(runtime: dict[str, object] | object) -> float | None:
    if not isinstance(runtime, dict):
        return None
    value = runtime.get("t_total", runtime.get("total_runtime_seconds"))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def _display(value: object) -> str:
    return "—" if value is None else str(value)


def _fraction(value: float | None) -> str:
    return "—" if value is None else f"{value:.6f}"


def _voltage(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def _status_label(status: str) -> str:
    return "STRUCTURALLY_INVALID" if status == _INVALID else "STRUCTURALLY_VALID"


def _append_matrix(lines: list[str], title: str, rows: tuple[object, ...]) -> None:
    """Append a generic deterministic table for dataclass matrix rows."""
    lines += ["", f"## {title}", ""]
    if not rows:
        lines += ["No rows available."]
        return
    fields = tuple(asdict(rows[0]))
    lines.append("| " + " | ".join(fields) + " |")
    lines.append("|" + "|".join("---" for _ in fields) + "|")
    for row in rows:
        values = asdict(row)
        lines.append("| " + " | ".join(_display(values[key]) for key in fields) + " |")
