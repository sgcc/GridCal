"""Energy inventory statistics are grouped without claiming AC fidelity."""

import pandas as pd
import pypsa
import pytest

from psdcalc.reduction.metrics.energy import collect_energy_aggregates
from psdcalc.reduction.metrics.stage_inventory import CompressionStage


def test_energy_aggregates_global_layer_component_and_anonymous_region() -> None:
    """Static Load, Generator, and Shunt sums retain their inventory scope."""
    network = pypsa.Network()
    for bus, region in (("private-A", "secret-zone"),
                        ("private-B", "secret-zone"),
                        ("private-C", "other-zone")):
        network.add("Bus", bus, v_nom=110, country=region)
    network.add("Line", "ab", bus0="private-A", bus1="private-B", x=0.1)
    network.add("Load", "demand", bus="private-A", p_set=4, q_set=2)
    network.add("Generator", "supply", bus="private-B", p_nom=9, p_set=5)
    network.add("ShuntImpedance", "reactor", bus="private-C", g=0.1, b=-0.2)
    values = collect_energy_aggregates(
        network, CompressionStage.PYPSA_FULL_PROJECTION)
    global_row, = (row for row in values if row.scope == "global")
    assert global_row.load_count == 1
    assert global_row.load_p_set == 4
    assert global_row.load_q_set == 2
    assert global_row.generator_p_nom == 9
    assert global_row.generator_p_set == 5
    assert global_row.shunt_b == pytest.approx(-0.2)
    assert len([row for row in values if row.scope == "source_component"]) == 2
    assert len([row for row in values if row.scope == "region"]) == 2
    assert all("secret" not in str(row) for row in values)


def test_dynamic_load_is_snapshot_weighted_on_detached_view() -> None:
    """Input time series use the public dense accessor on a copy."""
    network = pypsa.Network()
    network.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    network.snapshot_weightings["objective"] = [1.0, 2.0]
    network.add("Bus", "a", v_nom=110)
    network.add("Load", "demand", bus="a", p_set=1)
    network.components["Load"].dynamic["p_set"] = pd.DataFrame(
        {"demand": [3.0, 5.0]}, index=network.snapshots)
    before = network.components["Load"].dynamic["p_set"].copy(deep=True)
    values = collect_energy_aggregates(network, CompressionStage.FINAL_RELOADED)
    global_row, = (row for row in values if row.scope == "global")
    assert global_row.weighted_load_p == pytest.approx(13.0)
    assert network.components["Load"].dynamic["p_set"].equals(before)


def test_weighted_total_includes_static_fallback_for_other_devices() -> None:
    """A partly dynamic component contributes every device to weighted totals."""
    network = pypsa.Network()
    network.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    network.snapshot_weightings["objective"] = [1.0, 2.0]
    network.add("Bus", "a", v_nom=110)
    network.add("Load", "dynamic", bus="a", p_set=1)
    network.add("Load", "static", bus="a", p_set=4)
    network.components["Load"].dynamic["p_set"] = pd.DataFrame(
        {"dynamic": [3.0, 5.0]}, index=network.snapshots)
    values = collect_energy_aggregates(network, CompressionStage.FINAL_RELOADED)
    global_row, = (row for row in values if row.scope == "global")
    assert global_row.weighted_load_p == pytest.approx(25.0)
