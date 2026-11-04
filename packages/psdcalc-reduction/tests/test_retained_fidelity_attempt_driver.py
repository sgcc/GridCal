"""Synthetic execution-driver tests; no private grid inputs are used."""

from __future__ import annotations

import json
import hashlib
import importlib.metadata as metadata
import subprocess
import sys
from pathlib import Path

import pytest
import pandas as pd
import pypsa
import yaml

from experiment_tools.retained_fidelity_driver import (
    FrozenAttemptBindings, FrozenPolicyRun, RetainedAttemptError,
    _effective_digest, run_frozen_policies, verify_policy_artifacts,
)
from experiment_tools.retained_fidelity_attempt_cli import (
    execute_from_frozen_inputs, prepare_policy_runs,
)
from experiment_tools.retained_fidelity_outputs import (
    RetainedOutputError, verify_cross_policy_report,
)
from psdcalc.reduction.adaptive.candidates import _semantic_config_digest, _sidecar_digest
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import ReductionConfig
from psdcalc.reduction.fidelity.persistence import canonical_model_digest
from psdcalc.reduction.fidelity.persistence import FrozenThresholdSet
from psdcalc.reduction.fidelity.metrics import metric_policy_digest, operating_point_digest
from psdcalc.reduction.fidelity.pypsa_executor import (
    PowerFlowExecutionResult, PowerFlowExecutionStatus,
    PowerFlowSubnetworkStatus, PypsaAcPowerFlowExecutor,
)
from psdcalc.reduction.fidelity.contracts import (
    PowerFlowOperatingPointContract, PowerFlowMetricPolicy,
)
from psdcalc.reduction.policies.voltage_cutoff import VoltageCutoffPolicy
from psdcalc.reduction.protection.effective import source_ledger_digest
from psdcalc.reduction.topology.parallel_provenance import (
    ParallelSourceLedger, SourceProfileContract,
)
from test_three_voltage_pipeline import pipeline_config, three_voltage_source


def _inputs(tmp_path: Path):
    source = three_voltage_source()
    source.set_snapshots(source.snapshots[:1])
    source_path = tmp_path / "frozen-s1.nc"
    source.export_to_netcdf(source_path)
    sidecar = PypsaAdapter().identity_sidecar(source, "n")
    ledger = ParallelSourceLedger(SourceProfileContract("programmatic_pypsa", "unavailable"))
    policies = []
    for name, cutoff in (("BACKBONE_500", 500.0), ("BACKBONE_100", 100.0),
                         ("BACKBONE_30", 30.0)):
        base = pipeline_config(source_path, run_id="synthetic-retained", n_clusters=8)
        config: ReductionConfig = base.model_copy(update={
            "voltage_policy": base.voltage_policy.model_copy(update={
                "enabled": True, "mode": "freeze_at_or_above", "cutoff_kv": cutoff,
            }),
        })
        resolved = VoltageCutoffPolicy(True, "freeze_at_or_above", cutoff).resolve(
            source, sidecar)
        policies.append(FrozenPolicyRun(
            name, config, resolved.resolved_policy_fingerprint,
            _semantic_config_digest(config), _effective_digest(source, sidecar, ledger, config)))
    bindings = FrozenAttemptBindings(
        experiment_id="SYNTHETIC_RETAINED", attempt_id="ATTEMPT_003",
        source_model_digest=canonical_model_digest(source, sidecar),
        source_sidecar_digest=_sidecar_digest(sidecar),
        source_ledger_digest=source_ledger_digest(ledger),
    )
    return source, source_path, sidecar, ledger, tuple(policies), bindings


def test_three_policies_rebuild_independently_and_ignore_historical_candidate(
    tmp_path: Path,
) -> None:
    """Each policy runs a fresh pipeline from one immutable S1 and gets its own maps."""
    source, source_path, sidecar, ledger, policies, bindings = _inputs(tmp_path)
    decoy = tmp_path / "runs" / "attempt-001" / "levels"
    decoy.mkdir(parents=True)
    (decoy / "L2.nc").write_bytes(b"historical decoy is never an input")
    results = run_frozen_policies(
        source_network=source, source_path=source_path, source_sidecar=sidecar,
        source_ledger=ledger, policies=policies, bindings=bindings,
        output_root=tmp_path / "runs",
    )
    assert len(results) == 3
    assert len({result.candidate_model_digest for result in results}) >= 1
    assert len({result.candidate_identity for result in results}) == 3
    assert len({result.mapping_digest for result in results}) >= 1
    assert all(result.structural_status == "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"
               for result in results)
    assert all(result.ac_readiness_status == "AC_MODEL_READY" for result in results)
    assert all(verify_policy_artifacts(result.run_dir).candidate_identity ==
               result.candidate_identity for result in results)
    assert canonical_model_digest(source, sidecar) == bindings.source_model_digest
    assert (decoy / "L2.nc").read_bytes().startswith(b"historical decoy")
    structural = results[0].run_dir / "validation/multi_voltage_structural.json"
    structural.write_bytes(structural.read_bytes() + b" \n")
    with pytest.raises(RetainedAttemptError, match="CANDIDATE_PROVENANCE_INVALID"):
        verify_policy_artifacts(results[0].run_dir)


