from __future__ import annotations

import importlib
import json
from pathlib import Path

import pandas as pd
import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.fidelity.contracts import (
    PowerFlowMetricPolicy,
    PowerFlowOperatingPointContract,
    PowerFlowMetricThresholds,
)
from psdcalc.reduction.fidelity.mapping import (
    PfBranchEndpoints,
    derive_pf_comparison_mapping,
)
from psdcalc.reduction.fidelity.persistence import (
    FrozenThresholdSet,
    PowerFlowFidelityEvidenceStore,
    PowerFlowPersistenceError,
    PowerFlowThresholdProvenance,
    StructuralReportEvidence,
    PowerFlowThresholdSchema,
    canonical_model_digest,
    require_supported_pypsa_version,
)
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry


def test_fidelity_package_exposes_persistence_and_replay_api() -> None:
    fidelity = importlib.import_module("psdcalc.reduction.fidelity")

    assert hasattr(fidelity, "PowerFlowFidelityEvidenceBundle")
    assert hasattr(fidelity, "PowerFlowFidelityEvidenceStore")
    assert hasattr(fidelity, "StructuralReportEvidence")
    assert hasattr(fidelity, "FrozenThresholdSet")
    assert hasattr(fidelity, "ResolvedPowerFlowThresholds")


def test_packaged_threshold_resource_is_identical_to_frozen_source() -> None:
    root = Path(__file__).resolve().parents[3]
    source = root / "docs/validation/2026-10-31-pr022-beijing-threshold-set-v1.json"
    packaged = (Path(__file__).resolve().parents[1] /
                "src/psdcalc/reduction/resources/pr022-beijing-threshold-set-v1.json")
    assert source.read_bytes() == packaged.read_bytes()


def _network(*, line_x: float = 10.0, slack: bool = True,
             snapshot: object | None = None) -> tuple[pypsa.Network, object]:
    network = pypsa.Network() if snapshot is None else pypsa.Network(snapshots=[snapshot])
    network.add("Bus", "source", v_nom=110.0)
    network.add("Bus", "load_bus", v_nom=110.0)
    network.add("Line", "line", bus0="source", bus1="load_bus",
                r=1.0, x=line_x, b=0.0, s_nom=100.0)
    if slack:
        network.add("Generator", "slack", bus="source", control="Slack",
                    p_set=0.0, q_set=0.0, p_nom=100.0, vm_pu=1.02)
    network.add("Load", "load", bus="load_bus", p_set=40.0, q_set=10.0)
    return network, PypsaAdapter().identity_sidecar(network, "persist-grid")


def _mapping(network: pypsa.Network, sidecar: object):
    buses = tuple(sidecar.source_id("Bus", str(name)) for name in network.buses.index)
    line = sidecar.source_id("Line", "line")
    bus_map = tuple(MapEntry(bus, bus, "preserved", "pf-test") for bus in buses)
    branch_map = (MapEntry(line, line, "preserved", "pf-test"),)
    return derive_pf_comparison_mapping(
        MappingBundle(bus_map=bus_map, branch_map=branch_map, device_map=()),
        source_bus_ids=buses,
        target_bus_ids=buses,
        source_branch_endpoints=(PfBranchEndpoints(
            line, "Line", sidecar.source_id("Bus", "source"),
            sidecar.source_id("Bus", "load_bus")),),
        target_branch_endpoints=(PfBranchEndpoints(
            line, "Line", sidecar.source_id("Bus", "source"),
            sidecar.source_id("Bus", "load_bus")),),
        source_component_by_bus={bus: "component-0" for bus in buses},
        target_component_by_bus={bus: "component-0" for bus in buses},
    )


def _contract(network: pypsa.Network) -> PowerFlowOperatingPointContract:
    return PowerFlowOperatingPointContract(
        snapshots=(network.snapshots[0],), solver="pypsa.newton_raphson",
        tolerance=1e-8, max_iterations=None, distribute_slack=False,
        slack_weights=None, use_seed=False, control_policy="preserve_source_controls",
        tap_policy="fixed_at_source_schedule", phase_shift_policy="fixed_at_source_schedule",
        topology_digest="a" * 64, device_schedule_digest="b" * 64)


