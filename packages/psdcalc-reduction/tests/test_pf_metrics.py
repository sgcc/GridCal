from __future__ import annotations

import math

import numpy as np
import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.fidelity.contracts import (
    PowerFlowMetricPolicy,
    PowerFlowMetricThresholds,
    PowerFlowOperatingPointContract,
)
from psdcalc.reduction.fidelity.mapping import (
    PfBranchEndpoints,
    derive_pf_comparison_mapping,
)
from psdcalc.reduction.fidelity.metrics import (
    MetricError,
    align_component_angles,
    compare_power_flow_results,
    evaluate_metric_thresholds,
    relative_error,
    operating_point_digest,
    metric_policy_digest,
    summarize_errors,
    wrap_deg,
)
from psdcalc.reduction.fidelity.pypsa_executor import PypsaAcPowerFlowExecutor
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry


def _network(*, x: float = 10.0, slack_bus: str = "source",
             line_names: tuple[str, ...] = ("line",), snapshots: tuple[object, ...] | None = None
             ) -> tuple[pypsa.Network, object]:
    network = pypsa.Network() if not snapshots else pypsa.Network(snapshots=list(snapshots))
    for name in ("source", "load_bus"):
        network.add("Bus", name, v_nom=110.0)
    if slack_bus:
        network.add("Generator", "slack", bus=slack_bus, control="Slack",
                    p_set=0.0, q_set=0.0, p_nom=100.0, vm_pu=1.02)
    network.add("Load", "load", bus="load_bus", p_set=40.0, q_set=10.0)
    for line_name in line_names:
        network.add("Line", line_name, bus0="source", bus1="load_bus",
                    r=1.0 if len(line_names) == 1 else 2.0,
                    x=x if len(line_names) == 1 else 2.0 * x,
                    b=0.0, s_nom=100.0)
    return network, PypsaAdapter().identity_sidecar(network, "metrics")


def _mapping(source: pypsa.Network, source_sidecar: object,
             target: pypsa.Network, target_sidecar: object,
             *, line_rows: tuple[MapEntry, ...] | None = None,
             bus_targets: dict[str, str] | None = None):
    bus_targets = bus_targets or {name: name for name in source.buses.index}
    bus_rows = tuple(MapEntry(
        source_sidecar.source_id("Bus", name),
        target_sidecar.source_id("Bus", bus_targets[name]),
        "preserved" if name == bus_targets[name] else "clustered", "metric-test")
        for name in source.buses.index)
    if line_rows is None:
        line_rows = tuple(MapEntry(
            source_sidecar.source_id("Line", name),
            target_sidecar.source_id("Line", name), "preserved", "metric-test")
            for name in source.lines.index)
    source_branch_endpoints = tuple(PfBranchEndpoints(
        source_sidecar.source_id("Line", name), "Line",
        source_sidecar.source_id("Bus", row.bus0),
        source_sidecar.source_id("Bus", row.bus1))
        for name, row in source.lines.iterrows())
    target_branch_endpoints = tuple(PfBranchEndpoints(
        target_sidecar.source_id("Line", name), "Line",
        target_sidecar.source_id("Bus", row.bus0),
        target_sidecar.source_id("Bus", row.bus1))
        for name, row in target.lines.iterrows())
    source_ids = tuple(source_sidecar.source_id("Bus", name) for name in source.buses.index)
    target_ids = tuple(target_sidecar.source_id("Bus", name) for name in target.buses.index)
    source_components = {bus: "source-c0" for bus in source_ids}
    target_components = {bus: "target-c0" for bus in target_ids}
    mapping = derive_pf_comparison_mapping(
        MappingBundle(bus_map=bus_rows, branch_map=line_rows, device_map=()),
        source_bus_ids=source_ids, target_bus_ids=target_ids,
        source_branch_endpoints=source_branch_endpoints,
        target_branch_endpoints=target_branch_endpoints,
        source_component_by_bus=source_components,
        target_component_by_bus=target_components,
    )
    return mapping


