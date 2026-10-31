from __future__ import annotations

import math

import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.fidelity.contracts import (
    PF_FIDELITY_SOURCE_STAGE,
    PF_FIDELITY_TARGET_STAGE,
    PfFidelityStatus,
    PfSolveStatus,
    PowerFlowCoverage,
    PowerFlowFidelityReport,
    PowerFlowOperatingPointContract,
)
from psdcalc.reduction.fidelity.pypsa_executor import (
    PowerFlowExecutionStatus,
    PypsaAcPowerFlowExecutor,
    structural_input_signature,
)


def _network(*, slack: bool = True, snapshots: list[object] | None = None) -> tuple[pypsa.Network, object]:
    network = pypsa.Network() if snapshots is None else pypsa.Network(snapshots=snapshots)
    network.add("Bus", "source", v_nom=110.0)
    network.add("Bus", "load_bus", v_nom=110.0)
    network.add("Line", "line", bus0="source", bus1="load_bus",
                r=1.0, x=10.0, b=0.0, s_nom=100.0)
    if slack:
        network.add("Generator", "slack", bus="source", control="Slack",
                    p_set=0.0, q_set=0.0, p_nom=100.0, vm_pu=1.02)
    network.add("Load", "load", bus="load_bus", p_set=40.0, q_set=10.0)
    return network, PypsaAdapter().identity_sidecar(network, "synthetic")


def _contract(network: pypsa.Network, *, max_iterations: int | None = None,
              solver: str = "pypsa.newton_raphson",
              snapshots: tuple[object, ...] | None = None) -> PowerFlowOperatingPointContract:
    return PowerFlowOperatingPointContract(
        snapshots=snapshots or (network.snapshots[0],),
        solver=solver,
        tolerance=1e-8,
        max_iterations=max_iterations,
        distribute_slack=False,
        slack_weights=None,
        use_seed=False,
        control_policy="preserve_source_controls",
        tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest="a" * 64,
        device_schedule_digest="b" * 64,
    )


def test_executor_solves_two_bus_ac_network_on_a_copy_and_returns_stable_results() -> None:
    network, sidecar = _network()
    source_signature = PypsaAdapter().topology_signature(network, sidecar)
    result = PypsaAcPowerFlowExecutor().solve(network, sidecar, _contract(network))

    assert result.status is PowerFlowExecutionStatus.PF_CONVERGED
    assert result.converged is True
    assert result.snapshot == network.snapshots[0]
    assert result.subnetwork_status
    assert set(result.bus_vm_pu) == {
        sidecar.source_id("Bus", "source"), sidecar.source_id("Bus", "load_bus")
    }
    assert set(result.branch_p0) == {sidecar.source_id("Line", "line")}
    assert set(result.branch_p1) == {sidecar.source_id("Line", "line")}
    for values in (result.bus_vm_pu, result.bus_va_deg, result.bus_p_mw,
                   result.bus_q_mvar, result.branch_p0, result.branch_q0,
                   result.branch_p1, result.branch_q1):
        assert all(math.isfinite(value) for value in values.values())
    assert PypsaAdapter().topology_signature(network, sidecar) == source_signature
    assert network.lines_t.p0.empty
    assert network.buses_t.v_ang.empty
    assert structural_input_signature(network) == structural_input_signature(network.copy())


def test_structural_signature_ignores_solved_output_attributes() -> None:
    network, _ = _network()
    before = structural_input_signature(network)
    snapshot = network.snapshots[0]
    network.buses_t.v_ang.loc[snapshot, "source"] = 0.25
    network.buses_t.p.loc[snapshot, "source"] = 123.0
    network.lines_t.p0.loc[snapshot, "line"] = 456.0
    assert structural_input_signature(network) == before


@pytest.mark.parametrize(
    "kwargs",
    [
        {"snapshots": ("s0", "s1")},
        {"snapshots": ("s0",)},
        {"max_iterations": 20},
        {"solver": "pypsa.linear"},
    ],
)
def test_executor_fails_closed_for_unsupported_scope_or_solver(kwargs: dict[str, object]) -> None:
    network, sidecar = _network(snapshots=["s0", "s1"] if "snapshots" in kwargs else None)
    contract_kwargs = dict(kwargs)
    if "snapshots" in contract_kwargs:
        contract_kwargs["snapshots"] = ("s0", "s1")
    result = PypsaAcPowerFlowExecutor().solve(
        network, sidecar, _contract(network, **contract_kwargs))
    assert result.status is PowerFlowExecutionStatus.PF_EXECUTION_UNSUPPORTED
    assert result.converged is False
    assert result.failure_reason


