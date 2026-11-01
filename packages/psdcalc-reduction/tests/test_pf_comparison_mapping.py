from __future__ import annotations

import pytest

from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.fidelity.mapping import (
    PfBranchEndpoints,
    PfMappingError,
    derive_pf_comparison_mapping,
    aggregate_corridor_flows,
)
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry


def _id(component: str, key: str) -> StableId:
    return StableId("grid", component, key)


def _bus_entry(source: StableId, target: StableId, kind: str) -> MapEntry:
    return MapEntry(source, target, kind, "test-op")


def _branch_entry(source: StableId, target: StableId | None, kind: str) -> MapEntry:
    return MapEntry(source, target, kind, "test-op")


def _derive(
    *,
    buses: tuple[MapEntry, ...],
    branches: tuple[MapEntry, ...],
    source_bus_ids: tuple[StableId, ...],
    target_bus_ids: tuple[StableId, ...],
    source_branches: tuple[PfBranchEndpoints, ...],
    target_branches: tuple[PfBranchEndpoints, ...],
    source_components: dict[StableId, str] | None = None,
    target_components: dict[StableId, str] | None = None,
):
    return derive_pf_comparison_mapping(
        MappingBundle(bus_map=buses, branch_map=branches, device_map=()),
        source_bus_ids=source_bus_ids,
        target_bus_ids=target_bus_ids,
        source_branch_endpoints=source_branches,
        target_branch_endpoints=target_branches,
        source_component_by_bus=(source_components or
                                 {bus: "source-c0" for bus in source_bus_ids}),
        target_component_by_bus=(target_components or
                                 {bus: "target-c0" for bus in target_bus_ids}),
    )


def test_preserved_branch_orientation_is_positive() -> None:
    a, b, line = _id("Bus", "a"), _id("Bus", "b"), _id("Line", "l")
    mapping = _derive(
        buses=(_bus_entry(a, a, "preserved"), _bus_entry(b, b, "preserved")),
        branches=(_branch_entry(line, line, "preserved"),),
        source_bus_ids=(a, b), target_bus_ids=(a, b),
        source_branches=(PfBranchEndpoints(line, "Line", a, b),),
        target_branches=(PfBranchEndpoints(line, "Line", a, b),),
    )
    entry = mapping.branch_entries[0]
    assert entry.orientation_sign == 1
    assert entry.comparison_type == "preserved"


def test_reversed_target_branch_orientation_is_negative() -> None:
    a, b, source_line, target_line = (
        _id("Bus", "a"), _id("Bus", "b"), _id("Line", "s"), _id("Line", "t")
    )
    mapping = _derive(
        buses=(_bus_entry(a, a, "preserved"), _bus_entry(b, b, "preserved")),
        branches=(_branch_entry(source_line, target_line, "aggregated"),),
        source_bus_ids=(a, b), target_bus_ids=(a, b),
        source_branches=(PfBranchEndpoints(source_line, "Line", a, b),),
        target_branches=(PfBranchEndpoints(target_line, "Line", b, a),),
    )
    assert mapping.branch_entries[0].orientation_sign == -1


def test_many_to_one_corridor_aggregates_source_bus0_flow_in_target_direction() -> None:
    a1, a2, b1, b2 = (_id("Bus", k) for k in ("a1", "a2", "b1", "b2"))
    a, b = _id("Bus", "A"), _id("Bus", "B")
    s1, s2, target = (_id("Line", k) for k in ("s1", "s2", "t"))
    mapping = _derive(
        buses=(
            _bus_entry(a1, a, "clustered"), _bus_entry(a2, a, "clustered"),
            _bus_entry(b1, b, "clustered"), _bus_entry(b2, b, "clustered"),
        ),
        branches=(
            _branch_entry(s1, target, "aggregated"),
            _branch_entry(s2, target, "aggregated"),
        ),
        source_bus_ids=(a1, a2, b1, b2), target_bus_ids=(a, b),
        source_branches=(
            PfBranchEndpoints(s1, "Line", a1, b1),
            PfBranchEndpoints(s2, "Line", b2, a2),
        ),
        target_branches=(PfBranchEndpoints(target, "Line", a, b),),
    )
    assert [e.orientation_sign for e in mapping.branch_entries] == [1, -1]
    assert mapping.corridor_members[target] == mapping.branch_entries
    assert aggregate_corridor_flows(mapping, {s1: 10.0, s2: 4.0}) == {target: 6.0}