def test_source_identity_mismatch_fails_before_candidate_build(tmp_path: Path) -> None:
    """A drifted frozen S1 cannot be used for a new candidate."""
    source, source_path, sidecar, ledger, policies, bindings = _inputs(tmp_path)
    bad = FrozenAttemptBindings(
        bindings.experiment_id, bindings.attempt_id, "0" * 64,
        bindings.source_sidecar_digest, bindings.source_ledger_digest,
    )
    with pytest.raises(RetainedAttemptError, match="CANDIDATE_PROVENANCE_INVALID"):
        run_frozen_policies(
            source_network=source, source_path=source_path, source_sidecar=sidecar,
            source_ledger=ledger, policies=policies, bindings=bad,
            output_root=tmp_path / "runs",
        )
    assert not (tmp_path / "runs" / policies[0].config.run.id).exists()


def test_fresh_process_artifact_verification_detects_mapping_tamper(tmp_path: Path) -> None:
    """An independent interpreter verifies durable candidate provenance."""
    source, source_path, sidecar, ledger, policies, bindings = _inputs(tmp_path)
    result = run_frozen_policies(
        source_network=source, source_path=source_path, source_sidecar=sidecar,
        source_ledger=ledger, policies=policies[:1], bindings=bindings,
        output_root=tmp_path / "runs",
    )[0]
    script = (
        "from pathlib import Path; "
        "from experiment_tools.retained_fidelity_driver import verify_policy_artifacts; "
        f"print(verify_policy_artifacts(Path({str(result.run_dir)!r})).candidate_identity)"
    )
    completed = subprocess.run([sys.executable, "-c", script], check=True,
                               capture_output=True, text=True)
    assert completed.stdout.strip() == result.candidate_identity
    path = result.run_dir / "validation" / "candidate_provenance.json"
    payload = json.loads(path.read_text())
    payload["mapping_digest"] = "0" * 64
    path.write_text(json.dumps(payload))
    with pytest.raises(RetainedAttemptError, match="CANDIDATE_PROVENANCE_INVALID"):
        verify_policy_artifacts(result.run_dir)


def _pf_source() -> pypsa.Network:
    """Build one convergent three-voltage AC source for driver tests."""
    network = pypsa.Network()
    network.set_snapshots(pd.DatetimeIndex(["2030-01-01"]))
    for name, voltage in (("a", 110), ("b", 110), ("c", 35),
                          ("d", 35), ("x", 35), ("y", 35),
                          ("e", 10), ("f", 10)):
        network.add("Bus", name, v_nom=voltage)
    for name, left, right in (("ab", "a", "b"), ("cx", "c", "x"),
                              ("xy", "x", "y"), ("yd", "y", "d"),
                              ("ef", "e", "f")):
        network.add("Line", name, bus0=left, bus1=right, r=0.01, x=0.1, s_nom=100)
    network.add("Transformer", "t1", bus0="b", bus1="c", r=0.01, x=0.1, s_nom=100)
    network.add("Transformer", "t2", bus0="d", bus1="e", r=0.01, x=0.1, s_nom=100)
    network.add("Generator", "slack", bus="a", control="Slack", p_nom=100)
    network.add("Generator", "pv", bus="f", control="PV", p_nom=20)
    network.add("Load", "demand", bus="f", p_set=5, q_set=1)
    network.add("Load", "middle-x", bus="x", p_set=1, q_set=0.2)
    network.add("Load", "middle-y", bus="y", p_set=1, q_set=0.2)
    return network


