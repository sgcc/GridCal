"""Programmatic active-topology contracts for protected parallel Lines."""

import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import AggregationConfig, ParallelCorridorConfig
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.materializers.multi_voltage import MultiVoltageQuotientBuilder
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.parallel_corridors import ParallelCorridorAnalyzer
from psdcalc.reduction.topology.parallel_provenance import (
    LPlusAssociation, ParallelSourceLedger, SourceProfileContract,
)
from psdcalc.reduction.validation.protected_parallel import (
    ProtectedParallelStructuralValidator,
)


def _network(*, triangle: bool = False, inactive: bool = False,
             num_parallel: int = 1) -> pypsa.Network:
    """Return a tiny one-layer network with independent Line identities."""
    n = pypsa.Network()
    for bus in ("a", "b", "c") if triangle else ("a", "b"):
        n.add("Bus", bus, v_nom=110.0)
    n.add("Line", "l1", bus0="a", bus1="b", r=0.1, x=0.2,
          num_parallel=num_parallel)
    n.add("Line", "l2", bus0="a", bus1="b", r=0.2, x=0.3,
          active=not inactive)
    if triangle:
        n.add("Line", "l3", bus0="b", bus1="c", r=0.1, x=0.2)
        n.add("Line", "l4", bus0="c", bus1="a", r=0.1, x=0.2)
    return n


def _empty_protection() -> ProtectionSet:
    """Return no explicit protection actions."""
    return ProtectionSet(frozenset(), frozenset(), frozenset(),
                         frozenset(), frozenset())


def test_two_active_parallel_lines_form_parallel_only_cycle() -> None:
    """A simple-graph bridge with multiplicity two is protected."""
    n = _network(num_parallel=2)
    sidecar = PypsaAdapter().identity_sidecar(n, "test")
    plan = ParallelCorridorAnalyzer().analyze(n, sidecar, _empty_protection())
    corridor, = plan.corridors
    assert corridor.cycle_type == "parallel_only"
    assert {sid.source_key for sid in corridor.active_members} == {"l1", "l2"}
    assert corridor.protected
    assert len(plan.mandatory_singletons) == 2


def test_parallel_triangle_is_both_but_not_parallel_only() -> None:
    """Parallel multiplicity on a simple ring uses the exclusive both label."""
    n = _network(triangle=True)
    sidecar = PypsaAdapter().identity_sidecar(n, "test")
    plan = ParallelCorridorAnalyzer().analyze(n, sidecar, _empty_protection())
    corridor, = plan.corridors
    assert corridor.cycle_type == "both"
    assert not corridor.parallel_only_cycle
    assert corridor.simple_graph_cycle


def test_mixed_active_inactive_members_are_protected() -> None:
    """An inactive member retains identity but creates no active cycle."""
    n = _network(inactive=True, num_parallel=2)
    sidecar = PypsaAdapter().identity_sidecar(n, "test")
    plan = ParallelCorridorAnalyzer().analyze(n, sidecar, _empty_protection())
    corridor, = plan.corridors
    assert corridor.cycle_type is None
    assert {sid.source_key for sid in corridor.inactive_members} == {"l2"}
    assert corridor.protected


def test_all_inactive_corridor_fails_closed() -> None:
    """An all-inactive corridor cannot silently enter ordinary quotient."""
    n = _network(inactive=True)
    n.lines.loc["l1", "active"] = False
    sidecar = PypsaAdapter().identity_sidecar(n, "test")
    with pytest.raises(Exception, match="inactive parallel corridor"):
        ParallelCorridorAnalyzer().analyze(n, sidecar, _empty_protection())


