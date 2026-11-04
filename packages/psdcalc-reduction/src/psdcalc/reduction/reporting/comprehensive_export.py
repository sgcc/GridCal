"""Deterministic public exporters for comprehensive reduction reports."""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Mapping

from psdcalc.reduction.reporting.comprehensive import (
    ComprehensiveReductionReport,
    EvidenceValue,
)


class ComprehensiveExportError(ValueError):
    """Raised when report content contains disallowed private material."""


_PRIVATE_PATTERNS = (
    re.compile(r"/(?:Users|private|home|var)/[^\s\"']+"),
    re.compile(r"\bBPA\d{8}\b", re.IGNORECASE),
    re.compile(r"\b(?:StableId|StableID)\s*\([^)]*\)"),
)


def validate_public_export_text(text: str) -> None:
    """Reject absolute local paths and known raw case/device identity forms."""
    for pattern in _PRIVATE_PATTERNS:
        if pattern.search(text):
            raise ComprehensiveExportError("public report contains restricted identity material")


def _cell(value: object) -> str:
    """Format one scalar consistently across all tabular exporters."""
    if isinstance(value, EvidenceValue):
        return _cell(value.value) if value.state.value == "AVAILABLE" else value.state.value
    if value is None:
        return "NOT_RECORDED"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, ".17g")
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def _csv_text(headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> str:
    """Return deterministic CSV with stable column order and LF endings."""
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_cell(item) for item in row])
    return stream.getvalue()


def _value_rows(report: ComprehensiveReductionReport) -> list[tuple[object, ...]]:
    """Flatten scalar inventory, opportunity, structural and fidelity values."""
    rows: list[tuple[object, ...]] = []
    for run in report.runs:
        run_id = run.provenance.run_id
        sections: tuple[tuple[str, object], ...] = (
            ("source_inventory", run.source_inventory),
            ("opportunity", run.opportunity),
            ("structural", run.structural),
            ("performance", run.performance),
        )
        for section, model in sections:
            for name in type(model).model_fields:
                datum = getattr(model, name)
                if isinstance(datum, EvidenceValue):
                    rows.append((run_id, section, name, datum.value, datum.unit,
                                 datum.state.value, datum.outcome.value,
                                 datum.sample_count, datum.mapping_coverage,
                                 datum.threshold))
                elif isinstance(datum, (str, int, float, bool)) or datum is None:
                    rows.append((run_id, section, name, datum, None,
                                 "AVAILABLE" if datum is not None else "NOT_RECORDED",
                                 "NOT_EVALUATED", None, None, None))
        for name in type(run.structural.target_inventory).model_fields:
            datum = getattr(run.structural.target_inventory, name)
            if isinstance(datum, EvidenceValue):
                rows.append((run_id, "target_inventory", name, datum.value, datum.unit,
                             datum.state.value, datum.outcome.value, datum.sample_count,
                             datum.mapping_coverage, datum.threshold))
        for summary in run.structural.component_mappings:
            for name in ("source_count", "target_count", "preserved_identity_count",
                         "aggregated_count", "absorbed_internal_count", "mapping_coverage"):
                datum = getattr(summary, name)
                rows.append((run_id, "mapping_" + summary.component, name, datum.value,
                             datum.unit, datum.state.value, datum.outcome.value,
                             datum.sample_count, datum.mapping_coverage, datum.threshold))
        for observation in run.fidelity.observations:
            rows.append((run_id, "fidelity", observation.metric_name,
                         observation.observed.value, observation.observed.unit,
                         observation.observed.state.value, observation.outcome.value,
                         observation.sample_count,
                         observation.mapping_coverage,
                         observation.threshold.value))
        for side, subnetworks in (("source", run.fidelity.source_subnetworks),
                                  ("candidate", run.fidelity.candidate_subnetworks)):
            for subnetwork in subnetworks:
                rows.append((run_id, f"{side}_subnetwork_{subnetwork.ordinal}",
                             "converged", subnetwork.converged, None,
                             "AVAILABLE", "NOT_EVALUATED", None, None, None))
                for name in ("iterations", "final_error"):
                    value = getattr(subnetwork, name)
                    rows.append((run_id, f"{side}_subnetwork_{subnetwork.ordinal}", name,
                                 value, "iterations" if name == "iterations" else None,
                                 "AVAILABLE" if value is not None else "NOT_RECORDED",
                                 "NOT_EVALUATED", None, None, None))
        for name in ("pf_source_status", "pf_candidate_status", "fidelity_status"):
            datum = getattr(run.fidelity, name)
            rows.append((run_id, "fidelity", name, datum.value, None,
                         datum.state.value, datum.outcome.value, None, None, None))
        retained = run.retained_network_fidelity
        for group in retained.groups:
            for count_name in ("source_bus_count", "retained_bus_count", "compared_bus_count",
                               "missing_bus_count", "unmatched_bus_count",
                               "source_branch_count", "retained_branch_count",
                               "compared_branch_count", "missing_branch_count",
                               "unmatched_branch_count"):
                count_value = getattr(group, count_name)
                rows.append((run_id, f"retained_network_fidelity.{group.group.value}",
                             count_name, count_value.value, count_value.unit,
                             count_value.state.value, count_value.outcome.value,
                             count_value.sample_count,
                             group.mapping_coverage.value, count_value.threshold))
            for metric in ("bus_vm", "bus_va", "line_p", "line_q", "transformer_p",
                           "transformer_q", "injection_p", "injection_q"):
                stats = getattr(group, metric)
                for statistic in ("maximum", "rmse", "p95"):
                    datum = getattr(stats, statistic)
                    rows.append((run_id, f"retained_network_fidelity.{group.group.value}",
                                 f"{metric}.{statistic}", datum.value, datum.unit,
                                 datum.state.value, datum.outcome.value,
                                 stats.sample_count.value, group.mapping_coverage.value, None))
        for cutoff, voltage, scope, model in (
            (None, None, "interfaces", retained.interfaces),
            *((layer.cutoff_kv, layer.voltage_kv, "voltage_layer", layer)
              for layer in retained.layers),
        ):
            metrics = (("bus_vm", "bus_va", "line_p", "line_q", "transformer_p",
                        "transformer_q") if scope == "voltage_layer" else
                       ("source_p_mw", "target_p_mw", "p_error_mw", "source_q_mvar",
                        "target_q_mvar", "q_error_mvar", "region_net_injection_p_error_mw",
                        "region_net_injection_q_error_mvar"))
            for metric in metrics:
                stats = getattr(model, metric)
                statistics = (("minimum", "maximum", "mean", "max_absolute")
                              if hasattr(stats, "mean") else ("maximum", "rmse", "p95"))
                for statistic in statistics:
                    datum = getattr(stats, statistic)
                    suffix = f".{cutoff}kV.{voltage}kV" if cutoff is not None else ""
                    rows.append((run_id, f"retained_network_fidelity.{scope}",
                                 f"{metric}{suffix}.{statistic}", datum.value, datum.unit,
                                 datum.state.value, datum.outcome.value,
                                 stats.sample_count.value, None, None))
    return rows


