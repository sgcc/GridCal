"""Offline PyPSA adapter round-trip and source immutability tests."""

from pathlib import Path

import pypsa
import pandas as pd
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.exceptions import BackendCapabilityError


def test_unsupported_control_component_fails_explicitly() -> None:
    adapter = PypsaAdapter()
    network = small_network()
    network.add("GlobalConstraint", "co2", type="primary_energy",
                sense="<=", constant=10.0)
    sidecar = adapter.identity_sidecar(network, "sample")

    with pytest.raises(BackendCapabilityError, match="GlobalConstraint"):
        adapter.validate_model(network, sidecar)


def small_network() -> pypsa.Network:
    """Create a two-bus PyPSA network without external data."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110.0, x=0.0, y=0.0)
    network.add("Bus", "b", v_nom=110.0, x=1.0, y=0.0)
    network.add("Line", "ab", bus0="a", bus1="b", r=0.01, x=0.1, s_nom=100.0)
    network.add("Generator", "g", bus="a", p_nom=50.0, control="Slack")
    network.add("Load", "d", bus="b", p_set=20.0)
    return network


def test_clone_does_not_mutate_original() -> None:
    adapter = PypsaAdapter()
    original = small_network()
    clone = adapter.clone(original)
    clone.buses.loc["a", "v_nom"] = 220.0

    assert original.buses.loc["a", "v_nom"] == 110.0


def test_inventory_handles_optional_unconnected_link_port() -> None:
    adapter = PypsaAdapter()
    network = small_network()
    network.add("Link", "link", bus0="a", bus1="b", bus2="")
    network.links.loc["link", "bus2"] = pd.NA
    sidecar = adapter.identity_sidecar(network, "sample")

    assert adapter.inventory(network, sidecar).counts()["Link"] == 1


@pytest.mark.parametrize("format", ["netcdf", "csv"])
def test_round_trip_preserves_authoritative_sidecar(
    tmp_path: Path, format: str
) -> None:
    adapter = PypsaAdapter()
    original = small_network()
    sidecar = adapter.identity_sidecar(original, "sample")
    signature_before = adapter.topology_signature(original, sidecar)
    target = tmp_path / ("grid.nc" if format == "netcdf" else "grid_csv")

    restored = adapter.round_trip(original, sidecar, target, format)

    assert adapter.topology_signature(restored, sidecar) == signature_before
    assert sidecar.source_id("Bus", "a").source_key == "a"
    assert adapter.inventory(restored, sidecar).counts() == {
        "Bus": 2, "Generator": 1, "Line": 1, "Load": 1,
    }
    assert adapter.topology_signature(original, sidecar) == signature_before