def test_absorbed_internal_branch_has_no_target_or_orientation_and_is_excluded() -> None:
    a1, a2, a = _id("Bus", "a1"), _id("Bus", "a2"), _id("Bus", "A")
    internal = _id("Line", "inside")
    mapping = _derive(
        buses=(_bus_entry(a1, a, "clustered"), _bus_entry(a2, a, "clustered")),
        branches=(_branch_entry(internal, None, "absorbed_internal"),),
        source_bus_ids=(a1, a2), target_bus_ids=(a,),
        source_branches=(PfBranchEndpoints(internal, "Line", a1, a2),),
        target_branches=(),
    )
    entry = mapping.branch_entries[0]
    assert entry.target_branch_id is None
    assert entry.orientation_sign is None
    assert entry.comparison_type == "absorbed_internal"
    assert mapping.coverage.absorbed_internal_branch_count == 1
    assert aggregate_corridor_flows(mapping, {}) == {}


def test_missing_target_branch_fails_closed() -> None:
    a, b, source, missing = (_id("Bus", "a"), _id("Bus", "b"),
                             _id("Line", "s"), _id("Line", "missing"))
    with pytest.raises(PfMappingError, match="target branch"):
        _derive(
            buses=(_bus_entry(a, a, "preserved"), _bus_entry(b, b, "preserved")),
            branches=(_branch_entry(source, missing, "aggregated"),),
            source_bus_ids=(a, b), target_bus_ids=(a, b),
            source_branches=(PfBranchEndpoints(source, "Line", a, b),),
            target_branches=(),
        )


def test_orphan_target_branch_and_duplicate_target_endpoint_fail_closed() -> None:
    a, b = _id("Bus", "a"), _id("Bus", "b")
    source, target, orphan = (_id("Line", k) for k in ("s", "t", "orphan"))
    with pytest.raises(PfMappingError, match="target branch provenance"):
        _derive(
            buses=(_bus_entry(a, a, "preserved"), _bus_entry(b, b, "preserved")),
            branches=(_branch_entry(source, target, "preserved"),),
            source_bus_ids=(a, b), target_bus_ids=(a, b),
            source_branches=(PfBranchEndpoints(source, "Line", a, b),),
            target_branches=(PfBranchEndpoints(target, "Line", a, b),
                             PfBranchEndpoints(orphan, "Line", b, a)),
        )

    with pytest.raises(PfMappingError, match="duplicate target branch endpoint"):
        _derive(
            buses=(_bus_entry(a, a, "preserved"), _bus_entry(b, b, "preserved")),
            branches=(_branch_entry(source, target, "preserved"),),
            source_bus_ids=(a, b), target_bus_ids=(a, b),
            source_branches=(PfBranchEndpoints(source, "Line", a, b),),
            target_branches=(PfBranchEndpoints(target, "Line", a, b),
                             PfBranchEndpoints(target, "Line", a, b)),
        )


def test_flow_aggregation_requires_exact_finite_source_coverage() -> None:
    a, b, line = _id("Bus", "a"), _id("Bus", "b"), _id("Line", "line")
    mapping = _derive(
        buses=(_bus_entry(a, a, "preserved"), _bus_entry(b, b, "preserved")),
        branches=(_branch_entry(line, line, "preserved"),),
        source_bus_ids=(a, b), target_bus_ids=(a, b),
        source_branches=(PfBranchEndpoints(line, "Line", a, b),),
        target_branches=(PfBranchEndpoints(line, "Line", a, b),),
    )
    with pytest.raises(PfMappingError, match="flow coverage"):
        aggregate_corridor_flows(mapping, {})
    with pytest.raises(PfMappingError, match="finite number"):
        aggregate_corridor_flows(mapping, {line: float("nan")})