def _solve(network: pypsa.Network, sidecar: object):
    contract = PowerFlowOperatingPointContract(
        snapshots=(network.snapshots[0],), solver="pypsa.newton_raphson",
        tolerance=1e-8, max_iterations=None, distribute_slack=False,
        slack_weights=None, use_seed=False,
        control_policy="preserve_source_controls",
        tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest="a" * 64, device_schedule_digest="b" * 64,
    )
    return PypsaAcPowerFlowExecutor().solve(network, sidecar, contract)


def _policy() -> PowerFlowMetricPolicy:
    return PowerFlowMetricPolicy(1e-6, 1e-6, 1e-6)


def test_scalar_error_distribution_freezes_linear_p95_definition() -> None:
    result = summarize_errors([0.0, 0.0, 0.0, 0.0], [1.0, 2.0, 3.0, 4.0])
    assert result.count == 4
    assert result.rmse == pytest.approx(math.sqrt(7.5))
    assert result.p95_abs == pytest.approx(3.85)
    assert result.max_abs == 4.0
    assert result.mean_abs == 2.5


def test_angle_wrapping_and_component_alignment_are_gauge_invariant() -> None:
    assert wrap_deg(179.0 - (-179.0)) == pytest.approx(-2.0)
    ids = [StableId("test", "Bus", str(i)) for i in range(3)]
    errors, offsets = align_component_angles(
        {ids[0]: 0.0, ids[1]: -2.0, ids[2]: -5.0},
        {ids[0]: 17.0, ids[1]: 15.0, ids[2]: 12.0},
        {ids[0]: (ids[0], "component-a"), ids[1]: (ids[1], "component-a"),
         ids[2]: (ids[2], "component-a")},
    )
    assert offsets["component-a"] == pytest.approx(-17.0)
    assert summarize_errors([0.0] * 3, list(errors.values())).max_abs == pytest.approx(0.0)


def test_zero_reference_relative_error_uses_finite_denominator_floor() -> None:
    assert relative_error(2.0, 0.0, denominator_floor=0.5) == pytest.approx(4.0)
    with pytest.raises(MetricError):
        relative_error(2.0, 0.0, denominator_floor=0.0)


def test_solver_contract_and_metric_policy_digests_are_deterministic() -> None:
    network, _ = _network()
    contract = PowerFlowOperatingPointContract(
        snapshots=(network.snapshots[0],), solver="pypsa.newton_raphson",
        tolerance=1e-8, max_iterations=None, distribute_slack=False,
        slack_weights=None, use_seed=False,
        control_policy="preserve_source_controls",
        tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest="a" * 64, device_schedule_digest="b" * 64)
    assert operating_point_digest(contract) == operating_point_digest(contract)
    assert metric_policy_digest(_policy()) == metric_policy_digest(_policy())


def test_identical_synthetic_networks_have_zero_metrics_and_bus_groups_are_separate() -> None:
    source, source_sidecar = _network()
    target, target_sidecar = _network()
    mapping = _mapping(source, source_sidecar, target, target_sidecar)
    compared = compare_power_flow_results(
        _solve(source, source_sidecar), _solve(target, target_sidecar), mapping, _policy())
    assert compared.metrics.bus_voltage.count == 2
    assert compared.metrics.bus_voltage_preserved.count == 2
    assert compared.metrics.bus_voltage_aggregated.count == 0
    assert compared.metrics.branch_p.max_abs == pytest.approx(0.0, abs=1e-9)
    assert compared.metrics.branch_q.max_abs == pytest.approx(0.0, abs=1e-9)
    assert compared.metrics.cutset_p.max_abs == pytest.approx(0.0, abs=1e-9)
    assert compared.metrics.cutset_q.max_abs == pytest.approx(0.0, abs=1e-9)
    assert compared.metrics.cluster_injection.p_mw.max_abs == pytest.approx(0.0, abs=1e-9)
    assert compared.metrics.passive_branch_losses.passive_branch_active_abs_diff_mw == pytest.approx(0.0, abs=1e-9)
    assert compared.metrics.cutset_p.count == 2
    assert compared.metrics.cutset_q.count == 2