def test_builder_preserves_two_active_parallel_members() -> None:
    """The opt-in Builder keeps both Line StableIds and the cycle Gate."""
    n = _network(num_parallel=2)
    sidecar = PypsaAdapter().identity_sidecar(n, "test")
    stable = {sidecar.source_id("Bus", name): StableId("test", "Bus", name)
              for name in n.buses.index}
    partition = PartitionPlan(
        stable, {name: stable[sidecar.source_id("Bus", name)].encode()
                 for name in n.buses.index}, 2, 2, "reject")
    aggregation = AggregationConfig(parallel_corridors=ParallelCorridorConfig(
        policy="preserve_protected_members"))

    result = MultiVoltageQuotientBuilder().build(
        n, sidecar, partition, aggregation, _empty_protection())

    assert len(result.network.lines) == 2
    assert {row.source_id for row in result.mappings.line_map} == {
        sidecar.source_id("Line", "l1"), sidecar.source_id("Line", "l2")}
    assert all(row.mapping_type == "preserved"
               for row in result.mappings.line_map)
    assert result.preliminary.checks["cycle_corridors"]
    assert result.preliminary.checks["protected_parallel_line_semantics"]
    protected_name = result.target_sidecar.backend_name(
        sidecar.source_id("Line", "l1"))
    assert int(result.network.lines.loc[protected_name, "num_parallel"]) == 2


def test_single_l_plus_base_line_is_promoted_to_identity() -> None:
    """An L+ association protects its Line even without parallel siblings."""
    n = pypsa.Network()
    n.add("Bus", "a", v_nom=110)
    n.add("Bus", "b", v_nom=110)
    n.add("Line", "base", bus0="a", bus1="b", r=0.1, x=0.2)
    sidecar = PypsaAdapter().identity_sidecar(n, "test")
    base = sidecar.source_id("Line", "base")
    plan = ParallelCorridorAnalyzer().analyze(
        n, sidecar, _empty_protection(), lplus_base_lines=frozenset({base}))
    assert plan.protected_lines == frozenset({base})
    assert len(plan.mandatory_singletons) == 2


def test_explicit_identity_protects_single_line() -> None:
    """An explicit Line action does not depend on corridor multiplicity."""
    n = pypsa.Network()
    n.add("Bus", "a", v_nom=110)
    n.add("Bus", "b", v_nom=110)
    n.add("Line", "base", bus0="a", bus1="b", r=0.1, x=0.2)
    sidecar = PypsaAdapter().identity_sidecar(n, "test")
    base = sidecar.source_id("Line", "base")
    empty = _empty_protection()
    protected = ProtectionSet(empty.traced, frozenset({base}),
                              empty.terminal_buses, empty.singleton_buses,
                              empty.cannot_link)
    plan = ParallelCorridorAnalyzer().analyze(n, sidecar, protected)
    assert plan.protected_lines == frozenset({base})


def test_builder_detaches_l_plus_shunts_with_base_line() -> None:
    """Both terminal reactors retain their StableIds and ordered owners."""
    n = pypsa.Network()
    n.add("Bus", "a", v_nom=110)
    n.add("Bus", "b", v_nom=110)
    n.add("Line", "base", bus0="a", bus1="b", r=0.1, x=0.2)
    n.add("ShuntImpedance", "from", bus="a", b=-0.5)
    n.add("ShuntImpedance", "to", bus="b", b=-0.7)
    ids = PypsaAdapter().identity_sidecar(n, "test")
    base = ids.source_id("Line", "base")
    ledger = ParallelSourceLedger(
        SourceProfileContract("programmatic", "unavailable"), (),
        tuple(LPlusAssociation(base, "card", side,
                               ids.source_id("Bus", bus),
                               ids.source_id("ShuntImpedance", shunt), value)
              for side, bus, shunt, value in (
                  ("FROM", "a", "from", -0.5),
                  ("TO", "b", "to", -0.7))))
    stable = {ids.source_id("Bus", name): StableId("test", "Bus", name)
              for name in n.buses.index}
    partition = PartitionPlan(stable, {
        name: stable[ids.source_id("Bus", name)].encode()
        for name in n.buses.index}, 2, 2, "reject")
    aggregation = AggregationConfig(parallel_corridors=ParallelCorridorConfig(
        policy="preserve_protected_members"))
    result = MultiVoltageQuotientBuilder().build(
        n, ids, partition, aggregation, _empty_protection(),
        source_ledger=ledger)
    assert len(result.network.shunt_impedances) == 2
    assert {row.source_id for row in result.mappings.device_map} == {
        ids.source_id("ShuntImpedance", "from"),
        ids.source_id("ShuntImpedance", "to")}
    assert all(row.mapping_type == "preserved"
               for row in result.mappings.device_map)
    shunt = ids.source_id("ShuntImpedance", "from")
    wrong = result.network.copy()
    wrong.shunt_impedances.loc[
        result.target_sidecar.backend_name(shunt), "bus"] = (
            result.target_sidecar.backend_name(ids.source_id("Bus", "b")))
    checks, _ = ProtectedParallelStructuralValidator().check(
        n, wrong, ids, result.target_sidecar, result.mappings,
        result.parallel_plan,
        {row.source_id: row.target_id for row in result.mappings.bus_map},
        source_ledger=ledger)
    assert checks["l_plus_terminal_owner_consistency"] is False
    assert checks["l_plus_semantic_round_trip"] is False
    corrupted_device_rows = tuple(
        MapEntry(row.source_id, row.target_id, "aggregated", row.operation_id)
        if row.source_id == shunt else row
        for row in result.mappings.device_map)
    corrupted_mapping = MappingBundle(
        result.mappings.bus_map, result.mappings.branch_map,
        corrupted_device_rows, result.mappings.control_map)
    mapping_checks, _ = ProtectedParallelStructuralValidator().check(
        n, result.network, ids, result.target_sidecar, corrupted_mapping,
        result.parallel_plan,
        {row.source_id: row.target_id for row in result.mappings.bus_map},
        source_ledger=ledger)
    assert mapping_checks["l_plus_association_coverage"] is False


