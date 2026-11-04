"""Read-only aggregation of existing reduction and PF evidence."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field as dataclass_field
from typing import Mapping, Sequence

import networkx as nx
import pypsa

from psdcalc.reduction.backends.pypsa.adapter import IdentitySidecar
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.fidelity.contracts import (
    POWER_FLOW_DIAGNOSTIC_THRESHOLD_FIELDS,
    POWER_FLOW_HARD_THRESHOLD_FIELDS,
    PowerFlowMetricSet,
    PowerFlowMetricThresholds,
)
from psdcalc.reduction.fidelity.metrics import (
    PowerFlowThresholdEvaluation,
    metric_set_digest,
)
from psdcalc.reduction.fidelity.persistence import ResolvedPowerFlowThresholds
from psdcalc.reduction.fidelity.pypsa_executor import PowerFlowExecutionResult
from psdcalc.reduction.mapping.bundle import MappingBundle, MapEntry
from psdcalc.reduction.policies.voltage_cutoff import VoltageCutoffPolicy
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.reporting.comparison import CompressionComparisonReport
from psdcalc.reduction.reporting.opportunity import CompressionOpportunityReport
from psdcalc.reduction.reporting.comprehensive import (
    ComponentMappingSummary,
    ComprehensiveRunReport,
    EvidenceStatus,
    EvidenceValue,
    FidelityObservation,
    FidelitySection,
    MetricGroup,
    MetricOutcome,
    NetworkInventory,
    OpportunitySection,
    PFSubnetworkObservation,
    PerformanceSection,
    ReportProvenance,
    RetainedGroupComparison,
    RetainedNetworkFidelityReport,
    StageInventorySummary,
    StructuralComparison,
    VoltageLayerSummary,
    VoltagePolicyResolution,
    not_recorded,
)
from psdcalc.reduction.validation.multi_voltage_final import MultiVoltageStructuralResult
from psdcalc.reduction.reporting.retained_fidelity import compare_retained_network_fidelity


class ComprehensiveReportError(ValueError):
    """Raised when report inputs have conflicting or unverifiable identities."""


@dataclass(frozen=True)
class FidelityGroupInput:
    """Existing measured metrics and resolved thresholds for one topology group.

    ``resolved_thresholds`` should be taken from ``FrozenThresholdSet.resolve``
    or the exact frozen threshold object already used for the run. This adapter
    only reads its scalar values; it does not resolve or evaluate thresholds.
    """

    group: MetricGroup
    metrics: PowerFlowMetricSet
    evaluation: PowerFlowThresholdEvaluation
    resolved_thresholds: PowerFlowMetricThresholds | ResolvedPowerFlowThresholds | None
    source_ids_by_metric: Mapping[str, tuple[StableId, ...]] = dataclass_field(default_factory=dict)
    target_ids_by_metric: Mapping[str, tuple[StableId, ...]] = dataclass_field(default_factory=dict)
    mapping_coverage: float | None = None

    def __post_init__(self) -> None:
        if self.mapping_coverage is not None and (
                not math.isfinite(self.mapping_coverage)
                or not 0.0 <= self.mapping_coverage <= 1.0):
            raise ComprehensiveReportError("Fidelity-group mapping coverage is invalid")
        valid_metrics = set(_HARD_METRIC_PATHS) | set(_DIAGNOSTIC_METRIC_PATHS)
        for identity_map in (self.source_ids_by_metric, self.target_ids_by_metric):
            if set(identity_map) - valid_metrics:
                raise ComprehensiveReportError("Fidelity identity map references an unknown metric")
            if any(len(set(ids)) != len(ids) for ids in identity_map.values()):
                raise ComprehensiveReportError("Fidelity metric has duplicate identities")


_TABLES: tuple[tuple[str, str], ...] = (
    ("Bus", "buses"),
    ("Line", "lines"),
    ("Transformer", "transformers"),
    ("Link", "links"),
    ("Generator", "generators"),
    ("Load", "loads"),
    ("ShuntImpedance", "shunt_impedances"),
)
_BRANCHES = frozenset({"Line", "Transformer", "Link"})
_DEVICE_COMPONENTS = frozenset({"Generator", "Load", "ShuntImpedance"})
_TIMING_FIELD_MAP = {
    "source_pf_solver": "source_pf_solver_seconds",
    "source_pf_end_to_end": "source_pf_end_to_end_seconds",
    "candidate_pf_solver": "candidate_pf_solver_seconds",
    "candidate_pf_end_to_end": "candidate_pf_end_to_end_seconds",
    "candidate_reconstruction": "candidate_reconstruction_seconds",
    "structural_ac_readiness": "structural_ac_readiness_seconds",
    "fidelity_evaluation": "fidelity_evaluation_seconds",
    "attribution": "attribution_seconds",
    "controller_total": "controller_total_seconds",
    "evidence_persistence": "evidence_persistence_seconds",
    "fresh_replay": "fresh_replay_seconds",
    "reported_total": "reported_total_seconds",
}
_HARD_METRIC_PATHS: dict[str, tuple[str, str, str, str]] = {
    "vm_max_pu": ("bus_voltage", "max_absolute", "vm_max_pu", "pu"),
    "vm_rmse_pu": ("bus_voltage", "rmse", "vm_rmse_pu", "pu"),
    "cutset_p_max_mw": ("cutset_p", "max_absolute", "cutset_p_max_mw", "MW"),
    "cutset_q_max_mvar": ("cutset_q", "max_absolute", "cutset_q_max_mvar", "MVAr"),
    "loss_rel_max": ("passive_branch_losses", "passive_branch_active_relative_diff", "loss_rel_max", "fraction"),
}
_DIAGNOSTIC_METRIC_PATHS: dict[str, tuple[str, str, str, str]] = {
    "vm_p95_pu": ("bus_voltage", "p95_absolute", "vm_p95_pu", "pu"),
    "angle_rmse_deg": ("angle", "rmse", "angle_rmse_deg", "degree"),
    "angle_p95_deg": ("angle", "p95_absolute", "angle_p95_deg", "degree"),
    "angle_max_deg": ("angle", "max_absolute", "angle_max_deg", "degree"),
    "branch_p_rmse_mw": ("branch_p", "rmse", "branch_p_rmse_mw", "MW"),
    "branch_p_p95_mw": ("branch_p", "p95_absolute", "branch_p_p95_mw", "MW"),
    "branch_p_max_mw": ("branch_p", "max_absolute", "branch_p_max_mw", "MW"),
    "branch_q_rmse_mvar": ("branch_q", "rmse", "branch_q_rmse_mvar", "MVAr"),
    "branch_q_p95_mvar": ("branch_q", "p95_absolute", "branch_q_p95_mvar", "MVAr"),
    "branch_q_max_mvar": ("branch_q", "max_absolute", "branch_q_max_mvar", "MVAr"),
    "cutset_p_rmse_mw": ("cutset_p", "rmse", "cutset_p_rmse_mw", "MW"),
    "cutset_q_rmse_mvar": ("cutset_q", "rmse", "cutset_q_rmse_mvar", "MVAr"),
    "injection_p_rmse_mw": ("cluster_injection.p_mw", "rmse", "injection_p_rmse_mw", "MW"),
    "injection_p_max_mw": ("cluster_injection.p_mw", "max_absolute", "injection_p_max_mw", "MW"),
    "injection_q_rmse_mvar": ("cluster_injection.q_mvar", "rmse", "injection_q_rmse_mvar", "MVAr"),
    "injection_q_max_mvar": ("cluster_injection.q_mvar", "max_absolute", "injection_q_max_mvar", "MVAr"),
    "loss_abs_max_mw": ("passive_branch_losses", "passive_branch_active_abs_diff_mw", "loss_abs_max_mw", "MW"),
}


def _available(value: int | float | str | bool, unit: str | None = None,
               *, outcome: MetricOutcome = MetricOutcome.NOT_EVALUATED,
               sample_count: int | None = None,
               mapping_coverage: float | None = None,
               source_digest: str | None = None,
               target_digest: str | None = None,
               threshold: float | None = None) -> EvidenceValue:
    """Construct an available report datum without coercing its value."""
    return EvidenceValue(
        value=value, state=EvidenceStatus.AVAILABLE, unit=unit,
        sample_count=sample_count, mapping_coverage=mapping_coverage,
        source_identity_digest=source_digest, target_identity_digest=target_digest,
        threshold=threshold, outcome=outcome,
    )


def _identity_digest(ids: Sequence[StableId]) -> str:
    """Hash sorted encoded StableIds so public reports expose no raw identity."""
    payload = json.dumps(sorted(item.encode() for item in ids), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _number(value: object) -> int | float | None:
    """Return a finite numeric value without coercing booleans or strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ComprehensiveReportError("Nonfinite network inventory value")
    return int(value) if isinstance(value, int) else numeric


