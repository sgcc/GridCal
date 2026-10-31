"""Tests for the public voltage cutoff policy contract."""

import math

import pytest

from psdcalc.reduction.policies.voltage_cutoff import VoltageCutoffPolicy, voltage_policy_protection
from psdcalc.reduction.protection.engine import ProtectionEngine, ProtectionSet
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.topology.simplifier import VoltageAwareTopologySimplifier
from psdcalc.reduction.topology.parallel_corridors import ParallelCorridorAnalyzer
from psdcalc.reduction.topology.parallel_provenance import (
    LPlusAssociation, ParallelSourceLedger, SourceProfileContract,
)
from psdcalc.reduction.exceptions import ConfigSemanticError
from psdcalc.reduction.core.ids import IdentityBinding, IdentitySidecar, StableId
from psdcalc.reduction.config.models import (
    AggregationConfig, ParallelCorridorConfig, PartitionOptions,
)
from psdcalc.reduction.materializers.multi_voltage import MultiVoltageQuotientBuilder
from psdcalc.reduction.mapping.compose import compose_maps
from psdcalc.reduction.planners.layer_energy import VoltageLayerEnergyPlanner
from psdcalc.reduction.validation.multi_voltage_final import network_semantic_signature


def _network(voltage_layers):
    import pypsa

    network = pypsa.Network()
    bindings = []
    for index, voltage in enumerate(voltage_layers):
        name = f"bus-{voltage}-{index}"
        network.add("Bus", name, v_nom=voltage)
        bindings.append(IdentityBinding("Bus", name, StableId("test", "Bus", name)))
    return network, bindings


def test_cutoff_keeps_boundary_layers_and_does_not_require_cutoff_layer():
    import pypsa

    network, bindings = _network((525, 230, 115, 37))
    sidecar = IdentitySidecar(bindings)

    resolved = VoltageCutoffPolicy(True, "freeze_at_or_above", 230).resolve(network, sidecar)

    assert resolved.retained_voltage_layers == (230.0, 525.0)
    assert resolved.compressible_voltage_layers == (37.0, 115.0)
    assert resolved.retained_bus_count == 2


def test_cutoff_330_retains_actual_525_layer_without_330_layer():
    import pypsa

    network, bindings = _network((525, 230, 115, 37))
    sidecar = IdentitySidecar(bindings)

    resolved = VoltageCutoffPolicy(True, "freeze_at_or_above", 330).resolve(network, sidecar)

    assert resolved.retained_voltage_layers == (525.0,)
    assert resolved.compressible_voltage_layers == (37.0, 115.0, 230.0)


@pytest.mark.parametrize(
    "layers,low_cutoff,high_cutoff",
    [((525, 230, 115, 66), 110, 115), ((66, 37, 27.5), 35, 37)],
)
def test_equivalent_cutoffs_have_same_resolved_semantics(layers, low_cutoff, high_cutoff):
    network, bindings = _network(layers)
    sidecar = IdentitySidecar(bindings)
    low = VoltageCutoffPolicy(True, cutoff_kv=low_cutoff).resolve(network, sidecar)
    high = VoltageCutoffPolicy(True, cutoff_kv=high_cutoff).resolve(network, sidecar)
    assert low.requested_cutoff_kv == low_cutoff
    assert high.requested_cutoff_kv == high_cutoff
    assert low.retained_voltage_layers == high.retained_voltage_layers
    assert low.retained_bus_ids == high.retained_bus_ids
    assert low.retained_line_ids == high.retained_line_ids
    assert low.resolved_policy_fingerprint == high.resolved_policy_fingerprint


def test_disabled_policy_resolves_base_without_protection():
    network, bindings = _network((525, 230))
    resolved = VoltageCutoffPolicy(False).resolve(network, IdentitySidecar(bindings))
    assert resolved.policy_id == "BASE"
    assert resolved.retained_bus_ids == frozenset()
    assert resolved.retained_line_ids == frozenset()


