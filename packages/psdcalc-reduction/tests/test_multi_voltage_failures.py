"""Connected-component and deterministic end-to-end evidence tests."""

from pathlib import Path

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.pipelines.multi_voltage import MultiVoltageQuotientPipeline
from test_three_voltage_pipeline import pipeline_config, three_voltage_source


def test_disconnected_voltage_island_is_preserved(tmp_path: Path) -> None:
    """The final Gate accepts multiple original connected components."""
    source = three_voltage_source()
    source.add("Bus", "island-a", v_nom=35)
    source.add("Bus", "island-b", v_nom=35)
    source.add("Line", "island-line", bus0="island-a", bus1="island-b",
               r=0.01, x=0.1, s_nom=10)
    input_path = tmp_path / "source.nc"
    source.export_to_netcdf(input_path)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(source, "n")
    config = pipeline_config(input_path, run_id="island", n_clusters=8)
    result = MultiVoltageQuotientPipeline().run(
        original_network=source, source_sidecar=sidecar,
        config=config, store=ArtifactStore(tmp_path / "runs", "island"),
    )
    assert result.structural.status == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID")
    assert result.structural.checks["connected_components"] is True


def test_repeated_seed_produces_same_stable_maps(tmp_path: Path) -> None:
    """The same source, config, and seed produce identical StableId mappings."""
    source = three_voltage_source()
    input_path = tmp_path / "source.nc"
    source.export_to_netcdf(input_path)
    sidecar = PypsaAdapter().identity_sidecar(source, "n")
    results = []
    for run_id in ("determinism-a", "determinism-b"):
        config = pipeline_config(input_path, run_id=run_id)
        results.append(MultiVoltageQuotientPipeline().run(
            original_network=source, source_sidecar=sidecar,
            config=config, store=ArtifactStore(tmp_path / "runs", run_id),
        ))
    for field in ("bus_map", "branch_map", "device_map", "control_map"):
        assert [row.to_row() for row in getattr(results[0].composed_mappings, field)] == [
            row.to_row() for row in getattr(results[1].composed_mappings, field)]
    assert results[0].opportunity_report == results[1].opportunity_report