def _active(row: object) -> bool:
    """Read PyPSA static active state; an absent column means active."""
    value = row.get("active", True)  # type: ignore[attr-defined]
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        raise ComprehensiveReportError("Active branch state is missing")
    if not isinstance(value, bool):
        raise ComprehensiveReportError("Active branch state is not boolean")
    return value


def _table(network: pypsa.Network, attribute: str):
    """Return a PyPSA static component table or an empty frame."""
    value = getattr(network, attribute, None)
    if value is None:
        import pandas as pd

        return pd.DataFrame()
    return value


def _voltage_map(network: pypsa.Network) -> dict[str, float]:
    """Build exact nominal-voltage lookup; no tolerance or rounding is used."""
    result: dict[str, float] = {}
    for name, raw_value in network.buses.v_nom.items():
        value = _number(raw_value)
        if value is None or value <= 0.0:
            raise ComprehensiveReportError("Bus.v_nom must be positive and finite")
        result[str(name)] = float(value)
    return result


def _branch_endpoints(network: pypsa.Network):
    """Yield supported two-terminal branch rows and validate Link ports."""
    for component, attribute in (("Line", "lines"),
                                 ("Transformer", "transformers"),
                                 ("Link", "links")):
        frame = _table(network, attribute)
        for name, row in frame.iterrows():
            if component == "Link":
                for port in ("bus2", "bus3", "bus4"):
                    if port in row.index and not (row[port] is None or
                            isinstance(row[port], float) and math.isnan(row[port]) or
                            str(row[port]) == ""):
                        raise ComprehensiveReportError(
                            "Multi-terminal Link report topology is unsupported")
            bus0, bus1 = str(row.get("bus0", "")), str(row.get("bus1", ""))
            if bus0 not in network.buses.index or bus1 not in network.buses.index:
                raise ComprehensiveReportError("Branch endpoint is absent from Bus inventory")
            yield component, str(name), row, bus0, bus1


def _topology(network: pypsa.Network) -> tuple[nx.MultiGraph, int, int, int]:
    """Build active multigraph and count cross-voltage Line/Link records."""
    voltages = _voltage_map(network)
    graph = nx.MultiGraph()
    graph.add_nodes_from(str(name) for name in network.buses.index)
    cross_voltage_lines = 0
    cross_voltage_links = 0
    for component, _, row, bus0, bus1 in _branch_endpoints(network):
        if not _active(row):
            continue
        graph.add_edge(bus0, bus1, component=component)
        if voltages[bus0] != voltages[bus1]:
            if component == "Line":
                cross_voltage_lines += 1
            elif component == "Link":
                cross_voltage_links += 1
    return graph, cross_voltage_lines, cross_voltage_links, nx.number_connected_components(graph)


def _component_counts(network: pypsa.Network) -> dict[str, int]:
    """Count supported public PyPSA tables by component class."""
    return {component: len(_table(network, attribute))
            for component, attribute in _TABLES}


def _inventory(network: pypsa.Network) -> NetworkInventory:
    """Count source/target tables and active connected components."""
    counts = _component_counts(network)
    graph, _, _, components = _topology(network)
    del graph
    values = {
        "bus_count": counts["Bus"],
        "line_count": counts["Line"],
        "transformer_count": counts["Transformer"],
        "link_count": counts["Link"],
        "total_branch_count": counts["Line"] + counts["Transformer"] + counts["Link"],
        "generator_count": counts["Generator"],
        "load_count": counts["Load"],
        "shunt_count": counts["ShuntImpedance"],
        "connected_component_count": components,
    }
    return NetworkInventory(
        **{name: _available(value, "count") for name, value in values.items()},
        transformer_count_basis="BACKEND_ROWS",
        voltage_layers_kv=tuple(sorted(set(_voltage_map(network).values()))),
    )


def _layer_line_counts(network: pypsa.Network) -> tuple[dict[float, int], int]:
    """Count Lines by exact terminal voltage and separate malformed cross-layer Lines."""
    voltage = _voltage_map(network)
    counts = {value: 0 for value in set(voltage.values())}
    cross_layer = 0
    for name, row in network.lines.iterrows():
        v0, v1 = voltage[str(row.bus0)], voltage[str(row.bus1)]
        if v0 != v1:
            cross_layer += 1
        else:
            counts[v0] += 1
    return counts, cross_layer