def _persist(tmp_path: Path, *, target_x: float = 10.0, target_slack: bool = True,
             thresholds: PowerFlowMetricThresholds | None = None,
             threshold_provenance: PowerFlowThresholdProvenance | None = None,
             threshold_set: FrozenThresholdSet | None = None,
             storage_component: str | None = None):
    source, source_sidecar = _network()
    target, target_sidecar = _network(line_x=target_x, slack=target_slack)
    if storage_component is not None:
        for network in (source, target):
            if storage_component == "StorageUnit":
                network.add(storage_component, "storage", bus="source", p_nom=5.0, p_set=0.0, q_set=0.0)
            else:
                network.add(storage_component, "store", bus="source", e_nom=5.0, p_set=0.0, q_set=0.0)
        source_sidecar = PypsaAdapter().identity_sidecar(source, "persist-grid")
        target_sidecar = PypsaAdapter().identity_sidecar(target, "persist-grid")
    source_report, target_report = _structural_reports(source, source_sidecar, target, target_sidecar)
    mapping = _mapping(source, source_sidecar)
    store = ArtifactStore(tmp_path, "pf-run-001")
    evidence_store = PowerFlowFidelityEvidenceStore(store)
    bundle = evidence_store.persist(
        model_id="synthetic-grid", config_sha256="c" * 64,
        source_structural_report=source_report, target_structural_report=target_report,
        structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
        source_network=source, source_sidecar=source_sidecar,
        target_network=target, target_sidecar=target_sidecar,
        comparison_mapping=mapping, operating_point_contract=_contract(source),
        metric_policy=PowerFlowMetricPolicy(1e-6, 1e-6, 1e-6),
        thresholds=thresholds, threshold_provenance=threshold_provenance,
        threshold_set=threshold_set)
    return evidence_store, bundle


def _structural_reports(source: pypsa.Network, source_sidecar: object,
                        target: pypsa.Network, target_sidecar: object):
    source_report = StructuralReportEvidence.from_network(
        {"status": "STRUCTURALLY_VALID", "checks": {"connected_components": True}},
        stage="S1", structural_run_id="structural-run-001",
        artifact_reference="validation/topology_structural.json",
        network=source, sidecar=source_sidecar)
    target_report = StructuralReportEvidence.from_network(
        {"status": "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
         "checks": {"connected_components": True}},
        stage="S4", structural_run_id="structural-run-001",
        artifact_reference="validation/multi_voltage_structural.json",
        network=target, sidecar=target_sidecar)
    return source_report, target_report


def test_identical_network_clean_reload_replays_aggregate_evidence(tmp_path: Path) -> None:
    evidence_store, bundle = _persist(tmp_path)
    assert bundle.fidelity_report.fidelity_status.value == "PF_FIDELITY_MEASURED"
    del bundle
    replay = evidence_store.replay()
    assert replay.replay_verified is True
    assert replay.structural_status == "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"
    assert replay.report.fidelity_status.value == "PF_FIDELITY_MEASURED"
    assert replay.report.bus_voltage_metrics is not None
    assert replay.report.bus_voltage_metrics.rmse == pytest.approx(0.0, abs=1e-12)
    assert (evidence_store.directory / "comparison_map.parquet").is_file()
    assert (evidence_store.directory / "metrics.parquet").is_file()


def test_perturbed_target_clean_reload_reproduces_nonzero_metrics(tmp_path: Path) -> None:
    evidence_store, bundle = _persist(tmp_path, target_x=11.0)
    assert bundle.fidelity_report.branch_p_metrics is not None
    assert bundle.fidelity_report.branch_p_metrics.rmse > 0.0
    replay = evidence_store.replay()
    assert replay.report.branch_p_metrics is not None
    assert replay.report.branch_p_metrics.rmse == pytest.approx(
        bundle.fidelity_report.branch_p_metrics.rmse, rel=1e-10, abs=1e-12)