def render_retained_fidelity_csv(report: ComprehensiveReductionReport) -> str:
    """Render identity-preserved PF diagnostics and interface/layer views."""
    rows: list[tuple[object, ...]] = []

    def stat_cells(stats: object) -> tuple[object, ...]:
        """Render signed value summaries and residual summaries without conflation."""
        if hasattr(stats, "mean"):
            return (stats.minimum, stats.maximum, stats.mean, stats.max_absolute,
                    None, None, stats.sample_count)
        return (None, stats.maximum, None, stats.maximum, stats.rmse, stats.p95,
                stats.sample_count)

    for run in report.runs:
        retained = run.retained_network_fidelity
        for group in retained.groups:
            for metric in ("bus_vm", "bus_va", "line_p", "line_q", "transformer_p",
                           "transformer_q", "injection_p", "injection_q"):
                stats = getattr(group, metric)
                rows.append((run.provenance.run_id, "group", group.group.value, None, None,
                             metric, *stat_cells(stats),
                             group.status.value, group.mapping_coverage))
        for metric in ("source_p_mw", "target_p_mw", "p_error_mw", "source_q_mvar",
                       "target_q_mvar", "q_error_mvar", "region_net_injection_p_error_mw",
                       "region_net_injection_q_error_mvar"):
            stats = getattr(retained.interfaces, metric)
            rows.append((run.provenance.run_id, "interface", "CROSS_VOLTAGE_INTERFACES",
                         None, None, metric, *stat_cells(stats),
                         retained.interfaces.matched_interface_count.state.value,
                         retained.interfaces.mapping_coverage))
        for count_name in ("interface_count", "source_branch_member_count", "target_branch_count",
                           "matched_interface_count", "unmatched_interface_count"):
            rows.append((run.provenance.run_id, "interface_count", "CROSS_VOLTAGE_INTERFACES",
                         None, None, count_name, None,
                         getattr(retained.interfaces, count_name), None, None, None, None, None,
                         retained.interfaces.matched_interface_count.state.value,
                         retained.interfaces.mapping_coverage))
        for layer in retained.layers:
            for count_name in ("source_bus_count", "policy_protected_bus_count",
                               "retained_bus_count", "reduced_bus_count", "target_bus_count",
                               "source_line_count", "policy_protected_line_count",
                               "retained_line_count", "reduced_line_count", "target_line_count",
                               "source_transformer_count", "retained_transformer_count",
                               "reduced_transformer_count", "target_transformer_count",
                               "preserved_identity_coverage", "preserved_bus_identity_coverage",
                               "preserved_line_identity_coverage",
                               "preserved_transformer_identity_coverage"):
                rows.append((run.provenance.run_id, "voltage_layer_count", "RETAINED_BACKBONE",
                             layer.cutoff_kv, layer.voltage_kv, count_name,
                             None, getattr(layer, count_name), None, None, None, None, None,
                             layer.evaluation_status.value,
                             layer.preserved_identity_coverage))
            for metric in ("bus_vm", "bus_va", "line_p", "line_q", "transformer_p",
                           "transformer_q"):
                stats = getattr(layer, metric)
                rows.append((run.provenance.run_id, "voltage_layer", "RETAINED_BACKBONE",
                             layer.cutoff_kv, layer.voltage_kv, metric, *stat_cells(stats),
                             layer.evaluation_status.value,
                             layer.preserved_identity_coverage))
    return _csv_text(
        ("run_id", "scope", "group", "cutoff_kv", "voltage_kv", "metric", "minimum",
         "maximum", "mean", "max_absolute", "rmse", "p95", "sample_count", "status",
         "mapping_coverage"), rows)


