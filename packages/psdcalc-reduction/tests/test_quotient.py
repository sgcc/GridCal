"""PyPSA quotient materialization tests."""

import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import AggregationConfig
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.exceptions import BackendCapabilityError, MaterializationError
from psdcalc.reduction.materializers.quotient import QuotientMaterializer
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.protection.engine import ProtectionEngine


def chain_network() -> pypsa.Network:
    """Build a three-bus same-voltage network."""
    network = pypsa.Network()
    for index in range(3):
        network.add("Bus", f"b{index}", v_nom=110.0, x=float(index), y=0.0)
    for index in range(2):
        network.add("Line", f"l{index}", bus0=f"b{index}", bus1=f"b{index+1}",
                    r=0.01, x=0.1, s_nom=100.0)
    network.add("Generator", "g", bus="b0", p_nom=50.0)
    network.add("Load", "d", bus="b2", p_set=20.0)
    return network


def partition(sidecar: object) -> PartitionPlan:
    """Map the first two buses into one quotient bus."""
    c0 = StableId("n", "BusCluster", "c0")
    c1 = StableId("n", "BusCluster", "c1")
    stable = {sidecar.source_id("Bus", "b0"): c0,
              sidecar.source_id("Bus", "b1"): c0,
              sidecar.source_id("Bus", "b2"): c1}
    return PartitionPlan(stable, {f"b{i}": stable[sidecar.source_id("Bus", f"b{i}")].encode()
                                  for i in range(3)}, 2, 2, "reject")


def test_quotient_keeps_original_and_returns_linemap() -> None:
    network = chain_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    before = adapter.topology_signature(network, sidecar)

    result = QuotientMaterializer().materialize(
        network, sidecar, partition(sidecar), AggregationConfig(), protected
    )

    assert len(result.network.buses) == 2
    assert len(result.network.lines) == 1
    assert "l1" in result.linemap
    assert adapter.topology_signature(network, sidecar) == before


def test_transformer_network_is_rejected_before_quotient() -> None:
    network = chain_network()
    network.add("Transformer", "tx", bus0="b0", bus1="b1", x=0.1, s_nom=100.0)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])

    with pytest.raises(BackendCapabilityError, match="transformer"):
        QuotientMaterializer().materialize(
            network, sidecar, partition(sidecar), AggregationConfig(), protected
        )


def test_unknown_preserve_component_is_explicit_error() -> None:
    network = chain_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    config = AggregationConfig(preserve_components=["NotAComponent"])

    with pytest.raises(MaterializationError, match="NotAComponent"):
        QuotientMaterializer().materialize(
            network, sidecar, partition(sidecar), config, protected
        )