def test_line_reactance_perturbation_produces_nonzero_voltage_and_flow_metrics() -> None:
    source, source_sidecar = _network()
    target, target_sidecar = _network(x=12.0)
    mapping = _mapping(source, source_sidecar, target, target_sidecar)
    compared = compare_power_flow_results(
        _solve(source, source_sidecar), _solve(target, target_sidecar), mapping, _policy())
    assert compared.metrics.bus_voltage.max_abs > 1e-7
    assert compared.metrics.branch_q.max_abs > 1e-5
    assert compared.metrics.cluster_injection.p_mw.max_abs > 1e-7
    assert compared.metrics.passive_branch_losses.passive_branch_active_abs_diff_mw > 0.0


def test_reversed_branch_orientation_swaps_both_terminal_ports() -> None:
    source, source_sidecar = _network()
    target, target_sidecar = _network()
    target.lines.loc["line", ["bus0", "bus1"]] = ["load_bus", "source"]
    row = MapEntry(source_sidecar.source_id("Line", "line"),
                   target_sidecar.source_id("Line", "line"), "preserved", "metric-test")
    mapping = _mapping(source, source_sidecar, target, target_sidecar, line_rows=(row,))
    assert mapping.branch_entries[0].orientation_sign == -1
    compared = compare_power_flow_results(
        _solve(source, source_sidecar), _solve(target, target_sidecar), mapping, _policy())
    assert compared.metrics.branch_p.max_abs == pytest.approx(0.0, abs=1e-8)
    assert compared.metrics.branch_q.max_abs == pytest.approx(0.0, abs=1e-8)
    assert compared.metrics.branch_p1.max_abs == pytest.approx(0.0, abs=1e-8)
    assert compared.metrics.branch_q1.max_abs == pytest.approx(0.0, abs=1e-8)


def test_parallel_source_members_compare_once_as_one_target_corridor() -> None:
    source, source_sidecar = _network(line_names=("line_a", "line_b"))
    target, target_sidecar = _network()
    source_lines = tuple(MapEntry(
        source_sidecar.source_id("Line", name),
        target_sidecar.source_id("Line", "line"), "aggregated", "metric-test")
        for name in ("line_a", "line_b"))
    mapping = _mapping(source, source_sidecar, target, target_sidecar, line_rows=source_lines)
    compared = compare_power_flow_results(
        _solve(source, source_sidecar), _solve(target, target_sidecar), mapping, _policy())
    assert compared.metrics.branch_p.count == 1
    assert compared.coverage.target_corridor_count == 1
    assert compared.metrics.branch_p.max_abs == pytest.approx(0.0, abs=1e-8)
    assert compared.metrics.branch_q.max_abs == pytest.approx(0.0, abs=1e-8)
    assert compared.metrics.branch_p1.max_abs == pytest.approx(0.0, abs=1e-8)
    assert compared.metrics.branch_q1.max_abs == pytest.approx(0.0, abs=1e-8)