def test_actual_synthetic_pf_is_bound_to_new_candidate_and_reloaded(tmp_path: Path) -> None:
    """Run real PyPSA PF, serialize both roles, and verify identity after reload."""
    network = _pf_source()
    source_path = tmp_path / "frozen-s1.nc"
    network.export_to_netcdf(source_path)
    sidecar = PypsaAdapter().identity_sidecar(network, "n")
    ledger = ParallelSourceLedger(SourceProfileContract("programmatic_pypsa", "unavailable"))
    base = pipeline_config(source_path, run_id="new-500", n_clusters=8)
    config = base.model_copy(update={"voltage_policy": base.voltage_policy.model_copy(
        update={"enabled": True, "mode": "freeze_at_or_above", "cutoff_kv": 500.0})})
    fingerprint = VoltageCutoffPolicy(True, "freeze_at_or_above", 500.0).resolve(
        network, sidecar).resolved_policy_fingerprint
    binding = FrozenAttemptBindings(
        "SYNTHETIC_RETAINED", "ATTEMPT_003", canonical_model_digest(network, sidecar),
        _sidecar_digest(sidecar), source_ledger_digest(ledger))
    contract = PowerFlowOperatingPointContract(
        snapshots=tuple(network.snapshots), solver="pypsa.newton_raphson",
        tolerance=1e-8, max_iterations=None, distribute_slack=False,
        slack_weights=None, use_seed=False,
        control_policy="preserve_source_controls", tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest="a" * 64, device_schedule_digest="b" * 64,
    )
    threshold = FrozenThresholdSet.load()
    metric = PowerFlowMetricPolicy(**threshold.payload["metric_policy"])
    result = run_frozen_policies(
        source_network=network, source_path=source_path, source_sidecar=sidecar,
        source_ledger=ledger, policies=(FrozenPolicyRun(
            "BACKBONE_500", config, fingerprint, _semantic_config_digest(config),
            _effective_digest(network, sidecar, ledger, config)),), bindings=binding,
        output_root=tmp_path / "runs", pf_contract=contract,
        metric_policy=metric, threshold_set=threshold,
    )[0]
    assert result.source_pf_status == "PF_CONVERGED"
    assert result.candidate_pf_status == "PF_CONVERGED"
    assert result.fidelity_status is not None
    assert verify_policy_artifacts(result.run_dir) == result
    assert (result.run_dir / "pf/source.json").is_file()
    assert (result.run_dir / "pf/candidate.json").is_file()
    assert (result.run_dir / "metrics/retained_fidelity.json").is_file()
    for name in ("numeric_observations.json", "comprehensive.json",
                 "comprehensive.md", "comprehensive-metrics.csv",
                 "comprehensive-voltage-layers.csv",
                 "comprehensive-retained-fidelity.csv", "stage_timings.json"):
        assert (result.run_dir / "metrics" / name).is_file()
    assert (result.run_dir.parent / "cross_policy" / "comprehensive.json").is_file()
    observations = json.loads((result.run_dir / "metrics/numeric_observations.json").read_text())
    assert len(observations["numeric_observations"]) == 3
    assert observations["adaptive_evaluations"] == []
    assert observations["performance_timing_observations"]
    timing = json.loads((result.run_dir / "metrics/stage_timings.json").read_text())
    assert timing["timings_seconds"]["source_pf_solver"] >= 0
    assert timing["timings_seconds"]["candidate_pf_solver"] >= 0
    assert timing["stage_status"]["structural_gate"] == "NOT_RECORDED"
    assert timing["stage_status"]["fresh_replay"] == "RECORDED"
    assert timing["timings_seconds"]["fresh_replay"] >= 0
    binding = json.loads((result.run_dir / "pf/binding.json").read_text())
    source_result = json.loads((result.run_dir / "pf/source.json").read_text())
    candidate_result = json.loads((result.run_dir / "pf/candidate.json").read_text())
    assert binding["source_result_sha256"] == source_result["result_sha256"]
    assert binding["candidate_result_sha256"] == candidate_result["result_sha256"]
    assert source_result["subnetwork_status"][0]["subnetwork_ordinal"] == 0
    numeric = result.run_dir / "metrics/numeric_observations.json"
    numeric_bytes = numeric.read_bytes()
    manifest_path = result.run_dir / "run_manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    altered = json.loads(numeric_bytes)
    altered["numeric_observations"][2]["metric_observations"][0][
        "observed_value"]["value"] = 123.0
    altered["numeric_observations_digest"] = hashlib.sha256(json.dumps(
        altered["numeric_observations"], sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    numeric.write_text(json.dumps(altered, sort_keys=True))
    forged_manifest = json.loads(manifest_bytes)
    forged_manifest["artifact_sha256"]["numeric_observations"] = hashlib.sha256(
        numeric.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(forged_manifest))
    with pytest.raises(RetainedAttemptError, match="CANDIDATE_PROVENANCE_INVALID"):
        verify_policy_artifacts(result.run_dir)
    numeric.write_bytes(numeric_bytes)
    manifest_path.write_bytes(manifest_bytes)
    retained = result.run_dir / "metrics/retained_fidelity.json"
    retained_bytes = retained.read_bytes()
    retained.write_bytes(retained_bytes + b" \n")
    with pytest.raises(RetainedAttemptError, match="CANDIDATE_PROVENANCE_INVALID"):
        verify_policy_artifacts(result.run_dir)
    retained.write_bytes(retained_bytes)
    pf_binding = result.run_dir / "pf/binding.json"
    binding["threshold_set_sha256"] = "0" * 64
    pf_binding.write_text(json.dumps(binding))
    with pytest.raises(RetainedAttemptError, match="CANDIDATE_PROVENANCE_INVALID"):
        verify_policy_artifacts(result.run_dir)


def test_failure_stops_later_policies_without_reusing_previous_target(tmp_path: Path) -> None:
    """A failed second build leaves third policy NOT_RUN and a durable stop record."""
    source, source_path, sidecar, ledger, policies, bindings = _inputs(tmp_path)
    bad_config = policies[1].config.model_copy(update={
        "partition": policies[1].config.partition.model_copy(update={
            "target": policies[1].config.partition.target.model_copy(update={"n_clusters": 1})})})
    broken = (policies[0], FrozenPolicyRun(
        policies[1].policy_id, bad_config, policies[1].resolved_policy_fingerprint,
        _semantic_config_digest(bad_config),
        _effective_digest(source, sidecar, ledger, bad_config)), policies[2])
    with pytest.raises(Exception):
        run_frozen_policies(
            source_network=source, source_path=source_path, source_sidecar=sidecar,
            source_ledger=ledger, policies=broken, bindings=bindings,
            output_root=tmp_path / "runs",
        )
    stop = json.loads((tmp_path / "runs" / "attempt-stop.json").read_text())
    assert stop["failed_policy"] == policies[1].policy_id
    assert stop["policy_states"][policies[2].policy_id] == "NOT_RUN"
    assert not (tmp_path / "runs" / "attempt_003-backbone_30").exists()


def test_policy_order_and_semantic_binding_fail_closed(tmp_path: Path) -> None:
    """The formal ordered lease rejects policy substitution before any new run."""
    source, source_path, sidecar, ledger, policies, binding = _inputs(tmp_path)
    formal = FrozenAttemptBindings(
        binding.experiment_id, binding.attempt_id, binding.source_model_digest,
        binding.source_sidecar_digest, binding.source_ledger_digest,
        required_policy_order=tuple(item.policy_id for item in policies),
    )
    with pytest.raises(RetainedAttemptError, match="policy order"):
        run_frozen_policies(
            source_network=source, source_path=source_path, source_sidecar=sidecar,
            source_ledger=ledger, policies=policies[::-1], bindings=formal,
            output_root=tmp_path / "runs",
        )
    wrong = FrozenPolicyRun(
        policies[0].policy_id, policies[0].config,
        policies[0].resolved_policy_fingerprint, "0" * 64,
        policies[0].effective_protection_digest,
    )
    with pytest.raises(RetainedAttemptError, match="semantic config"):
        run_frozen_policies(
            source_network=source, source_path=source_path, source_sidecar=sidecar,
            source_ledger=ledger, policies=(wrong,), bindings=binding,
            output_root=tmp_path / "runs",
        )


def test_frozen_template_reconstructs_three_policy_configs_without_run_artifacts(
    tmp_path: Path,
) -> None:
    """The launcher derives three configs from one template and frozen digests."""
    source, source_path, sidecar, ledger, policies, binding = _inputs(tmp_path)
    template = policies[0].config
    freeze = {"policies": [{
        "policy_id": item.policy_id,
        "requested_cutoff_kv": item.config.voltage_policy.cutoff_kv,
        "resolved_policy_fingerprint": item.resolved_policy_fingerprint,
        "semantic_config_digest": item.semantic_config_digest,
        "effective_protection_digest": item.effective_protection_digest,
    } for item in policies]}
    prepared = prepare_policy_runs(
        freeze, template, source_path=source_path, output_root=tmp_path / "future-run")
    assert tuple(item.policy_id for item in prepared) == tuple(item.policy_id for item in policies)
    assert tuple(item.semantic_config_digest for item in prepared) == tuple(
        item.semantic_config_digest for item in policies)
    assert not (tmp_path / "future-run").exists()


def test_three_policy_actual_pf_reports_and_fresh_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same driver builds three separate solved candidates and full exports."""
    source = _pf_source()
    source_path = tmp_path / "frozen-s1.nc"
    source.export_to_netcdf(source_path)
    sidecar = PypsaAdapter().identity_sidecar(source, "n")
    ledger = ParallelSourceLedger(SourceProfileContract("programmatic_pypsa", "unavailable"))
    policies = []
    for label, cutoff, clusters in (("BACKBONE_100", 100.0, 7),
                                    ("BACKBONE_30", 30.0, 8),
                                    ("BACKBONE_5", 5.0, 8)):
        base = pipeline_config(source_path, run_id=label.lower(), n_clusters=clusters)
        config = base.model_copy(update={
            "voltage_policy": base.voltage_policy.model_copy(update={
                "enabled": True, "mode": "freeze_at_or_above", "cutoff_kv": cutoff}),
            "partition": base.partition.model_copy(update={
                "target": base.partition.target.model_copy(update={"per_layer": {}})}),
        })
        resolved = VoltageCutoffPolicy(True, "freeze_at_or_above", cutoff).resolve(source, sidecar)
        policies.append(FrozenPolicyRun(label, config, resolved.resolved_policy_fingerprint,
                                        _semantic_config_digest(config),
                                        _effective_digest(source, sidecar, ledger, config)))
    binding = FrozenAttemptBindings(
        "SYNTHETIC_RETAINED", "ATTEMPT_004", canonical_model_digest(source, sidecar),
        _sidecar_digest(sidecar), source_ledger_digest(ledger),
        required_policy_order=tuple(item.policy_id for item in policies))
    threshold = FrozenThresholdSet.load()
    metric = PowerFlowMetricPolicy(**threshold.payload["metric_policy"])
    contract = PowerFlowOperatingPointContract(
        snapshots=tuple(source.snapshots), solver="pypsa.newton_raphson",
        tolerance=1e-8, max_iterations=None, distribute_slack=False,
        slack_weights=None, use_seed=False,
        control_policy="preserve_source_controls", tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest="a" * 64, device_schedule_digest="b" * 64,
    )
    original_solve = PypsaAcPowerFlowExecutor.solve
    solve_calls = []

    def observed_solve(self, network, identity_sidecar, operating_contract, **kwargs):
        solve_calls.append((network, operating_contract))
        return original_solve(self, network, identity_sidecar, operating_contract, **kwargs)

    monkeypatch.setattr(PypsaAcPowerFlowExecutor, "solve", observed_solve)
    result = run_frozen_policies(
        source_network=source, source_path=source_path, source_sidecar=sidecar,
        source_ledger=ledger, policies=policies, bindings=binding,
        output_root=tmp_path / "runs", pf_contract=contract,
        metric_policy=metric, threshold_set=threshold)
    assert len(result) == 3
    assert len(solve_calls) == 4  # one S1 solve and one fresh solve per policy
    assert all(item[1] == contract for item in solve_calls)
    assert {row.policy_id: row.fidelity_status for row in result} == {
        "BACKBONE_100": "PF_FIDELITY_OUTSIDE_TOLERANCE",
        "BACKBONE_30": "PF_FIDELITY_OUTSIDE_TOLERANCE",
        "BACKBONE_5": "PF_FIDELITY_WITHIN_TOLERANCE",
    }
    assert len({row.candidate_identity for row in result}) == 3
    for row in result:
        assert row.source_pf_status == "PF_CONVERGED"
        assert row.candidate_pf_status == "PF_CONVERGED"
        assert row.structural_status == "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"
        assert row.ac_readiness_status == "AC_MODEL_READY"
        observed = json.loads((row.run_dir / "metrics/numeric_observations.json").read_text())
        assert observed["schema"] == "adaptive-numeric-observations/v1"
        assert len(observed["numeric_observations"]) == 3
        assert observed["adaptive_evaluations"] == []
        report = json.loads((row.run_dir / "metrics/comprehensive.json").read_text())
        assert report["runs"][0]["fidelity"]["observations"]
        if row.policy_id == "BACKBONE_100":
            assert report["runs"][0]["retained_network_fidelity"][
                "interfaces"]["matched_interface_count"]["value"] == 2
            assert report["runs"][0]["structural"]["target_inventory"][
                "bus_count"]["value"] == 7
        assert (row.run_dir / "metrics/comprehensive-retained-fidelity.csv").is_file()
        script = ("from pathlib import Path; from experiment_tools.retained_fidelity_driver "
                  "import verify_policy_artifacts; "
                  f"verify_policy_artifacts(Path({str(row.run_dir)!r}))")
        subprocess.run([sys.executable, "-c", script], check=True, capture_output=True)
    cross = json.loads((tmp_path / "runs/cross_policy/comprehensive.json").read_text())
    assert len(cross["runs"]) == 3
    assert (tmp_path / "runs/cross_policy/comprehensive-policy-comparison.csv").is_file()
    verify_cross_policy_report(tmp_path / "runs", tuple(row.run_dir for row in result))
    cross_path = tmp_path / "runs/cross_policy/comprehensive.json"
    cross_bytes = cross_path.read_bytes()
    cross_path.write_bytes(cross_bytes + b" ")
    with pytest.raises(RetainedOutputError):
        verify_cross_policy_report(tmp_path / "runs", tuple(row.run_dir for row in result))


def test_frozen_attempt004_entry_runs_three_synthetic_policies(tmp_path: Path) -> None:
    """The real-case entry validates a revision-2 lease before synthetic execution."""
    source = _pf_source()
    source_s1 = tmp_path / "S1.nc"
    source.export_to_netcdf(source_s1)
    source = PypsaAdapter().load(source_s1, "netcdf")
    sidecar = PypsaAdapter().identity_sidecar(source, "n")
    sidecar_path = tmp_path / "sidecar.json"
    sidecar_path.write_text(json.dumps(sidecar.to_rows()))
    ledger = ParallelSourceLedger(SourceProfileContract("programmatic_pypsa", "unavailable"))
    ledger_dir = tmp_path / "ledger"
    ledger.save_parquet(ledger_dir)
    source_dat, source_ref = tmp_path / "synthetic.dat", tmp_path / "synthetic.ref"
    source_dat.write_text("synthetic only\n")
    source_ref.write_text("synthetic reference\n")
    parser_root = tmp_path / "parser"
    parser_root.mkdir()
    parser_rows = []
    for index in range(22):
        name = f"stub_{index:02}.py"
        path = parser_root / name
        path.write_text(f"# synthetic parser manifest row {index}\n")
        parser_rows.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    output_root = tmp_path / "attempt004"
    template = pipeline_config(source_s1, run_id="synthetic-entry", n_clusters=8)
    template_path = tmp_path / "template.yaml"
    template_path.write_text(yaml.safe_dump(template.model_dump(mode="json")))
    threshold = FrozenThresholdSet.load()
    metric = PowerFlowMetricPolicy(**threshold.payload["metric_policy"])
    contract = PowerFlowOperatingPointContract(
        snapshots=tuple(source.snapshots), solver="pypsa.newton_raphson",
        tolerance=1e-8, max_iterations=None, distribute_slack=False,
        slack_weights=None, use_seed=False,
        control_policy="preserve_source_controls", tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest="a" * 64, device_schedule_digest="b" * 64,
    )
    policy_rows = []
    for label, cutoff in (("BACKBONE_500", 500.0), ("BACKBONE_220", 220.0),
                          ("BACKBONE_100", 100.0)):
        config = template.model_copy(update={
            "input": template.input.model_copy(update={"path": str(source_s1.resolve())}),
            "run": template.run.model_copy(update={"output_dir": str(output_root.resolve())}),
            "voltage_policy": template.voltage_policy.model_copy(update={
                "enabled": True, "mode": "freeze_at_or_above", "cutoff_kv": cutoff}),
        })
        resolved = VoltageCutoffPolicy(True, "freeze_at_or_above", cutoff).resolve(source, sidecar)
        policy_rows.append({
            "policy_id": label, "requested_cutoff_kv": cutoff,
            "resolved_policy_fingerprint": resolved.resolved_policy_fingerprint,
            "semantic_config_digest": _semantic_config_digest(config),
            "effective_protection_digest": _effective_digest(source, sidecar, ledger, config),
        })
    package_root = Path(__file__).resolve().parents[1]
    repo_root = package_root.parents[1]
    original_freeze = repo_root / "docs/validation/beijing-retained-fidelity-v1-freeze.json"
    original_freeze_sha = hashlib.sha256(original_freeze.read_bytes()).hexdigest()
    preflight_path = tmp_path / "synthetic-preflight.json"
    preflight_bytes = b'{"schema":"retained-fidelity-full-e2e-preflight/v1","status":"PASS"}\n'
    preflight_path.write_bytes(preflight_bytes)
    preflight_sha = hashlib.sha256(preflight_bytes).hexdigest()
    freeze = {
        "freeze_revision": 2, "original_freeze_sha256": original_freeze_sha,
        "synthetic_full_e2e_preflight": {"status": "PASS", "sha256": preflight_sha},
        "experiment": {"experiment_id": "SYNTHETIC_RETAINED"},
        "parser_provenance": {"manifest": parser_rows},
        "input_identity": {
            "source_dat_sha256": hashlib.sha256(source_dat.read_bytes()).hexdigest(),
            "reference_input_sha256": hashlib.sha256(source_ref.read_bytes()).hexdigest(),
            "source_model_digest": canonical_model_digest(source, sidecar),
            "source_sidecar_digest": _sidecar_digest(sidecar),
            "source_ledger_digest": source_ledger_digest(ledger),
        },
        "pf_executor": {"solver": contract.solver, "tolerance": contract.tolerance,
                        "distribute_slack": contract.distribute_slack,
                        "use_seed": contract.use_seed},
        "operating_point": {"source_topology_digest": contract.topology_digest,
                            "source_device_schedule_digest": contract.device_schedule_digest,
                            "digest": operating_point_digest(contract)},
        "fidelity_contract": {"threshold_artifact_sha256": threshold.sha256,
                              "metric_policy_digest": metric_policy_digest(metric)},
        "policies": policy_rows,
    }
    freeze_path = tmp_path / "freeze-revision-2.json"
    freeze_path.write_text(json.dumps(freeze, sort_keys=True))
    import subprocess as _subprocess
    commit = _subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root,
                                      text=True).strip()
    tree = _subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo_root,
                                    text=True).strip()
    driver_dir = package_root / "experiment_tools"
    driver_files = {name: hashlib.sha256((driver_dir / name).read_bytes()).hexdigest()
                    for name in ("retained_fidelity_driver.py",
                                 "retained_fidelity_attempt_cli.py",
                                 "retained_fidelity_outputs.py")}
    lease = {
        "status": "READY_NOT_EXECUTED",
        "attempt_id": "BEIJING_RETAINED_NETWORK_FIDELITY_V1_ATTEMPT_004",
        "freeze_revision": 2,
        "original_freeze_sha256": original_freeze_sha,
        "freeze_revision_sha256": hashlib.sha256(freeze_path.read_bytes()).hexdigest(),
        "synthetic_full_e2e_preflight": {
            "status": "PASS", "sha256": preflight_sha, "path": str(preflight_path)},
        "authorized_policies": ["BACKBONE_500", "BACKBONE_220", "BACKBONE_100"],
        "driver_files_sha256": driver_files,
        "config_template_sha256": hashlib.sha256(template_path.read_bytes()).hexdigest(),
        "source_s1_sha256": hashlib.sha256(source_s1.read_bytes()).hexdigest(),
        "source_sidecar_file_sha256": hashlib.sha256(sidecar_path.read_bytes()).hexdigest(),
        "frozen_implementation_commit": commit, "frozen_implementation_tree": tree,
        "runtime_environment": {
            "python": sys.version.split()[0], "pypsa": metadata.version("pypsa"),
            "package_version": "0.8.0",
            "uv_lock_sha256": hashlib.sha256((package_root / "uv.lock").read_bytes()).hexdigest(),
            "locked_versions": {name: metadata.version(name) for name in (
                "pydantic", "numpy", "pandas", "networkx", "scipy",
                "highspy", "polars")},
        },
    }
    lease_path = tmp_path / "lease.json"
    lease_path.write_text(json.dumps(lease, sort_keys=True))
    bad_lease = tmp_path / "bad-lease.json"
    bad_lease.write_text(json.dumps({**lease, "freeze_revision_sha256": "0" * 64}))
    with pytest.raises(RetainedAttemptError, match="freeze revision bytes"):
        execute_from_frozen_inputs(
            freeze_path=freeze_path, lease_path=bad_lease,
            source_dat=source_dat, source_ref=source_ref, source_s1=source_s1,
            source_sidecar=sidecar_path, source_ledger_dir=ledger_dir,
            parser_src=parser_root, config_template=template_path,
            output_root=tmp_path / "should-not-exist",
        )
    assert not (tmp_path / "should-not-exist").exists()
    wrong_runtime = tmp_path / "wrong-runtime-lease.json"
    wrong_runtime.write_text(json.dumps({**lease, "runtime_environment": {
        **lease["runtime_environment"], "locked_versions": {
            **lease["runtime_environment"]["locked_versions"], "pydantic": "0.0.0"}}}))
    with pytest.raises(RetainedAttemptError, match="pydantic version"):
        execute_from_frozen_inputs(
            freeze_path=freeze_path, lease_path=wrong_runtime,
            source_dat=source_dat, source_ref=source_ref, source_s1=source_s1,
            source_sidecar=sidecar_path, source_ledger_dir=ledger_dir,
            parser_src=parser_root, config_template=template_path,
            output_root=tmp_path / "should-not-exist",
        )
    preflight_path.write_text('{"schema":"retained-fidelity-full-e2e-preflight/v1","status":"FAIL"}\n')
    with pytest.raises(RetainedAttemptError, match="synthetic preflight bytes"):
        execute_from_frozen_inputs(
            freeze_path=freeze_path, lease_path=lease_path,
            source_dat=source_dat, source_ref=source_ref, source_s1=source_s1,
            source_sidecar=sidecar_path, source_ledger_dir=ledger_dir,
            parser_src=parser_root, config_template=template_path,
            output_root=tmp_path / "should-not-exist",
        )
    preflight_path.write_bytes(preflight_bytes)
    execute_from_frozen_inputs(
        freeze_path=freeze_path, lease_path=lease_path,
        source_dat=source_dat, source_ref=source_ref, source_s1=source_s1,
        source_sidecar=sidecar_path, source_ledger_dir=ledger_dir,
        parser_src=parser_root, config_template=template_path,
        output_root=output_root,
    )
    summary = json.loads((output_root / "attempt-summary.json").read_text())
    assert [row["policy_id"] for row in summary["results"]] == [
        "BACKBONE_500", "BACKBONE_220", "BACKBONE_100"]
    assert all(row["candidate_pf_status"] == "PF_CONVERGED" for row in summary["results"])


def test_frozen_attempt004_rejects_source_drift_before_pf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real source-drift gate rejects changed code before any PF call."""
    package_root = Path(__file__).resolve().parents[1]
    repo_root = package_root.parents[1]
    source_reference_commit = "e16de2fb2d6a5f96246b1e47f361bb0d897607e5"
    source_reference_tree = "8adbfbd057181a82b502c758403e9d19fda8a07a"
    source_path = package_root / "src/psdcalc/reduction"
    drift = subprocess.run(
        ["git", "diff", "--quiet", source_reference_commit, "--",
         "packages/psdcalc-reduction/src/psdcalc/reduction"],
        cwd=repo_root, check=False,
    )
    assert drift.returncode == 1
    actual_source_reference_tree = subprocess.check_output(
        ["git", "rev-parse", f"{source_reference_commit}^{{tree}}"],
        cwd=repo_root, text=True,
    ).strip()
    assert actual_source_reference_tree == source_reference_tree
    metrics_path = source_path / "fidelity/metrics.py"
    current_metrics_sha = hashlib.sha256(metrics_path.read_bytes()).hexdigest()
    source_reference_metrics = subprocess.check_output(
        ["git", "show", f"{source_reference_commit}:packages/psdcalc-reduction/src/psdcalc/reduction/fidelity/metrics.py"],
        cwd=repo_root,
    )
    assert hashlib.sha256(source_reference_metrics).hexdigest() != current_metrics_sha

    original_freeze = repo_root / "docs/validation/beijing-retained-fidelity-v1-freeze.json"
    original_freeze_sha = hashlib.sha256(original_freeze.read_bytes()).hexdigest()
    preflight_bytes = (
        b'{"schema":"retained-fidelity-full-e2e-preflight/v1","status":"PASS"}\n'
    )
    preflight_path = tmp_path / "synthetic-preflight.json"
    preflight_path.write_bytes(preflight_bytes)
    preflight_sha = hashlib.sha256(preflight_bytes).hexdigest()
    freeze = {
        "freeze_revision": 2,
        "original_freeze_sha256": original_freeze_sha,
        "synthetic_full_e2e_preflight": {"status": "PASS", "sha256": preflight_sha},
        "input_identity": {
            "source_dat_sha256": hashlib.sha256(b"synthetic dat\n").hexdigest(),
            "reference_input_sha256": hashlib.sha256(b"synthetic ref\n").hexdigest(),
        },
    }
    freeze_path = tmp_path / "freeze-revision-2.json"
    freeze_path.write_text(json.dumps(freeze, sort_keys=True))
    source_dat = tmp_path / "synthetic.dat"
    source_dat.write_bytes(b"synthetic dat\n")
    source_ref = tmp_path / "synthetic.ref"
    source_ref.write_bytes(b"synthetic ref\n")
    source_s1 = tmp_path / "synthetic-s1.nc"
    source_s1.write_bytes(b"not loaded after source drift is rejected")
    source_sidecar = tmp_path / "synthetic-sidecar.json"
    source_sidecar.write_bytes(b"[]")
    source_ledger_dir = tmp_path / "ledger"
    source_ledger_dir.mkdir()
    parser_src = tmp_path / "parser"
    parser_src.mkdir()
    config_template = tmp_path / "config.yaml"
    config_template.write_text("synthetic config\n")
    output_root = tmp_path / "must-not-be-created"
    driver_dir = package_root / "experiment_tools"
    driver_files = {
        name: hashlib.sha256((driver_dir / name).read_bytes()).hexdigest()
        for name in (
            "retained_fidelity_driver.py",
            "retained_fidelity_attempt_cli.py",
            "retained_fidelity_outputs.py",
        )
    }
    lock_path = package_root / "uv.lock"
    lease = {
        "status": "READY_NOT_EXECUTED",
        "attempt_id": "BEIJING_RETAINED_NETWORK_FIDELITY_V1_ATTEMPT_004",
        "freeze_revision": 2,
        "original_freeze_sha256": original_freeze_sha,
        "freeze_revision_sha256": hashlib.sha256(freeze_path.read_bytes()).hexdigest(),
        "synthetic_full_e2e_preflight": {
            "status": "PASS", "sha256": preflight_sha, "path": str(preflight_path),
        },
        "authorized_policies": ["BACKBONE_500", "BACKBONE_220", "BACKBONE_100"],
        "driver_files_sha256": driver_files,
        "config_template_sha256": hashlib.sha256(config_template.read_bytes()).hexdigest(),
        "source_s1_sha256": hashlib.sha256(source_s1.read_bytes()).hexdigest(),
        "source_sidecar_file_sha256": hashlib.sha256(source_sidecar.read_bytes()).hexdigest(),
        "frozen_implementation_commit": source_reference_commit,
        "frozen_implementation_tree": source_reference_tree,
        "runtime_environment": {
            "python": sys.version.split()[0],
            "pypsa": metadata.version("pypsa"),
            "package_version": "0.8.0",
            "uv_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
            "locked_versions": {
                name: metadata.version(name)
                for name in ("pydantic", "numpy", "pandas", "networkx", "scipy", "highspy", "polars")
            },
        },
    }
    lease_path = tmp_path / "lease.json"
    lease_path.write_text(json.dumps(lease, sort_keys=True))

    pf_calls = 0

    def reject_unexpected_pf(*args: object, **kwargs: object) -> None:
        nonlocal pf_calls
        pf_calls += 1
        raise AssertionError("PF must not run before source-drift rejection")

    monkeypatch.setattr(pypsa.Network, "pf", reject_unexpected_pf)
    with pytest.raises(RetainedAttemptError, match="^FROZEN_IMPLEMENTATION_CHANGE_REQUIRED$"):
        execute_from_frozen_inputs(
            freeze_path=freeze_path,
            lease_path=lease_path,
            source_dat=source_dat,
            source_ref=source_ref,
            source_s1=source_s1,
            source_sidecar=source_sidecar,
            source_ledger_dir=source_ledger_dir,
            parser_src=parser_src,
            config_template=config_template,
            output_root=output_root,
        )
    assert pf_calls == 0
    assert not output_root.exists()


def test_nonconvergent_source_pf_stops_before_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed source solve is recorded without relabeling it as a target failure."""
    source, source_path, sidecar, ledger, policies, bindings = _inputs(tmp_path)
    threshold = FrozenThresholdSet.load()
    contract = PowerFlowOperatingPointContract(
        snapshots=tuple(source.snapshots), solver="pypsa.newton_raphson",
        tolerance=1e-8, max_iterations=None, distribute_slack=False,
        slack_weights=None, use_seed=False,
        control_policy="preserve_source_controls", tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest="a" * 64, device_schedule_digest="b" * 64,
    )
    calls = []

    def failed_solve(self, network, identity_sidecar, operating_contract, **kwargs):
        calls.append(network)
        return PowerFlowExecutionResult(
            PowerFlowExecutionStatus.PF_NOT_CONVERGED, False,
            source.snapshots[0], (PowerFlowSubnetworkStatus(0, False, 3, 0.1),),
            failure_reason="synthetic non-convergence")

    monkeypatch.setattr(PypsaAcPowerFlowExecutor, "solve", failed_solve)
    with pytest.raises(RetainedAttemptError, match="SOURCE_PF_NOT_CONVERGED"):
        run_frozen_policies(
            source_network=source, source_path=source_path, source_sidecar=sidecar,
            source_ledger=ledger, policies=policies, bindings=bindings,
            output_root=tmp_path / "runs", pf_contract=contract,
            metric_policy=PowerFlowMetricPolicy(**threshold.payload["metric_policy"]),
            threshold_set=threshold,
        )
    assert len(calls) == 1
    stop = json.loads((tmp_path / "runs/attempt-stop.json").read_text())
    assert stop["failure_code"] == "SOURCE_PF_NOT_CONVERGED"
    assert stop["policy_states"][policies[0].policy_id] == "FAILED"
    assert all(stop["policy_states"][row.policy_id] == "NOT_RUN" for row in policies[1:])
    assert not list((tmp_path / "runs").glob("*/levels/L2.nc"))


def test_nonconvergent_candidate_has_no_fidelity_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Candidate failure remains a PF outcome with fidelity NOT_RECORDED."""
    source = _pf_source()
    source_path = tmp_path / "S1.nc"
    source.export_to_netcdf(source_path)
    sidecar = PypsaAdapter().identity_sidecar(source, "n")
    ledger = ParallelSourceLedger(SourceProfileContract("programmatic_pypsa", "unavailable"))
    base = pipeline_config(source_path, run_id="nonconvergent", n_clusters=8)
    config = base.model_copy(update={"voltage_policy": base.voltage_policy.model_copy(
        update={"enabled": True, "mode": "freeze_at_or_above", "cutoff_kv": 500.0})})
    policy = FrozenPolicyRun(
        "BACKBONE_500", config,
        VoltageCutoffPolicy(True, "freeze_at_or_above", 500.0).resolve(
            source, sidecar).resolved_policy_fingerprint,
        _semantic_config_digest(config), _effective_digest(source, sidecar, ledger, config))
    bindings = FrozenAttemptBindings(
        "SYNTHETIC_RETAINED", "ATTEMPT_004", canonical_model_digest(source, sidecar),
        _sidecar_digest(sidecar), source_ledger_digest(ledger))
    threshold = FrozenThresholdSet.load()
    contract = PowerFlowOperatingPointContract(
        snapshots=tuple(source.snapshots), solver="pypsa.newton_raphson",
        tolerance=1e-8, max_iterations=None, distribute_slack=False,
        slack_weights=None, use_seed=False,
        control_policy="preserve_source_controls", tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest="a" * 64, device_schedule_digest="b" * 64,
    )
    original_solve = PypsaAcPowerFlowExecutor.solve
    calls = []

    def second_solve_fails(self, network, identity_sidecar, operating_contract, **kwargs):
        calls.append(network)
        if len(calls) == 1:
            return original_solve(self, network, identity_sidecar, operating_contract,
                                  **kwargs)
        return PowerFlowExecutionResult(
            PowerFlowExecutionStatus.PF_NOT_CONVERGED, False,
            source.snapshots[0], (PowerFlowSubnetworkStatus(0, False, 4, 0.01),),
            failure_reason="synthetic target non-convergence")

    monkeypatch.setattr(PypsaAcPowerFlowExecutor, "solve", second_solve_fails)
    result = run_frozen_policies(
        source_network=source, source_path=source_path, source_sidecar=sidecar,
        source_ledger=ledger, policies=(policy,), bindings=bindings,
        output_root=tmp_path / "run", pf_contract=contract,
        metric_policy=PowerFlowMetricPolicy(**threshold.payload["metric_policy"]),
        threshold_set=threshold,
    )[0]
    assert len(calls) == 2
    assert result.source_pf_status == "PF_CONVERGED"
    assert result.candidate_pf_status == "PF_NOT_CONVERGED"
    assert result.fidelity_status is None
    assert verify_policy_artifacts(result.run_dir) == result
    assert not (result.run_dir / "metrics/fidelity.json").exists()
    report = json.loads((result.run_dir / "metrics/comprehensive.json").read_text())
    assert report["runs"][0]["fidelity"]["fidelity_status"]["state"] == "NOT_RECORDED"