def test_timestamped_single_snapshot_and_dynamic_load_replay_from_clean_reload(tmp_path: Path) -> None:
    snapshot = pd.Timestamp("2026-10-31T12:00:00")
    source, source_sidecar = _network(snapshot=snapshot)
    target, target_sidecar = _network(snapshot=snapshot)
    source.loads_t.p_set.loc[snapshot, "load"] = 42.0
    target.loads_t.p_set.loc[snapshot, "load"] = 42.0
    evidence_store = PowerFlowFidelityEvidenceStore(ArtifactStore(tmp_path, "pf-run-dynamic"))
    source_report, target_report = _structural_reports(source, source_sidecar, target, target_sidecar)
    bundle = evidence_store.persist(
        model_id="synthetic-grid", config_sha256="c" * 64,
        source_structural_report=source_report, target_structural_report=target_report,
        structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
        source_network=source, source_sidecar=source_sidecar,
        target_network=target, target_sidecar=target_sidecar,
        comparison_mapping=_mapping(source, source_sidecar),
        operating_point_contract=_contract(source),
        metric_policy=PowerFlowMetricPolicy(1e-6, 1e-6, 1e-6))
    assert bundle.operating_point_contract.snapshots == (snapshot,)
    assert evidence_store.replay().replay_verified is True


def test_single_multiindex_snapshot_replays_from_clean_reload(tmp_path: Path) -> None:
    snapshot = (2030, "winter")
    snapshots = pd.MultiIndex.from_tuples(
        [snapshot], names=["investment_period", "scenario"])
    source, source_sidecar = _network()
    target, target_sidecar = _network()
    source.set_snapshots(snapshots)
    target.set_snapshots(snapshots)
    source.loads_t.p_set.loc[snapshot, "load"] = 42.0
    target.loads_t.p_set.loc[snapshot, "load"] = 42.0
    evidence_store = PowerFlowFidelityEvidenceStore(ArtifactStore(tmp_path, "pf-run-multiindex"))
    source_report, target_report = _structural_reports(source, source_sidecar, target, target_sidecar)
    bundle = evidence_store.persist(
        model_id="synthetic-grid", config_sha256="c" * 64,
        source_structural_report=source_report, target_structural_report=target_report,
        structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
        source_network=source, source_sidecar=source_sidecar,
        target_network=target, target_sidecar=target_sidecar,
        comparison_mapping=_mapping(source, source_sidecar),
        operating_point_contract=_contract(source),
        metric_policy=PowerFlowMetricPolicy(1e-6, 1e-6, 1e-6))
    assert bundle.operating_point_contract.snapshots == (snapshot,)
    assert evidence_store.replay().replay_verified is True


def test_source_nonconvergence_is_replayed_without_fidelity_metrics(tmp_path: Path) -> None:
    source, source_sidecar = _network(slack=False)
    target, target_sidecar = _network()
    mapping = _mapping(source, source_sidecar)
    source_report, target_report = _structural_reports(source, source_sidecar, target, target_sidecar)
    store = ArtifactStore(tmp_path, "pf-run-002")
    evidence_store = PowerFlowFidelityEvidenceStore(store)
    bundle = evidence_store.persist(
        model_id="synthetic-grid", config_sha256="c" * 64,
        source_structural_report=source_report, target_structural_report=target_report,
        structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
        source_network=source, source_sidecar=source_sidecar,
        target_network=target, target_sidecar=target_sidecar,
        comparison_mapping=mapping, operating_point_contract=_contract(source),
        metric_policy=PowerFlowMetricPolicy(1e-6, 1e-6, 1e-6))
    assert bundle.fidelity_report.fidelity_status.value == "SOURCE_PF_NOT_CONVERGED"
    assert bundle.fidelity_report.bus_voltage_metrics is None
    replay = evidence_store.replay()
    assert replay.source_execution_status == "PF_NOT_CONVERGED"
    assert replay.target_execution_status == "NOT_RUN"
    assert replay.report.fidelity_status.value == "SOURCE_PF_NOT_CONVERGED"
    assert replay.report.bus_voltage_metrics is None


def test_target_nonconvergence_is_replayed_without_fidelity_metrics(tmp_path: Path) -> None:
    source, source_sidecar = _network()
    target, target_sidecar = _network(slack=False)
    source_report, target_report = _structural_reports(source, source_sidecar, target, target_sidecar)
    store = ArtifactStore(tmp_path, "pf-run-003")
    evidence_store = PowerFlowFidelityEvidenceStore(store)
    bundle = evidence_store.persist(
        model_id="synthetic-grid", config_sha256="c" * 64,
        source_structural_report=source_report, target_structural_report=target_report,
        structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
        source_network=source, source_sidecar=source_sidecar,
        target_network=target, target_sidecar=target_sidecar,
        comparison_mapping=_mapping(source, source_sidecar),
        operating_point_contract=_contract(source),
        metric_policy=PowerFlowMetricPolicy(1e-6, 1e-6, 1e-6))
    assert bundle.fidelity_report.fidelity_status.value == "TARGET_PF_NOT_CONVERGED"
    replay = evidence_store.replay()
    assert replay.structural_status == "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"
    assert replay.source_execution_status == "PF_CONVERGED"
    assert replay.target_execution_status == "PF_NOT_CONVERGED"
    assert replay.report.fidelity_status.value == "TARGET_PF_NOT_CONVERGED"
    assert replay.report.bus_voltage_metrics is None


