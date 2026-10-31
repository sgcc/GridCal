"""Offline three-voltage end-to-end structural acceptance."""

import json
import hashlib
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pypsa
import pytest
import yaml

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.loader import load_config
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.exceptions import (
    BackendCapabilityError, MappingError, PartitionError, SignatureError,
)
from psdcalc.reduction.pipelines.multi_voltage import MultiVoltageQuotientPipeline
from psdcalc.reduction.metrics.stage_inventory import CompressionStage, StageInventory
from psdcalc.reduction.reporting.comparison import build_pipeline_comparison
from psdcalc.reduction.reporting.voltage_matrix import CrossScenarioCompressionReport
from psdcalc.reduction.validation.multi_voltage_final import network_semantic_signature


def three_voltage_source() -> pypsa.Network:
    """Create a protected 110-kV ring, a compressible 35-kV path, and a feeder."""
    network = pypsa.Network()
    snapshots = pd.date_range("2030-01-01", periods=2, freq="h")
    network.set_snapshots(snapshots)
    network.snapshot_weightings["objective"] = [1.0, 2.0]
    for name, voltage in (("a", 110), ("b", 110), ("c", 110),
                          ("x", 35), ("z1", 35), ("z2", 35), ("y", 35),
                          ("u", 10), ("v", 10)):
        network.add("Bus", name, v_nom=voltage)
    for name, left, right in (
        ("ab", "a", "b"), ("bc", "b", "c"), ("ca", "c", "a"),
        ("xz1", "x", "z1"), ("z1z2", "z1", "z2"),
        ("z2y", "z2", "y"), ("uv", "u", "v"),
    ):
        network.add("Line", name, bus0=left, bus1=right,
                    r=0.01, x=0.1, s_nom=100)
    network.add("TransformerType", "T", s_nom=100, tap_step=2.5,
                phase_shift=3.0)
    network.add("Transformer", "tx1", bus0="a", bus1="x", type="T",
                r=0.01, x=0.1, s_nom=100, tap_position=1)
    network.add("Transformer", "tx2", bus0="y", bus1="u",
                r=0.01, x=0.1, s_nom=100)
    network.components["Transformer"].dynamic["s_max_pu"] = pd.DataFrame(
        {"tx1": [0.9, 0.8], "tx2": [1.0, 1.0]}, index=snapshots)
    network.add("Generator", "slack", bus="a", control="Slack", p_nom=100)
    network.add("Generator", "pv", bus="c", control="PV", p_nom=20)
    network.add("ShuntImpedance", "sh", bus="x", g=0.01)
    network.add("Load", "demand", bus="v",
                p_set=pd.Series([5.0, 7.0], index=snapshots))
    network.add("Load", "mid1", bus="z1", p_set=2.0)
    network.add("Load", "mid2", bus="z2", p_set=3.0)
    return network


def pipeline_config(input_path: Path, *, run_id: str = "three-voltage",
                    n_clusters: int = 7) -> object:
    """Resolve one explicit multi-voltage structural configuration."""
    target = {"type": "count", "n_clusters": n_clusters}
    if n_clusters == 7:
        target["per_layer"] = {"10": 1, "35": 3, "110": 3}
    return load_config({
        "schema_version": "0.1.0",
        "run": {"id": run_id, "seed": 17, "output_dir": str(input_path.parent / "runs")},
        "input": {"backend": "pypsa", "path": str(input_path),
                  "format": "netcdf", "source_namespace": "n"},
        "task": {"type": "planning", "profile": "planning_aggregation",
                 "reduction_role": "candidate", "final_authority": "original"},
        "pipeline": {"materialization_family": "multi_voltage_quotient"},
        "simplification": {"operations": [
            {"id": "topology", "type": "voltage_aware_topology",
             "enabled": True, "mode": "keep_transformer_interface"}
        ]},
        "partition": {"planner": "pypsa_kmeans",
                      "target": target,
                      "connectivity_policy": "reject"},
        "aggregation": {"backend": "pypsa", "aggregate_one_ports": ["Load"]},
    })


