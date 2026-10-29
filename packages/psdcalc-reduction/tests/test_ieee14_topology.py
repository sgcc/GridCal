"""Offline IEEE-14 topology fixture with synthetic branch parameters."""

import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.protection.engine import ProtectionEngine
from psdcalc.reduction.topology.simplifier import VoltageAwareTopologySimplifier


def test_ieee14_topology_keeps_cycles_and_transformer_interfaces() -> None:
    network = pypsa.Network()
    for number in range(1, 15):
        network.add("Bus", str(number), v_nom=110.0)
    lines = [
        (1, 2), (1, 5), (2, 3), (2, 4), (2, 5), (3, 4), (4, 5),
        (6, 11), (6, 12), (6, 13), (7, 8), (7, 9), (9, 10),
        (9, 14), (10, 11), (12, 13), (13, 14),
    ]
    for a, b in lines:
        network.add("Line", f"{a}-{b}", bus0=str(a), bus1=str(b),
                    r=0.01, x=0.1, s_nom=100)
    for a, b in ((4, 7), (4, 9), (5, 6)):
        network.add("Transformer", f"{a}-{b}", bus0=str(a), bus1=str(b),
                    r=0.01, x=0.1)
    network.add("Generator", "slack", bus="1", control="Slack", p_nom=100)
    network.add("Load", "demand", bus="14", p_set=10)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "ieee14")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])

    result = VoltageAwareTopologySimplifier().simplify(network, sidecar, protection)

    assert result.structural.status == "STRUCTURALLY_VALID"
    assert set(result.network.transformers.index) == {"4-7", "4-9", "5-6"}
    assert all(name in result.network.lines.index for name in result.cycle_edges)
    assert "1-2" in result.cycle_edges
    assert "7-8" not in result.cycle_edges
