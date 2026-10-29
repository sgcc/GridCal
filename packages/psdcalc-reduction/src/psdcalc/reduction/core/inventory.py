"""Backend-neutral inventory rows keyed by stable identities."""

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.exceptions import IdentityError


@dataclass(frozen=True)
class InventoryItem:
    """One component and its stable terminal identities."""

    source_id: StableId
    component: str
    terminals: tuple[StableId, ...] = ()
    controllable: bool = False

    def __post_init__(self) -> None:
        if self.source_id.component != self.component:
            raise IdentityError("Inventory component differs from StableId component")


class GridInventory:
    """Unique, deterministically ordered component inventory."""

    def __init__(self, items: Iterable[InventoryItem]) -> None:
        rows = tuple(sorted(items, key=lambda item: item.source_id))
        if len({item.source_id for item in rows}) != len(rows):
            raise IdentityError("Duplicate StableId in inventory")
        self.items = rows

    def counts(self) -> dict[str, int]:
        """Count components by type in sorted key order."""
        counts = Counter(item.component for item in self.items)
        return dict(sorted(counts.items()))
