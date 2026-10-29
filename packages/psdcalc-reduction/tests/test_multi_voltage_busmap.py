"""Stable-ID multi-voltage partition validation and target allocation."""

import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.exceptions import PartitionError
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.planners.multi_voltage_busmap import MultiVoltageBusmapValidator
from psdcalc.reduction.protection.engine import ProtectionEngine
from psdcalc.reduction.topology.voltage_layers import VoltageLayerClassifier


def fixture_network() -> pypsa.Network:
    """Return a two-layer network with a Transformer interface."""
    network = pypsa.Network()
    for name, voltage in (("a", 110), ("b", 110), ("c", 110),
                          ("x", 10), ("y", 10)):
        network.add("Bus", name, v_nom=voltage)
    network.add("Line", "ab", bus0="a", bus1="b", x=0.1)
    network.add("Line", "bc", bus0="b", bus1="c", x=0.1)
    network.add("Line", "xy", bus0="x", bus1="y", x=0.1)
    network.add("Transformer", "tx", bus0="a", bus1="x", x=0.1)
    return network


def context(network: pypsa.Network) -> tuple:
    """Build sidecar, voltage layers, protection, and validator."""
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    layers = VoltageLayerClassifier().classify(network, sidecar)
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    return sidecar, layers, protection, MultiVoltageBusmapValidator()


def plan_for(sidecar: object, assignments: dict[str, str],
             requested: int | None = None,
             policy: str = "reject") -> PartitionPlan:
    """Construct one transient plan with stable cluster targets."""
    stable = {sidecar.source_id("Bus", name): StableId("n", "BusCluster", label)
              for name, label in assignments.items()}
    backend = {name: stable[sidecar.source_id("Bus", name)].encode()
               for name in assignments}
    count = len(set(stable.values()))
    return PartitionPlan(stable, backend, requested or count, count, policy)


def test_transformer_terminal_must_be_singleton() -> None:
    """A Transformer endpoint cannot be grouped with a free same-layer bus."""
    network = fixture_network()
    sidecar, layers, protection, validator = context(network)
    partition = plan_for(sidecar, {"a": "hv", "b": "hv", "c": "c",
                                   "x": "x", "y": "y"})

    with pytest.raises(PartitionError, match="singleton"):
        validator.validate(network, sidecar, partition, layers, protection)


def test_cross_voltage_target_is_rejected() -> None:
    """Stable targets never span exact nominal-voltage layers."""
    network = fixture_network()
    sidecar, layers, protection, validator = context(network)
    partition = plan_for(sidecar, {"a": "a", "b": "shared", "c": "c",
                                   "x": "x", "y": "shared"})

    with pytest.raises(PartitionError, match="cross-voltage target"):
        validator.validate(network, sidecar, partition, layers, protection)


def test_floor_uses_free_induced_components_after_singleton_removal() -> None:
    """A singleton articulation separates free leaves into two clusters."""
    network = pypsa.Network()
    for name in ("center", "left", "right"):
        network.add("Bus", name, v_nom=110)
    for name in ("left", "right"):
        network.add("Line", name, bus0="center", bus1=name, x=0.1)
    sidecar, layers, protection, validator = context(network)
    protected = type(protection)(protection.traced, protection.preserved_components,
                                 protection.terminal_buses,
                                 frozenset({sidecar.source_id("Bus", "center")}),
                                 protection.cannot_link)

    targets = validator.allocate_targets(network, sidecar, layers, protected, 3)

    assert targets == {"110": 3}
    with pytest.raises(PartitionError, match="minimum"):
        validator.allocate_targets(network, sidecar, layers, protected, 2)


def test_all_per_layer_overrides_must_sum_to_global_target() -> None:
    """Fully specified layer targets cannot leave an implicit remainder."""
    network = fixture_network()
    sidecar, layers, protection, validator = context(network)

    with pytest.raises(PartitionError, match="sum"):
        validator.allocate_targets(network, sidecar, layers, protection, 5,
                                   per_layer={"10": 2, "110": 2})


def test_split_disconnected_is_deterministic_and_records_actual_count() -> None:
    """Connectivity repair creates stable split IDs without changing request."""
    network = pypsa.Network()
    for name in ("a", "b", "c"):
        network.add("Bus", name, v_nom=110)
    network.add("Line", "ab", bus0="a", bus1="b", x=0.1)
    network.add("Line", "bc", bus0="b", bus1="c", x=0.1)
    sidecar, layers, protection, validator = context(network)
    partition = plan_for(sidecar, {"a": "same", "b": "middle", "c": "same"},
                         requested=2, policy="split_disconnected")

    first = validator.validate(network, sidecar, partition, layers, protection)
    second = validator.validate(network, sidecar, partition, layers, protection)

    assert first.requested_cluster_count == 2
    assert first.actual_cluster_count == 3
    assert first.global_busmap == second.global_busmap


def test_largest_remainder_allocates_only_free_capacity() -> None:
    """The remaining budget goes to a layer with available free Bus capacity."""
    network = fixture_network()
    sidecar, layers, protection, validator = context(network)

    assert validator.allocate_targets(
        network, sidecar, layers, protection, 5
    ) == {"10": 2, "110": 3}


def test_region_boundary_blocks_a_same_voltage_cluster() -> None:
    """A common voltage does not permit a cluster across declared regions."""
    network = fixture_network()
    network.buses.loc["b", "country"] = "R1"
    network.buses.loc["c", "country"] = "R2"
    sidecar, layers, protection, validator = context(network)
    partition = plan_for(sidecar, {"a": "a", "b": "shared", "c": "shared",
                                   "x": "x", "y": "y"})

    with pytest.raises(PartitionError, match="subnetwork or region"):
        validator.validate(network, sidecar, partition, layers, protection)
