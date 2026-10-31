"""Derived, fail-closed mappings used to compare two solved network stages.

This module is deliberately solver-agnostic. It consumes authoritative StableId
maps and endpoint inventories and never mutates a PyPSA network or runs PF.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal, Mapping

from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.fidelity.contracts import PowerFlowCoverage
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry


class PfMappingError(ValueError):
    """Raised when a PF-comparison mapping is incomplete or ambiguous."""


@dataclass(frozen=True)
class PfBranchEndpoints:
    """Stable identity and ordered terminal buses for a two-terminal branch."""

    branch_id: StableId
    component: Literal["Line", "Transformer", "Link"]
    bus0: StableId
    bus1: StableId
    additional_terminals: tuple[StableId, ...] = ()

    def __post_init__(self) -> None:
        if self.component not in {"Line", "Transformer", "Link"}:
            raise PfMappingError("unsupported branch component")
        if self.branch_id.component != self.component:
            raise PfMappingError("branch StableId component does not match endpoint component")
        if self.bus0 == self.bus1:
            raise PfMappingError("self-loop branch endpoints are ambiguous")
        if self.additional_terminals:
            raise PfMappingError("multi-terminal branches are unsupported for PF mapping")


@dataclass(frozen=True)
class PfBusComparisonEntry:
    """A source Bus and its target group classification."""

    source_bus_id: StableId
    target_bus_id: StableId
    comparison_type: Literal[
        "preserved_singleton", "aggregated_group_member", "mapped_singleton"
    ]
    source_component_id: str
    target_component_id: str


@dataclass(frozen=True)
class PfBranchComparisonEntry:
    """One source branch comparison row derived from the quotient map."""

    source_branch_id: StableId
    target_branch_id: StableId | None
    component: Literal["Line", "Transformer", "Link"]
    source_bus0: StableId
    source_bus1: StableId
    mapped_source_bus0: StableId
    mapped_source_bus1: StableId
    orientation_sign: Literal[-1, 1] | None
    comparison_type: Literal[
        "preserved", "aggregated_corridor_member", "absorbed_internal"
    ]

    @property
    def endpoint_target_buses(self) -> tuple[StableId, StableId]:
        """Return the two target clusters owning this source branch's terminals."""
        return self.mapped_source_bus0, self.mapped_source_bus1


@dataclass(frozen=True)
class PfCutsetComparisonEntry:
    """One branch contribution at a cluster boundary endpoint."""

    cluster_bus_id: StableId
    source_branch_id: StableId
    target_branch_id: StableId
    orientation_sign: Literal[-1, 1]
    source_terminal_index: Literal[0, 1]
    target_terminal_index: Literal[0, 1]


@dataclass(frozen=True)
class PfComponentCorrespondence:
    """One source connected component and its unique target component."""

    source_component_id: str
    target_component_id: str


@dataclass(frozen=True)
class PfComparisonMapping:
    """Validated derived mapping and coverage for one source/target pair."""

    bus_entries: tuple[PfBusComparisonEntry, ...]
    branch_entries: tuple[PfBranchComparisonEntry, ...]
    component_correspondence: tuple[PfComponentCorrespondence, ...]
    cutsets_by_cluster: Mapping[StableId, tuple[PfCutsetComparisonEntry, ...]]
    corridor_members: Mapping[StableId, tuple[PfBranchComparisonEntry, ...]]
    mapping_digest: str
    coverage: PowerFlowCoverage

    @property
    def global_unique_cutset_branches(self) -> tuple[StableId, ...]:
        """Return target corridor identities appearing in any cluster cutset once."""
        return tuple(sorted({entry.target_branch_id
                             for entries in self.cutsets_by_cluster.values()
                             for entry in entries}))

    @property
    def cutset_target_branches_by_cluster(self) -> Mapping[StableId, tuple[StableId, ...]]:
        """Return unique target branch identities for each local cluster cutset."""
        return {
            cluster: tuple(sorted({entry.target_branch_id for entry in entries}))
            for cluster, entries in self.cutsets_by_cluster.items()
        }


