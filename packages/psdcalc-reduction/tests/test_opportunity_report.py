import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.planners.multi_voltage_busmap import MultiVoltageBusmapValidator
from psdcalc.reduction.topology.simplifier import VoltageAwareTopologySimplifier
from psdcalc.reduction.topology.voltage_layers import VoltageLayerClassifier
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
    assert (report.source_bus_count, report.simplified_bus_count,
            report.final_bus_count, report.minimum_required_bus_count) == (3, 1, 1, 1)
    assert report.topology_only_realized_compression_fraction == 2 / 3
    assert report.total_realized_compression_fraction == 2 / 3
    assert report.per_layer['110']['source_bus_count'] == 3
    assert report.per_layer['110']['simplified_bus_count'] == 1
    assert report.per_layer['110']['final_bus_count'] == 1


def test_report_tracks_layer_plan_counts_and_separate_anchor_categories():
    n = pypsa.Network()
    for bus in ('a', 'b'):
        n.add('Bus', bus, v_nom=110)
    n.add('Line', 'ab', bus0='a', bus1='b', r=0.1, x=0.2)
    n.add('ShuntImpedance', 'sh', bus='a', g=0.01)
    n.add('Generator', 'g', bus='a', control='PV', p_nom=1)
    sidecar = PypsaAdapter().identity_sidecar(n, 'test')
    protection = ProtectionSet(*(frozenset() for _ in range(5)))
    topology = VoltageAwareTopologySimplifier().simplify(n, sidecar, protection)
    report = build_compression_opportunity_report(
        n, sidecar, topology, topology.network, topology.mappings, protection,
        layer_counts={'110': (2, 2, 2)},
    )
    assert report.shunt_endpoint_count == 1
    assert report.control_endpoint_count == 1
    assert report.anchor_overlaps['shunt_control'] == 1
    assert report.per_layer['110']['requested_cluster_count'] == 2
    assert report.per_layer['110']['planned_cluster_count'] == 2
    assert report.per_layer['110']['actual_cluster_count'] == 2


def test_report_floor_uses_planner_active_topology_floor():
    n = pypsa.Network()
    for bus in ('a', 'b'):
        n.add('Bus', bus, v_nom=110)
    n.add('Line', 'ab', bus0='a', bus1='b', active=False, r=0.1, x=0.2)
    sidecar = PypsaAdapter().identity_sidecar(n, 'test')
    protection = ProtectionSet(*(frozenset() for _ in range(5)))
    layers = VoltageLayerClassifier().classify(n, sidecar)
    floor = MultiVoltageBusmapValidator().minimum_required_bus_counts(
        n, sidecar, layers, protection)
    topology = VoltageAwareTopologySimplifier().simplify(n, sidecar, protection)
    report = build_compression_opportunity_report(
        n, sidecar, topology, topology.network, topology.mappings, protection)
    assert floor == {'110': 2}
    assert report.minimum_required_bus_count == sum(floor.values())
    assert report.theoretical_maximum_bus_compression_fraction == 0


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