@pytest.mark.parametrize(
    ("artifact", "mutate"),
    [
        ("target_model.nc", "model"),
        ("comparison_map.parquet", "mapping"),
        ("evidence.json", "operating_point"),
        ("evidence.json", "metric_policy"),
        ("evidence.json", "threshold"),
        ("evidence.json", "replay_tolerance"),
        ("evidence.json", "solver_provenance"),
        ("metrics.parquet", "metrics"),
        ("report.json", "report"),
    ],
)
def test_replay_rejects_tampered_persisted_inputs(tmp_path: Path, artifact: str, mutate: str) -> None:
    evidence_store, _ = _persist(tmp_path)
    path = evidence_store.directory / artifact
    if mutate == "model":
        network = PypsaAdapter().load(path, "netcdf")
        network.lines.loc["line", "x"] = 22.0
        network.export_to_netcdf(path)
    elif mutate == "mapping":
        frame = pd.read_parquet(path)
        row = frame.index[frame["kind"] == "branch"][0]
        payload = json.loads(frame.loc[row, "payload"])
        payload["orientation_sign"] = -1
        frame.loc[row, "payload"] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        frame.to_parquet(path, index=False)
    elif mutate == "operating_point":
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["operating_point_contract"]["tolerance"] = 2e-8
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutate == "metric_policy":
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["metric_policy"]["p_denominator_floor_mw"] = 2e-6
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutate == "threshold":
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["threshold_digest"] = "f" * 64
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutate == "replay_tolerance":
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["replay_tolerance"]["absolute"] = 10.0
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutate == "solver_provenance":
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["solver_provenance"]["python_version"] = "0.0.0"
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif mutate == "metrics":
        frame = pd.read_parquet(path)
        row = frame.index[(frame["metric"] == "bus_voltage") & (frame["field"] == "rmse")][0]
        frame.loc[row, "value"] = 1.0
        frame.to_parquet(path, index=False)
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["metrics"]["bus_voltage"]["rmse"] = 0.5
        path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PowerFlowPersistenceError):
        evidence_store.replay()


def test_replay_tolerance_and_acceptance_threshold_schema_are_separate() -> None:
    schema = PowerFlowThresholdSchema()
    definitions = {item.name: item for item in schema.definitions}
    assert schema.threshold_set_id == "UNCONFIGURED"
    assert definitions["vm_max_pu"].classification == "HARD"
    assert definitions["branch_p_p95_mw"].classification == "DIAGNOSTIC"
    assert definitions["vm_rmse_pu"].mandatory is True
    assert definitions["target_pf_convergence"].classification == "HARD"
    assert definitions["mapping_coverage"].mandatory is True
    require_supported_pypsa_version("1.2.9")
    with pytest.raises(PowerFlowPersistenceError, match="1.2.x") as exc_info:
        require_supported_pypsa_version("1.3.0")
    assert exc_info.value.code == "PF_REPLAY_SOLVER_INCOMPATIBLE"


def test_threshold_evaluation_exposes_hard_and_diagnostic_outcomes_separately() -> None:
    metrics_module = importlib.import_module("psdcalc.reduction.fidelity.metrics")
    assert hasattr(metrics_module, "evaluate_metric_thresholds_detailed")


def test_canonical_model_digest_uses_dynamic_inputs_but_excludes_solved_outputs() -> None:
    network, sidecar = _network()
    baseline = canonical_model_digest(network, sidecar)
    snapshot = network.snapshots[0]
    network.loads_t.p_set.loc[snapshot, "load"] = 52.0
    assert canonical_model_digest(network, sidecar) != baseline

    unchanged_input_digest = canonical_model_digest(network, sidecar)
    network.buses["v_mag_pu"] = 1.001
    network.lines["p0"] = 3.25
    assert canonical_model_digest(network, sidecar) == unchanged_input_digest