def test_retained_line_requires_both_endpoints_in_same_retained_layer():
    import pypsa

    network, bindings = _network((525, 525, 115))
    network.add("Line", "retained", bus0="bus-525-0", bus1="bus-525-1", r=0.1, x=0.2)
    bindings.append(IdentityBinding("Line", "retained", StableId("test", "Line", "retained")))
    network.add("Line", "cross", bus0="bus-525-0", bus1="bus-115-2", r=0.1, x=0.2)
    bindings.append(IdentityBinding("Line", "cross", StableId("test", "Line", "cross")))
    result = VoltageCutoffPolicy(True, cutoff_kv=500).resolve(network, IdentitySidecar(bindings))
    assert result.retained_line_ids == frozenset({StableId("test", "Line", "retained")})


def test_voltage_policy_protection_maps_retained_ids_to_existing_contract():
    network, bindings = _network((525, 230))
    resolved = VoltageCutoffPolicy(True, cutoff_kv=230).resolve(network, IdentitySidecar(bindings))
    protection = voltage_policy_protection(resolved)
    assert protection.singleton_buses == resolved.retained_bus_ids
    assert protection.preserved_components == resolved.retained_line_ids
    assert not protection.traced
    assert not protection.terminal_buses
    assert not protection.cannot_link


def test_protection_union_is_complete_idempotent_and_commutative():
    ids = [StableId("test", component, name) for component, name in (
        ("Bus", "a"), ("Bus", "b"), ("Line", "c"),
        ("Bus", "d"), ("Line", "e"), ("Bus", "f"), ("Line", "g"),
    )]
    a = ProtectionSet(frozenset(), frozenset(), frozenset(), frozenset({ids[0]}), frozenset())
    cutoff = ProtectionSet(frozenset(), frozenset({ids[2]}), frozenset(), frozenset({ids[1]}), frozenset())
    parallel = ProtectionSet(frozenset(), frozenset({ids[4]}), frozenset(), frozenset({ids[3]}), frozenset())
    lplus = ProtectionSet(frozenset(), frozenset({ids[6]}), frozenset(), frozenset({ids[5]}), frozenset())
    empty = ProtectionSet(frozenset(), frozenset(), frozenset(), frozenset(), frozenset())
    merged = a.union(cutoff).union(parallel).union(lplus)
    assert merged.singleton_buses == frozenset(ids[i] for i in (0, 1, 3, 5))
    assert merged.preserved_components == frozenset(ids[i] for i in (2, 4, 6))
    assert a.union(empty) == a == empty.union(a)
    assert merged.union(merged) == merged
    assert merged.union(a) == a.union(merged)


def _l1_fixture():
    import pypsa

    network = pypsa.Network()
    for name, voltage in (("h1", 230), ("h2", 230), ("h3", 230),
                          ("hr1", 230), ("hr2", 230),
                          ("l1", 66), ("l2", 66), ("l3", 66)):
        network.add("Bus", name, v_nom=voltage)
    for name, a, b in (("h12", "h1", "h2"), ("h23", "h2", "h3"),
                       ("h31", "h3", "h1"), ("hr1", "h1", "hr1"),
                       ("hr2", "hr1", "hr2"), ("l12", "l1", "l2"),
                       ("l23", "l2", "l3")):
        network.add("Line", name, bus0=a, bus1=b, r=0.01, x=0.1)
    network.add("Transformer", "tx", bus0="h2", bus1="l1", r=0.01, x=0.1)
    network.add("Load", "load", bus="l3", p_set=2.0)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "l1-test")
    return network, sidecar, adapter


