"""A uniform persistent mapping row contract."""

from dataclasses import dataclass
from typing import Literal

from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.exceptions import MappingError


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