def test_three_voltage_pipeline_persists_final_structural_evidence(
    tmp_path: Path,
) -> None:
    """Source, topology, energy, quotient, reload, maps, and report close."""
    original = three_voltage_source()
    input_path = tmp_path / "source.nc"
    original.export_to_netcdf(input_path)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(original, "n")
    original_buses = original.buses.copy(deep=True)
    config = pipeline_config(input_path)
    store = ArtifactStore(tmp_path / "runs", config.run.id)

    result = MultiVoltageQuotientPipeline().run(
        original_network=original, source_sidecar=sidecar,
        config=config, store=store,
    )

    assert tuple(result.stage_inventories) == tuple(
        stage.value for stage in list(CompressionStage)[2:])
    assert result.stage_inventories[CompressionStage.TOPOLOGY_SIMPLIFIED.value].bus_count == len(
        PypsaAdapter().load(store.run_dir / "levels/L1.nc", "netcdf").buses)
    assert result.stage_inventories[CompressionStage.QUOTIENT_CANDIDATE.value].bus_count == (
        result.stage_inventories[CompressionStage.FINAL_RELOADED.value].bus_count)
    assert all(result.phase_runtime[key] >= 0 for key in (
        "topology", "planning", "builder", "persist_reload_gate"))
    assert tuple(result.stage_energy) == tuple(
        stage.value for stage in list(CompressionStage)[2:])
    assert any(row.scope == "global" for row in result.stage_energy[
        CompressionStage.QUOTIENT_CANDIDATE.value])
    assert all("minimum_required_bus_count" in row and
               "transformer_endpoint_count" in row and
               "ring_endpoint_count" in row
               for row in result.opportunity_report.per_layer.values())
    comparison = build_pipeline_comparison(
        model_id="SYNTHETIC", source_stage=StageInventory(
            CompressionStage.PSD_BPA_SOURCE,
            bus_count=len(original.buses), line_count=len(original.lines)),
        projected_network=original, result=result,
        config_sha256=result.manifest.config_hash,
        code_sha="a" * 40, tree_sha="b" * 40)
    assert comparison.accepted_final_bus_count == len(
        PypsaAdapter().load(result.final_network_path, "netcdf").buses)
    assert sum(row.projected_bus_count for row in comparison.voltage_layers) == len(original.buses)
    assert sum(row.final_bus_count for row in comparison.voltage_layers) == comparison.accepted_final_bus_count
    assert len([row for row in comparison.energy_aggregates
                if row.scope == "global"]) == 4
    assert comparison.voltage_policy.policy_id == "BASE"
    assert comparison.voltage_policy.round_trip_validated is True
    assert len(comparison.mappings["semantic_sha256"]) == 64
    matrix = CrossScenarioCompressionReport.create((comparison,))
    assert matrix.scenarios[0].final_structural_status == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID")
    assert matrix.source_sha256 == comparison.source_sha256

    assert result.structural.status == "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"
    assert result.manifest.status == result.structural.status
    resolved_path = store.run_dir / "resolved_config.yaml"
    resolved_bytes = resolved_path.read_bytes()
    resolved = yaml.safe_load(resolved_bytes)
    assert resolved["input"]["path"] == str(input_path.resolve())
    assert resolved["voltage_layers"] == {"aliases": {}}
    assert result.manifest.config_hash == hashlib.sha256(resolved_bytes).hexdigest()
    assert result.manifest.artifacts["resolved_config"] == "resolved_config.yaml"
    assert (store.run_dir / "resolved_config.sha256").read_text().strip() == (
        result.manifest.config_hash)
    assert result.final_network_path.exists()
    assert result.target_sidecar_path.exists()
    assert (store.run_dir / "validation/multi_voltage_structural.json").exists()
    assert (store.run_dir / "validation/opportunity.json").exists()
    assert (store.run_dir / "levels/L1.nc").exists()
    assert (store.run_dir / "maps/bus_map.parquet").exists()
    assert (store.run_dir / "records").is_dir()
    assert len(result.composed_mappings.bus_map) == len(original.buses)
    assert result.opportunity_report.realized_bus_compression_fraction > 0
    opportunity = result.opportunity_report
    assert opportunity.source_bus_count == len(original.buses)
    assert opportunity.simplified_bus_count >= opportunity.final_bus_count
    assert opportunity.minimum_required_bus_count <= opportunity.final_bus_count
    assert set(opportunity.per_layer) == {"10", "35", "110"}
    assert sum(row["final_bus_count"] for row in opportunity.per_layer.values()) == (
        opportunity.final_bus_count)
    assert all(row["requested_cluster_count"] is not None
               for row in opportunity.per_layer.values())
    reloaded = adapter.load(result.final_network_path, "netcdf")
    assert opportunity.final_bus_count == len(reloaded.buses)
    assert reloaded.snapshots.equals(original.snapshots)
    assert reloaded.snapshot_weightings.equals(original.snapshot_weightings)
    assert set(reloaded.transformers.index) == {"tx1", "tx2"}
    assert reloaded.get_switchable_as_dense(
        "Transformer", "s_max_pu").equals(
            original.get_switchable_as_dense("Transformer", "s_max_pu"))
    assert original.buses.equals(original_buses)
    persisted = json.loads((store.run_dir / "run_manifest.json").read_text())
    assert persisted["status"] == result.structural.status