def test_duplicate_source_branch_mapping_fails_closed() -> None:
    a, b, source, target = (_id("Bus", "a"), _id("Bus", "b"),
                            _id("Line", "s"), _id("Line", "t"))
    row = _branch_entry(source, target, "aggregated")
    with pytest.raises(PfMappingError, match="duplicate source branch"):
        _derive(
            buses=(_bus_entry(a, a, "preserved"), _bus_entry(b, b, "preserved")),
            branches=(row, row), source_bus_ids=(a, b), target_bus_ids=(a, b),
            source_branches=(PfBranchEndpoints(source, "Line", a, b),),
            target_branches=(PfBranchEndpoints(target, "Line", a, b),),
        )


def test_ambiguous_orientation_fails_closed() -> None:
    a, b, c = (_id("Bus", k) for k in ("a", "b", "c"))
    source, target = _id("Line", "s"), _id("Line", "t")
    buses = (_bus_entry(a, a, "preserved"), _bus_entry(b, b, "preserved"),
             _bus_entry(c, c, "preserved"))
    with pytest.raises(PfMappingError, match="orientation"):
        _derive(
            buses=buses, branches=(_branch_entry(source, target, "aggregated"),),
            source_bus_ids=(a, b, c), target_bus_ids=(a, b, c),
            source_branches=(PfBranchEndpoints(source, "Line", a, b),),
            target_branches=(PfBranchEndpoints(target, "Line", a, c),),
        )


def test_transformer_uses_same_endpoint_orientation_contract() -> None:
    a, b = _id("Bus", "a"), _id("Bus", "b")
    source, target = _id("Transformer", "s"), _id("Transformer", "t")
    mapping = _derive(
        buses=(_bus_entry(a, a, "preserved"), _bus_entry(b, b, "preserved")),
        branches=(_branch_entry(source, target, "preserved"),),
        source_bus_ids=(a, b), target_bus_ids=(a, b),
        source_branches=(PfBranchEndpoints(source, "Transformer", b, a),),
        target_branches=(PfBranchEndpoints(target, "Transformer", a, b),),
    )
    assert mapping.branch_entries[0].orientation_sign == -1


def test_multiterminal_link_fails_closed() -> None:
    a, b, extra = (_id("Bus", k) for k in ("a", "b", "extra"))
    link, target = _id("Link", "l"), _id("Link", "t")
    with pytest.raises(PfMappingError, match="multi-terminal"):
        _derive(
            buses=tuple(_bus_entry(bus, bus, "preserved") for bus in (a, b, extra)),
            branches=(_branch_entry(link, target, "preserved"),),
            source_bus_ids=(a, b, extra), target_bus_ids=(a, b, extra),
            source_branches=(PfBranchEndpoints(link, "Link", a, b,
                                               additional_terminals=(extra,)),),
            target_branches=(PfBranchEndpoints(target, "Link", a, b),),
        )


def test_bus_mapping_marks_singleton_and_aggregated_groups_and_digest_is_stable() -> None:
    a, a2, b = (_id("Bus", k) for k in ("a", "a2", "b"))
    A, B = _id("Bus", "A"), _id("Bus", "b")
    buses = (_bus_entry(a, A, "clustered"), _bus_entry(a2, A, "clustered"),
             _bus_entry(b, B, "preserved"))
    source_components = {a: "src0", a2: "src0", b: "src0"}
    target_components = {A: "dst0", B: "dst0"}
    mapping = _derive(
        buses=buses, branches=(), source_bus_ids=(a, a2, b), target_bus_ids=(A, B),
        source_branches=(), target_branches=(),
        source_components=source_components, target_components=target_components,
    )
    kinds = {entry.source_bus_id: entry.comparison_type for entry in mapping.bus_entries}
    assert kinds[a] == "aggregated_group_member"
    assert kinds[a2] == "aggregated_group_member"
    assert kinds[b] == "preserved_singleton"
    reversed_mapping = _derive(
        buses=tuple(reversed(buses)), branches=(),
        source_bus_ids=(b, a2, a), target_bus_ids=(B, A),
        source_branches=(), target_branches=(),
        source_components=source_components, target_components=target_components,
    )
    assert mapping.mapping_digest == reversed_mapping.mapping_digest