def _layer_topology(network: pypsa.Network) -> dict[float, tuple[int, int]]:
    """Count active radial and meshed Line components within each exact layer."""
    voltage = _voltage_map(network)
    result: dict[float, tuple[int, int]] = {}
    for layer in sorted(set(voltage.values())):
        graph = nx.MultiGraph()
        nodes = [bus for bus, value in voltage.items() if value == layer]
        graph.add_nodes_from(nodes)
        for name, row in network.lines.iterrows():
            if not _active(row):
                continue
            bus0, bus1 = str(row.bus0), str(row.bus1)
            if voltage[bus0] == layer and voltage[bus1] == layer:
                graph.add_edge(bus0, bus1, key=str(name))
        radial = 0
        meshed = 0
        for component_nodes in nx.connected_components(graph):
            component_graph = graph.subgraph(component_nodes)
            if component_graph.number_of_edges() == max(0, component_graph.number_of_nodes() - 1):
                radial += 1
            else:
                meshed += 1
        result[layer] = (radial, meshed)
    return result


def _layer_transformer_interfaces(network: pypsa.Network) -> dict[float, int]:
    """Count transformer terminals incident on each actual voltage layer."""
    voltage = _voltage_map(network)
    counts = {value: 0 for value in set(voltage.values())}
    for _, row in network.transformers.iterrows():
        bus0, bus1 = str(row.bus0), str(row.bus1)
        v0, v1 = voltage[bus0], voltage[bus1]
        if v0 != v1:
            counts[v0] += 1
            counts[v1] += 1
    return counts


def _network_identity_ids(network: pypsa.Network, sidecar: IdentitySidecar) -> dict[str, tuple[StableId, ...]]:
    """Resolve every reportable object to its authoritative sidecar StableId."""
    output: dict[str, tuple[StableId, ...]] = {}
    for component, attribute in _TABLES:
        table = _table(network, attribute)
        output[component] = tuple(sorted(
            (sidecar.source_id(component, str(name)) for name in table.index),
            key=lambda item: item.encode(),
        ))
    return output


def _identity_maps(
    source_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    target_network: pypsa.Network,
    target_sidecar: IdentitySidecar,
    mappings: MappingBundle,
) -> tuple[dict[str, tuple[MapEntry, ...]], float, float]:
    """Validate source coverage and target existence, then summarize identities."""
    source_ids = _network_identity_ids(source_network, source_sidecar)
    target_ids = _network_identity_ids(target_network, target_sidecar)
    rows_by_component: dict[str, list[MapEntry]] = {name: [] for name, _ in _TABLES}
    object_rows = mappings.bus_map + mappings.branch_map + mappings.device_map
    for row in object_rows:
        if row.source_id.component not in rows_by_component:
            continue
        if row.source_id in {item.source_id for item in rows_by_component[row.source_id.component]}:
            raise ComprehensiveReportError("Duplicate source identity in MappingBundle")
        if row.target_id is None and row.mapping_type != "absorbed_internal":
            raise ComprehensiveReportError("Missing target identity outside absorbed_internal map")
        if row.target_id is not None:
            if row.target_id.component != row.source_id.component:
                raise ComprehensiveReportError("Source and target mapping component differ")
            if row.target_id not in set(target_ids[row.target_id.component]):
                raise ComprehensiveReportError("Mapping target identity is absent from target network")
        rows_by_component[row.source_id.component].append(row)
    for component, expected_ids in source_ids.items():
        actual = {row.source_id for row in rows_by_component[component]}
        if not actual.issubset(set(expected_ids)):
            raise ComprehensiveReportError("Mapping source identity is absent from source network")
    source_total = sum(len(values) for values in source_ids.values())
    mapped_total = sum(len(values) for values in rows_by_component.values())
    source_coverage = mapped_total / source_total if source_total else 1.0
    target_total = sum(len(values) for values in target_ids.values())
    mapped_targets = {row.target_id for rows in rows_by_component.values()
                      for row in rows if row.target_id is not None}
    target_coverage = (len(mapped_targets) / target_total) if target_total else 1.0
    if mappings.line_map != tuple(row for row in mappings.branch_map
                                  if row.source_id.component == "Line"):
        raise ComprehensiveReportError("Line projection differs from authoritative branch map")
    if mappings.transformer_map != tuple(row for row in mappings.branch_map
                                         if row.source_id.component == "Transformer"):
        raise ComprehensiveReportError("Transformer projection differs from authoritative branch map")
    return {name: tuple(values) for name, values in rows_by_component.items()}, source_coverage, target_coverage


def _component_mapping_summaries(
    source_network: pypsa.Network,
    target_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    target_sidecar: IdentitySidecar,
    mappings: MappingBundle | None,
) -> tuple[tuple[ComponentMappingSummary, ...], EvidenceValue, EvidenceValue]:
    """Summarize mappings by component while keeping all StableIds private."""
    if mappings is None:
        return (), not_recorded(), not_recorded()
    rows_by_component, source_coverage, target_coverage = _identity_maps(
        source_network, source_sidecar, target_network, target_sidecar, mappings)
    source_ids = _network_identity_ids(source_network, source_sidecar)
    target_ids = _network_identity_ids(target_network, target_sidecar)
    summaries: list[ComponentMappingSummary] = []
    for component, _ in _TABLES:
        rows = rows_by_component[component]
        expected = source_ids[component]
        targets = target_ids[component]
        mapped_source_ids = {row.source_id for row in rows}
        coverage = len(mapped_source_ids) / len(expected) if expected else 1.0
        preserved = sum(row.mapping_type == "preserved" and row.source_id == row.target_id
                        for row in rows)
        aggregated = sum(row.mapping_type in {"aggregated", "clustered", "moved"}
                         for row in rows)
        absorbed = sum(row.mapping_type == "absorbed_internal" for row in rows)
        summaries.append(ComponentMappingSummary(
            component=component,
            source_count=_available(len(expected), "count"),
            target_count=_available(len(targets), "count"),
            preserved_identity_count=_available(preserved, "count"),
            aggregated_count=_available(aggregated, "source objects"),
            absorbed_internal_count=_available(absorbed, "source branches"),
            mapping_coverage=_available(coverage, "fraction"),
            source_identity_digest=_identity_digest(expected),
            target_identity_digest=_identity_digest(targets),
        ))
    return (
        tuple(summaries),
        _available(source_coverage, "fraction"),
        _available(target_coverage, "fraction"),
    )