def render_metrics_csv(report: ComprehensiveReductionReport) -> str:
    """Render the canonical metrics inventory as deterministic CSV."""
    return _csv_text(
        ("run_id", "section", "metric", "value", "unit", "status", "outcome",
         "sample_count", "mapping_coverage", "threshold"),
        _value_rows(report),
    )


def render_voltage_layers_csv(report: ComprehensiveReductionReport) -> str:
    """Render exact voltage-layer rows for each resolved policy."""
    rows: list[tuple[object, ...]] = []
    for run in report.runs:
        for layer in run.voltage_layers:
            rows.append((run.provenance.run_id, layer.policy_id, layer.voltage_kv,
                         layer.source_bus_count.value, layer.source_line_count.value,
                         layer.retained_bus_count.value, layer.reduced_bus_count.value,
                         layer.retained_line_count.value, layer.reduced_line_count.value,
                         layer.protected_bus_count.value, layer.protected_line_count.value,
                         layer.rejected_count.value, layer.radial_structure_count.value,
                         layer.meshed_structure_count.value,
                         layer.transformer_interface_count.value,
                         layer.bus_compression_fraction.value,
                         layer.line_compression_fraction.value,
                         layer.retained_identity_coverage.value,
                         layer.retained_line_identity_coverage.value))
    return _csv_text(
        ("run_id", "policy_id", "voltage_kv", "source_bus_count", "source_line_count",
         "retained_bus_count", "reduced_bus_count", "retained_line_count",
         "reduced_line_count", "protected_bus_count", "protected_line_count",
         "rejected_count", "radial_structure_count", "meshed_structure_count",
         "transformer_interface_count", "bus_compression_fraction",
         "line_compression_fraction", "retained_identity_coverage",
         "retained_line_identity_coverage"), rows)