def test_missing_bus_source_or_unowned_target_fails_closed() -> None:
    a, b, target_a, target_b, orphan = (
        _id("Bus", k) for k in ("a", "b", "A", "B", "orphan")
    )
    with pytest.raises(PfMappingError, match="source Bus coverage"):
        _derive(
            buses=(_bus_entry(a, target_a, "preserved"),), branches=(),
            source_bus_ids=(a, b), target_bus_ids=(target_a,),
            source_branches=(), target_branches=(),
        )
    with pytest.raises(PfMappingError, match="target Bus provenance"):
        _derive(
            buses=(_bus_entry(a, target_a, "preserved"),
                   _bus_entry(b, target_b, "preserved")), branches=(),
            source_bus_ids=(a, b), target_bus_ids=(target_a, target_b, orphan),
            source_branches=(), target_branches=(),
        )


def test_one_source_component_cannot_map_to_multiple_target_components() -> None:
    a, b = _id("Bus", "a"), _id("Bus", "b")
    A, B = _id("Bus", "A"), _id("Bus", "B")
    with pytest.raises(PfMappingError, match="component correspondence"):
        _derive(
            buses=(_bus_entry(a, A, "preserved"), _bus_entry(b, B, "preserved")),
            branches=(), source_bus_ids=(a, b), target_bus_ids=(A, B),
            source_branches=(), target_branches=(),
            source_components={a: "src0", b: "src0"},
            target_components={A: "dst0", B: "dst1"},
        )


def test_cluster_cutsets_include_only_intercluster_branches_and_count_corridors_once() -> None:
    a1, a2, b1, b2 = (_id("Bus", k) for k in ("a1", "a2", "b1", "b2"))
    A, B = _id("Bus", "A"), _id("Bus", "B")
    e1, e2, internal, target, target_internal = (
        _id("Line", k) for k in ("e1", "e2", "internal", "t", "unused")
    )
    mapping = _derive(
        buses=(_bus_entry(a1, A, "clustered"), _bus_entry(a2, A, "clustered"),
               _bus_entry(b1, B, "clustered"), _bus_entry(b2, B, "clustered")),
        branches=(_branch_entry(e1, target, "aggregated"),
                  _branch_entry(e2, target, "aggregated"),
                  _branch_entry(internal, None, "absorbed_internal")),
        source_bus_ids=(a1, a2, b1, b2), target_bus_ids=(A, B),
        source_branches=(PfBranchEndpoints(e1, "Line", a1, b1),
                         PfBranchEndpoints(e2, "Line", b2, a2),
                         PfBranchEndpoints(internal, "Line", a1, a2)),
        target_branches=(PfBranchEndpoints(target, "Line", A, B),),
    )
    assert [e.source_branch_id for e in mapping.cutsets_by_cluster[A]] == [e1, e2]
    assert [e.source_branch_id for e in mapping.cutsets_by_cluster[B]] == [e1, e2]
    assert mapping.cutset_target_branches_by_cluster[A] == (target,)
    assert mapping.cutset_target_branches_by_cluster[B] == (target,)
    assert mapping.global_unique_cutset_branches == (target,)
    assert internal not in {entry.source_branch_id for entries in mapping.cutsets_by_cluster.values()
                            for entry in entries}

