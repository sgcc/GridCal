"""Energy feature construction after a structural topology stage."""

import pypsa

from psdcalc.reduction.features.energy import EnergyFeatureBuilder


def test_load_generation_and_renewable_time_features() -> None:
    network = pypsa.Network()
    network.set_snapshots(["s0", "s1"])
    network.add("Bus", "a", v_nom=110, x=0, y=0)
    network.add("Bus", "b", v_nom=110, x=1, y=0)
    network.add("Load", "demand", bus="a", p_set=5)
    network.add("Generator", "wind", bus="b", carrier="wind", p_nom=10,
                p_max_pu=[0.2, 0.8])

    result = EnergyFeatureBuilder().build(
        network, ("load_generation", "renewable_time")
    )

    assert result.loc["a", "load_mw"] == 5
    assert result.loc["b", "generation_mw"] == 10
    assert result.loc["b", "renewable_s0_mw"] == 2
    assert result.loc["b", "renewable_s1_mw"] == 8