def render_policy_comparison_csv(report: ComprehensiveReductionReport) -> str:
    """Render a compact cross-policy table derived only from report fields."""
    rows: list[tuple[object, ...]] = []
    for run in report.runs:
        layers_by_policy: dict[str, list[object]] = {}
        for layer in run.voltage_layers:
            layers_by_policy.setdefault(layer.policy_id, []).append(layer)
        for policy in run.voltage_policies:
            layer_rows = layers_by_policy.get(policy.policy_id, [])
            sum_value = lambda attribute: sum(
                getattr(layer, attribute).value for layer in layer_rows
                if getattr(layer, attribute).value is not None
            ) if layer_rows else None
            rows.append((
                run.provenance.run_id,
                policy.policy_id,
                policy.requested_cutoff_kv,
                ";".join(_cell(value) for value in policy.retained_voltage_layers_kv),
                ";".join(_cell(value) for value in policy.compressible_voltage_layers_kv),
                ";".join(policy.equivalent_policy_ids),
                policy.resolved_policy_fingerprint,
                sum_value("source_bus_count"),
                sum_value("retained_bus_count"),
                sum_value("reduced_bus_count"),
                sum_value("source_line_count"),
                sum_value("retained_line_count"),
                sum_value("reduced_line_count"),
            ))
    return _csv_text(
        ("run_id", "policy_id", "requested_cutoff_kv", "retained_voltage_layers_kv",
         "compressible_voltage_layers_kv", "equivalent_policy_ids",
         "resolved_policy_fingerprint", "source_bus_count", "retained_bus_count",
         "reduced_bus_count", "source_line_count", "retained_line_count",
         "reduced_line_count"), rows)