def test_network_without_slack_returns_nonconverged_without_metric_payload() -> None:
    network, sidecar = _network(slack=False)
    result = PypsaAcPowerFlowExecutor().solve(network, sidecar, _contract(network))
    assert result.status is PowerFlowExecutionStatus.PF_NOT_CONVERGED
    assert result.converged is False
    assert result.bus_vm_pu == {}
    assert result.branch_p0 == {}
    assert result.failure_reason
    assert len(result.subnetwork_status) == 1
    assert result.subnetwork_status[0].converged is False


def test_target_pf_failure_is_fidelity_failure_not_structural_invalidation() -> None:
    source_network, source_sidecar = _network()
    target_network, target_sidecar = _network(slack=False)
    executor = PypsaAcPowerFlowExecutor()
    source_result = executor.solve(source_network, source_sidecar, _contract(source_network))
    target_result = executor.solve(target_network, target_sidecar, _contract(target_network))
    assert source_result.status is PowerFlowExecutionStatus.PF_CONVERGED
    assert target_result.status is PowerFlowExecutionStatus.PF_NOT_CONVERGED

    report = PowerFlowFidelityReport(
        model_id="synthetic", run_id="target-failure",
        config_sha256="a" * 64, structural_report_digest="b" * 64,
        source_structural_report_digest="c" * 64, mapping_digest="d" * 64,
        solver_contract_digest="e" * 64, metric_policy_digest="f" * 64,
        s1_model_digest="1" * 64, s4_model_digest="2" * 64,
        source_stage=PF_FIDELITY_SOURCE_STAGE, target_stage=PF_FIDELITY_TARGET_STAGE,
        target_structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
        operating_point=_contract(source_network),
        source_pf_status=PfSolveStatus.CONVERGED,
        target_pf_status=PfSolveStatus.NOT_CONVERGED,
        bus_voltage_metrics=None, bus_voltage_preserved_metrics=None,
        bus_voltage_aggregated_metrics=None, angle_metrics=None,
        branch_p_metrics=None, branch_q_metrics=None,
        branch_p1_metrics=None, branch_q1_metrics=None,
        cutset_p_metrics=None, cutset_q_metrics=None,
        injection_metrics=None, loss_metrics=None,
        coverage=PowerFlowCoverage(2, 2, 1, 1, 0, 1, preserved_bus_count=2),
        fidelity_status=PfFidelityStatus.TARGET_PF_NOT_CONVERGED,
        failure_reason="target solver did not converge",
    )
    assert report.fidelity_status is PfFidelityStatus.TARGET_PF_NOT_CONVERGED
    assert report.target_structural_status == "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"
    assert report.metric_set is None


def test_executor_rejects_non_ac_bus_carriers() -> None:
    network, sidecar = _network()
    network.buses.loc["load_bus", "carrier"] = "DC"
    result = PypsaAcPowerFlowExecutor().solve(network, sidecar, _contract(network))
    assert result.status is PowerFlowExecutionStatus.PF_EXECUTION_UNSUPPORTED
    assert "only AC" in result.failure_reason


def test_executor_checks_every_connected_subnetwork() -> None:
    network = pypsa.Network()
    for name in ("a0", "a1", "b0", "b1"):
        network.add("Bus", name, v_nom=110.0)
    network.add("Line", "line_a", bus0="a0", bus1="a1", r=1.0, x=10.0, s_nom=100.0)
    network.add("Line", "line_b", bus0="b0", bus1="b1", r=1.0, x=10.0, s_nom=100.0)
    network.add("Generator", "slack_a", bus="a0", control="Slack", p_nom=100.0)
    network.add("Generator", "slack_b", bus="b0", control="Slack", p_nom=100.0)
    network.add("Load", "load_a", bus="a1", p_set=10.0, q_set=2.0)
    network.add("Load", "load_b", bus="b1", p_set=20.0, q_set=4.0)
    sidecar = PypsaAdapter().identity_sidecar(network, "two-island")
    result = PypsaAcPowerFlowExecutor().solve(network, sidecar, _contract(network))
    assert result.converged is True
    assert len(result.subnetwork_status) == 2
    assert all(item.converged for item in result.subnetwork_status)
