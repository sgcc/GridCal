"""Offline PyPSA NetCDF metadata and Transformer round-trip checks."""

from pathlib import Path

import pandas as pd
import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter


def test_multi_investment_and_transformer_metadata_round_trip(tmp_path: Path) -> None:
    """The supported time metadata and Transformer references survive export."""
    network = pypsa.Network()
    network.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    network.set_investment_periods([2030, 2040])
    network.snapshot_weightings["objective"] = [1.0, 2.0, 3.0, 4.0]
    network.investment_period_weightings["objective"] = [1.5, 2.5]
    network.add("Bus", "high", v_nom=110)
    network.add("Bus", "low", v_nom=35)
    network.add("TransformerType", "typed", s_nom=100, tap_step=2.5,
                phase_shift=3.0)
    network.add("Transformer", "tx", bus0="high", bus1="low", type="typed",
                tap_position=1, r=0.01, x=0.1, s_nom=100)
    network.components["Transformer"].dynamic["s_max_pu"] = pd.DataFrame(
        {"tx": [0.9, 0.8, 0.7, 0.6]}, index=network.snapshots)
    path = tmp_path / "multi.nc"
    adapter = PypsaAdapter()
    adapter.export(network, path, "netcdf")
    restored = adapter.load(path, "netcdf")
    assert restored.snapshots.equals(network.snapshots)
    assert restored.snapshot_weightings.equals(network.snapshot_weightings)
    assert restored.investment_periods.equals(network.investment_periods)
    assert restored.investment_period_weightings.equals(
        network.investment_period_weightings)
    assert restored.transformers.loc["tx", "type"] == "typed"
    assert restored.transformer_types.loc["typed", "phase_shift"] == 3.0
    assert restored.components["Transformer"].dynamic["s_max_pu"].equals(
        network.components["Transformer"].dynamic["s_max_pu"])