def _md_table(headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> list[str]:
    """Render escaped Markdown table rows."""
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        cells = [str(_cell(value)).replace("|", "\\|").replace("\n", " ") for value in row]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def render_markdown(report: ComprehensiveReductionReport) -> str:
    """Render the required A–F sections without making physical-equivalence claims."""
    lines = [f"# Comprehensive Reduction Report: {report.report_id}", "",
             f"Schema: `{report.schema_version}`", "",
             "This report distinguishes recorded evidence from unavailable data. "
             "Structural bounds are not global optima. PF fidelity values do not "
             "establish physical equivalence.", ""]
    for run in report.runs:
        inv = run.source_inventory
        lines += [f"## Run `{run.provenance.run_id}`", "",
                  f"Study: `{run.provenance.study_id}`; package: "
                  f"`{_cell(run.provenance.package_version)}`; PyPSA: "
                  f"`{_cell(run.provenance.pypsa_version)}`.", "",
                  "### A. Source Network Inventory", ""]
        lines += _md_table(("Component", "Count", "Basis"), [
            ("Bus", inv.bus_count, "backend rows"),
            ("Line", inv.line_count, "backend rows"),
            ("Transformer", inv.transformer_count, inv.transformer_count_basis),
            ("Transformer physical 3-winding units", inv.transformer_3w_physical_count, "physical units"),
            ("Transformer projected T-legs", inv.transformer_3w_t_leg_projection_count, "projection rows"),
            ("Link", inv.link_count, "backend rows"),
            ("Total branches", inv.total_branch_count, "Line + Transformer + Link"),
            ("Generator / Load / Shunt", f"{_cell(inv.generator_count)} / {_cell(inv.load_count)} / {_cell(inv.shunt_count)}", "backend rows"),
            ("Connected components", inv.connected_component_count, "active topology"),
        ]) + ["", "Voltage layers (kV): " + ", ".join(_cell(v) for v in inv.voltage_layers_kv) + ".", "",
                  "### B. Compression Opportunity and Eligibility", ""]
        op = run.opportunity
        lines += _md_table(("Measure", "Value", "Interpretation"), [
            ("Protected / strict singleton / anchors", f"{_cell(op.protected_bus_count.value)} / {_cell(op.strict_singleton_bus_count.value)} / {_cell(op.anchor_bus_count.value)}", "identity constraints"),
            ("Radial eligible / rejected / candidates", f"{_cell(op.radial_tree_eligible_count.value)} / {_cell(op.radial_tree_rejected_count.value)} / {_cell(op.radial_tree_candidate_count.value)}", "NOT_RECORDED is not zero"),
            ("Series eligible / rejected / candidates", f"{_cell(op.series_chain_eligible_count.value)} / {_cell(op.series_chain_rejected_count.value)} / {_cell(op.series_chain_candidate_count.value)}", "NOT_RECORDED is not zero"),
            ("Retained cycle rank / parallel corridors", f"{_cell(op.retained_cycle_rank.value)} / {_cell(op.retained_parallel_corridor_count.value)}", "graph cycle rank, not a simple-cycle enumeration"),
            ("Eligible / rejected", f"{_cell(op.eligible_count.value)} / {_cell(op.rejected_count.value)}", "candidate counts"),
            ("Minimum clusters", op.minimum_cluster_count.value, "constraint-derived floor"),
            ("Structural ceiling Bus / Line / branches", f"{_cell(op.structural_compression_ceiling_bus_fraction.value)} / {_cell(op.structural_compression_ceiling_line_fraction.value)} / {_cell(op.structural_compression_ceiling_total_branch_fraction.value)}", "theoretical structural bound, not optimum"),
            ("Planning candidate Bus / Line / branches", f"{_cell(op.planning_candidate_bus_fraction.value)} / {_cell(op.planning_candidate_line_fraction.value)} / {_cell(op.planning_candidate_total_branch_fraction.value)}", "planner output"),
            ("Actual achieved Bus / Line / branches", f"{_cell(op.actual_achieved_bus_fraction.value)} / {_cell(op.actual_achieved_line_fraction.value)} / {_cell(op.actual_achieved_total_branch_fraction.value)}", "materialized result"),
            ("Actual accepted Bus / Line / branches", f"{_cell(op.actual_accepted_bus_fraction.value)} / {_cell(op.actual_accepted_line_fraction.value)} / {_cell(op.actual_accepted_total_branch_fraction.value)}", "final Gate accepted"),
        ]) + ["", "Rejection histogram: `" + _cell(op.rejection_histogram) + "`.", "",
              "### C. Structural Reduction Comparison", ""]
        target = run.structural.target_inventory
        lines += _md_table(("Component", "Source", "Target", "Compression"), [
            ("Bus", inv.bus_count, target.bus_count.value, _cell(op.actual_achieved_bus_fraction.value)),
            ("Line", inv.line_count, target.line_count.value, _cell(run.structural.line_compression_fraction.value)),
            ("Transformer", inv.transformer_count, target.transformer_count.value, "NOT_RECORDED unless reported"),
            ("Link", inv.link_count, target.link_count.value, "NOT_RECORDED unless reported"),
            ("Total branches", inv.total_branch_count, target.total_branch_count.value, _cell(run.structural.total_branch_compression_fraction.value)),
            ("Generator / Load / Shunt", "source inventory above", "mapping summary", _cell(run.structural.mapping_coverage.value)),
            ("Absorbed internal / parallel corridors", f"{_cell(run.structural.absorbed_internal_branch_count.value)} / {_cell(run.structural.aggregated_parallel_corridor_count.value)}", "mapping", "not a line-compression denominator"),
            ("Connected components", run.structural.source_connected_components.value, run.structural.target_connected_components.value, "per source subnetwork"),
            ("Protected cycle preservation / Gate", run.structural.protected_cycle_preservation.value, run.structural.structural_gate_status.value, "structure only"),
        ]) + ["", "### D. Voltage-Layer and Retained-Network Analysis", ""]
        layer_rows = [(row.policy_id, row.voltage_kv, row.source_bus_count.value,
                       row.source_line_count.value, row.retained_bus_count.value,
                       row.reduced_bus_count.value, row.protected_bus_count.value,
                       row.protected_line_count.value, row.radial_structure_count.value,
                       row.meshed_structure_count.value, row.transformer_interface_count.value,
                       row.retained_identity_coverage.value)
                      for row in run.voltage_layers]
        lines += _md_table(("Policy", "kV", "Source B/L", "Retained B", "Reduced B",
                            "Protected B/L", "Radial", "Meshed", "Transformer interfaces",
                            "Retained identity coverage"), [
            (policy, voltage, f"{_cell(bus)} / {_cell(line)}", retained, reduced,
             f"{_cell(pb)} / {_cell(pl)}", radial, meshed, interfaces, coverage)
            for policy, voltage, bus, line, retained, reduced, pb, pl, radial, meshed,
            interfaces, coverage in layer_rows
        ]) + ["", "Policy resolution:", ""]
        lines += _md_table(("Policy", "Requested cutoff kV", "Retained layers kV",
                            "Compressible layers kV", "Equivalent policies"), [
            (p.policy_id, p.requested_cutoff_kv,
             ", ".join(_cell(v) for v in p.retained_voltage_layers_kv),
             ", ".join(_cell(v) for v in p.compressible_voltage_layers_kv),
             ", ".join(p.equivalent_policy_ids)) for p in run.voltage_policies
        ]) + ["", "### E. AC Power Flow Fidelity", ""]
        lines += _md_table(("Group", "Metric", "Severity", "Observed", "Unit", "N",
                            "Coverage", "Source identity", "Target identity", "Threshold",
                            "Outcome", "Metric digest", "Threshold digest"), [
            (obs.group.value, obs.metric_name, obs.severity, obs.observed.value,
             obs.observed.unit, obs.sample_count, obs.mapping_coverage,
             obs.source_identity_digest, obs.target_identity_digest,
             obs.threshold.value, obs.outcome.value, obs.metric_digest,
             obs.threshold_digest) for obs in run.fidelity.observations
        ]) if run.fidelity.observations else ["No subgroup PF observations were recorded.", ""]
        lines += [f"Source PF: `{_cell(run.fidelity.pf_source_status.value)}`; candidate PF: "
                  f"`{_cell(run.fidelity.pf_candidate_status.value)}`; fidelity: "
                  f"`{_cell(run.fidelity.fidelity_status.value)}`.", "",
                  "New topology groups are diagnostic only and do not modify PR-022 admission.", "",
                  "Retained-network diagnostics (identity-preserved comparisons only):", ""]
        retained = run.retained_network_fidelity
        retained_rows = []
        for group in retained.groups:
            for metric in ("bus_vm", "bus_va", "line_p", "line_q", "transformer_p",
                           "transformer_q", "injection_p", "injection_q"):
                stats = getattr(group, metric)
                retained_rows.append((group.group.value, metric,
                                      stats.maximum, stats.rmse, stats.p95,
                                      stats.sample_count, group.mapping_coverage,
                                      group.status.value))
        lines += _md_table(("Group", "Metric", "Max", "RMSE", "P95", "N",
                            "Mapping coverage", "Status"), retained_rows)
        interface = retained.interfaces
        lines += ["", "Cross-voltage interface diagnostics:", ""]
        lines += _md_table(("Metric", "Max", "RMSE", "P95", "N", "Coverage"), [
            (name, getattr(interface, name).maximum, getattr(interface, name).rmse,
             getattr(interface, name).p95, getattr(interface, name).sample_count,
             interface.mapping_coverage)
            for name in ("p_error_mw", "q_error_mvar", "region_net_injection_p_error_mw",
                         "region_net_injection_q_error_mvar")])
        lines += ["", "Direction-normalized interface flows:", ""]
        lines += _md_table(("Metric", "Min", "Max", "Mean", "Max absolute", "N"), [
            (name, getattr(interface, name).minimum, getattr(interface, name).maximum,
             getattr(interface, name).mean, getattr(interface, name).max_absolute,
             getattr(interface, name).sample_count)
            for name in ("source_p_mw", "target_p_mw", "source_q_mvar", "target_q_mvar")])
        lines += ["", "Retained voltage-layer diagnostics:", ""]
        lines += _md_table(("Cutoff kV", "Actual kV", "Source / protected / retained / reduced / target Bus",
                            "Source / protected / retained / reduced / target Line",
                            "Source / retained / target Transformer rows",
                            "Bus / Line / Transformer identity coverage"), [
            (layer.cutoff_kv, layer.voltage_kv,
             " / ".join(_cell(value.value) for value in (
                 layer.source_bus_count, layer.policy_protected_bus_count,
                 layer.retained_bus_count, layer.reduced_bus_count, layer.target_bus_count)),
             " / ".join(_cell(value.value) for value in (
                 layer.source_line_count, layer.policy_protected_line_count,
                 layer.retained_line_count, layer.reduced_line_count, layer.target_line_count)),
             " / ".join(_cell(value.value) for value in (
                 layer.source_transformer_count, layer.retained_transformer_count,
                 layer.target_transformer_count)),
             " / ".join(_cell(value.value) for value in (
                 layer.preserved_bus_identity_coverage,
                 layer.preserved_line_identity_coverage,
                 layer.preserved_transformer_identity_coverage)))
            for layer in retained.layers])
        lines += [""]
        lines += _md_table(("Cutoff kV", "Actual kV", "Preserved Bus coverage",
                            "Vm RMSE", "Va RMSE", "Line P RMSE", "Line Q RMSE",
                            "Transformer P RMSE", "Transformer Q RMSE", "Status"), [
            (layer.cutoff_kv, layer.voltage_kv, layer.preserved_identity_coverage,
             layer.bus_vm.rmse, layer.bus_va.rmse, layer.line_p.rmse, layer.line_q.rmse,
             layer.transformer_p.rmse, layer.transformer_q.rmse,
             layer.evaluation_status.value) for layer in retained.layers])
        lines += ["", "These retained-network metrics are DIAGNOSTIC ONLY; no PR-022 HARD threshold or admission result is changed.", "",
                  "Existing PF subnetwork results:", ""]
        pf_rows = []
        for side, subnetworks in (("SOURCE", run.fidelity.source_subnetworks),
                                  ("CANDIDATE", run.fidelity.candidate_subnetworks)):
            for subnetwork in subnetworks:
                pf_rows.append((side, subnetwork.ordinal, subnetwork.converged,
                                subnetwork.iterations, subnetwork.final_error))
        lines += (_md_table(("Side", "Subnetwork", "Converged", "Iterations", "Final error"),
                            pf_rows) if pf_rows
                  else ["Subnetwork iterations/errors: NOT_RECORDED.", ""])
        lines += [f"Sanitized failure codes: source `{_cell(run.fidelity.source_failure_code)}`, "
                  f"candidate `{_cell(run.fidelity.candidate_failure_code)}`; solver contract "
                  f"`{_cell(run.fidelity.solver_contract_id)}` / "
                  f"`{_cell(run.fidelity.solver_contract_digest)}`.", "",
                  "### F. Runtime, Replay and Evidence Provenance", ""]
        perf = run.performance
        lines += _md_table(("Phase", "Solver-only s", "End-to-end s", "Scope"), [
            ("Source PF", perf.source_pf_solver_seconds.value, perf.source_pf_end_to_end_seconds.value, "same-run diagnostic"),
            ("Candidate PF", perf.candidate_pf_solver_seconds.value, perf.candidate_pf_end_to_end_seconds.value, "same-run diagnostic"),
            ("Candidate reconstruction", perf.candidate_reconstruction_seconds.value, "NOT_APPLICABLE", "same-run diagnostic"),
            ("Structural / AC readiness", perf.structural_ac_readiness_seconds.value, "NOT_APPLICABLE", "same-run diagnostic"),
            ("Fidelity / attribution", f"{_cell(perf.fidelity_evaluation_seconds.value)} / {_cell(perf.attribution_seconds.value)}", "NOT_APPLICABLE", "same-run diagnostic"),
            ("Controller / persistence / replay", f"{_cell(perf.controller_total_seconds.value)} / {_cell(perf.evidence_persistence_seconds.value)} / {_cell(perf.fresh_replay_seconds.value)}", "NOT_APPLICABLE", "same-run diagnostic"),
        ]) + ["", "Source model SHA-256: `" + _cell(run.provenance.source_model_sha256) + "`; sidecar SHA-256: `" + _cell(run.provenance.source_sidecar_sha256) + "`.",
              "Evidence sources: " + ", ".join(f"`{source}`" for source in run.provenance.evidence_sources) + ".", ""]
    result = "\n".join(lines).rstrip() + "\n"
    validate_public_export_text(result)
    return result


def export_report_bundle(
    report: ComprehensiveReductionReport,
    output_directory: str | Path,
) -> Mapping[str, Path]:
    """Write JSON, Markdown and three canonical CSV views from one report object."""
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    contents = {
        "json": report.to_json(),
        "markdown": render_markdown(report),
        "metrics_csv": render_metrics_csv(report),
        "retained_fidelity_csv": render_retained_fidelity_csv(report),
        "voltage_layers_csv": render_voltage_layers_csv(report),
        "policy_comparison_csv": render_policy_comparison_csv(report),
    }
    written: dict[str, Path] = {}
    suffixes = {
        "json": ".json",
        "markdown": ".md",
        "metrics_csv": "-metrics.csv",
        "retained_fidelity_csv": "-retained-fidelity.csv",
        "voltage_layers_csv": "-voltage-layers.csv",
        "policy_comparison_csv": "-policy-comparison.csv",
    }
    for name, content in contents.items():
        validate_public_export_text(content)
        path = output / f"{report.report_id}{suffixes[name]}"
        path.write_text(content, encoding="utf-8", newline="\n")
        written[name] = path
    return written
