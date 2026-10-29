"""Offline acceptance for all three multi-voltage energy planners."""

import json
from pathlib import Path

import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.loader import load_config
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.pipelines.multi_voltage import MultiVoltageQuotientPipeline
from psdcalc.reduction.planners.layer_energy import VoltageLayerEnergyPlanner
from psdcalc.reduction.protection.engine import ProtectionEngine
from psdcalc.reduction.topology.simplifier import VoltageAwareTopologySimplifier
from psdcalc.reduction.topology.voltage_layers import VoltageLayerClassifier

from test_three_voltage_pipeline import pipeline_config, three_voltage_source


@pytest.mark.parametrize("planner", [
    "pypsa_kmeans", "pypsa_hac", "pypsa_modularity",
])
def test_each_planner_closes_multi_voltage_pipeline(
    planner: str, tmp_path: Path,
) -> None:
    """Layer budgets, global IDs, persistence, and reruns agree."""
    original = three_voltage_source()
    input_path = tmp_path / "source.nc"
    original.export_to_netcdf(input_path)
    adapter = PypsaAdapter()
    source_sidecar = adapter.identity_sidecar(original, "n")
    config_data = pipeline_config(input_path).model_dump(mode="json")
    config_data["partition"]["planner"] = planner
    config_data["features"] = {"builders": [{"type": "load_generation"}]}
    config_data["run"]["id"] = f"{planner}-first"
    config = load_config(config_data)

    protection = ProtectionEngine().compile(
        adapter.inventory(original, source_sidecar), [],
        profile=config.task.profile,
    )
    topology = VoltageAwareTopologySimplifier().simplify(
        original, source_sidecar, protection,
        mode="keep_transformer_interface",
    )
    parent_sidecar = adapter.identity_sidecar(topology.network, "n")
    plan = VoltageLayerEnergyPlanner().plan(
        topology.network, parent_sidecar, protection,
        planner=planner, n_clusters=7, seed=17,
        builders=config.features.builders, options=config.partition.options,
        connectivity_policy="reject", per_layer={"10": 1, "35": 3, "110": 3},
    )
    assert set(plan.stable_busmap) == {
        parent_sidecar.source_id("Bus", str(name))
        for name in topology.network.buses.index
    }
    assert all(isinstance(source, StableId) and isinstance(target, StableId)
               for source, target in plan.stable_busmap.items())
    assert plan.backend_busmap == {
        parent_sidecar.backend_name(source): target.encode()
        for source, target in plan.stable_busmap.items()
    }
    layers = VoltageLayerClassifier().classify(topology.network, parent_sidecar)
    for layer in layers:
        assert len({plan.stable_busmap[source] for source in layer.bus_ids}) == (
            {"10": 1, "35": 3, "110": 3}[layer.key]
        )
    assert len(set(plan.stable_busmap.values())) == 7

    first = MultiVoltageQuotientPipeline().run(
        original_network=original, source_sidecar=source_sidecar,
        config=config,
        store=ArtifactStore(tmp_path / "runs", config.run.id),
    )
    config_data["run"]["id"] = f"{planner}-again"
    second_config = load_config(config_data)
    second = MultiVoltageQuotientPipeline().run(
        original_network=original, source_sidecar=source_sidecar,
        config=second_config,
        store=ArtifactStore(tmp_path / "runs", second_config.run.id),
    )
    expected_status = "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"
    for result in (first, second):
        assert result.structural.status == expected_status
        assert result.manifest.status == expected_status
        assert result.final_network_path.exists()
        reloaded = adapter.load(result.final_network_path, "netcdf")
        assert len(reloaded.buses) == 7
        assert reloaded.snapshots.equals(original.snapshots)
        assert set(reloaded.transformers.index) == {"tx1", "tx2"}
        saved_plan = json.loads(
            (result.final_network_path.parent.parent / "input/plan.json").read_text()
        )
        assert saved_plan["busmap"] == {
            source.encode(): target.encode()
            for source, target in plan.stable_busmap.items()
        }
    assert first.composed_mappings.bus_map == second.composed_mappings.bus_map
    assert first.final_network_path.read_bytes() == second.final_network_path.read_bytes()