def test_absorbed_internal_branch_is_excluded_and_cutsets_use_terminal_flows() -> None:
    source = pypsa.Network()
    for name in ("inside_a", "inside_b", "load_bus"):
        source.add("Bus", name, v_nom=110.0)
    source.add("Line", "internal", bus0="inside_a", bus1="inside_b",
               r=1.0, x=10.0, s_nom=100.0)
    source.add("Line", "boundary", bus0="inside_b", bus1="load_bus",
               r=1.0, x=10.0, s_nom=100.0)
    source.add("Generator", "slack", bus="inside_b", control="Slack",
               p_set=0.0, q_set=0.0, p_nom=100.0, vm_pu=1.02)
    source.add("Load", "load", bus="load_bus", p_set=20.0, q_set=5.0)
    source_sidecar = PypsaAdapter().identity_sidecar(source, "absorbed")

    target = pypsa.Network()
    target.add("Bus", "cluster", v_nom=110.0)
    target.add("Bus", "load_bus", v_nom=110.0)
    target.add("Line", "boundary_target", bus0="cluster", bus1="load_bus",
               r=1.0, x=10.0, s_nom=100.0)
    target.add("Generator", "slack", bus="cluster", control="Slack",
               p_set=0.0, q_set=0.0, p_nom=100.0, vm_pu=1.02)
    target.add("Load", "load", bus="load_bus", p_set=20.0, q_set=5.0)
    target_sidecar = PypsaAdapter().identity_sidecar(target, "absorbed")

    source_id = lambda component, name: source_sidecar.source_id(component, name)
    target_id = lambda component, name: target_sidecar.source_id(component, name)
    branch_rows = (
        MapEntry(source_id("Line", "internal"), None, "absorbed_internal", "metric-test"),
        MapEntry(source_id("Line", "boundary"), target_id("Line", "boundary_target"), "aggregated", "metric-test"),
    )
    mapping = _mapping(source, source_sidecar, target, target_sidecar,
                       line_rows=branch_rows,
                       bus_targets={"inside_a": "cluster", "inside_b": "cluster",
                                    "load_bus": "load_bus"})
    compared = compare_power_flow_results(
        _solve(source, source_sidecar), _solve(target, target_sidecar), mapping, _policy())
    assert compared.coverage.absorbed_internal_branch_count == 1
    assert compared.coverage.comparable_branch_count == 1
    assert compared.metrics.branch_p.count == 1
    assert compared.metrics.branch_q.count == 1
    assert compared.metrics.cutset_p.count == 2
    assert compared.metrics.cutset_q.count == 2
    assert compared.metrics.branch_p.max_abs == pytest.approx(0.0, abs=1e-8)
    assert compared.metrics.cluster_injection.p_mw.max_abs == pytest.approx(0.0, abs=1e-8)


def test_threshold_evaluation_is_separate_from_measurement() -> None:
    source, source_sidecar = _network()
    target, target_sidecar = _network(x=12.0)
    mapping = _mapping(source, source_sidecar, target, target_sidecar)
    metrics = compare_power_flow_results(
        _solve(source, source_sidecar), _solve(target, target_sidecar), mapping, _policy()).metrics
    assert not PowerFlowMetricThresholds().is_frozen
    loose = PowerFlowMetricThresholds(
        vm_max_pu=100.0, vm_rmse_pu=100.0, angle_max_deg=360.0,
        branch_p_max_mw=1000.0, branch_q_max_mvar=1000.0,
        cutset_p_max_mw=1000.0, cutset_q_max_mvar=1000.0,
        loss_abs_max_mw=100.0, loss_rel_max=100.0)
    strict = PowerFlowMetricThresholds(
        vm_max_pu=0.0, vm_rmse_pu=0.0, angle_max_deg=0.0,
        branch_p_max_mw=0.0, branch_q_max_mvar=0.0,
        cutset_p_max_mw=0.0, cutset_q_max_mvar=0.0,
        loss_abs_max_mw=0.0, loss_rel_max=0.0)
    assert evaluate_metric_thresholds(metrics, loose).value == "PF_FIDELITY_WITHIN_TOLERANCE"
    assert evaluate_metric_thresholds(metrics, strict).value == "PF_FIDELITY_OUTSIDE_TOLERANCE"


def test_metric_threshold_evaluator_rejects_unconfigured_thresholds() -> None:
    with pytest.raises(MetricError, match="not configured"):
        evaluate_metric_thresholds(None, PowerFlowMetricThresholds())