def test_cutoff_preserves_high_voltage_identity_and_simplifies_lower_layer():
    network, sidecar, adapter = _l1_fixture()
    unprotected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    baseline = VoltageAwareTopologySimplifier().simplify(network, sidecar, unprotected)
    baseline_radial = {row.source_id: row for row in baseline.mappings.line_map}
    assert any(baseline_radial[sidecar.source_id("Line", name)].mapping_type != "preserved"
               for name in ("hr1", "hr2"))
    resolved = VoltageCutoffPolicy(True, cutoff_kv=230).resolve(network, sidecar)
    protections = ProtectionEngine().compile(adapter.inventory(network, sidecar), []).union(
        voltage_policy_protection(resolved))
    result = VoltageAwareTopologySimplifier().simplify(network, sidecar, protections)
    bus_rows = {row.source_id: row for row in result.mappings.bus_map}
    line_rows = {row.source_id: row for row in result.mappings.line_map}
    high_bus_ids = {sidecar.source_id("Bus", name) for name in ("h1", "h2", "h3", "hr1", "hr2")}
    high_line_ids = {sidecar.source_id("Line", name) for name in ("h12", "h23", "h31", "hr1", "hr2")}
    assert all(bus_rows[source].target_id == source and bus_rows[source].mapping_type == "preserved" for source in high_bus_ids)
    assert all(line_rows[source].target_id == source and line_rows[source].mapping_type == "preserved" for source in high_line_ids)
    assert len({bus_rows[source].target_id for source in high_bus_ids}) == len(high_bus_ids)
    assert len(result.network.buses) < len(network.buses)
    assert any(row.mapping_type == "absorbed_internal" for row in result.mappings.line_map if row.source_id not in high_line_ids)


def test_parallel_protection_and_cutoff_both_reach_l1():
    import pypsa

    network = pypsa.Network()
    for name, voltage in (("h1", 230), ("h2", 230), ("p1", 66), ("p2", 66)):
        network.add("Bus", name, v_nom=voltage)
    network.add("Line", "high", bus0="h1", bus1="h2", r=0.01, x=0.1)
    network.add("Line", "parallel-a", bus0="p1", bus1="p2", r=0.01, x=0.1)
    network.add("Line", "parallel-b", bus0="p1", bus1="p2", r=0.02, x=0.1)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "parallel-cutoff")
    resolved = VoltageCutoffPolicy(True, cutoff_kv=230).resolve(network, sidecar)
    cutoff = voltage_policy_protection(resolved)
    initial = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    parallel = ParallelCorridorAnalyzer().analyze(network, sidecar, initial)
    effective = initial.union(cutoff).union(ProtectionSet(
        frozenset(), parallel.protected_lines, frozenset(), parallel.mandatory_singletons, frozenset()))
    result = VoltageAwareTopologySimplifier().simplify(network, sidecar, effective)
    rows = {row.source_id: row for row in result.mappings.line_map}
    high_id = sidecar.source_id("Line", "high")
    pair_ids = {sidecar.source_id("Line", name) for name in ("parallel-a", "parallel-b")}
    assert high_id in cutoff.preserved_components and high_id not in parallel.protected_lines
    assert pair_ids.issubset(parallel.protected_lines) and pair_ids.isdisjoint(cutoff.preserved_components)
    assert rows[high_id].target_id == high_id and rows[high_id].mapping_type == "preserved"
    assert all(rows[item].target_id == item and rows[item].mapping_type == "preserved" for item in pair_ids)


def test_lplus_and_cutoff_both_reach_l1_with_ledger_ownership():
    import pypsa

    network = pypsa.Network()
    for name, voltage in (("h1", 230), ("h2", 230), ("l1", 66), ("l2", 66)):
        network.add("Bus", name, v_nom=voltage)
    network.add("Line", "high", bus0="h1", bus1="h2", r=0.01, x=0.1)
    network.add("Line", "base", bus0="l1", bus1="l2", r=0.02, x=0.2)
    network.add("ShuntImpedance", "terminal", bus="l1", b=-0.25)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "lplus-cutoff")
    base_id = sidecar.source_id("Line", "base")
    shunt_id = sidecar.source_id("ShuntImpedance", "terminal")
    terminal_id = sidecar.source_id("Bus", "l1")
    association = LPlusAssociation(base_id, "source-card-token", "FROM", terminal_id,
                                  shunt_id, -3.0, source_value_state="finite")
    ledger = ParallelSourceLedger(SourceProfileContract("programmatic", "unavailable"), (), (association,))
    ledger.validate(network, sidecar)
    resolved = VoltageCutoffPolicy(True, cutoff_kv=230).resolve(network, sidecar)
    cutoff = voltage_policy_protection(resolved)
    lplus = ProtectionSet(frozenset(), frozenset({base_id, shunt_id}),
                          frozenset({terminal_id}), frozenset(), frozenset())
    effective = ProtectionEngine().compile(adapter.inventory(network, sidecar), []).union(cutoff).union(lplus)
    result = VoltageAwareTopologySimplifier().simplify(network, sidecar, effective)
    rows = {row.source_id: row for row in result.mappings.line_map}
    high_id = sidecar.source_id("Line", "high")
    assert rows[high_id].target_id == high_id and rows[high_id].mapping_type == "preserved"
    assert rows[base_id].target_id == base_id and rows[base_id].mapping_type == "preserved"
    assert high_id in cutoff.preserved_components and high_id not in lplus.preserved_components
    assert base_id in lplus.preserved_components and base_id not in cutoff.preserved_components
    assert ledger.l_plus == (association,)
    assert (association.source_card_identity, association.terminal_side,
            association.terminal_bus_id, association.shunt_id,
            association.source_value_state, association.source_reactor_value) == (
                "source-card-token", "FROM", terminal_id, shunt_id, "finite", -3.0)


