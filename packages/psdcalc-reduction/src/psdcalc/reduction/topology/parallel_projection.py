"""Project authoritative L0 BPA provenance through explicit topology maps."""

from dataclasses import dataclass

import pypsa

from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import MappingError
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.topology.parallel_provenance import ParallelSourceLedger


@dataclass(frozen=True)
class ProjectedParallelProvenance:
    """L1 subset ledger plus complete L0-to-L1 Line lineage and anonymous counts."""

    ledger: ParallelSourceLedger
    lineage: tuple[MapEntry, ...]
    derived_lines: frozenset[StableId]
    summary: dict[str, int]


def reject_derived_protected_lines(
    projection: ProjectedParallelProvenance,
    protected_lines: frozenset[StableId],
) -> None:
    """Fail closed if a synthetic L1 Line gains protected circuit status."""
    if projection.derived_lines & protected_lines:
        raise MappingError("DERIVED_L1_LINE_REQUIRES_PROTECTED_IDENTITY")


def project_parallel_source_ledger(
    source_ledger: ParallelSourceLedger,
    topology_mappings: MappingBundle,
    source_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    simplified_network: pypsa.Network,
    simplified_sidecar: IdentitySidecar,
    *, protected_lines: frozenset[StableId] = frozenset(),
) -> ProjectedParallelProvenance:
    """Carry only exact preserved source Lines, rejecting protected or L+ loss."""
    source_ledger.validate(source_network, source_sidecar)
    target_lines = {simplified_sidecar.source_id("Line", str(name))
                    for name in simplified_network.lines.index}
    def unique(rows: tuple[MapEntry, ...], source: StableId) -> MapEntry:
        matches = [row for row in rows if row.source_id == source]
        if len(matches) != 1:
            raise MappingError("topology_provenance_projection: missing or duplicate map")
        return matches[0]

    carried = []
    lineage = []
    counts = {"preserved": 0, "absorbed_internal": 0, "aggregated": 0}
    for item in source_ledger.lines:
        row = unique(topology_mappings.branch_map, item.line_id)
        if row.mapping_type == "preserved":
            if row.target_id != item.line_id or item.line_id not in target_lines:
                raise MappingError("topology_provenance_projection: preserved Line identity changed")
            carried.append(item)
        elif row.mapping_type in {"absorbed_internal", "aggregated"}:
            if row.mapping_type == "aggregated" and row.target_id not in target_lines:
                raise MappingError("topology_provenance_projection: derived Line absent")
            if item.line_id in protected_lines:
                raise MappingError("PROTECTED_SOURCE_LINE_LOST_IN_L1")
        else:
            raise MappingError("topology_provenance_projection: unsupported Line mapping")
        if item.line_id in protected_lines and row.mapping_type != "preserved":
            raise MappingError("PROTECTED_SOURCE_LINE_LOST_IN_L1")
        counts[row.mapping_type] += 1
        lineage.append(row)
    for item in source_ledger.l_plus:
        base = unique(topology_mappings.branch_map, item.base_line_id)
        shunt = unique(topology_mappings.device_map, item.shunt_id)
        bus = unique(topology_mappings.bus_map, item.terminal_bus_id)
        if base.mapping_type != "preserved" or base.target_id != item.base_line_id:
            raise MappingError("l_plus_l1_base_line_identity")
        if shunt.mapping_type != "preserved" or shunt.target_id != item.shunt_id:
            raise MappingError("l_plus_l1_shunt_identity")
        if bus.mapping_type != "preserved" or bus.target_id != item.terminal_bus_id:
            raise MappingError("l_plus_l1_terminal_owner")
    projected = ParallelSourceLedger(source_ledger.profile, tuple(carried),
                                     source_ledger.l_plus, "preserved_subset")
    projected.validate(simplified_network, simplified_sidecar)
    carried_ids = {item.line_id for item in carried}
    derived = frozenset(target_lines - carried_ids)
    summary = {
        "l0_source_line_count": len(source_ledger.lines),
        "l1_line_count": len(target_lines),
        "l0_to_l1_preserved_line_count": counts["preserved"],
        "l0_to_l1_absorbed_internal_count": counts["absorbed_internal"],
        "l0_to_l1_aggregated_count": counts["aggregated"],
        "l1_derived_line_count": len(derived),
        "protected_l0_line_count": len(protected_lines),
        "protected_l1_preserved_count": len(protected_lines),
        "protected_l1_lost_count": 0,
        "l_plus_association_count": len(source_ledger.l_plus),
        "l_plus_base_preserved_count": len(source_ledger.l_plus),
        "l_plus_shunt_preserved_count": len(source_ledger.l_plus),
        "l_plus_terminal_preserved_count": len(source_ledger.l_plus),
    }
    return ProjectedParallelProvenance(projected, tuple(lineage), derived, summary)