def test_pipeline_failure_writes_manifest_and_evidence(tmp_path: Path) -> None:
    """An impossible target fails explicitly and leaves run evidence."""
    original = three_voltage_source()
    input_path = tmp_path / "source.nc"
    original.export_to_netcdf(input_path)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(original, "n")
    config = pipeline_config(input_path, run_id="too-small", n_clusters=2)
    store = ArtifactStore(tmp_path / "runs", config.run.id)

    with pytest.raises(PartitionError):
        MultiVoltageQuotientPipeline().run(
            original_network=original, source_sidecar=sidecar,
            config=config, store=store,
        )
    assert store.manifest.status == "FAILED"
    failure = json.loads((store.run_dir / "validation/failure.json").read_text())
    assert failure["error_type"] == "PartitionError"


def test_reloaded_voltage_corruption_is_final_invalid(tmp_path: Path) -> None:
    """A corrupt clean reload produces INVALID rather than Builder success."""
    class CorruptReloadAdapter(PypsaAdapter):
        """Model an altered NetCDF readback without patching PyPSA."""

        def load(self, path: Path, format: str) -> pypsa.Network:
            """Alter one target voltage after the public NetCDF import."""
            network = super().load(path, format)
            if path.name == "L2.nc":
                first_bus = network.buses.index[0]
                network.buses.loc[first_bus, "v_nom"] = 999.0
            return network

    original = three_voltage_source()
    input_path = tmp_path / "source.nc"
    original.export_to_netcdf(input_path)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(original, "n")
    config = pipeline_config(input_path, run_id="corrupt-reload")
    store = ArtifactStore(tmp_path / "runs", config.run.id)
    result = MultiVoltageQuotientPipeline(CorruptReloadAdapter()).run(
        original_network=original, source_sidecar=sidecar,
        config=config, store=store,
    )
    assert result.structural.status == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID")
    assert result.structural.checks["voltage_layers"] is False
    assert result.manifest.status == result.structural.status


def test_cross_voltage_link_fails_with_evidence(tmp_path: Path) -> None:
    """An unsupported cross-layer Link records a failed run."""
    original = three_voltage_source()
    original.add("Link", "cross", bus0="b", bus1="x", p_nom=1)
    input_path = tmp_path / "source.nc"
    original.export_to_netcdf(input_path)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(original, "n")
    config = pipeline_config(input_path, run_id="cross-link")
    store = ArtifactStore(tmp_path / "runs", config.run.id)
    with pytest.raises(BackendCapabilityError):
        MultiVoltageQuotientPipeline().run(
            original_network=original, source_sidecar=sidecar,
            config=config, store=store,
        )
    assert store.manifest.status == "FAILED"
    failure = json.loads((store.run_dir / "validation/failure.json").read_text())
    assert failure["error_type"] == "BackendCapabilityError"


def test_reloaded_generator_parameter_corruption_is_invalid(tmp_path: Path) -> None:
    """The final Gate compares supported values with the Builder child."""
    class AlterGeneratorAdapter(PypsaAdapter):
        """Model one changed Generator value on clean reload."""

        def load(self, path: Path, format: str) -> pypsa.Network:
            """Alter a supported static value after public import."""
            network = super().load(path, format)
            if path.name == "L2.nc":
                network.generators.loc["slack", "p_nom"] = 999.0
            return network

    original = three_voltage_source()
    input_path = tmp_path / "source.nc"
    original.export_to_netcdf(input_path)
    sidecar = PypsaAdapter().identity_sidecar(original, "n")
    config = pipeline_config(input_path, run_id="generator-corrupt")
    result = MultiVoltageQuotientPipeline(AlterGeneratorAdapter()).run(
        original_network=original, source_sidecar=sidecar,
        config=config, store=ArtifactStore(tmp_path / "runs", config.run.id),
    )
    assert result.structural.status == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID")
    assert result.structural.checks["netcdf_round_trip"] is False
    assert result.structural.details["netcdf_round_trip"] == "Generator.static"


def test_supplied_network_must_match_hashed_input(tmp_path: Path) -> None:
    """The input SHA cannot be attributed to a different memory model."""
    original = three_voltage_source()
    other = three_voltage_source()
    other.generators.loc["slack", "p_nom"] = 77.0
    input_path = tmp_path / "source.nc"
    other.export_to_netcdf(input_path)
    sidecar = PypsaAdapter().identity_sidecar(original, "n")
    config = pipeline_config(input_path, run_id="wrong-source")
    store = ArtifactStore(tmp_path / "runs", config.run.id)
    with pytest.raises(SignatureError):
        MultiVoltageQuotientPipeline().run(
            original_network=original, source_sidecar=sidecar,
            config=config, store=store,
        )
    assert store.manifest.status == "FAILED"
    failure = json.loads((store.run_dir / "validation/failure.json").read_text())
    assert failure["error_type"] == "SignatureError"


