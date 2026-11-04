from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pypsa
import pytest

from experiment_tools.retained_pf_evidence import (
    RetainedPfEvidenceError,
    deserialize_power_flow_result,
    read_power_flow_result,
    serialize_power_flow_result,
    write_power_flow_result,
)
from psdcalc.reduction.adaptive.observations import (
    NumericObservationBundle,
    ObservationAvailability,
    PerformanceTimingObservation,
    power_flow_execution_observation,
)
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.fidelity.contracts import PowerFlowOperatingPointContract
from psdcalc.reduction.fidelity.pypsa_executor import (
    PowerFlowExecutionStatus,
    PypsaAcPowerFlowExecutor,
)


def _contract(network: pypsa.Network) -> PowerFlowOperatingPointContract:
    return PowerFlowOperatingPointContract(
        snapshots=(network.snapshots[0],),
        solver="pypsa.newton_raphson",
        tolerance=1e-8,
        max_iterations=None,
        distribute_slack=False,
        slack_weights=None,
        use_seed=False,
        control_policy="preserve_source_controls",
        tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest="a" * 64,
        device_schedule_digest="b" * 64,
    )


def _two_bus_network(*, slack: bool = True) -> tuple[pypsa.Network, object]:
    network = pypsa.Network()
    network.add("Bus", "source", v_nom=110.0)
    network.add("Bus", "load_bus", v_nom=110.0)
    network.add("Line", "line", bus0="source", bus1="load_bus",
                r=1.0, x=10.0, b=0.0, s_nom=100.0)
    if slack:
        network.add("Generator", "slack", bus="source", control="Slack",
                    p_set=0.0, q_set=0.0, p_nom=100.0, vm_pu=1.02)
    network.add("Load", "load", bus="load_bus", p_set=40.0, q_set=10.0)
    return network, PypsaAdapter().identity_sidecar(network, "synthetic-pf-driver")


def _two_island_network() -> tuple[pypsa.Network, object]:
    network = pypsa.Network()
    for name in ("a0", "a1", "b0", "b1"):
        network.add("Bus", name, v_nom=110.0)
    for label, source, target, p, q in (
        ("a", "a0", "a1", 10.0, 2.0),
        ("b", "b0", "b1", 20.0, 4.0),
    ):
        network.add("Line", f"line_{label}", bus0=source, bus1=target,
                    r=1.0, x=10.0, s_nom=100.0)
        network.add("Generator", f"slack_{label}", bus=source,
                    control="Slack", p_nom=100.0)
        network.add("Load", f"load_{label}", bus=target, p_set=p, q_set=q)
    return network, PypsaAdapter().identity_sidecar(network, "synthetic-two-island")


def _observation(result: object, *, role: str, candidate: bool = False) -> object:
    return power_flow_execution_observation(
        result,
        observation_id=("2" if candidate else "1") * 64,
        role="initial_candidate" if candidate else role,
        source_model_digest="a" * 64,
        candidate_model_digest="b" * 64 if candidate else None,
        candidate_identity="c" * 64 if candidate else None,
        source_pf_status=PowerFlowExecutionStatus.PF_CONVERGED.value if candidate else None,
        solver_contract_digest="d" * 64,
        operating_point_digest="e" * 64,
        solver_contract_summary={"solver": "pypsa.newton_raphson"},
    )