@pytest.mark.parametrize("component", ["StorageUnit", "Store"])
@pytest.mark.parametrize("attribute", ["p_set", "q_set", "active"])
def test_canonical_model_digest_includes_storage_component_pf_inputs(
        component: str, attribute: str) -> None:
    network, _ = _network()
    if component == "StorageUnit":
        network.add(component, "storage", bus="source", p_nom=20.0, p_set=0.0, q_set=0.0)
    else:
        network.add(component, "store", bus="source", e_nom=20.0, p_set=0.0, q_set=0.0)
    sidecar = PypsaAdapter().identity_sidecar(network, "persist-grid")
    backend_name = "storage" if component == "StorageUnit" else "store"
    baseline = canonical_model_digest(network, sidecar)

    if attribute == "active":
        network.components[component].static.loc[backend_name, attribute] = False
    else:
        network.components[component].static.loc[backend_name, attribute] = 2.0

    assert canonical_model_digest(network, sidecar) != baseline


@pytest.mark.parametrize("component", ["StorageUnit", "Store"])
@pytest.mark.parametrize("dynamic_attribute", ["p_set", "q_set"])
def test_canonical_model_digest_includes_dynamic_storage_inputs(component: str, dynamic_attribute: str) -> None:
    snapshot = pd.Timestamp("2026-10-31T12:00:00")
    network, _ = _network(snapshot=snapshot)
    if component == "StorageUnit":
        network.add(component, "storage", bus="source", p_nom=20.0, p_set=0.0, q_set=0.0)
    else:
        network.add(component, "store", bus="source", e_nom=20.0, p_set=0.0, q_set=0.0)
    sidecar = PypsaAdapter().identity_sidecar(network, "persist-grid")
    baseline = canonical_model_digest(network, sidecar)
    table = network.storage_units_t if component == "StorageUnit" else network.stores_t
    backend_name = "storage" if component == "StorageUnit" else "store"
    getattr(table, dynamic_attribute).loc[snapshot, backend_name] = 3.0
    assert canonical_model_digest(network, sidecar) != baseline


@pytest.mark.parametrize("component", ["StorageUnit", "Store"])
def test_canonical_model_digest_tracks_storage_add_remove_and_bus_move(component: str) -> None:
    network, _ = _network()
    baseline_sidecar = PypsaAdapter().identity_sidecar(network, "persist-grid")
    baseline = canonical_model_digest(network, baseline_sidecar)
    if component == "StorageUnit":
        network.add(component, "storage", bus="source", p_nom=5.0, p_set=0.0, q_set=0.0)
    else:
        network.add(component, "store", bus="source", e_nom=5.0, p_set=0.0, q_set=0.0)
    sidecar = PypsaAdapter().identity_sidecar(network, "persist-grid")
    with_component = canonical_model_digest(network, sidecar)
    assert with_component != baseline
    name = "storage" if component == "StorageUnit" else "store"
    network.components[component].static.loc[name, "bus"] = "load_bus"
    assert canonical_model_digest(network, sidecar) != with_component
    network.remove(component, name)
    assert canonical_model_digest(network, baseline_sidecar) == baseline


def test_pf_evidence_persists_and_replays_structural_report_payloads(tmp_path: Path) -> None:
    evidence_store, _ = _persist(tmp_path)
    source_report = evidence_store.directory / "source_structural_report.json"
    target_report = evidence_store.directory / "target_structural_report.json"
    assert source_report.is_file()
    assert target_report.is_file()
    assert evidence_store.replay().replay_verified is True


@pytest.mark.parametrize("which", ["source", "target"])
def test_replay_rejects_substituted_structural_report(tmp_path: Path, which: str) -> None:
    evidence_store, _ = _persist(tmp_path)
    path = evidence_store.directory / f"{which}_structural_report.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["report"]["checks"]["tampered_from_another_run"] = True
    payload["structural_run_id"] = "different-structural-run"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    with pytest.raises(PowerFlowPersistenceError) as exc_info:
        evidence_store.replay()
    assert exc_info.value.code == "PF_EVIDENCE_DIGEST_MISMATCH"


