"""A uniform persistent mapping row contract."""

from dataclasses import dataclass
import math
from collections.abc import Mapping
from typing import Literal

from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.exceptions import IdentityError, MappingError


MappingType = Literal[
    "preserved", "clustered", "aggregated", "absorbed_internal", "moved",
]


@dataclass(frozen=True)
class MapEntry:
    """One source-to-target relationship with explicit absorption."""

    source_id: StableId
    target_id: StableId | None
    mapping_type: MappingType
    operation_id: str
    weight: float = 1.0
    level_from: str = "L0"
    level_to: str = "L1"
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.mapping_type == "absorbed_internal":
            if self.target_id is not None:
                raise MappingError("absorbed_internal must have no target_id")
        elif self.target_id is None:
            raise MappingError("Non-absorbed mapping requires target_id")
        if not self.operation_id or self.weight < 0:
            raise MappingError("Mapping operation_id and nonnegative weight are required")

    def to_row(self) -> dict[str, str | float | None]:
        """Serialize a mapping row with encoded stable IDs."""
        return {
            "source_id": self.source_id.encode(),
            "target_id": self.target_id.encode() if self.target_id else None,
            "mapping_type": self.mapping_type,
            "weight": self.weight,
            "level_from": self.level_from,
            "level_to": self.level_to,
            "operation_id": self.operation_id,
            "reason": self.reason,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> "MapEntry":
        """Decode one strict Parquet row to authoritative StableIds."""
        required = {"source_id", "target_id", "mapping_type", "weight",
                    "level_from", "level_to", "operation_id", "reason"}
        if set(row) != required:
            raise MappingError("Persistent map row has missing or unknown columns")
        source = row["source_id"]
        target = row["target_id"]
        kind = row["mapping_type"]
        weight = row["weight"]
        reason = row["reason"]
        if (not isinstance(source, str) or
                target is not None and not isinstance(target, str) or
                kind not in {"preserved", "clustered", "aggregated",
                             "absorbed_internal", "moved"} or
                isinstance(weight, bool) or not isinstance(weight, (int, float)) or
                not math.isfinite(float(weight)) or
                reason is not None and not isinstance(reason, str) or
                any(not isinstance(row[key], str) or not row[key]
                    for key in ("level_from", "level_to", "operation_id"))):
            raise MappingError("Persistent map row contains an invalid value")
        try:
            return cls(
                StableId.decode(source),
                StableId.decode(target) if target is not None else None,
                kind, row["operation_id"], float(weight),
                row["level_from"], row["level_to"], reason,
            )
        except (IdentityError, MappingError) as exc:
            raise MappingError(f"Cannot decode persistent map row: {exc}") from exc
