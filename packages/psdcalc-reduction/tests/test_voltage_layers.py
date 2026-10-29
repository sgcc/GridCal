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