def test_persist_rejects_structural_report_from_another_run(tmp_path: Path) -> None:
    source, source_sidecar = _network()
    target, target_sidecar = _network()
    source_report, target_report = _structural_reports(source, source_sidecar, target, target_sidecar)
    foreign_target = StructuralReportEvidence.from_network(
        target_report.payload, stage="S4", structural_run_id="another-structural-run",
        artifact_reference=target_report.artifact_reference,
        network=target, sidecar=target_sidecar)
    with pytest.raises(PowerFlowPersistenceError) as exc_info:
        PowerFlowFidelityEvidenceStore(ArtifactStore(tmp_path, "pf-cross-run")).persist(
            model_id="synthetic-grid", config_sha256="c" * 64,
            source_structural_report=source_report, target_structural_report=foreign_target,
            structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
            source_network=source, source_sidecar=source_sidecar,
            target_network=target, target_sidecar=target_sidecar,
            comparison_mapping=_mapping(source, source_sidecar),
            operating_point_contract=_contract(source),
            metric_policy=PowerFlowMetricPolicy(1e-6, 1e-6, 1e-6))
    assert exc_info.value.code == "PF_STRUCTURAL_REPORT_INVALID"


def test_v1_threshold_provenance_rejects_unbound_runtime_scalars(tmp_path: Path) -> None:
    thresholds = PowerFlowMetricThresholds(
        vm_max_pu=0.01, vm_rmse_pu=0.005, cutset_p_max_mw=5.0,
        cutset_q_max_mvar=5.0, loss_rel_max=0.02)
    provenance = PowerFlowThresholdProvenance(
        threshold_set_id="PR022_AC_PF_FIDELITY_V1", version="1",
        rationale="V1 cannot be represented by unbound runtime scalars.",
        pre_registration_date="2026-10-31")
    with pytest.raises(PowerFlowPersistenceError) as exc_info:
        _persist(tmp_path, thresholds=thresholds, threshold_provenance=provenance)
    assert exc_info.value.code == "PF_THRESHOLD_PROVENANCE_INVALID"


def test_frozen_threshold_set_resolves_reference_dependent_cutset_limits() -> None:
    frozen = FrozenThresholdSet.load()
    resolved = frozen.resolve(1000.0, 400.0)
    assert resolved.threshold_set_id == "PR022_AC_PF_FIDELITY_V1"
    assert resolved.threshold_set_sha256 == "4688f0f021f5aaabbfcb75bd1dcd8a1228753669dd415761d2d1b5471dac3f24"
    assert resolved.thresholds.vm_max_pu == pytest.approx(0.01)
    assert resolved.thresholds.vm_rmse_pu == pytest.approx(0.005)
    assert resolved.thresholds.cutset_p_max_mw == pytest.approx(10.0)
    assert resolved.thresholds.cutset_q_max_mvar == pytest.approx(8.0)
    assert resolved.thresholds.loss_rel_max == pytest.approx(0.02)