def test_corrupted_persisted_mapping_fails_with_evidence(tmp_path: Path) -> None:
    """The final Gate cannot consume a different on-disk composed map."""
    class CorruptMapStore(ArtifactStore):
        """Alter the just-written composed Bus Parquet file."""

        def record_artifact(self, name: str, relative: str) -> None:
            """Keep the manifest reference, then damage one persisted target."""
            super().record_artifact(name, relative)
            if name == "bus_map":
                path = self.path(relative)
                rows = pq.read_table(path).to_pylist()
                rows[0]["target_id"] = rows[1]["target_id"]
                pq.write_table(pa.Table.from_pylist(rows), path)

    original = three_voltage_source()
    input_path = tmp_path / "source.nc"
    original.export_to_netcdf(input_path)
    config = pipeline_config(input_path, run_id="damaged-map")
    store = CorruptMapStore(tmp_path / "runs", config.run.id)
    with pytest.raises(MappingError, match="Parquet readback"):
        MultiVoltageQuotientPipeline().run(
            original_network=original,
            source_sidecar=PypsaAdapter().identity_sidecar(original, "n"),
            config=config, store=store)
    assert store.manifest.status == "FAILED"
    assert (store.run_dir / "validation/failure.json").exists()


def test_corrupted_target_sidecar_fails_with_evidence(tmp_path: Path) -> None:
    """The Builder sidecar must exactly match the persisted binding table."""
    class CorruptSidecarStore(ArtifactStore):
        """Alter one target binding before the pipeline reads it back."""

        def write_json(self, relative: str, value: object) -> Path:
            """Save an altered target binding for one audit artifact."""
            if relative == "input/target_ids.json":
                value = [dict(row) for row in value]
                value[0]["backend_name"] = "damaged-binding"
            return super().write_json(relative, value)

    original = three_voltage_source()
    input_path = tmp_path / "source.nc"
    original.export_to_netcdf(input_path)
    config = pipeline_config(input_path, run_id="damaged-sidecar")
    store = CorruptSidecarStore(tmp_path / "runs", config.run.id)
    with pytest.raises(SignatureError, match="Target identity sidecar"):
        MultiVoltageQuotientPipeline().run(
            original_network=original,
            source_sidecar=PypsaAdapter().identity_sidecar(original, "n"),
            config=config, store=store)
    assert store.manifest.status == "FAILED"
    assert (store.run_dir / "validation/failure.json").exists()


def test_multi_period_pipeline_keeps_source_semantics(tmp_path: Path) -> None:
    """MultiIndex and dynamic input comparison leaves original network intact."""
    original = three_voltage_source()
    original.set_investment_periods([2030, 2040])
    assert isinstance(original.snapshots, pd.MultiIndex)
    original.snapshot_weightings["objective"] = [1.0, 2.0, 3.0, 4.0]
    original.investment_period_weightings["objective"] = [1.5, 2.5]
    original.components["Load"].dynamic["p_set"] = pd.DataFrame(
        {"demand": [5.0, 7.0, 6.0, 8.0]}, index=original.snapshots)
    original.components["Transformer"].dynamic["s_max_pu"] = pd.DataFrame(
        {"tx1": [0.9, 0.8, 0.85, 0.75]}, index=original.snapshots)
    signature = network_semantic_signature(original)
    input_path = tmp_path / "source.nc"
    original.export_to_netcdf(input_path)
    config = pipeline_config(input_path, run_id="multi-period")
    result = MultiVoltageQuotientPipeline().run(
        original_network=original,
        source_sidecar=PypsaAdapter().identity_sidecar(original, "n"),
        config=config,
        store=ArtifactStore(tmp_path / "runs", config.run.id),
    )
    assert result.structural.status == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID")
    assert result.structural.checks["input_semantic_immutability"] is True
    assert result.structural.checks["netcdf_round_trip"] is True
    assert network_semantic_signature(original) == signature
    reloaded = PypsaAdapter().load(result.final_network_path, "netcdf")
    assert reloaded.snapshots.equals(original.snapshots)
    assert reloaded.snapshot_weightings.equals(original.snapshot_weightings)
    assert reloaded.investment_periods.equals(original.investment_periods)
    assert reloaded.investment_period_weightings.equals(
        original.investment_period_weightings)
    assert not reloaded.components["Load"].dynamic["p_set"].empty
    assert not reloaded.components["Transformer"].dynamic["s_max_pu"].empty