def _comparison_inventory(stage: object) -> NetworkInventory:
    """Adapt a stage inventory without conflating physical units and backend rows."""
    def datum(value: object, unit: str = "count") -> EvidenceValue:
        number = _number(value)
        return _available(number, unit) if number is not None else not_recorded()

    line = _number(getattr(stage, "line_count", None))
    transformer = _number(getattr(stage, "transformer_count", None))
    link = _number(getattr(stage, "link_count", None))
    total = line + transformer + link if all(v is not None for v in (line, transformer, link)) else None
    return NetworkInventory(
        bus_count=datum(getattr(stage, "bus_count", None)),
        line_count=datum(line),
        transformer_count=datum(transformer),
        transformer_count_basis="BACKEND_ROWS" if transformer is not None else "NOT_RECORDED",
        transformer_3w_physical_count=datum(
            getattr(stage, "source_transformer3w_group_count", None)),
        transformer_3w_t_leg_projection_count=datum(
            getattr(stage, "source_transformer3w_leg_count", None)),
        link_count=datum(link),
        total_branch_count=datum(total),
        generator_count=datum(getattr(stage, "generator_count", None)),
        load_count=datum(getattr(stage, "load_count", None)),
        shunt_count=datum(getattr(stage, "shunt_count", None)),
        connected_component_count=datum(
            getattr(stage, "active_connected_component_count", None)),
    )


def _stage_summaries(comparison: CompressionComparisonReport | None) -> tuple[StageInventorySummary, ...]:
    """Copy all original stage inventories from the existing comparison report."""
    if comparison is None:
        return ()
    return tuple(StageInventorySummary(
        stage=stage.stage.value,
        inventory=_comparison_inventory(stage),
    ) for stage in comparison.stages.values())


def _metric_observations(item: FidelityGroupInput) -> tuple[FidelityObservation, ...]:
    """Project existing metric summaries and frozen thresholds without re-evaluation."""
    actual_digest = metric_set_digest(item.metrics)
    if item.evaluation.metrics_digest is not None and item.evaluation.metrics_digest != actual_digest:
        raise ComprehensiveReportError("Metric digest differs from the threshold evaluation")
    threshold_object = item.resolved_thresholds
    if isinstance(threshold_object, ResolvedPowerFlowThresholds):
        thresholds = threshold_object.thresholds
        resolved_digest = threshold_object.digest
    else:
        thresholds = threshold_object
        resolved_digest = item.evaluation.threshold_digest
    hard_names = set(POWER_FLOW_HARD_THRESHOLD_FIELDS)
    diagnostic_names = set(POWER_FLOW_DIAGNOSTIC_THRESHOLD_FIELDS)
    if hard_names != set(_HARD_METRIC_PATHS) or diagnostic_names != set(_DIAGNOSTIC_METRIC_PATHS):
        raise ComprehensiveReportError("Report metric projection differs from PR-022 metric catalog")
    output: list[FidelityObservation] = []
    for name, severity, path in (
        *((key, "HARD", value) for key, value in _HARD_METRIC_PATHS.items()),
        *((key, "DIAGNOSTIC", value) for key, value in _DIAGNOSTIC_METRIC_PATHS.items()),
    ):
        metric_path, metric_attribute, threshold_attribute, unit = path
        node: object = item.metrics
        for piece in metric_path.split("."):
            node = getattr(node, piece)
        observed_value = getattr(node, metric_attribute)
        sample_count = getattr(node, "sample_count", None)
        threshold_value = (getattr(thresholds, threshold_attribute)
                           if thresholds is not None else None)
        source_ids = item.source_ids_by_metric.get(name, ())
        target_ids = item.target_ids_by_metric.get(name, ())
        source_digest = _identity_digest(source_ids) if source_ids else None
        target_digest = _identity_digest(target_ids) if target_ids else None
        diagnostic_group = item.group is not MetricGroup.WHOLE_NETWORK
        effective_severity = "DIAGNOSTIC" if diagnostic_group else severity
        observed = (_available(observed_value, unit, sample_count=sample_count,
                               mapping_coverage=item.mapping_coverage,
                               source_digest=source_digest, target_digest=target_digest)
                    if observed_value is not None else not_recorded())
        threshold = (_available(threshold_value, unit)
                     if threshold_value is not None else not_recorded())
        if diagnostic_group:
            outcome = (MetricOutcome.DIAGNOSTIC_ONLY if observed_value is not None
                       else MetricOutcome.NOT_RECORDED)
        elif severity == "HARD":
            outcome = (MetricOutcome.FAIL if name in item.evaluation.hard_failures else
                       MetricOutcome.PASS if observed_value is not None and threshold_value is not None else
                       MetricOutcome.NOT_EVALUATED)
        else:
            outcome = MetricOutcome.DIAGNOSTIC_ONLY if observed_value is not None else MetricOutcome.NOT_RECORDED
        output.append(FidelityObservation(
            group=item.group,
            metric_name=name,
            severity=effective_severity,
            observed=observed,
            unit=unit,
            sample_count=sample_count,
            sample_count_status=(EvidenceStatus.AVAILABLE if sample_count is not None
                                 else EvidenceStatus.NOT_RECORDED),
            mapping_coverage=item.mapping_coverage,
            mapping_coverage_status=(EvidenceStatus.AVAILABLE if item.mapping_coverage is not None
                                    else EvidenceStatus.NOT_RECORDED),
            source_identity_digest=source_digest,
            target_identity_digest=target_digest,
            identity_binding_status=(EvidenceStatus.AVAILABLE
                                     if source_digest is not None and target_digest is not None
                                     else EvidenceStatus.NOT_RECORDED),
            threshold=threshold,
            outcome=outcome,
            metric_digest=actual_digest,
            threshold_digest=resolved_digest,
        ))
    return tuple(output)


def _pf_status(execution: PowerFlowExecutionResult | None) -> EvidenceValue:
    """Expose PF state while suppressing untrusted raw exception text."""
    if execution is None:
        return not_recorded()
    outcome = MetricOutcome.PASS if execution.converged else MetricOutcome.FAIL
    return _available(execution.status.value, "status", outcome=outcome)


def _subnetwork_observations(
    execution: PowerFlowExecutionResult | None,
    side: str,
) -> tuple[PFSubnetworkObservation, ...]:
    """Copy per-subnetwork numerical status without adding solver calls."""
    if execution is None:
        return ()
    return tuple(PFSubnetworkObservation(
        side=side, ordinal=row.subnetwork_ordinal, converged=row.converged,
        iterations=row.iterations, final_error=row.error,
    ) for row in execution.subnetwork_status)


def _mapping_identity_rows(mappings: MappingBundle | None) -> tuple[MapEntry, ...]:
    """Return unique object-map rows, excluding control ownership projections."""
    if mappings is None:
        return ()
    return mappings.bus_map + mappings.branch_map + mappings.device_map


def _evidence_count(value: int | None) -> EvidenceValue:
    """Return count evidence or explicit absence."""
    return _available(value, "count") if value is not None else not_recorded()


def _fraction(numerator: int, denominator: int) -> EvidenceValue:
    """Return a bounded compression fraction or not-applicable for empty source."""
    if denominator <= 0:
        return EvidenceValue(state=EvidenceStatus.NOT_APPLICABLE)
    value = numerator / denominator
    if not 0.0 <= value <= 1.0:
        raise ComprehensiveReportError("Compression fraction is outside [0, 1]")
    return _available(value, "fraction")