def test_actual_synthetic_source_and_candidate_pf_roundtrip_through_json(tmp_path: Path) -> None:
    network, sidecar = _two_bus_network()
    executor = PypsaAcPowerFlowExecutor()
    source_calls: list[tuple[float, str]] = []
    candidate_calls: list[tuple[float, str]] = []
    source_result = executor.solve(
        network, sidecar, _contract(network),
        timing_observer=lambda seconds, status: source_calls.append((seconds, status)),
    )
    candidate_result = executor.solve(
        network.copy(), sidecar, _contract(network),
        timing_observer=lambda seconds, status: candidate_calls.append((seconds, status)),
    )
    assert source_result.status is PowerFlowExecutionStatus.PF_CONVERGED
    assert candidate_result.status is PowerFlowExecutionStatus.PF_CONVERGED
    assert len(source_calls) == len(candidate_calls) == 1
    direct_path = tmp_path / "direct-source-result.json"
    direct_digest = write_power_flow_result(direct_path, source_result)
    assert read_power_flow_result(direct_path) == source_result
    assert direct_digest == serialize_power_flow_result(source_result)["result_sha256"]

    observations = NumericObservationBundle(
        run_id="synthetic-run",
        numeric_observations=(
            _observation(source_result, role="source"),
            _observation(candidate_result, role="source", candidate=True),
        ),
        performance_timing_observations=(
            PerformanceTimingObservation(
                operation_name="source_pf_solver", role="source", duration=source_calls[0][0],
                duration_unit="s", timing_scope="solver_only", run_id="synthetic-run",
                process_identity="synthetic-test", backend="PyPSA",
                backend_version=pypsa.__version__, operation_status=source_calls[0][1],
            ),
            PerformanceTimingObservation(
                operation_name="candidate_pf_solver", role="initial_candidate",
                duration=candidate_calls[0][0], duration_unit="s", timing_scope="solver_only",
                run_id="synthetic-run", process_identity="synthetic-test", backend="PyPSA",
                backend_version=pypsa.__version__, operation_status=candidate_calls[0][1],
            ),
        ),
        adaptive_evaluations=(),
    )
    document = {
        "source_result": serialize_power_flow_result(source_result),
        "candidate_result": serialize_power_flow_result(candidate_result),
        "numeric_observations": observations.to_payload(),
    }
    path = tmp_path / "synthetic-pf-evidence.json"
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False))
    loaded = json.loads(path.read_text())

    source_loaded = deserialize_power_flow_result(loaded["source_result"])
    candidate_loaded = deserialize_power_flow_result(loaded["candidate_result"])
    bundle_loaded = NumericObservationBundle.from_payload(loaded["numeric_observations"])
    assert source_loaded.status is source_result.status
    assert candidate_loaded.status is candidate_result.status
    for before, after in ((source_result, source_loaded), (candidate_result, candidate_loaded)):
        assert after.converged is before.converged
        assert len(after.subnetwork_status) == 1
        row = after.subnetwork_status[0]
        assert row.subnetwork_ordinal == 0
        assert isinstance(row.iterations, int)
        assert isinstance(row.error, float)
        assert row.iterations == before.subnetwork_status[0].iterations
        assert row.error == before.subnetwork_status[0].error
        assert after.bus_vm_pu == before.bus_vm_pu
        assert after.branch_p0 == before.branch_p0
    source_observation, candidate_observation = bundle_loaded.numeric_observations
    assert source_observation.role == "source"
    assert candidate_observation.role == "initial_candidate"
    assert source_observation.pf_status == source_loaded.status.value
    assert candidate_observation.pf_status == candidate_loaded.status.value
    assert source_observation.subnetwork_observations[0].subnetwork_ordinal == 0
    assert candidate_observation.subnetwork_observations[0].subnetwork_ordinal == 0
    for result, observation in (
        (source_loaded, source_observation), (candidate_loaded, candidate_observation),
    ):
        assert len(result.subnetwork_status) == len(observation.subnetwork_observations)
        for status, numeric in zip(
            result.subnetwork_status, observation.subnetwork_observations, strict=True,
        ):
            assert numeric.subnetwork_ordinal == status.subnetwork_ordinal
            assert numeric.converged is status.converged
            assert numeric.iterations.value == status.iterations
            assert numeric.final_mismatch.value == status.error

    # A fresh interpreter reloads the exact durable payload and verifies both digests.
    package_root = Path(__file__).resolve().parents[1]
    source_root = package_root / "src"
    child_code = """
import json, sys
from pathlib import Path
import psdcalc
from experiment_tools.retained_pf_evidence import deserialize_power_flow_result
from psdcalc.reduction.adaptive.observations import NumericObservationBundle
path = Path(sys.argv[1])
doc = json.loads(path.read_text())
source = deserialize_power_flow_result(doc['source_result'])
candidate = deserialize_power_flow_result(doc['candidate_result'])
bundle = NumericObservationBundle.from_payload(doc['numeric_observations'])
assert Path(psdcalc.__file__).resolve().is_relative_to(Path(sys.argv[2]).resolve())
assert source.subnetwork_status[0].subnetwork_ordinal == 0
assert candidate.subnetwork_status[0].subnetwork_ordinal == 0
assert len(bundle.numeric_observations) == 2
print(source.status.value, candidate.status.value, len(bundle.numeric_observations))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(source_root), str(package_root), env.get("PYTHONPATH", "")))
    child = subprocess.run(
        [sys.executable, "-c", child_code, str(path), str(source_root)],
        cwd=package_root, env=env, capture_output=True, text=True, check=False,
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "PF_CONVERGED PF_CONVERGED 2"


def test_actual_multisubnetwork_pf_preserves_ordinals_and_none_values() -> None:
    network, sidecar = _two_island_network()
    result = PypsaAcPowerFlowExecutor().solve(network, sidecar, _contract(network))
    payload = serialize_power_flow_result(result)
    loaded = deserialize_power_flow_result(payload)
    assert loaded.status is PowerFlowExecutionStatus.PF_CONVERGED
    assert [row.subnetwork_ordinal for row in loaded.subnetwork_status] == [0, 1]
    assert all(isinstance(row.iterations, int) for row in loaded.subnetwork_status)
    assert all(isinstance(row.error, float) for row in loaded.subnetwork_status)


def test_nonconverged_result_keeps_none_and_not_recorded_distinct() -> None:
    network, sidecar = _two_bus_network(slack=False)
    result = PypsaAcPowerFlowExecutor().solve(network, sidecar, _contract(network))
    loaded = deserialize_power_flow_result(serialize_power_flow_result(result))
    assert loaded.status is PowerFlowExecutionStatus.PF_NOT_CONVERGED
    assert not loaded.converged
    assert loaded.failure_reason
    assert len(loaded.subnetwork_status) == 1
    row = loaded.subnetwork_status[0]
    assert row.subnetwork_ordinal == 0
    assert row.iterations is None
    assert row.error is None
    observation = _observation(loaded, role="source")
    row_observation = observation.subnetwork_observations[0]
    assert row_observation.iterations.value is None
    assert row_observation.iterations.availability is ObservationAvailability.NOT_RECORDED
    assert row_observation.final_mismatch.value is None
    assert row_observation.final_mismatch.availability is ObservationAvailability.NOT_RECORDED


def test_serializer_rejects_old_or_missing_fields_and_nonfinite_values() -> None:
    network, sidecar = _two_bus_network()
    result = PypsaAcPowerFlowExecutor().solve(network, sidecar, _contract(network))
    payload = serialize_power_flow_result(result)

    old_field = json.loads(json.dumps(payload))
    row = old_field["subnetwork_status"][0]
    row["ordinal"] = row.pop("subnetwork_ordinal")
    with pytest.raises(RetainedPfEvidenceError):
        deserialize_power_flow_result(old_field)

    missing = json.loads(json.dumps(payload))
    del missing["subnetwork_status"][0]["iterations"]
    with pytest.raises(RetainedPfEvidenceError):
        deserialize_power_flow_result(missing)

    nonfinite = json.loads(json.dumps(payload))
    nonfinite["subnetwork_status"][0]["error"] = math.inf
    with pytest.raises(RetainedPfEvidenceError):
        deserialize_power_flow_result(nonfinite)

    tampered = json.loads(json.dumps(payload))
    tampered["converged"] = False
    with pytest.raises(RetainedPfEvidenceError):
        deserialize_power_flow_result(tampered)


def test_timing_changes_do_not_change_numeric_observation_digest() -> None:
    network, sidecar = _two_bus_network()
    result = PypsaAcPowerFlowExecutor().solve(network, sidecar, _contract(network))
    observation = _observation(result, role="source")

    def bundle(duration: float) -> NumericObservationBundle:
        timing = PerformanceTimingObservation(
            operation_name="source_pf_solver", role="source", duration=duration,
            duration_unit="s", timing_scope="solver_only", run_id="timing-run",
            process_identity="synthetic-test", backend="PyPSA",
            backend_version=pypsa.__version__,
        )
        return NumericObservationBundle("timing-run", (observation,), (timing,), ())

    first = bundle(0.1).to_payload()
    second = bundle(0.2).to_payload()
    assert first["numeric_observations_digest"] == second["numeric_observations_digest"]
    assert first["timing_observations_digest"] != second["timing_observations_digest"]