def derive_pf_comparison_mapping(
    bundle: MappingBundle,
    *,
    source_bus_ids: tuple[StableId, ...],
    target_bus_ids: tuple[StableId, ...],
    source_branch_endpoints: tuple[PfBranchEndpoints, ...],
    target_branch_endpoints: tuple[PfBranchEndpoints, ...],
    source_component_by_bus: Mapping[StableId, str],
    target_component_by_bus: Mapping[StableId, str],
) -> PfComparisonMapping:
    """Validate authoritative maps and derive bus, branch, component and cutset views.

    Every source Bus and supported branch must be mapped exactly once. Target
    branches must be referenced by at least one source branch, and all target
    buses must have source provenance. A collapsed pair of mapped terminals is
    valid only when the authoritative branch row explicitly says
    ``absorbed_internal``.
    """
    source_buses = _unique_ids(source_bus_ids, "source Bus")
    target_buses = _unique_ids(target_bus_ids, "target Bus")
    src_endpoints = _unique_endpoint_map(source_branch_endpoints, "source")
    dst_endpoints = _unique_endpoint_map(target_branch_endpoints, "target")

    bus_rows = _unique_map_rows(bundle.bus_map, "source Bus")
    if set(bus_rows) != set(source_buses):
        raise PfMappingError("source Bus coverage does not match authoritative bus_map")
    target_bus_set = set(target_buses)
    if any(row.target_id not in target_bus_set for row in bus_rows.values()):
        raise PfMappingError("bus_map references a missing target Bus")
    target_owners: dict[StableId, list[StableId]] = defaultdict(list)
    for source, row in bus_rows.items():
        assert row.target_id is not None
        target_owners[row.target_id].append(source)
    if set(target_owners) != set(target_buses):
        raise PfMappingError("target Bus provenance is incomplete")
    _validate_component_inventory(source_buses, source_component_by_bus, "source")
    _validate_component_inventory(target_buses, target_component_by_bus, "target")

    component_targets: dict[str, set[str]] = defaultdict(set)
    bus_entries: list[PfBusComparisonEntry] = []
    for source in sorted(source_buses):
        row = bus_rows[source]
        assert row.target_id is not None
        source_component = source_component_by_bus[source]
        target_component = target_component_by_bus[row.target_id]
        component_targets[source_component].add(target_component)
        if len(target_owners[row.target_id]) > 1:
            kind = "aggregated_group_member"
        elif row.mapping_type == "preserved" and row.source_id == row.target_id:
            kind = "preserved_singleton"
        else:
            kind = "mapped_singleton"
        bus_entries.append(PfBusComparisonEntry(
            source, row.target_id, kind, source_component, target_component))
    if any(len(targets) != 1 for targets in component_targets.values()):
        raise PfMappingError("component correspondence maps one source component to multiple targets")
    component_correspondence = tuple(
        PfComponentCorrespondence(source, next(iter(targets)))
        for source, targets in sorted(component_targets.items()))

    branch_rows = {
        source: row for source, row in _unique_map_rows(bundle.branch_map, "source branch").items()
        if source.component in {"Line", "Transformer", "Link"}
    }
    if set(branch_rows) != set(src_endpoints):
        raise PfMappingError("source branch coverage does not match authoritative branch_map")
    target_branch_ids = set(dst_endpoints)
    for endpoints in src_endpoints.values():
        if endpoints.bus0 not in source_buses or endpoints.bus1 not in source_buses:
            raise PfMappingError("source branch endpoint references a missing Bus")
    for endpoints in dst_endpoints.values():
        if endpoints.bus0 not in target_buses or endpoints.bus1 not in target_buses:
            raise PfMappingError("branch endpoint references a missing Bus")
    referenced_target_ids = {
        row.target_id for row in branch_rows.values() if row.target_id is not None
    }
    if referenced_target_ids != target_branch_ids:
        raise PfMappingError("target branch provenance is missing or contains an orphan target branch")

    branch_entries: list[PfBranchComparisonEntry] = []
    corridor_members: dict[StableId, list[PfBranchComparisonEntry]] = defaultdict(list)
    for source_id in sorted(src_endpoints):
        source = src_endpoints[source_id]
        row = branch_rows[source_id]
        if source.component != row.source_id.component:
            raise PfMappingError("source branch component differs from mapping row")
        mapped0 = bus_rows[source.bus0].target_id
        mapped1 = bus_rows[source.bus1].target_id
        assert mapped0 is not None and mapped1 is not None
        if mapped0 == mapped1:
            if row.mapping_type != "absorbed_internal" or row.target_id is not None:
                raise PfMappingError("collapsed source branch terminals must be absorbed_internal")
            entry = PfBranchComparisonEntry(
                source_id, None, source.component, source.bus0, source.bus1,
                mapped0, mapped1, None, "absorbed_internal")
        else:
            if row.mapping_type == "absorbed_internal" or row.target_id is None:
                raise PfMappingError("non-collapsed source branch cannot be absorbed_internal")
            target = dst_endpoints.get(row.target_id)
            if target is None:
                raise PfMappingError(f"missing target branch {row.target_id.encode()}")
            if target.component != source.component:
                raise PfMappingError("source and target branch components differ")
            if (mapped0, mapped1) == (target.bus0, target.bus1):
                sign: Literal[-1, 1] = 1
            elif (mapped0, mapped1) == (target.bus1, target.bus0):
                sign = -1
            else:
                raise PfMappingError("branch endpoint orientation is ambiguous")
            kind: Literal["preserved", "aggregated_corridor_member", "absorbed_internal"]
            kind = "preserved" if row.mapping_type == "preserved" else "aggregated_corridor_member"
            entry = PfBranchComparisonEntry(
                source_id, row.target_id, source.component, source.bus0, source.bus1,
                mapped0, mapped1, sign, kind)
            corridor_members[row.target_id].append(entry)
        branch_entries.append(entry)

    cutsets: dict[StableId, list[PfCutsetComparisonEntry]] = defaultdict(list)
    for entry in branch_entries:
        if entry.target_branch_id is None:
            continue
        left, right = entry.endpoint_target_buses
        if left == right:
            continue
        assert entry.orientation_sign is not None
        cutsets[left].append(PfCutsetComparisonEntry(
            left, entry.source_branch_id, entry.target_branch_id, entry.orientation_sign,
            0, 0 if entry.orientation_sign == 1 else 1))
        cutsets[right].append(PfCutsetComparisonEntry(
            right, entry.source_branch_id, entry.target_branch_id, entry.orientation_sign,
            1, 1 if entry.orientation_sign == 1 else 0))

    absorbed_count = sum(e.comparison_type == "absorbed_internal" for e in branch_entries)
    comparable_count = len(branch_entries) - absorbed_count
    coverage = PowerFlowCoverage(
        source_bus_count=len(source_buses), mapped_bus_count=len(bus_entries),
        source_branch_count=len(branch_entries), comparable_branch_count=comparable_count,
        absorbed_internal_branch_count=absorbed_count,
        target_corridor_count=len(corridor_members),
        preserved_bus_count=sum(e.comparison_type == "preserved_singleton" for e in bus_entries),
        aggregated_bus_count=sum(e.comparison_type == "aggregated_group_member" for e in bus_entries),
    )
    canonical = {
        "buses": [
            (e.source_bus_id.encode(), e.target_bus_id.encode(), e.comparison_type,
             e.source_component_id, e.target_component_id)
            for e in bus_entries
        ],
        "branches": [
            (e.source_branch_id.encode(),
             e.target_branch_id.encode() if e.target_branch_id else None,
             e.component, e.source_bus0.encode(), e.source_bus1.encode(),
             e.mapped_source_bus0.encode(), e.mapped_source_bus1.encode(),
             e.orientation_sign, e.comparison_type)
            for e in branch_entries
        ],
        "components": [(e.source_component_id, e.target_component_id)
                       for e in component_correspondence],
    }
    digest = hashlib.sha256(json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    return PfComparisonMapping(
        tuple(bus_entries), tuple(branch_entries), component_correspondence,
        {key: tuple(value) for key, value in sorted(cutsets.items())},
        {key: tuple(value) for key, value in sorted(corridor_members.items())},
        digest, coverage,
    )


def aggregate_corridor_flows(
    mapping: PfComparisonMapping,
    source_bus0_flows: Mapping[StableId, float],
) -> dict[StableId, float]:
    """Sum source bus0 flows once per target corridor after orientation correction.

    Absorbed internal branches are excluded. The input must contain exactly the
    non-absorbed source branch identities represented by the mapping.
    """
    comparable = {entry.source_branch_id: entry for entry in mapping.branch_entries
                  if entry.target_branch_id is not None}
    if set(source_bus0_flows) != set(comparable):
        raise PfMappingError("source branch flow coverage does not match comparable branch map")
    totals: dict[StableId, float] = defaultdict(float)
    for source_id, value in source_bus0_flows.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise PfMappingError("source branch flow must be a finite number")
        entry = comparable[source_id]
        assert entry.target_branch_id is not None and entry.orientation_sign is not None
        totals[entry.target_branch_id] += entry.orientation_sign * float(value)
    return dict(sorted(totals.items()))


def _unique_ids(values: tuple[StableId, ...], label: str) -> set[StableId]:
    """Return a StableId set after rejecting duplicates and invalid identifiers."""
    if any(not isinstance(value, StableId) for value in values):
        raise PfMappingError(f"{label} inventory contains a non-StableId")
    if len(set(values)) != len(values):
        raise PfMappingError(f"duplicate {label} StableId")
    return set(values)


def _unique_endpoint_map(
    values: tuple[PfBranchEndpoints, ...], label: str,
) -> dict[StableId, PfBranchEndpoints]:
    """Build a duplicate-checked branch endpoint index."""
    result: dict[StableId, PfBranchEndpoints] = {}
    for item in values:
        if not isinstance(item, PfBranchEndpoints):
            raise PfMappingError(f"{label} branch endpoint inventory has invalid row")
        if item.branch_id in result:
            raise PfMappingError(f"duplicate {label} branch endpoint StableId")
        result[item.branch_id] = item
    return result


def _unique_map_rows(rows: tuple[MapEntry, ...], label: str) -> dict[StableId, MapEntry]:
    """Index map rows by source identity, rejecting duplicate owners."""
    result: dict[StableId, MapEntry] = {}
    for row in rows:
        if not isinstance(row, MapEntry):
            raise PfMappingError(f"{label} map contains invalid row")
        if row.source_id in result:
            raise PfMappingError(f"duplicate {label} mapping source")
        result[row.source_id] = row
    return result


def _validate_component_inventory(
    buses: set[StableId], component_by_bus: Mapping[StableId, str], label: str,
) -> None:
    """Require exactly one non-empty component label for each inventory Bus."""
    if set(component_by_bus) != buses:
        raise PfMappingError(f"{label} component inventory does not match Bus inventory")
    if any(not isinstance(value, str) or not value.strip()
           for value in component_by_bus.values()):
        raise PfMappingError(f"{label} component inventory contains an invalid label")