def _opportunity_section(
    source_network: pypsa.Network,
    target_network: pypsa.Network | None,
    opportunity: CompressionOpportunityReport | None,
    comparison: CompressionComparisonReport | None,
    structural: MultiVoltageStructuralResult | None,
    protection: ProtectionSet | None,
    eligibility_counts: Mapping[str, int] | None,
) -> OpportunitySection:
    """Aggregate existing opportunity evidence and actual network counts."""
    eligibility_counts = eligibility_counts or {}
    allowed = {"radial_tree_eligible", "radial_tree_rejected", "series_chain_eligible",
               "series_chain_rejected", "eligible", "rejected"}
    if set(eligibility_counts) - allowed:
        raise ComprehensiveReportError("Unknown eligibility evidence key")
    if any(isinstance(value, bool) or value < 0 for value in eligibility_counts.values()):
        raise ComprehensiveReportError("Eligibility evidence counts must be nonnegative integers")
    source_counts = _component_counts(source_network)
    source_branches = sum(source_counts[name] for name in _BRANCHES)
    if target_network is None:
        target_counts = None
        target_branches = None
    else:
        target_counts = _component_counts(target_network)
        target_branches = sum(target_counts[name] for name in _BRANCHES)
    gates_passed = bool(structural and structural.status.endswith("_VALID"))
    protected_buses: set[StableId] = set()
    if protection is not None:
        protected_buses.update(protection.terminal_buses)
        protected_buses.update(protection.singleton_buses)
    histogram = (dict(sorted(opportunity.rejection_reason_counts.items()))
                 if opportunity is not None else None)
    eligible_data = lambda name: _evidence_count(eligibility_counts.get(name))
    candidate_bus = (comparison.candidate_bus_compression_fraction
                     if comparison is not None else None)
    candidate_line = (comparison.candidate_line_compression_fraction
                      if comparison is not None else None)
    candidate_total_branch: float | None = None
    if comparison is not None:
        source_stage = comparison.stages.get("S1_PYPSA_FULL_PROJECTION")
        candidate_stage = comparison.stages.get("S3_QUOTIENT_CANDIDATE")
        if source_stage is not None and candidate_stage is not None:
            source_total = sum((source_stage.line_count or 0,
                                source_stage.transformer_count or 0,
                                source_stage.link_count or 0))
            candidate_total = sum((candidate_stage.line_count or 0,
                                   candidate_stage.transformer_count or 0,
                                   candidate_stage.link_count or 0))
            if source_total > 0:
                candidate_total_branch = (source_total - candidate_total) / source_total
    bus_ceil = (opportunity.theoretical_maximum_bus_compression_fraction
                if opportunity is not None else None)
    actual_bus = (_fraction(source_counts["Bus"] - target_counts["Bus"], source_counts["Bus"])
                  if target_counts is not None else not_recorded())
    actual_line = (_fraction(source_counts["Line"] - target_counts["Line"], source_counts["Line"])
                   if target_counts is not None else not_recorded())
    actual_branch = (_fraction(source_branches - target_branches, source_branches)
                     if target_branches is not None else not_recorded())
    accepted_bus = actual_bus if gates_passed else EvidenceValue(state=EvidenceStatus.NOT_APPLICABLE)
    accepted_line = actual_line if gates_passed else EvidenceValue(state=EvidenceStatus.NOT_APPLICABLE)
    accepted_branch = actual_branch if gates_passed else EvidenceValue(state=EvidenceStatus.NOT_APPLICABLE)
    target_graph = _topology(target_network)[0] if target_network is not None else None
    retained_cycle_rank = (target_graph.number_of_edges() - target_graph.number_of_nodes()
                           + nx.number_connected_components(target_graph)
                           if target_graph is not None else None)
    parallel_count = None
    if target_network is not None:
        corridors: dict[tuple[str, str], int] = {}
        for _, name, row, bus0, bus1 in _branch_endpoints(target_network):
            if _active(row) and row is not None and _ == "Line":
                edge = tuple(sorted((bus0, bus1)))
                corridors[edge] = corridors.get(edge, 0) + 1
        parallel_count = sum(value > 1 for value in corridors.values())
    rejection_histogram_value = (
        _available(histogram, "rejection categories") if histogram is not None else not_recorded())
    return OpportunitySection(
        protected_bus_count=_available(len(protected_buses), "unique buses")
            if protection is not None else not_recorded(),
        strict_singleton_bus_count=_available(len(protection.singleton_buses), "unique buses")
            if protection is not None else not_recorded(),
        anchor_bus_count=not_recorded(),
        radial_tree_candidate_count=_evidence_count(
            opportunity.radial_tree_opportunities if opportunity is not None else None),
        radial_tree_eligible_count=eligible_data("radial_tree_eligible"),
        radial_tree_rejected_count=eligible_data("radial_tree_rejected"),
        series_chain_candidate_count=_evidence_count(
            opportunity.series_node_opportunities if opportunity is not None else None),
        series_chain_eligible_count=eligible_data("series_chain_eligible"),
        series_chain_rejected_count=eligible_data("series_chain_rejected"),
        retained_cycle_rank=_evidence_count(retained_cycle_rank),
        retained_parallel_corridor_count=_evidence_count(parallel_count),
        eligible_count=eligible_data("eligible"),
        rejected_count=eligible_data("rejected"),
        rejection_histogram=rejection_histogram_value,
        minimum_cluster_count=_evidence_count(
            opportunity.minimum_required_bus_count if opportunity is not None else None),
        structural_compression_ceiling_bus_fraction=(
            _available(bus_ceil, "fraction") if bus_ceil is not None else not_recorded()),
        structural_compression_ceiling_line_fraction=not_recorded(),
        structural_compression_ceiling_total_branch_fraction=not_recorded(),
        planning_candidate_bus_fraction=(
            _available(candidate_bus, "fraction") if candidate_bus is not None else not_recorded()),
        planning_candidate_line_fraction=(
            _available(candidate_line, "fraction") if candidate_line is not None else not_recorded()),
        planning_candidate_total_branch_fraction=(
            _available(candidate_total_branch, "fraction")
            if candidate_total_branch is not None else not_recorded()),
        actual_achieved_bus_fraction=actual_bus,
        actual_achieved_line_fraction=actual_line,
        actual_achieved_total_branch_fraction=actual_branch,
        actual_accepted_bus_fraction=accepted_bus,
        actual_accepted_line_fraction=accepted_line,
        actual_accepted_total_branch_fraction=accepted_branch,
    )