def test_v1_threshold_resolution_is_persisted_and_recomputed_on_replay(tmp_path: Path) -> None:
    evidence_store, bundle = _persist(tmp_path, threshold_set=FrozenThresholdSet.load())
    evidence = json.loads((evidence_store.directory / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["threshold_set_id"] == "PR022_AC_PF_FIDELITY_V1"
    assert evidence["threshold_set_sha256"] == "4688f0f021f5aaabbfcb75bd1dcd8a1228753669dd415761d2d1b5471dac3f24"
    assert evidence["resolved_thresholds"]["resolved_thresholds_digest"] == bundle.resolved_thresholds[
        "resolved_thresholds_digest"]
    assert evidence["thresholds"]["cutset_p_max_mw"] == evidence["resolved_thresholds"][
        "resolved_thresholds"]["cutset_p_max_mw"]
    assert evidence_store.replay().replay_verified is True


@pytest.mark.parametrize("tamper", [
    "threshold_file", "threshold_sha", "vm_scalar", "cutset_p", "cutset_q",
    "reference_p", "threshold_id", "resolved_digest",
])
def test_replay_rejects_v1_threshold_binding_mismatch(tmp_path: Path, tamper: str) -> None:
    evidence_store, _ = _persist(tmp_path, threshold_set=FrozenThresholdSet.load())
    if tamper == "threshold_file":
        path = evidence_store.directory / "threshold_set_v1.json"
        path.write_bytes(path.read_bytes() + b" ")
    else:
        path = evidence_store.directory / "evidence.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if tamper == "threshold_sha":
            payload["threshold_set_sha256"] = "0" * 64
        elif tamper == "vm_scalar":
            payload["thresholds"]["vm_max_pu"] = 0.02
        elif tamper == "cutset_p":
            payload["resolved_thresholds"]["resolved_thresholds"]["cutset_p_max_mw"] += 1.0
        elif tamper == "cutset_q":
            payload["resolved_thresholds"]["resolved_thresholds"]["cutset_q_max_mvar"] += 1.0
        elif tamper == "reference_p":
            payload["resolved_thresholds"]["reference_cutset_p_mw"] += 1.0
        elif tamper == "threshold_id":
            payload["threshold_set_id"] = "PR022_AC_PF_FIDELITY_V1-OTHER"
        else:
            payload["resolved_thresholds"]["resolved_thresholds_digest"] = "0" * 64
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    with pytest.raises(PowerFlowPersistenceError) as exc_info:
        evidence_store.replay()
    assert exc_info.value.code == "PF_EVIDENCE_DIGEST_MISMATCH"


@pytest.mark.parametrize("component", ["StorageUnit", "Store"])
@pytest.mark.parametrize("dynamic", [False, True])
def test_replay_rejects_tampered_storage_component_input(tmp_path: Path, component: str, dynamic: bool) -> None:
    evidence_store, _ = _persist(tmp_path, storage_component=component)
    path = evidence_store.directory / "source_model.nc"
    network = PypsaAdapter().load(path, "netcdf")
    attribute = "storage_units" if component == "StorageUnit" else "stores"
    name = "storage" if component == "StorageUnit" else "store"
    if dynamic:
        table = network.storage_units_t if component == "StorageUnit" else network.stores_t
        getattr(table, "p_set").loc[network.snapshots[0], name] = 1.0
    else:
        getattr(network, attribute).loc[name, "q_set"] = 1.0
    network.export_to_netcdf(path)
    with pytest.raises(PowerFlowPersistenceError) as exc_info:
        evidence_store.replay()
    assert exc_info.value.code == "PF_EVIDENCE_DIGEST_MISMATCH"


def test_persisted_public_report_is_aggregate_only_and_bound_to_all_inputs(tmp_path: Path) -> None:
    evidence_store, bundle = _persist(tmp_path)
    report_path = evidence_store.directory / "report.json"
    report_text = report_path.read_text(encoding="utf-8")
    evidence = json.loads((evidence_store.directory / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["structural_report_digest"] == bundle.structural_report_digest
    assert evidence["mapping_digest"] == bundle.mapping_digest
    assert evidence["source_model_digest"] == bundle.source_model_digest
    assert evidence["target_model_digest"] == bundle.target_model_digest
    assert evidence["operating_point_digest"] == bundle.operating_point_digest
    assert evidence["metric_policy_digest"] == bundle.metric_policy_digest
    assert evidence["threshold_digest"] is None
    assert evidence["report_digest"] == bundle.report_digest
    assert "load_bus" not in report_text
    assert '"slack"' not in report_text
    assert "StableId" not in report_text
    assert "source_id" not in report_text
    assert '"model_id"' not in report_text
    assert '"run_id"' not in report_text
    assert "bus_vm_pu" not in report_text
    report = json.loads(report_text)
    assert set(report["metrics"]["branch_p0"]) == {"count", "rmse", "p95_abs", "max_abs", "mean_abs"}
    assert "[" not in str(report["metrics"]["bus_voltage"])
    artifacts = evidence_store.artifact_store.manifest.artifacts
    assert "pf_fidelity_source_model" in artifacts
    assert "pf_fidelity_target_sidecar" in artifacts


def test_persistence_exports_structural_inputs_without_stale_pf_outputs(tmp_path: Path) -> None:
    source, source_sidecar = _network()
    target, target_sidecar = _network()
    source_report, target_report = _structural_reports(source, source_sidecar, target, target_sidecar)
    source.buses["v_mag_pu"] = 0.987
    source.lines["p0"] = 17.0
    source_digest = canonical_model_digest(source, source_sidecar)
    evidence_store = PowerFlowFidelityEvidenceStore(ArtifactStore(tmp_path, "pf-run-inputs"))
    evidence_store.persist(
        model_id="synthetic-grid", config_sha256="c" * 64,
        source_structural_report=source_report, target_structural_report=target_report,
        structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
        source_network=source, source_sidecar=source_sidecar,
        target_network=target, target_sidecar=target_sidecar,
        comparison_mapping=_mapping(source, source_sidecar),
        operating_point_contract=_contract(source),
        metric_policy=PowerFlowMetricPolicy(1e-6, 1e-6, 1e-6))
    reloaded = PypsaAdapter().load(evidence_store.directory / "source_model.nc", "netcdf")
    assert canonical_model_digest(source, source_sidecar) == source_digest
    assert (reloaded.buses.get("v_mag_pu", pd.Series(dtype=float)) == 0.987).sum() == 0
    assert (reloaded.lines.get("p0", pd.Series(dtype=float)) == 17.0).sum() == 0


def test_configured_thresholds_require_and_replay_preregistration_provenance(tmp_path: Path) -> None:
    thresholds = PowerFlowMetricThresholds(
        vm_max_pu=0.1, vm_rmse_pu=0.1, angle_max_deg=1.0,
        branch_p_max_mw=1.0, branch_q_max_mvar=1.0,
        cutset_p_max_mw=1.0, cutset_q_max_mvar=1.0,
        loss_abs_max_mw=1.0, loss_rel_max=1.0)
    provenance = PowerFlowThresholdProvenance(
        threshold_set_id="SYNTHETIC-TEST", version="test-1",
        rationale="Synthetic persistence fixture only.",
        pre_registration_date="2026-10-31")
    evidence_store, bundle = _persist(
        tmp_path, thresholds=thresholds, threshold_provenance=provenance)
    assert bundle.threshold_set_id == "SYNTHETIC-TEST"
    replay = evidence_store.replay()
    assert replay.report.thresholds_digest == bundle.threshold_digest
    assert replay.report.fidelity_status == bundle.fidelity_report.fidelity_status
    evidence_path = evidence_store.directory / "evidence.json"
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["threshold_provenance"]["rationale"] = "tampered rationale"
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PowerFlowPersistenceError) as exc_info:
        evidence_store.replay()
    assert exc_info.value.code == "PF_EVIDENCE_DIGEST_MISMATCH"


def test_configured_thresholds_without_provenance_fail_closed(tmp_path: Path) -> None:
    thresholds = PowerFlowMetricThresholds(
        vm_max_pu=0.1, vm_rmse_pu=0.1, angle_max_deg=1.0,
        branch_p_max_mw=1.0, branch_q_max_mvar=1.0,
        cutset_p_max_mw=1.0, cutset_q_max_mvar=1.0,
        loss_abs_max_mw=1.0, loss_rel_max=1.0)
    with pytest.raises(PowerFlowPersistenceError) as exc_info:
        _persist(tmp_path, thresholds=thresholds)
    assert exc_info.value.code == "PF_THRESHOLD_PROVENANCE_INVALID"


def test_diagnostic_threshold_failure_is_reported_without_changing_tolerance_status(tmp_path: Path) -> None:
    thresholds = PowerFlowMetricThresholds(
        vm_max_pu=100.0, vm_rmse_pu=100.0,
        cutset_p_max_mw=1000.0, cutset_q_max_mvar=1000.0,
        loss_abs_max_mw=100.0, loss_rel_max=100.0,
        branch_p_max_mw=0.0)
    provenance = PowerFlowThresholdProvenance(
        threshold_set_id="SYNTHETIC-DIAGNOSTIC", version="test-1",
        rationale="Synthetic diagnostic classification check.",
        pre_registration_date="2026-10-31")
    evidence_store, bundle = _persist(
        tmp_path, target_x=11.0, thresholds=thresholds,
        threshold_provenance=provenance)
    assert bundle.fidelity_report.fidelity_status.value == "PF_FIDELITY_WITHIN_TOLERANCE"
    assert "branch_p_max_mw" in bundle.fidelity_report.threshold_diagnostic_failures
    replay = evidence_store.replay()
    assert replay.report.fidelity_status.value == "PF_FIDELITY_WITHIN_TOLERANCE"
    assert replay.report.threshold_hard_failures == ()
    assert "branch_p_max_mw" in replay.report.threshold_diagnostic_failures