@pytest.mark.parametrize("inactive_count", [0, 1])
def test_three_distinct_members_preserve_exact_active_sets(inactive_count: int) -> None:
    """Three Line StableIds remain three regardless of static active state."""
    n = _network()
    n.add("Line", "l3", bus0="b", bus1="a", r=0.4, x=0.5,
          active=inactive_count == 0)
    ids = PypsaAdapter().identity_sidecar(n, "test")
    stable = {ids.source_id("Bus", name): StableId("test", "Bus", name)
              for name in n.buses.index}
    partition = PartitionPlan(stable, {
        name: stable[ids.source_id("Bus", name)].encode()
        for name in n.buses.index}, 2, 2, "reject")
    aggregation = AggregationConfig(parallel_corridors=ParallelCorridorConfig(
        policy="preserve_protected_members"))
    built = MultiVoltageQuotientBuilder().build(
        n, ids, partition, aggregation, _empty_protection())
    assert len(built.network.lines) == 3
    assert len(built.mappings.line_map) == 3
    assert all(row.mapping_type == "preserved"
               for row in built.mappings.line_map)
    assert built.preliminary.checks["protected_parallel_active_state_sets"]
    assert built.preliminary.checks["protected_parallel_line_semantics"]


def test_num_parallel_attribute_does_not_create_second_member() -> None:
    """PyPSA's num_parallel remains an attribute on one independent Line."""
    n = pypsa.Network()
    n.add("Bus", "a", v_nom=110)
    n.add("Bus", "b", v_nom=110)
    n.add("Line", "one", bus0="a", bus1="b", r=0.1, x=0.2,
          num_parallel=2)
    ids = PypsaAdapter().identity_sidecar(n, "test")
    plan = ParallelCorridorAnalyzer().analyze(n, ids, _empty_protection())
    assert plan.corridors == ()
    assert not plan.protected_lines


def test_nonprotected_parallel_triangle_keeps_ordinary_aggregation() -> None:
    """A simple ring's parallel corridor still uses the public quotient."""
    n = _network(triangle=True)
    ids = PypsaAdapter().identity_sidecar(n, "test")
    stable = {ids.source_id("Bus", name): StableId("test", "Bus", name)
              for name in n.buses.index}
    partition = PartitionPlan(stable, {
        name: stable[ids.source_id("Bus", name)].encode()
        for name in n.buses.index}, 3, 3, "reject")
    aggregation = AggregationConfig(parallel_corridors=ParallelCorridorConfig(
        policy="preserve_protected_members"))
    built = MultiVoltageQuotientBuilder().build(
        n, ids, partition, aggregation, _empty_protection())
    assert len(built.network.lines) == 3
    line_rows = {row.source_id.source_key: row for row in built.mappings.line_map}
    assert line_rows["l1"].target_id == line_rows["l2"].target_id
    assert line_rows["l1"].mapping_type == "aggregated"
    assert built.preliminary.checks["cycle_corridors"]