def _mapping_protection_sets(protection: ProtectionSet | None) -> tuple[set[StableId], set[StableId]]:
    """Return explicit protected Bus and Line identities from protection sources."""
    if protection is None:
        return set(), set()
    buses = set(protection.terminal_buses) | set(protection.singleton_buses)
    line_ids = {item for item in protection.preserved_components if item.component == "Line"}
    return buses, line_ids


def _voltage_policies(
    network: pypsa.Network,
    sidecar: IdentitySidecar,
    policies: Sequence[VoltageCutoffPolicy],
) -> tuple[VoltagePolicyResolution, ...]:
    """Resolve cutoffs against exact v_nom layers and group equal layer sets."""
    resolved = [(policy, policy.resolve(network, sidecar)) for policy in policies]
    keys: dict[tuple[tuple[float, ...], tuple[float, ...]], list[str]] = {}
    for _, item in resolved:
        key = (item.retained_voltage_layers, item.compressible_voltage_layers)
        keys.setdefault(key, []).append(item.policy_id)
    output: list[VoltagePolicyResolution] = []
    for policy, item in resolved:
        key = (item.retained_voltage_layers, item.compressible_voltage_layers)
        equivalences = tuple(sorted(value for value in keys[key] if value != item.policy_id))
        layer_payload = {
            "source": list(item.source_voltage_layers),
            "retained": list(item.retained_voltage_layers),
            "compressible": list(item.compressible_voltage_layers),
        }
        fingerprint = hashlib.sha256(json.dumps(
            layer_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        output.append(VoltagePolicyResolution(
            policy_id=item.policy_id,
            requested_cutoff_kv=item.requested_cutoff_kv,
            source_voltage_layers_kv=item.source_voltage_layers,
            retained_voltage_layers_kv=item.retained_voltage_layers,
            compressible_voltage_layers_kv=item.compressible_voltage_layers,
            equivalent_policy_ids=equivalences,
            resolved_policy_fingerprint=fingerprint,
        ))
    return tuple(output)


def _layer_summaries(
    source_network: pypsa.Network,
    target_network: pypsa.Network | None,
    source_sidecar: IdentitySidecar,
    mappings: MappingBundle | None,
    policies: Sequence[VoltagePolicyResolution],
    protection: ProtectionSet | None,
    eligibility_by_layer: Mapping[float, Mapping[str, int]] | None,
) -> tuple[VoltageLayerSummary, ...]:
    """Build exact voltage-layer counts with explicit missing eligibility evidence."""
    source_voltage = _voltage_map(source_network)
    source_ids = _network_identity_ids(source_network, source_sidecar)
    source_bus_by_id = {source_sidecar.source_id("Bus", name): name for name in source_voltage}
    source_line_counts, _ = _layer_line_counts(source_network)
    topology_counts = _layer_topology(source_network)
    transformer_interfaces = _layer_transformer_interfaces(source_network)
    target_voltage = _voltage_map(target_network) if target_network is not None else None
    target_line_counts = _layer_line_counts(target_network)[0] if target_network is not None else {}
    preserved_buses: set[StableId] = set()
    preserved_lines: set[StableId] = set()
    if mappings is not None:
        preserved_buses = {row.source_id for row in mappings.bus_map
                           if row.source_id == row.target_id and row.mapping_type == "preserved"}
        preserved_lines = {row.source_id for row in mappings.line_map
                           if row.source_id == row.target_id and row.mapping_type == "preserved"}
    protected_buses, protected_lines = _mapping_protection_sets(protection)
    policy_rows: tuple[VoltagePolicyResolution | None, ...] = tuple(policies) or (None,)
    output: list[VoltageLayerSummary] = []
    for policy in policy_rows:
        retained_bus_ids: set[StableId] = set()
        retained_line_ids: set[StableId] = set()
        if policy is not None:
            resolved = next(item for item in policies if item.policy_id == policy.policy_id)
            # The policy contract resolves sidecar identities on the source network.
            original_policy = next(
                item for item in policies if item.policy_id == resolved.policy_id)
            del original_policy
            cutoff = policy.requested_cutoff_kv
            if cutoff is not None:
                retained_bus_ids = {
                    sid for sid, name in source_bus_by_id.items()
                    if source_voltage[name] >= cutoff
                }
                for name, row in source_network.lines.iterrows():
                    bus0, bus1 = str(row.bus0), str(row.bus1)
                    if (source_voltage[bus0] >= cutoff and source_voltage[bus1] >= cutoff
                            and source_voltage[bus0] == source_voltage[bus1]):
                        retained_line_ids.add(source_sidecar.source_id("Line", str(name)))
        for layer in sorted(set(source_voltage.values())):
            source_buses = {sid for sid, name in source_bus_by_id.items()
                            if source_voltage[name] == layer}
            source_bus_count = len(source_buses)
            source_line_count = source_line_counts[layer]
            target_bus_count = (sum(value == layer for value in target_voltage.values())
                                if target_voltage is not None else None)
            target_line_count = target_line_counts.get(layer) if target_network is not None else None
            radial, meshed = topology_counts[layer]
            eligible = (eligibility_by_layer or {}).get(layer, {})
            protected_bus_ids = (retained_bus_ids if policy is not None
                                 else protected_buses & source_buses)
            protected_line_layer = (retained_line_ids if policy is not None
                                    else protected_lines)
            protected_line_ids_layer = {
                sid for sid in protected_line_layer
                if sid in set(source_ids["Line"])
                and _source_line_layer(source_network, source_sidecar, sid) == layer
            }
            identity_bus_denominator = protected_bus_ids if policy is not None else source_buses
            bus_identity_fraction = (
                len(preserved_buses & identity_bus_denominator) / len(identity_bus_denominator)
                if mappings is not None and identity_bus_denominator else None)
            identity_line_denominator = (protected_line_ids_layer if policy is not None
                                         else {sid for sid in source_ids["Line"]
                                               if _source_line_layer(source_network, source_sidecar, sid) == layer})
            line_identity_fraction = (
                len(preserved_lines & identity_line_denominator) / len(identity_line_denominator)
                if mappings is not None and identity_line_denominator else None)
            output.append(VoltageLayerSummary(
                voltage_kv=layer,
                policy_id=policy.policy_id if policy is not None else None,
                source_bus_count=_available(source_bus_count, "count"),
                source_line_count=_available(source_line_count, "count"),
                retained_bus_count=_evidence_count(target_bus_count),
                reduced_bus_count=_evidence_count(
                    max(0, source_bus_count - target_bus_count)
                    if target_bus_count is not None else None),
                retained_line_count=_evidence_count(target_line_count),
                reduced_line_count=_evidence_count(
                    max(0, source_line_count - target_line_count)
                    if target_line_count is not None else None),
                protected_bus_count=_available(len(protected_bus_ids), "unique buses"),
                protected_line_count=_available(len(protected_line_ids_layer), "unique lines"),
                rejected_count=_evidence_count(eligible.get("rejected")),
                radial_structure_count=_available(radial, "connected components"),
                meshed_structure_count=_available(meshed, "connected components"),
                transformer_interface_count=_available(transformer_interfaces[layer], "terminals"),
                bus_compression_fraction=(
                    _fraction(source_bus_count - target_bus_count, source_bus_count)
                    if target_bus_count is not None else not_recorded()),
                line_compression_fraction=(
                    _fraction(source_line_count - target_line_count, source_line_count)
                    if target_line_count is not None else not_recorded()),
                retained_identity_coverage=(
                    _available(bus_identity_fraction, "fraction")
                    if bus_identity_fraction is not None else not_recorded()),
                retained_line_identity_coverage=(
                    _available(line_identity_fraction, "fraction")
                    if line_identity_fraction is not None else not_recorded()),
            ))
    return tuple(output)


def _source_line_layer(network: pypsa.Network, sidecar: IdentitySidecar,
                       source_id: StableId) -> float | None:
    """Return one Line's exact nominal-voltage layer when both ends agree."""
    name = sidecar.backend_name(source_id)
    if name not in network.lines.index:
        raise ComprehensiveReportError("Source Line identity is absent from source network")
    row = network.lines.loc[name]
    v0 = float(network.buses.at[row.bus0, "v_nom"])
    v1 = float(network.buses.at[row.bus1, "v_nom"])
    return v0 if v0 == v1 else None


def _structural_comparison(
    source_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    target_network: pypsa.Network | None,
    target_sidecar: IdentitySidecar | None,
    mappings: MappingBundle | None,
    comparison: CompressionComparisonReport | None,
    structural: MultiVoltageStructuralResult | None,
) -> StructuralComparison:
    """Create mapping, connectivity, cycle, and final status summaries."""
    source_counts = _component_counts(source_network)
    source_graph, cross_lines, cross_links, source_components = _topology(source_network)
    stage_rows = _stage_summaries(comparison)
    if target_network is None:
        target_inventory = NetworkInventory()
        target_components: int | None = None
        target_graph = None
        target_cross_lines = None
        target_cross_links = None
    else:
        target_inventory = _inventory(target_network)
        if comparison is not None:
            target_stage = comparison.stages.get("S4_FINAL_RELOADED")
            if target_stage is not None:
                adapted_target = _comparison_inventory(target_stage)
                target_inventory = target_inventory.model_copy(update={
                    "transformer_3w_physical_count": adapted_target.transformer_3w_physical_count,
                    "transformer_3w_t_leg_projection_count": adapted_target.transformer_3w_t_leg_projection_count,
                })
        target_graph, target_cross_lines, target_cross_links, target_components = _topology(target_network)
    mapping_summaries: tuple[ComponentMappingSummary, ...] = ()
    source_coverage = not_recorded()
    target_coverage = not_recorded()
    if mappings is not None and target_network is not None and target_sidecar is not None:
        mapping_summaries, source_coverage, target_coverage = _component_mapping_summaries(
            source_network, target_network, source_sidecar, target_sidecar, mappings)
    branches = _mapping_identity_rows(mappings)
    preserved_count = sum(row.mapping_type == "preserved" and row.source_id == row.target_id
                          for row in branches)
    absorbed_count = sum(row.mapping_type == "absorbed_internal" for row in branches)
    parallel_count = (comparison.parallel_corridors.get("ordinary_aggregated_parallel_corridors", 0)
                      if comparison is not None else None)
    source_branch_count = sum(source_counts[name] for name in _BRANCHES)
    target_branch_count = (
        sum(_component_counts(target_network)[name] for name in _BRANCHES)
        if target_network is not None else None)
    source_line_count = source_counts["Line"]
    target_line_count = len(target_network.lines) if target_network is not None else None
    cycle_checks: list[bool] = []
    if structural is not None:
        cycle_checks = [bool(value) for key, value in structural.checks.items()
                        if any(term in key.casefold()
                               for term in ("cycle", "ring", "parallel"))]
    cycle_status = (_available(all(cycle_checks), "boolean",
                                outcome=MetricOutcome.PASS if all(cycle_checks) else MetricOutcome.FAIL)
                    if cycle_checks else not_recorded())
    gate_status = (_available(structural.status, "status",
                               outcome=(MetricOutcome.PASS
                                        if structural.status.endswith("_VALID")
                                        else MetricOutcome.FAIL))
                   if structural is not None else not_recorded())
    return StructuralComparison(
        stages=stage_rows,
        target_inventory=target_inventory,
        component_mappings=mapping_summaries,
        absorbed_internal_branch_count=_available(absorbed_count, "source branches")
            if mappings is not None else not_recorded(),
        aggregated_parallel_corridor_count=_evidence_count(parallel_count),
        cross_voltage_line_count=_available(max(cross_lines, target_cross_lines or 0), "lines"),
        cross_voltage_link_count=_available(max(cross_links, target_cross_links or 0), "links"),
        preserved_identity_count=_available(preserved_count, "source objects")
            if mappings is not None else not_recorded(),
        mapping_coverage=source_coverage,
        source_connected_components=_available(source_components, "components"),
        target_connected_components=_evidence_count(target_components),
        protected_cycle_preservation=cycle_status,
        structural_gate_status=gate_status,
        line_compression_fraction=(
            _fraction(source_line_count - target_line_count, source_line_count)
            if target_line_count is not None else not_recorded()),
        total_branch_compression_fraction=(
            _fraction(source_branch_count - target_branch_count, source_branch_count)
            if target_branch_count is not None else not_recorded()),
    )


def _fidelity_section(
    groups: Sequence[FidelityGroupInput],
    source_pf: PowerFlowExecutionResult | None,
    candidate_pf: PowerFlowExecutionResult | None,
    solver_contract_id: str | None,
    solver_contract_digest: str | None,
) -> FidelitySection:
    """Project existing PF and metric evidence with no solver or admission call."""
    observations = tuple(
        row for item in groups for row in _metric_observations(item))
    keys = [(item.group, item.metric_name) for item in observations]
    if len(set(keys)) != len(keys):
        raise ComprehensiveReportError("Duplicate Fidelity metric group and name")
    threshold_digests = {item.evaluation.threshold_digest for item in groups
                         if item.evaluation.threshold_digest is not None}
    metric_digests = {item.evaluation.metrics_digest for item in groups
                      if item.evaluation.metrics_digest is not None}
    if len(threshold_digests) > 1:
        raise ComprehensiveReportError("Fidelity groups use different threshold digests")
    if len(metric_digests) > 1 and all(item.group is MetricGroup.WHOLE_NETWORK for item in groups):
        raise ComprehensiveReportError("Whole-network fidelity reports contain conflicting metric digests")
    whole = next((item for item in groups if item.group is MetricGroup.WHOLE_NETWORK), None)
    fidelity = (_available(
        whole.evaluation.fidelity_status.value, "status",
        outcome=(MetricOutcome.PASS if whole.evaluation.fidelity_status.value.endswith("WITHIN_TOLERANCE")
                 else MetricOutcome.FAIL)) if whole is not None else not_recorded())
    return FidelitySection(
        pf_source_status=_pf_status(source_pf),
        pf_candidate_status=_pf_status(candidate_pf),
        fidelity_status=fidelity,
        metric_digest=next(iter(metric_digests)) if len(metric_digests) == 1 else None,
        threshold_digest=next(iter(threshold_digests)) if threshold_digests else None,
        solver_contract_id=solver_contract_id,
        solver_contract_digest=solver_contract_digest,
        source_failure_code=(source_pf.status.value if source_pf is not None and not source_pf.converged else None),
        candidate_failure_code=(candidate_pf.status.value if candidate_pf is not None and not candidate_pf.converged else None),
        source_subnetworks=_subnetwork_observations(source_pf, "SOURCE"),
        candidate_subnetworks=_subnetwork_observations(candidate_pf, "CANDIDATE"),
        observations=observations,
    )


def _performance_section(
    timings: Mapping[str, float] | None,
    provenance: ReportProvenance,
) -> PerformanceSection:
    """Validate phase timings as nonnegative diagnostic-only observations."""
    timings = timings or {}
    unknown = set(timings) - set(_TIMING_FIELD_MAP)
    if unknown:
        raise ComprehensiveReportError("Unknown performance timing phase")
    values: dict[str, EvidenceValue] = {}
    for phase, field_name in _TIMING_FIELD_MAP.items():
        raw = timings.get(phase)
        if raw is None:
            values[field_name] = not_recorded()
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or raw < 0:
            raise ComprehensiveReportError("Runtime seconds must be finite and nonnegative")
        values[field_name] = _available(float(raw), "seconds",
                                        outcome=MetricOutcome.DIAGNOSTIC_ONLY)
    return PerformanceSection(**values, software_versions=dict(provenance.software_versions))


def build_comprehensive_run(
    *,
    provenance: ReportProvenance,
    source_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    target_network: pypsa.Network | None = None,
    target_sidecar: IdentitySidecar | None = None,
    mappings: MappingBundle | None = None,
    opportunity: CompressionOpportunityReport | None = None,
    comparison: CompressionComparisonReport | None = None,
    structural: MultiVoltageStructuralResult | None = None,
    protection: ProtectionSet | None = None,
    eligibility_counts: Mapping[str, int] | None = None,
    eligibility_by_layer: Mapping[float, Mapping[str, int]] | None = None,
    fidelity_groups: Sequence[FidelityGroupInput] = (),
    source_pf: PowerFlowExecutionResult | None = None,
    candidate_pf: PowerFlowExecutionResult | None = None,
    solver_contract_id: str | None = None,
    solver_contract_digest: str | None = None,
    timings: Mapping[str, float] | None = None,
    voltage_policies: Sequence[VoltageCutoffPolicy] = (),
    retained_cutoffs_kv: Sequence[float] = (500.0, 220.0, 100.0),
) -> ComprehensiveRunReport:
    """Build report sections from existing network, mapping, and PF evidence.

    The function is read-only. It never invokes PyPSA PF, recomputes existing
    thresholds, changes Gates, or turns a structural bound into an optimum.
    """
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           for value in retained_cutoffs_kv):
        raise ComprehensiveReportError("Retained voltage cutoffs cannot be boolean")
    cutoff_values = tuple(float(value) for value in retained_cutoffs_kv)
    if (any(not math.isfinite(value) or value <= 0.0 for value in cutoff_values)
            or len(set(cutoff_values)) != len(cutoff_values)):
        raise ComprehensiveReportError(
            "Retained voltage cutoffs must be unique, positive, and finite")
    if (provenance.source_model_sha256 is not None and comparison is not None
            and comparison.source_sha256 is not None
            and provenance.source_model_sha256 != comparison.source_sha256):
        raise ComprehensiveReportError("Source model digest differs from comparison report")
    if solver_contract_digest is not None and len(solver_contract_digest) != 64:
        raise ComprehensiveReportError("Solver contract digest must be SHA-256")
    if comparison is not None and mappings is not None:
        expected_mapping_digest = comparison.mappings.get("semantic_sha256")
        if (expected_mapping_digest is not None
                and expected_mapping_digest != mappings.semantic_digest()):
            raise ComprehensiveReportError("MappingBundle digest differs from comparison report")
    if target_network is not None and target_sidecar is None and mappings is not None:
        raise ComprehensiveReportError("Target sidecar is required to validate MappingBundle identities")
    source_inventory = _inventory(source_network)
    if comparison is not None:
        source_stage = comparison.stages.get("S1_PYPSA_FULL_PROJECTION")
        if source_stage is not None:
            adapted = _comparison_inventory(source_stage)
            source_inventory = source_inventory.model_copy(update={
                "transformer_3w_physical_count": adapted.transformer_3w_physical_count,
                "transformer_3w_t_leg_projection_count": adapted.transformer_3w_t_leg_projection_count,
            })
    comparison_structure = _structural_comparison(
        source_network, source_sidecar, target_network, target_sidecar,
        mappings, comparison, structural)
    policies = _voltage_policies(source_network, source_sidecar, voltage_policies)
    layers = _layer_summaries(
        source_network, target_network, source_sidecar, mappings, policies,
        protection, eligibility_by_layer)
    opportunity_section = _opportunity_section(
        source_network, target_network, opportunity, comparison, structural,
        protection, eligibility_counts)
    if target_network is not None and target_sidecar is not None:
        retained_fidelity = compare_retained_network_fidelity(
            source_network=source_network,
            target_network=target_network,
            source_sidecar=source_sidecar,
            target_sidecar=target_sidecar,
            mappings=mappings,
            source_pf=source_pf,
            target_pf=candidate_pf,
            cutoff_kv=retained_cutoffs_kv,
        )
    else:
        retained_fidelity = RetainedNetworkFidelityReport(
            groups=tuple(RetainedGroupComparison(group=group) for group in MetricGroup))
    return ComprehensiveRunReport(
        provenance=provenance,
        source_inventory=source_inventory,
        opportunity=opportunity_section,
        structural=comparison_structure,
        voltage_layers=layers,
        voltage_policies=policies,
        fidelity=_fidelity_section(
            fidelity_groups, source_pf, candidate_pf,
            solver_contract_id, solver_contract_digest),
        retained_network_fidelity=retained_fidelity,
        performance=_performance_section(timings, provenance),
    )
