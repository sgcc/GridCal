"""Render one validated comparison object to JSON, Markdown and Parquet."""

from dataclasses import asdict
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from psdcalc.reduction.metrics.stage_inventory import CompressionStage
from psdcalc.reduction.reporting.comparison import CompressionComparisonReport
from psdcalc.reduction.reporting.redaction import assert_public_payload
from psdcalc.reduction.core.artifact_store import ArtifactStore


def _table(rows: list[dict[str, object]], path: Path) -> None:
    """Write a typed empty table when the corresponding report section is empty."""
    table = (pa.Table.from_pylist(rows) if rows else
             pa.table({"_empty": pa.array([], type=pa.string())}))
    pq.write_table(table, path)


def render_comparison_markdown(report: CompressionComparisonReport) -> str:
    """Render anonymous A–H summary tables from the canonical report."""
    lines = [f"# Compression comparison: {report.model_id}", "",
             f"Run: `{report.run_id}`", "",
             f"Final structural status: `{report.final_structural_status}`", "",
             f"Physical fidelity: `{report.physical_fidelity_status}`", "",
             "## A. Five-stage scale", "",
             "| Stage | Bus | Line | Transformer | Generator | Load | Shunt | Components | Simple rank | Multi rank |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for stage in CompressionStage:
        row = report.stages[stage.value]
        values = (row.bus_count, row.line_count, row.transformer_count,
                  row.generator_count, row.load_count, row.shunt_count,
                  row.active_connected_component_count, row.simple_cycle_rank,
                  row.multigraph_cycle_rank)
        lines.append("| " + stage.value + " | " + " | ".join(
            "—" if value is None else str(value) for value in values) + " |")
    source = report.stages[CompressionStage.PSD_BPA_SOURCE.value]
    lines += ["", "## B. Source transformer materialization", "",
              "| 2W source | 3W groups | 3W T legs | S1 Transformer |",
              "|---:|---:|---:|---:|",
              f"| {source.source_transformer2w_count} | {source.source_transformer3w_group_count} | "
              f"{source.source_transformer3w_leg_count} | "
              f"{report.stages[CompressionStage.PYPSA_FULL_PROJECTION.value].transformer_count} |",
              "", "## C. Voltage layers", "",
              "| kV | S0 | S1 | S2 | S3 | S4 | Candidate fraction | Accepted fraction |",
              "|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in report.voltage_layers:
        lines.append(f"| {row.voltage_kv} | {row.source_bus_count} | "
                     f"{row.projected_bus_count} | {row.simplified_bus_count} | "
                     f"{row.candidate_bus_count} | {row.final_bus_count} | "
                     f"{row.candidate_compression_fraction:.6f} | "
                     f"{row.accepted_compression_fraction if row.accepted_compression_fraction is not None else '—'} |")
    policy = report.voltage_policy
    lines += ["", "## C1. Voltage policy", "",
              "| Policy | Enabled | Mode | Requested cutoff kV | Source layers | Retained layers | Compressible layers | Retained Bus | Retained Line | Fingerprint | Round-trip validated |",
              "|---|---|---|---:|---|---|---|---:|---:|---|---|"]
    def layers(values: tuple[float, ...]) -> str:
        return ", ".join(str(value) for value in values) or "—"
    lines.append(
        f"| {policy.policy_id} | {policy.enabled} | {policy.mode} | "
        f"{policy.requested_cutoff_kv if policy.requested_cutoff_kv is not None else '—'} | "
        f"{layers(policy.source_voltage_layers)} | {layers(policy.retained_voltage_layers)} | "
        f"{layers(policy.compressible_voltage_layers)} | {policy.retained_bus_count} | "
        f"{policy.retained_line_count} | {policy.resolved_policy_fingerprint} | "
        f"{policy.round_trip_validated} |"
    )
    lines += ["", "## C2. Protection cost", "",
              "| Unique protected Bus | Unique protected Line | Cutoff Bus membership | Cutoff Line membership | Parallel Line membership | L+ base Line membership |",
              "|---:|---:|---:|---:|---:|---:|"]
    lines.append("| " + " | ".join(str(report.protection_costs.get(key, 0)) for key in (
        "unique_protected_bus_count", "unique_protected_line_count",
        "cutoff_mandatory_singleton_membership_count",
        "cutoff_identity_preserved_line_membership_count",
        "parallel_protected_line_membership_count",
        "l_plus_base_line_membership_count")) + " |")
    if report.named_gates:
        lines += ["", "## H1. Named structural Gates", "",
                  "| Gate | Passed |", "|---|---|"]
        lines.extend(f"| {name} | {passed} |" for name, passed in sorted(report.named_gates.items()))
    lines += ["", "## D. Device mapping", "",
              "| Component | Source | Target | Preserved | Aggregated | Absorbed | Moved |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for row in report.device_transitions:
        lines.append(f"| {row.component} | {row.source_count} | {row.target_count} | "
                     f"{row.preserved_count} | {row.aggregated_count} | "
                     f"{row.absorbed_internal_count} | {row.moved_count} |")
    for title, values in (("E. Topology", report.topology),
                          ("E. Parallel corridors", report.parallel_corridors)):
        lines += ["", "## " + title, "", "| Metric | Value |", "|---|---:|"]
        lines.extend(f"| {key} | {value} |" for key, value in sorted(values.items()))
    lines += ["", "## F. Energy scale", "",
              "| Stage | Scope | kV | Load | Load P | Load Q | Generator | P nom | Generator P | Shunt | g | b |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in report.energy_aggregates:
        values = (row.stage.value, row.scope, row.voltage_kv,
                  row.load_count, row.load_p_set, row.load_q_set,
                  row.generator_count, row.generator_p_nom,
                  row.generator_p_set, row.shunt_count, row.shunt_g,
                  row.shunt_b)
        lines.append("| " + " | ".join(
            "—" if value is None else str(value) for value in values) + " |")
    lines += ["", "## G. Runtime", "", "| Metric | Value |", "|---|---:|"]
    lines.extend(f"| {key} | {value} |" for key, value in sorted(report.runtime.items()))
    lines += ["", "## H. Final structural Gate", "",
              "| Failed check |", "|---|"]
    lines.extend(f"| {check} |" for check in report.failed_checks)
    lines += ["", f"Candidate Bus compression: {report.candidate_bus_compression_fraction}",
              f"Accepted Bus compression: {report.accepted_bus_compression_fraction if report.accepted_bus_compression_fraction is not None else '—'}",
              "", "AGGREGATION/INVENTORY STATISTIC; NOT AC POWER-FLOW FIDELITY", ""]
    return "\n".join(lines)


def write_comparison_report(
        report: CompressionComparisonReport, directory: Path,
        *, forbidden_tokens: tuple[str, ...] = ()) -> dict[str, Path]:
    """Persist every public representation from one validated report object."""
    payload = report.to_dict()
    assert_public_payload(payload, forbidden_tokens=forbidden_tokens)
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    def write_text(name: str, content: str) -> None:
        paths[name] = directory / name
        paths[name].write_text(content, encoding="utf-8")

    write_text("compression_comparison.json", report.to_json())
    write_text("compression_comparison.md", render_comparison_markdown(report))
    write_text("runtime_cost.json", json.dumps(dict(report.runtime), sort_keys=True, indent=2) + "\n")
    write_text("gate_summary.json", json.dumps({
        "status": report.final_structural_status,
        "failed_checks": list(report.failed_checks),
    }, sort_keys=True, indent=2) + "\n")
    tables = {
        "stage_inventory.parquet": [asdict(item) for item in report.stages.values()],
        "voltage_layer_comparison.parquet": [asdict(item) for item in report.voltage_layers],
        "device_transition.parquet": [asdict(item) for item in report.device_transitions],
        "energy_aggregate.parquet": [asdict(item) for item in report.energy_aggregates],
        "topology_comparison.parquet": [dict(report.topology)] if report.topology else [],
        "parallel_corridor_comparison.parquet": [dict(report.parallel_corridors)] if report.parallel_corridors else [],
        "mapping_comparison.parquet": [dict(report.mappings)] if report.mappings else [],
    }
    for name, rows in tables.items():
        path = directory / name
        _table(rows, path)
        paths[name] = path
    return paths


def persist_pipeline_comparison(
        report: CompressionComparisonReport, store: ArtifactStore,
        *, forbidden_tokens: tuple[str, ...] = ()) -> dict[str, Path]:
    """Write all public formats under one run and register every artifact."""
    if store.manifest.run_id != report.run_id:
        raise ValueError("Comparison run ID differs from artifact store")
    paths = write_comparison_report(
        report, store.path("reports/compression-comparison"),
        forbidden_tokens=forbidden_tokens)
    for name, path in paths.items():
        store.record_artifact("comparison_" + name.replace(".", "_"),
                              str(path.relative_to(store.run_dir)))
    return paths