def test_cutoff_identity_lines_survive_l2_while_low_voltage_parallel_aggregates():
    """Cutoff-only lines retain identity while ordinary low-voltage parallels aggregate."""
    import pypsa

    network = pypsa.Network()
    for name, voltage in (("h1", 230), ("h2", 230), ("h3", 230),
                          ("l3", 66), ("l4", 66),
                          ("q1", 66), ("q2", 66), ("q3", 66)):
        network.add("Bus", name, v_nom=voltage)
    network.add("Line", "high-ordinary", bus0="h1", bus1="h2", r=0.01, x=0.1)
    network.add("Line", "high-parallel-a", bus0="h2", bus1="h3", r=0.02, x=0.1)
    network.add("Line", "high-parallel-b", bus0="h2", bus1="h3", r=0.03, x=0.1)
    network.add("Line", "high-inactive", bus0="h1", bus1="h3",
                r=0.05, x=0.2, active=False)
    for name, a, b in (("low-34-a", "l3", "l4"), ("low-34-b", "l3", "l4"),
                       ("low-q12", "q1", "q2"), ("low-q23", "q2", "q3")):
        network.add("Line", name, bus0=a, bus1=b, r=0.04, x=0.2)
    for name, bus in (("load-l4", "l4"), ("load-q1", "q1"),
                      ("load-q2", "q2"), ("load-q3", "q3")):
        network.add("Load", name, bus=bus, p_set=1.0)
    adapter = PypsaAdapter()
    source_sidecar = adapter.identity_sidecar(network, "l2-cutoff")
    before = network_semantic_signature(network)
    initial = ProtectionEngine().compile(adapter.inventory(network, source_sidecar), [])
    resolved = VoltageCutoffPolicy(True, cutoff_kv=230).resolve(network, source_sidecar)
    protection = initial.union(voltage_policy_protection(resolved))

    topology = VoltageAwareTopologySimplifier().simplify(
        network, source_sidecar, protection)
    parent_sidecar = adapter.identity_sidecar(topology.network, "l2-cutoff")
    # Retained objects have source identity through L1; use that L1 namespace for
    # the synthetic identity adapter used by the in-memory planner and Builder.
    l1_protection = ProtectionEngine().compile(
        adapter.inventory(topology.network, parent_sidecar), []).union(
            voltage_policy_protection(VoltageCutoffPolicy(
                True, cutoff_kv=230).resolve(topology.network, parent_sidecar)))
    plan = VoltageLayerEnergyPlanner().plan(
        topology.network, parent_sidecar, l1_protection,
        planner="pypsa_kmeans", n_clusters=6, seed=17, builders=(),
        options=PartitionOptions(), connectivity_policy="reject",
        aliases={}, per_layer={"66": 3, "230": 3},
    )
    built = MultiVoltageQuotientBuilder().build(
        topology.network, parent_sidecar, plan,
        AggregationConfig(aggregate_one_ports=["Load"]), l1_protection)
    assert plan.requested_cluster_count == plan.actual_cluster_count == 6
    assert built.requested_cluster_count == built.actual_cluster_count == 6

    source_line_ids = {source_sidecar.source_id("Line", name) for name in network.lines.index}
    l1_rows = {row.source_id: row for row in topology.mappings.line_map}
    l2_rows = {row.source_id: row for row in built.mappings.line_map}
    high_names = ("high-ordinary", "high-parallel-a", "high-parallel-b",
                  "high-inactive")
    high_line_ids = {parent_sidecar.source_id("Line", name) for name in high_names}
    for name in high_names:
        line_id = parent_sidecar.source_id("Line", name)
        assert l2_rows[line_id].target_id == line_id
        assert l2_rows[line_id].mapping_type == "preserved"
    high_bus_rows = {row.source_id: row for row in built.mappings.bus_map}
    high_bus_ids = {parent_sidecar.source_id("Bus", name)
                    for name in ("h1", "h2", "h3")}
    high_targets = {high_bus_rows[bus_id].target_id for bus_id in high_bus_ids}
    assert high_targets == high_bus_ids
    assert all(sum(row.target_id == target for row in built.mappings.bus_map) == 1
               for target in high_targets)
    assert built.preliminary.checks["protected_parallel_line_semantics"], (
        built.preliminary.details,
        {key: frame.to_dict() for key, frame in
         built.network.components["Line"].dynamic.items() if not frame.empty},
    )
    assert built.parallel_plan is None
    low_parallel_ids = {parent_sidecar.source_id("Line", name)
                        for name in ("low-34-a", "low-34-b")}
    low_targets = {l2_rows[line_id].target_id for line_id in low_parallel_ids}
    assert len(low_targets) == 1
    assert built.network.lines.shape[0] < topology.network.lines.shape[0]
    composed_lines = compose_maps(topology.mappings.line_map, built.mappings.line_map)
    composed_by_source = {row.source_id: row for row in composed_lines}
    for name in high_names:
        source_id = source_sidecar.source_id("Line", name)
        assert composed_by_source[source_id].target_id == source_id
        assert composed_by_source[source_id].mapping_type == "preserved"
    assert {row.source_id for row in topology.mappings.line_map} == source_line_ids
    assert len(composed_lines) == len(source_line_ids)
    assert not bool(built.network.lines.loc[
        built.target_sidecar.backend_name(parent_sidecar.source_id("Line", "high-inactive")),
        "active",
    ])
    assert len(built.network.lines) < len(topology.network.lines)
    assert not built.preliminary.checks["cycle_corridors"]
    assert all(l1_rows[source_id].target_id == source_id and
               l1_rows[source_id].mapping_type == "preserved"
               for source_id in high_line_ids)
    assert len({row.source_id for row in composed_lines}) == len(composed_lines)
    preserve_parallel = MultiVoltageQuotientBuilder().build(
        topology.network, parent_sidecar, plan,
        AggregationConfig(
            parallel_corridors=ParallelCorridorConfig(
                policy="preserve_protected_members"),
        ),
        l1_protection,
    )
    preserved_parallel_ids = {
        parent_sidecar.source_id("Line", name)
        for name in ("high-parallel-a", "high-parallel-b", "low-34-a", "low-34-b")
    }
    preserved_parallel_rows = {
        row.source_id: row for row in preserve_parallel.mappings.line_map
    }
    assert all(preserved_parallel_rows[source_id].target_id == source_id and
               preserved_parallel_rows[source_id].mapping_type == "preserved"
               for source_id in preserved_parallel_ids)
    assert preserve_parallel.parallel_plan is not None
    assert preserve_parallel.parallel_plan.protected_corridor_lines == preserved_parallel_ids
    assert parent_sidecar.source_id("Line", "high-ordinary") not in (
        preserve_parallel.parallel_plan.protected_corridor_lines)
    assert network_semantic_signature(network) == before


