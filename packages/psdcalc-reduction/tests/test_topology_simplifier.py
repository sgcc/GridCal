"""Ring and transformer interface behavior on offline PyPSA fixtures."""

import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.protection.engine import ProtectionEngine
from psdcalc.reduction.topology.simplifier import VoltageAwareTopologySimplifier


def test_ring_feeder_and_transformer_interface() -> None:
    original = pypsa.Network()
    for name, voltage in (("a", 110), ("b", 110), ("c", 110),
                          ("r1", 110), ("r2", 110), ("lv", 10)):
        original.add("Bus", name, v_nom=voltage)
    for name, a, b in (("ab", "a", "b"), ("bc", "b", "c"),
                       ("ca", "c", "a"), ("ar1", "a", "r1"),
                       ("r1r2", "r1", "r2")):
        original.add("Line", name, bus0=a, bus1=b, r=0.01, x=0.1)
    original.add("Transformer", "t", bus0="b", bus1="lv", x=0.1, r=0.01)
    original.add("Load", "demand", bus="r2", p_set=5.0)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(original, "n")
    protection = ProtectionEngine().compile(adapter.inventory(original, sidecar), [])
    before = adapter.topology_signature(original, sidecar)

    result = VoltageAwareTopologySimplifier().simplify(original, sidecar, protection)

    assert set(result.network.lines.index) == {"ab", "bc", "ca"}
    assert set(result.network.transformers.index) == {"t"}
    assert set(result.network.buses.index) == {"a", "b", "c", "lv"}
    assert result.network.loads.loc["demand", "bus"] == "a"
    assert result.status == "STRUCTURALLY_SIMPLIFIED"
    assert result.structural.status == "STRUCTURALLY_VALID"
    assert {"ab", "bc", "ca"}.issubset(result.cycle_edges)
    assert adapter.topology_signature(original, sidecar) == before
    assert len(result.records) >= 1


def test_zero_injection_series_chain_collapses() -> None:
    original = pypsa.Network()
    for name in ("a", "x", "y", "b"):
        original.add("Bus", name, v_nom=110)
    for name, start, end in (("ax", "a", "x"), ("xy", "x", "y"),
                             ("yb", "y", "b")):
        original.add("Line", name, bus0=start, bus1=end, r=0.01, x=0.1)
    original.add("Generator", "slack", bus="a", control="Slack", p_nom=10)
    original.add("Generator", "pv", bus="b", control="PV", p_nom=10)
    original.add("Load", "load", bus="b", p_set=2)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(original, "n")
    protection = ProtectionEngine().compile(adapter.inventory(original, sidecar), [])

    result = VoltageAwareTopologySimplifier().simplify(original, sidecar, protection)

    assert set(result.network.buses.index) == {"a", "b"}
    assert len(result.network.lines) == 1
    line = result.network.lines.iloc[0]
    assert {line.bus0, line.bus1} == {"a", "b"}
    assert abs(line.r - 0.03) < 1e-12
    assert result.structural.status == "STRUCTURALLY_VALID"
