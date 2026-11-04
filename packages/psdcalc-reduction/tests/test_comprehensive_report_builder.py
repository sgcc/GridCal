"""Synthetic tests for comprehensive report aggregation."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pypsa
import pytest


def test_comprehensive_report_builder_module_is_available():
    """The public builder is introduced in the implementation commit."""
    assert importlib.util.find_spec(
        "psdcalc.reduction.reporting.comprehensive_builder") is not None


def test_comprehensive_reporting_modules_import_on_supported_python():
    """Public report modules import on every supported Python version."""
    for module_name in (
        "psdcalc.reduction.reporting",
        "psdcalc.reduction.reporting.comprehensive_builder",
        "psdcalc.reduction.reporting.comprehensive_export",
        "psdcalc.reduction.reporting.retained_fidelity",
    ):
        assert importlib.import_module(module_name) is not None


def _three_layer_network() -> pypsa.Network:
    """Create exact 525/230/115 kV rings, parallel corridor and radial spur."""
    network = pypsa.Network()
    for name, voltage in (
        ("h0", 525.0), ("h1", 525.0), ("h2", 525.0),
        ("m0", 230.0), ("m1", 230.0),
        ("l0", 115.0), ("l1", 115.0), ("l2", 115.0),
    ):
        network.add("Bus", name, v_nom=voltage)
    for name, bus0, bus1 in (
        ("h01", "h0", "h1"), ("h12", "h1", "h2"), ("h20", "h2", "h0"),
        ("m01a", "m0", "m1"), ("m01b", "m0", "m1"),
        ("l01", "l0", "l1"), ("l12", "l1", "l2"),
    ):
        network.add("Line", name, bus0=bus0, bus1=bus1,
                    r=0.01, x=0.1, s_nom=100.0)
    network.add("Transformer", "t-hm", bus0="h0", bus1="m0",
                s_nom=100.0, r=0.01, x=0.1)
    network.add("Transformer", "t-ml", bus0="m1", bus1="l0",
                s_nom=100.0, r=0.01, x=0.1)
    network.add("Generator", "g-h", bus="h1", p_nom=50.0, control="PQ")
    network.add("Load", "d-l", bus="l2", p_set=20.0, q_set=4.0)
    network.add("ShuntImpedance", "s-l", bus="l1", g=0.0, b=0.001)
    return network


def _identity_mappings(network, sidecar):
    """Create stable-ID identity maps for supported source and target objects."""
    from psdcalc.reduction.mapping.bundle import MapEntry, MappingBundle

    def rows(component: str, names):
        return tuple(
            MapEntry(sidecar.source_id(component, str(name)),
                     sidecar.source_id(component, str(name)), "preserved", "identity")
            for name in names
        )

    return MappingBundle(
        bus_map=rows("Bus", network.buses.index),
        branch_map=(rows("Line", network.lines.index)
                    + rows("Transformer", network.transformers.index)),
        device_map=(rows("Generator", network.generators.index)
                    + rows("Load", network.loads.index)
                    + rows("ShuntImpedance", network.shunt_impedances.index)),
    )


def _provenance(run_id: str = "synthetic-run"):
    from psdcalc.reduction.reporting.comprehensive import ReportProvenance

    return ReportProvenance(study_id="synthetic-study", run_id=run_id,
                            package_version="0.8.0", pypsa_version=pypsa.__version__)


def test_builder_groups_exact_voltage_layers_and_reports_structure_without_pf():
    """Structure is reportable with no PF evidence and actual kV labels."""
    from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
    from psdcalc.reduction.reporting.comprehensive_builder import build_comprehensive_run

    source = _three_layer_network()
    sidecar = PypsaAdapter().identity_sidecar(source, "synthetic")
    mappings = _identity_mappings(source, sidecar)
    report = build_comprehensive_run(
        provenance=_provenance(), source_network=source, source_sidecar=sidecar,
        target_network=source, target_sidecar=sidecar, mappings=mappings,
    )
    assert report.source_inventory.voltage_layers_kv == (115.0, 230.0, 525.0)
    assert [row.voltage_kv for row in report.voltage_layers] == [115.0, 230.0, 525.0]
    assert report.source_inventory.line_count.value == 7
    assert report.source_inventory.transformer_count.value == 2
    assert report.source_inventory.total_branch_count.value == 9
    assert report.fidelity.observations == ()
    assert report.fidelity.fidelity_status.state.value == "NOT_RECORDED"


def test_cutoff_policies_group_by_actual_retained_layers():
    """Cutoffs selecting identical actual layers share a policy group."""
    from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
    from psdcalc.reduction.policies.voltage_cutoff import VoltageCutoffPolicy
    from psdcalc.reduction.reporting.comprehensive_builder import build_comprehensive_run

    source = _three_layer_network()
    sidecar = PypsaAdapter().identity_sidecar(source, "synthetic")
    report = build_comprehensive_run(
        provenance=_provenance(), source_network=source, source_sidecar=sidecar,
        voltage_policies=(
            VoltageCutoffPolicy(enabled=True, cutoff_kv=230.0),
            VoltageCutoffPolicy(enabled=True, cutoff_kv=200.0),
        ),
    )
    policy_a, policy_b = report.voltage_policies
    assert policy_a.retained_voltage_layers_kv == (230.0, 525.0)
    assert policy_b.retained_voltage_layers_kv == (230.0, 525.0)
    assert policy_a.equivalent_policy_ids == (policy_b.policy_id,)
    assert policy_b.equivalent_policy_ids == (policy_a.policy_id,)


def test_identity_map_reports_full_coverage_and_hashes_instead_of_ids():
    """Stable source identities are summarized by digest, never raw names."""
    from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
    from psdcalc.reduction.reporting.comprehensive_builder import build_comprehensive_run

    source = _three_layer_network()
    sidecar = PypsaAdapter().identity_sidecar(source, "synthetic-private")
    mappings = _identity_mappings(source, sidecar)
    report = build_comprehensive_run(
        provenance=_provenance(), source_network=source, source_sidecar=sidecar,
        target_network=source, target_sidecar=sidecar, mappings=mappings,
    )
    assert report.structural.mapping_coverage.value == 1.0
    assert report.structural.component_mappings
    assert "synthetic-private:Bus:h0" not in report.model_dump_json()


def test_target_mapping_identity_conflict_fails_closed():
    """A map row to a target object absent from the sidecar cannot be reported."""
    from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
    from psdcalc.reduction.core.ids import StableId
    from psdcalc.reduction.mapping.bundle import MapEntry, MappingBundle
    from psdcalc.reduction.reporting.comprehensive_builder import (
        ComprehensiveReportError,
        build_comprehensive_run,
    )

    source = _three_layer_network()
    source_sidecar = PypsaAdapter().identity_sidecar(source, "source")
    target_sidecar = PypsaAdapter().identity_sidecar(source, "target")
    good = _identity_mappings(source, source_sidecar)
    corrupted_bus_rows = list(good.bus_map)
    corrupted_bus_rows[0] = MapEntry(
        corrupted_bus_rows[0].source_id,
        StableId("target", "Bus", "missing"),
        "clustered", "corrupt",
    )
    bad = MappingBundle(tuple(corrupted_bus_rows), good.branch_map, good.device_map)
    with pytest.raises(ComprehensiveReportError, match="target identity"):
        build_comprehensive_run(
            provenance=_provenance(), source_network=source,
            source_sidecar=source_sidecar, target_network=source,
            target_sidecar=target_sidecar, mappings=bad,
        )


def test_existing_hard_and_diagnostic_metrics_project_without_changing_admission():
    """Report grouping copies existing outcomes, values, thresholds, and digests."""
    from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
    from psdcalc.reduction.fidelity.contracts import (
        PowerFlowErrorSummary,
        PowerFlowInjectionMetrics,
        PowerFlowLossMetrics,
        PowerFlowMetricSet,
        PowerFlowMetricThresholds,
    )
    from psdcalc.reduction.fidelity.metrics import evaluate_metric_thresholds_detailed
    from psdcalc.reduction.reporting.comprehensive import MetricGroup
    from psdcalc.reduction.reporting.comprehensive_builder import (
        FidelityGroupInput,
        build_comprehensive_run,
    )

    source = _three_layer_network()
    sidecar = PypsaAdapter().identity_sidecar(source, "synthetic-private")
    mappings = _identity_mappings(source, sidecar)
    summary = PowerFlowErrorSummary(1, 0.0, 0.0, 0.0, 0.0)
    metrics = PowerFlowMetricSet(
        bus_voltage=summary,
        angle=summary,
        branch_p=summary,
        branch_q=summary,
        cutset_p=summary,
        cutset_q=summary,
        cluster_injection=PowerFlowInjectionMetrics(summary, summary),
        passive_branch_losses=PowerFlowLossMetrics(0.0, 0.0, 0.0),
    )
    thresholds = PowerFlowMetricThresholds(
        vm_max_pu=0.0,
        vm_rmse_pu=0.0,
        cutset_p_max_mw=0.0,
        cutset_q_max_mvar=0.0,
        loss_rel_max=0.0,
    )
    evaluation = evaluate_metric_thresholds_detailed(metrics, thresholds)
    bus_metrics = {"vm_max_pu", "vm_rmse_pu", "vm_p95_pu", "angle_rmse_deg",
                   "angle_p95_deg", "angle_max_deg", "injection_p_rmse_mw",
                   "injection_p_max_mw", "injection_q_rmse_mvar", "injection_q_max_mvar"}
    line_metrics = {"cutset_p_max_mw", "cutset_q_max_mvar", "loss_rel_max",
                    "branch_p_rmse_mw", "branch_p_p95_mw", "branch_p_max_mw",
                    "branch_q_rmse_mvar", "branch_q_p95_mvar", "branch_q_max_mvar",
                    "cutset_p_rmse_mw", "cutset_q_rmse_mvar", "loss_abs_max_mw"}
    all_metrics = bus_metrics | line_metrics
    groups = tuple(FidelityGroupInput(
        group=group_name,
        metrics=metrics,
        evaluation=evaluation,
        resolved_thresholds=thresholds,
        source_ids_by_metric={
            name: tuple(sidecar.source_id("Bus", bus) for bus in source.buses.index)
            if name in bus_metrics else tuple(sidecar.source_id("Line", line) for line in source.lines.index)
            for name in all_metrics
        },
        target_ids_by_metric={
            name: tuple(sidecar.source_id("Bus", bus) for bus in source.buses.index)
            if name in bus_metrics else tuple(sidecar.source_id("Line", line) for line in source.lines.index)
            for name in all_metrics
        },
        mapping_coverage=1.0,
    ) for group_name in MetricGroup)
    report = build_comprehensive_run(
        provenance=_provenance(), source_network=source, source_sidecar=sidecar,
        target_network=source, target_sidecar=sidecar, mappings=mappings,
        fidelity_groups=groups,
    )
    assert len(report.fidelity.observations) == 88
    whole = [row for row in report.fidelity.observations
             if row.group is MetricGroup.WHOLE_NETWORK]
    hard = [row for row in whole if row.severity == "HARD"]
    diagnostic = [row for row in whole if row.severity == "DIAGNOSTIC"]
    assert len(hard) == 5
    assert len(diagnostic) == 17
    assert all(row.outcome.value == "PASS" for row in hard)
    assert all(row.outcome.value == "DIAGNOSTIC_ONLY" for row in diagnostic)
    assert next(row for row in hard if row.metric_name == "vm_max_pu").threshold.value == 0.0
    subset = [row for row in report.fidelity.observations
              if row.group is MetricGroup.RETAINED_BACKBONE]
    assert len(subset) == 22
    assert all(row.severity == "DIAGNOSTIC" for row in subset)
    assert all(row.outcome.value == "DIAGNOSTIC_ONLY" for row in subset)
    assert report.fidelity.fidelity_status.value == evaluation.fidelity_status.value


def test_fidelity_identity_binding_is_per_metric_not_one_mixed_identity_set():
    """Bus and branch fidelity metrics bind to their own stable identity sets."""
    from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
    from psdcalc.reduction.fidelity.contracts import (
        PowerFlowErrorSummary,
        PowerFlowInjectionMetrics,
        PowerFlowLossMetrics,
        PowerFlowMetricSet,
        PowerFlowMetricThresholds,
    )
    from psdcalc.reduction.fidelity.metrics import evaluate_metric_thresholds_detailed
    from psdcalc.reduction.reporting.comprehensive import MetricGroup
    from psdcalc.reduction.reporting.comprehensive_builder import (
        FidelityGroupInput,
        _identity_digest,
        _metric_observations,
    )

    network = _three_layer_network()
    sidecar = PypsaAdapter().identity_sidecar(network, "synthetic-private")
    buses = tuple(sidecar.source_id("Bus", name) for name in network.buses.index)
    lines = tuple(sidecar.source_id("Line", name) for name in network.lines.index)
    summary = PowerFlowErrorSummary(1, 0.0, 0.0, 0.0, 0.0)
    metrics = PowerFlowMetricSet(
        bus_voltage=summary,
        angle=summary,
        branch_p=summary,
        branch_q=summary,
        cutset_p=summary,
        cutset_q=summary,
        cluster_injection=PowerFlowInjectionMetrics(summary, summary),
        passive_branch_losses=PowerFlowLossMetrics(0.0, 0.0, 0.0),
    )
    thresholds = PowerFlowMetricThresholds(
        vm_max_pu=0.0, vm_rmse_pu=0.0, cutset_p_max_mw=0.0,
        cutset_q_max_mvar=0.0, loss_rel_max=0.0,
    )
    evaluation = evaluate_metric_thresholds_detailed(metrics, thresholds)
    input_data = FidelityGroupInput(
        group=MetricGroup.WHOLE_NETWORK,
        metrics=metrics,
        evaluation=evaluation,
        resolved_thresholds=thresholds,
        source_ids_by_metric={"vm_rmse_pu": buses, "branch_p_rmse_mw": lines},
        target_ids_by_metric={"vm_rmse_pu": buses, "branch_p_rmse_mw": lines},
    )

    observations = {row.metric_name: row for row in _metric_observations(input_data)}

    assert observations["vm_rmse_pu"].source_identity_digest == _identity_digest(buses)
    assert observations["branch_p_rmse_mw"].source_identity_digest == _identity_digest(lines)
    assert observations["vm_rmse_pu"].source_identity_digest != observations["branch_p_rmse_mw"].source_identity_digest
    assert observations["branch_q_rmse_mvar"].identity_binding_status.value == "NOT_RECORDED"


def test_pf_status_subnetworks_and_failure_code_are_copied_without_raw_exception():
    """PF observability reuses existing execution data and suppresses raw text."""
    from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
    from psdcalc.reduction.fidelity.pypsa_executor import (
        PowerFlowExecutionResult,
        PowerFlowExecutionStatus,
        PowerFlowSubnetworkStatus,
    )
    from psdcalc.reduction.reporting.comprehensive_builder import build_comprehensive_run

    source = _three_layer_network()
    sidecar = PypsaAdapter().identity_sidecar(source, "synthetic-private")
    source_pf = PowerFlowExecutionResult(
        status=PowerFlowExecutionStatus.PF_CONVERGED,
        converged=True,
        snapshot="snapshot-1",
        subnetwork_status=(
            PowerFlowSubnetworkStatus(0, True, 4, 1e-8),
            PowerFlowSubnetworkStatus(1, True, 3, 2e-8),
        ),
    )
    candidate_pf = PowerFlowExecutionResult(
        status=PowerFlowExecutionStatus.PF_NOT_CONVERGED,
        converged=False,
        snapshot="snapshot-1",
        subnetwork_status=(PowerFlowSubnetworkStatus(0, False, 20, 0.01),),
        failure_reason=f"private exception {Path('synthetic-model.dat').resolve()}",
    )
    report = build_comprehensive_run(
        provenance=_provenance(), source_network=source, source_sidecar=sidecar,
        source_pf=source_pf, candidate_pf=candidate_pf,
    )
    assert report.fidelity.pf_source_status.value == "PF_CONVERGED"
    assert report.fidelity.pf_candidate_status.value == "PF_NOT_CONVERGED"
    assert [(row.iterations, row.final_error)
            for row in report.fidelity.source_subnetworks] == [(4, 1e-8), (3, 2e-8)]
    assert report.fidelity.candidate_failure_code == "PF_NOT_CONVERGED"
    assert "private exception" not in report.model_dump_json()
    no_pf = build_comprehensive_run(
        provenance=_provenance("missing-pf"), source_network=source,
        source_sidecar=sidecar,
    )
    assert no_pf.fidelity.pf_source_status.state.value == "NOT_RECORDED"


def test_three_winding_physical_units_are_separate_from_t_leg_projection_rows():
    """Reported T-leg rows never masquerade as physical transformer counts."""
    from types import SimpleNamespace

    from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
    from psdcalc.reduction.metrics.stage_inventory import CompressionStage, StageInventory
    from psdcalc.reduction.reporting.comprehensive_builder import build_comprehensive_run

    source = _three_layer_network()
    sidecar = PypsaAdapter().identity_sidecar(source, "synthetic-private")
    stage_values = (
        (CompressionStage.PSD_BPA_SOURCE, len(source.buses), len(source.lines)),
        (CompressionStage.PYPSA_FULL_PROJECTION, len(source.buses), len(source.lines)),
        (CompressionStage.TOPOLOGY_SIMPLIFIED, len(source.buses), len(source.lines)),
        (CompressionStage.QUOTIENT_CANDIDATE, len(source.buses), len(source.lines)),
        (CompressionStage.FINAL_RELOADED, len(source.buses), len(source.lines)),
    )
    stages = {
        stage.value: StageInventory(
            stage=stage,
            bus_count=bus_count,
            line_count=line_count,
            transformer_count=len(source.transformers),
            source_transformer3w_group_count=1,
            source_transformer3w_leg_count=3,
        )
        for stage, bus_count, line_count in stage_values
    }
    comparison = SimpleNamespace(
        stages=stages,
        source_sha256=None,
        mappings={},
        parallel_corridors={},
        candidate_bus_compression_fraction=0.0,
        candidate_line_compression_fraction=0.0,
    )
    report = build_comprehensive_run(
        provenance=_provenance(), source_network=source, source_sidecar=sidecar,
        target_network=source, target_sidecar=sidecar,
        comparison=comparison,
    )
    assert report.source_inventory.transformer_3w_physical_count.value == 1
    assert report.source_inventory.transformer_3w_t_leg_projection_count.value == 3
    assert report.structural.target_inventory.transformer_3w_physical_count.value == 1
    assert report.structural.target_inventory.transformer_3w_t_leg_projection_count.value == 3