def test_cutoff_and_lplus_line_identity_survive_l2_with_shunt_semantics():
    """The common L2 identity path preserves cutoff and L+ Lines together."""
    import pypsa

    network = pypsa.Network()
    for name, voltage in (("h1", 230), ("h2", 230), ("l1", 66),
                          ("l2", 66), ("l3", 66), ("l4", 66)):
        network.add("Bus", name, v_nom=voltage)
    network.add("Line", "high", bus0="h1", bus1="h2", r=0.01, x=0.1)
    for name, a, b in (("base", "l1", "l2"), ("low-23", "l2", "l3"),
                       ("low-34", "l3", "l4")):
        network.add("Line", name, bus0=a, bus1=b, r=0.02, x=0.2)
    network.add("ShuntImpedance", "terminal", bus="l1", b=-0.25)
    adapter = PypsaAdapter()
    source_sidecar = adapter.identity_sidecar(network, "l2-lplus")
    before = network_semantic_signature(network)
    base_id = source_sidecar.source_id("Line", "base")
    shunt_id = source_sidecar.source_id("ShuntImpedance", "terminal")
    terminal_id = source_sidecar.source_id("Bus", "l1")
    association = LPlusAssociation(
        base_id, "source-card-token", "FROM", terminal_id, shunt_id, -3.0,
        source_value_state="finite")
    ledger = ParallelSourceLedger(
        SourceProfileContract("programmatic", "unavailable"), (), (association,))
    resolved = VoltageCutoffPolicy(True, cutoff_kv=230).resolve(network, source_sidecar)
    initial = ProtectionEngine().compile(adapter.inventory(network, source_sidecar), [])
    protection = initial.union(voltage_policy_protection(resolved)).union(
        ProtectionSet(frozenset(), frozenset({base_id, shunt_id}),
                      frozenset({terminal_id}), frozenset(), frozenset()))
    topology = VoltageAwareTopologySimplifier().simplify(
        network, source_sidecar, protection)
    parent_sidecar = adapter.identity_sidecar(topology.network, "l2-lplus")
    ledger.validate(topology.network, parent_sidecar)
    parent_protection = ProtectionEngine().compile(
        adapter.inventory(topology.network, parent_sidecar), []).union(
            voltage_policy_protection(VoltageCutoffPolicy(
                True, cutoff_kv=230).resolve(topology.network, parent_sidecar))).union(
                    ProtectionSet(frozenset(), frozenset({base_id, shunt_id}),
                                  frozenset({terminal_id}), frozenset(), frozenset()))
    plan = VoltageLayerEnergyPlanner().plan(
        topology.network, parent_sidecar, parent_protection,
        planner="pypsa_kmeans", n_clusters=5, seed=17, builders=(),
        options=PartitionOptions(), connectivity_policy="reject",
        aliases={}, per_layer={"66": 3, "230": 2},
    )
    built = MultiVoltageQuotientBuilder().build(
        topology.network, parent_sidecar, plan,
        AggregationConfig(aggregate_one_ports=["Load"], parallel_corridors=
                          ParallelCorridorConfig(policy="preserve_protected_members")),
        parent_protection, source_ledger=ledger)
    l2_rows = {row.source_id: row for row in built.mappings.line_map}
    for line_id in (parent_sidecar.source_id("Line", "high"), base_id):
        assert l2_rows[line_id].target_id == line_id
        assert l2_rows[line_id].mapping_type == "preserved"
    assert shunt_id in {row.source_id for row in built.mappings.device_map}
    assert built.preliminary.checks["protected_parallel_line_semantics"], built.preliminary.details
    assert built.preliminary.checks["l_plus_association_coverage"]
    assert built.preliminary.checks["l_plus_terminal_owner_consistency"]
    assert built.preliminary.checks["l_plus_semantic_round_trip"]
    assert built.preliminary.checks["l_plus_source_value_state_consistency"]
    assert 66 < 230
    assert len(built.network.buses) < len(topology.network.buses)
    assert network_semantic_signature(network) == before


@pytest.mark.parametrize("cutoff", [0, -1, math.nan, math.inf, True, None])
def test_invalid_enabled_cutoff_is_rejected(cutoff):
    with pytest.raises(ConfigSemanticError):
        VoltageCutoffPolicy(True, cutoff_kv=cutoff)


def test_fingerprint_is_deterministic_for_same_resolved_policy():
    network, bindings = _network((525, 230, 115))
    sidecar = IdentitySidecar(bindings)
    first = VoltageCutoffPolicy(True, cutoff_kv=110).resolve(network, sidecar)
    second = VoltageCutoffPolicy(True, cutoff_kv=115).resolve(network, sidecar)
    assert first.resolved_policy_fingerprint == second.resolved_policy_fingerprint
