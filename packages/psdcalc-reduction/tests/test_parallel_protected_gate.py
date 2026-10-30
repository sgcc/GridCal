"""Fail-closed member, direction, state and mapping checks."""

import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import AggregationConfig, ParallelCorridorConfig
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.exceptions import MaterializationError, PartitionError
from psdcalc.reduction.materializers.protected_parallel import (
    DetachedLine, ProtectedParallelLineHandler,
)
from psdcalc.reduction.materializers.multi_voltage import MultiVoltageQuotientBuilder
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.validation.protected_parallel import ProtectedParallelStructuralValidator


def _case():
    n = pypsa.Network()
    n.add("Bus", "a", v_nom=110)
    n.add("Bus", "b", v_nom=110)
    n.add("Line", "one", bus0="a", bus1="b", r=0.1, x=0.2)
    n.add("Line", "two", bus0="b", bus1="a", r=0.3, x=0.4,
          active=False)
    ids = PypsaAdapter().identity_sidecar(n, "t")
    stable = {ids.source_id("Bus", name): StableId("t", "Bus", name)
              for name in n.buses.index}
    partition = PartitionPlan(stable, {
        name: stable[ids.source_id("Bus", name)].encode()
        for name in n.buses.index}, 2, 2, "reject")
    protection = ProtectionSet(*[frozenset() for _ in range(5)])
    aggregation = AggregationConfig(parallel_corridors=ParallelCorridorConfig(
        policy="preserve_protected_members"))
    built = MultiVoltageQuotientBuilder().build(
        n, ids, partition, aggregation, protection)
    return n, ids, built


def _checks(n, ids, built, *, target=None, target_ids=None, mapping=None):
    return ProtectedParallelStructuralValidator().check(
        n, target or built.network, ids, target_ids or built.target_sidecar,
        mapping or built.mappings, built.parallel_plan,
        {row.source_id: row.target_id for row in built.mappings.bus_map})[0]


def test_exact_protected_member_and_state_sets() -> None:
    n, ids, built = _case()
    checks = _checks(n, ids, built)
    assert all(checks.values())
    corrupted = built.network.copy()
    name = built.target_sidecar.backend_name(ids.source_id("Line", "two"))
    corrupted.lines.loc[name, "active"] = True
    changed = _checks(n, ids, built, target=corrupted)
    assert not changed["protected_parallel_active_state_sets"]
    assert not changed["protected_parallel_line_semantics"]


def test_reversed_line_direction_and_mapping_swap_fail() -> None:
    n, ids, built = _case()
    one = ids.source_id("Line", "one")
    name = built.target_sidecar.backend_name(one)
    corrupted = built.network.copy()
    left = corrupted.lines.loc[name, "bus0"]
    right = corrupted.lines.loc[name, "bus1"]
    corrupted.lines.loc[name, "bus0"] = right
    corrupted.lines.loc[name, "bus1"] = left
    assert not _checks(n, ids, built, target=corrupted)[
        "protected_parallel_line_semantics"]
    rows = tuple(MapEntry(row.source_id, StableId("t", "Line", "wrong"),
                          row.mapping_type, row.operation_id)
                 if row.source_id == one else row
                 for row in built.mappings.branch_map)
    mapping = MappingBundle(built.mappings.bus_map, rows,
                            built.mappings.device_map,
                            built.mappings.control_map)
    assert not _checks(n, ids, built, mapping=mapping)[
        "protected_parallel_member_coverage"]


def test_target_stable_id_replacement_fails_at_equal_member_count() -> None:
    n, ids, built = _case()
    one = ids.source_id("Line", "one")
    replacement = StableId("t", "Line", "replacement")
    target_ids = IdentitySidecar.from_rows([
        {**row, "source_id": replacement.encode()}
        if row["source_id"] == one.encode() else row
        for row in built.target_sidecar.to_rows()])
    checks = _checks(n, ids, built, target_ids=target_ids)
    assert not checks["protected_parallel_member_coverage"]


def test_non_singleton_protected_endpoint_fails_before_detach() -> None:
    """A merged protected terminal is rejected on complete active topology."""
    n = pypsa.Network()
    for name in ("a", "b", "c"):
        n.add("Bus", name, v_nom=110)
    n.add("Line", "one", bus0="a", bus1="b", r=0.1, x=0.2)
    n.add("Line", "two", bus0="a", bus1="b", r=0.2, x=0.3)
    n.add("Line", "bc", bus0="b", bus1="c", r=0.1, x=0.2)
    ids = PypsaAdapter().identity_sidecar(n, "t")
    a = ids.source_id("Bus", "a")
    b = ids.source_id("Bus", "b")
    c = ids.source_id("Bus", "c")
    target = StableId("t", "BusCluster", "merged")
    plan = PartitionPlan({a: target, b: b, c: target},
                         {"a": "merged", "b": "b", "c": "merged"},
                         2, 2, "reject")
    aggregation = AggregationConfig(parallel_corridors=ParallelCorridorConfig(
        policy="preserve_protected_members"))
    with pytest.raises(PartitionError):
        MultiVoltageQuotientBuilder().build(
            n, ids, plan, aggregation,
            ProtectionSet(*[frozenset() for _ in range(5)]))
    assert set(n.lines.index) == {"one", "two", "bc"}


def test_reconnect_name_collision_fails_without_overwrite() -> None:
    """A deterministic protected backend name cannot replace another Line."""
    n = pypsa.Network()
    n.add("Bus", "a", v_nom=110)
    n.add("Bus", "b", v_nom=110)
    source_id = StableId("t", "Line", "one")
    handler = ProtectedParallelLineHandler()
    name = handler._name("110", source_id)
    n.add("Line", name, bus0="a", bus1="b", r=0.1, x=0.2)
    detached = (DetachedLine(source_id, "one", {
        "bus0": "a", "bus1": "b", "r": 0.2, "x": 0.3}, {}),)
    with pytest.raises(MaterializationError, match="collision"):
        handler.reconnect(n, detached, {"a": "a", "b": "b"}, "110")
    assert float(n.lines.loc[name, "r"]) == 0.1
