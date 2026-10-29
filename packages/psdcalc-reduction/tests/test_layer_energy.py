"""Offline tests for post-simplification voltage-layer energy planning."""

import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import PartitionOptions
from psdcalc.reduction.exceptions import PartitionError
from psdcalc.reduction.planners.layer_energy import VoltageLayerEnergyPlanner
from psdcalc.reduction.protection.engine import ProtectionEngine


def three_layer_network() -> pypsa.Network:
    """Create one compressible high-voltage corridor and two interfaces."""
    network = pypsa.Network()
    for name, voltage in (("a", 110), ("b", 110), ("c", 110),
                          ("x", 35), ("y", 35), ("u", 10), ("v", 10)):
        network.add("Bus", name, v_nom=voltage)
    for name, bus0, bus1 in (("ab", "a", "b"), ("bc", "b", "c"),
                             ("xy", "x", "y"), ("uv", "u", "v")):
        network.add("Line", name, bus0=bus0, bus1=bus1, r=0.01, x=0.1)
    network.add("Transformer", "tx1", bus0="a", bus1="x", x=0.1)
    network.add("Transformer", "tx2", bus0="y", bus1="u", x=0.1)
    return network


def test_layer_energy_planner_preserves_interfaces_and_seed() -> None:
    """The seeded planner merges only free buses within their voltage layer."""
    network = three_layer_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    kwargs = dict(
        planner="pypsa_kmeans", n_clusters=6, seed=17,
        builders=(), options=PartitionOptions(),
        connectivity_policy="reject", aliases={},
        per_layer={"10": 2, "35": 2, "110": 2},
    )
    first = VoltageLayerEnergyPlanner().plan(
        network, sidecar, protection, **kwargs,
    )
    second = VoltageLayerEnergyPlanner().plan(
        network, sidecar, protection, **kwargs,
    )
    assert first.stable_busmap == second.stable_busmap
    assert first.actual_cluster_count == 6
    assert first.stable_busmap[sidecar.source_id("Bus", "b")] == (
        first.stable_busmap[sidecar.source_id("Bus", "c")]
    )
    for name in ("a", "x", "y", "u"):
        source = sidecar.source_id("Bus", name)
        assert sum(value == first.stable_busmap[source]
                   for value in first.stable_busmap.values()) == 1


def test_layer_energy_enforces_cluster_size_bounds() -> None:
    """A configured maximum cannot be bypassed in the multi-voltage path."""
    network = three_layer_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    with pytest.raises(PartitionError, match="cluster-size"):
        VoltageLayerEnergyPlanner().plan(
            network, sidecar, protection,
            planner="pypsa_kmeans", n_clusters=6, seed=17,
            builders=(), options=PartitionOptions(),
            per_layer={"10": 2, "35": 2, "110": 2},
            max_cluster_size=1,
        )
