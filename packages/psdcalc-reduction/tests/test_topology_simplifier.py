"""Ring and transformer interface behavior on offline PyPSA fixtures."""

import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.protection.engine import ProtectionEngine
from psdcalc.reduction.topology.cycle_corridors import analyze_cycle_corridors
from psdcalc.reduction.topology.simplifier import VoltageAwareTopologySimplifier
from psdcalc.reduction.topology.simplifier import _cycle_lines, _line_graph


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
    assert result.records[0].parent_grid_hash != result.records[0].input_signature


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


def test_cross_region_feeder_is_retained_with_reason() -> None:
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110, country="west")
    network.add("Bus", "b", v_nom=110, country="east")
    network.add("Line", "ab", bus0="a", bus1="b", r=0.01, x=0.1)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])

    result = VoltageAwareTopologySimplifier().simplify(network, sidecar, protection)

    assert set(result.network.buses.index) == {"a", "b"}
    assert any(row.get("reason") == "cross_region" for row in result.topology_roles)


def test_three_port_link_terminal_is_an_anchor() -> None:
    network = pypsa.Network()
    for name in ("a", "b", "c", "d"):
        network.add("Bus", name, v_nom=110)
    network.add("Line", "cd", bus0="c", bus1="d", r=0.01, x=0.1)
    network.add("Link", "link", bus0="a", bus1="b", bus2="c")
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])

    result = VoltageAwareTopologySimplifier().simplify(network, sidecar, protection)

    assert "c" in result.network.buses.index
    assert result.network.links.loc["link", "bus2"] == "c"
    assert result.structural.status == "STRUCTURALLY_VALID"


def test_inactive_line_is_excluded_from_ring_and_simplifier_graph() -> None:
    """A switched-off closing edge cannot create a ring or protect a feeder."""
    network = pypsa.Network()
    for name in ("a", "b", "c"):
        network.add("Bus", name, v_nom=110)
    network.add("Line", "ab", bus0="a", bus1="b", x=0.1)
    network.add("Line", "bc", bus0="b", bus1="c", x=0.1)
    network.add("Line", "ca", bus0="c", bus1="a", x=0.1, active=False)
    graph = _line_graph(network, 110.0)
    assert {frozenset(edge) for edge in graph.edges} == {
        frozenset(("a", "b")), frozenset(("b", "c")),
    }
    assert not _cycle_lines(network, graph)
    assert not analyze_cycle_corridors(network).cycle_lines
