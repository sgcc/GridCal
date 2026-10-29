import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.simplifier import VoltageAwareTopologySimplifier
from psdcalc.reduction.reporting.opportunity import build_compression_opportunity_report


def test_report_chain():
    n = pypsa.Network()
    for bus in ('a', 'b', 'c'):
        n.add('Bus', bus, v_nom=110)
    n.add('Line', 'ab', bus0='a', bus1='b', r=0.1, x=0.2)
    n.add('Line', 'bc', bus0='b', bus1='c', r=0.1, x=0.2)
    sidecar = PypsaAdapter().identity_sidecar(n, 'test')
    protection = ProtectionSet(*(frozenset() for _ in range(5)))
    topology = VoltageAwareTopologySimplifier().simplify(n, sidecar, protection)
    report = build_compression_opportunity_report(n, sidecar, topology, topology.network, topology.mappings, protection)
    assert report.ring_endpoint_count == 0
    assert report.radial_tree_opportunities == 1
    assert report.realized_bus_compression_fraction == 2 / 3
    assert report.theoretical_maximum_bus_compression_fraction == 2 / 3


def test_report_rejects_tampered_bus_mapping():
    from dataclasses import replace
    import pytest
    from psdcalc.reduction.exceptions import MappingError

    n = pypsa.Network()
    n.add('Bus', 'a', v_nom=110)
    n.add('Bus', 'b', v_nom=110)
    sidecar = PypsaAdapter().identity_sidecar(n, 'test')
    protection = ProtectionSet(*(frozenset() for _ in range(5)))
    topology = VoltageAwareTopologySimplifier().simplify(n, sidecar, protection)
    bad = replace(topology.mappings, bus_map=topology.mappings.bus_map[:1])
    with pytest.raises(MappingError):
        build_compression_opportunity_report(n, sidecar, topology, n, bad, protection)
