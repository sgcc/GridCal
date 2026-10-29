"""Composition of source-ID maps across deterministic operations."""

from collections.abc import Iterable

from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.exceptions import MappingError


def compose_maps(first: Iterable[MapEntry], second: Iterable[MapEntry]) -> tuple[MapEntry, ...]:
    """Compose adjacent maps while preserving internal absorption semantics."""
    second_by_source = {row.source_id: row for row in second}
    result: list[MapEntry] = []
    for row in first:
        if row.target_id is None:
            result.append(row)
            continue
        next_row = second_by_source.get(row.target_id)
        if next_row is None:
            raise MappingError(f"No composed target for {row.target_id.encode()}")
        absorbed = next_row.target_id is None
        kind = ("absorbed_internal" if absorbed else
                "preserved" if row.mapping_type == next_row.mapping_type == "preserved"
                else "aggregated")
        result.append(MapEntry(
            row.source_id, next_row.target_id, kind,
            operation_id=f"{row.operation_id}+{next_row.operation_id}",
            weight=row.weight * next_row.weight,
            level_from=row.level_from, level_to=next_row.level_to,
            reason=next_row.reason if absorbed else None,
        ))
    return tuple(result)
