"""Compile declarative protection rules into hard mapping constraints."""

from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Iterable

from psdcalc.reduction.config.models import ProtectionRule
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.core.inventory import GridInventory, InventoryItem
from psdcalc.reduction.exceptions import ProtectionError
from psdcalc.reduction.protection.builtin_rules import rules_for_profile
from psdcalc.reduction.protection.selectors import matches


@dataclass(frozen=True)
class ProtectionSet:
    """Compiled source-ID constraints consumed by planners and validators."""

    traced: frozenset[StableId]
    preserved_components: frozenset[StableId]
    terminal_buses: frozenset[StableId]
    singleton_buses: frozenset[StableId]
    cannot_link: frozenset[frozenset[StableId]]

    def union(self, other: "ProtectionSet") -> "ProtectionSet":
        """Combine two protection contracts by set union in every category."""
        return ProtectionSet(
            traced=self.traced | other.traced,
            preserved_components=self.preserved_components | other.preserved_components,
            terminal_buses=self.terminal_buses | other.terminal_buses,
            singleton_buses=self.singleton_buses | other.singleton_buses,
            cannot_link=self.cannot_link | other.cannot_link,
        )


class ProtectionEngine:
    """Evaluate selectors and preserve five distinct protection actions."""

    def compile(
        self,
        inventory: GridInventory,
        rules: Iterable[ProtectionRule],
        *,
        profile: str | None = None,
        base_dir: Path = Path("."),
    ) -> ProtectionSet:
        """Return compiled constraints or raise ProtectionError on ambiguity."""
        traced: set[StableId] = set()
        components: set[StableId] = set()
        terminals: set[StableId] = set()
        singletons: set[StableId] = set()
        cannot_link: set[frozenset[StableId]] = set()
        rows = inventory.items
        for rule in (*rules_for_profile(profile), *rules):
            matched = [item for item in rows if matches(item, rule, base_dir=base_dir)]
            if rule.action == "trace_only":
                traced.update(item.source_id for item in matched)
            elif rule.action == "preserve_component_identity":
                components.update(item.source_id for item in matched)
            elif rule.action == "preserve_terminal_bus":
                terminals.update(bus for item in matched for bus in _buses(item))
            elif rule.action == "preserve_bus_as_singleton":
                singletons.update(bus for item in matched for bus in _buses(item))
            elif rule.action == "cannot_link":
                selected_buses = [item.source_id for item in matched
                                  if item.component == "Bus"]
                if len(selected_buses) == 1 and all(
                    item.component == "Bus" for item in matched
                ):
                    raise ProtectionError("cannot_link bus group requires at least two buses")
                cannot_link.update(frozenset(pair) for pair in
                                   combinations(selected_buses, 2))
                for item in matched:
                    if item.component == "Bus":
                        continue
                    buses = _buses(item)
                    if len(buses) < 2:
                        raise ProtectionError(f"cannot_link requires at least two buses: {item.source_id.encode()}")
                    cannot_link.update(frozenset(pair) for pair in combinations(buses, 2))
            else:
                raise ProtectionError(f"Unknown protection action: {rule.action}")
        return ProtectionSet(frozenset(traced), frozenset(components),
                             frozenset(terminals), frozenset(singletons),
                             frozenset(cannot_link))


def _buses(item: InventoryItem) -> tuple[StableId, ...]:
    """Get a selected bus itself or all device terminals."""
    if item.component == "Bus":
        return (item.source_id,)
    return item.terminals
