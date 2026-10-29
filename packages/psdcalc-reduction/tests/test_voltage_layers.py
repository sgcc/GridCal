"""Voltage layer classification and partition-budget contracts."""

import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.exceptions import BackendCapabilityError
from psdcalc.reduction.topology.voltage_layers import VoltageLayerClassifier


def test_exact_voltage_layers_require_declared_alias() -> None:
    """Nearby nominal voltages remain distinct unless explicitly aliased."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110.0)
    network.add("Bus", "b", v_nom=109.9)
    sidecar = PypsaAdapter().identity_sidecar(network, "n")

    classifier = VoltageLayerClassifier()
    assert len(classifier.classify(network, sidecar)) == 2
    assert len(classifier.classify(network, sidecar, aliases={109.9: 110.0})) == 1


def test_cross_voltage_line_fails_before_materialization() -> None:
    """A Line is never treated as a Transformer interface."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110.0)
    network.add("Bus", "b", v_nom=10.0)
    network.add("Line", "ab", bus0="a", bus1="b", x=0.1)
    sidecar = PypsaAdapter().identity_sidecar(network, "n")

    with pytest.raises(BackendCapabilityError, match="cross-voltage Line"):
        VoltageLayerClassifier().classify(network, sidecar)


@pytest.mark.parametrize("voltage", [0.0, float("nan"), float("inf")])
def test_invalid_bus_voltage_fails(voltage: float) -> None:
    """Zero and nonfinite nominal voltages cannot define a layer."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=1)
    network.buses.loc["a", "v_nom"] = voltage
    sidecar = PypsaAdapter().identity_sidecar(network, "n")
    with pytest.raises(BackendCapabilityError, match="v_nom"):
        VoltageLayerClassifier().classify(network, sidecar)


@pytest.mark.parametrize("aliases", [
    {"bad": 110.0}, {10.0: 0.0}, {10.0: float("nan")},
    {float("inf"): 110.0},
])
def test_invalid_alias_fails(aliases: dict[object, object]) -> None:
    """Aliases must be explicit finite positive numeric pairs."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=10)
    sidecar = PypsaAdapter().identity_sidecar(network, "n")
    with pytest.raises(BackendCapabilityError, match="alias"):
        VoltageLayerClassifier().classify(network, sidecar, aliases=aliases)


def test_alias_cannot_collapse_transformer_layers() -> None:
    """An explicit alias still cannot turn a Transformer into a self-layer."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110)
    network.add("Bus", "b", v_nom=10)
    network.add("Transformer", "tx", bus0="a", bus1="b", x=0.1)
    sidecar = PypsaAdapter().identity_sidecar(network, "n")
    with pytest.raises(BackendCapabilityError, match="distinct voltage layers"):
        VoltageLayerClassifier().classify(network, sidecar,
                                          aliases={10.0: 110.0})
