"""Protection action contract tests."""

from pathlib import Path
import csv

from psdcalc.reduction.config.models import MatchConfig, ProtectionRule
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.core.inventory import GridInventory, InventoryItem
from psdcalc.reduction.protection.engine import ProtectionEngine


def test_five_actions_have_distinct_effects() -> None:
    a = StableId("n", "Bus", "a")
    b = StableId("n", "Bus", "b")
    line = StableId("n", "Line", "ab")
    gen = StableId("n", "Generator", "g")
    inventory = GridInventory([
        InventoryItem(a, "Bus", attributes={"v_nom": 500.0}),
        InventoryItem(b, "Bus", attributes={"v_nom": 110.0}),
        InventoryItem(line, "Line", (a, b)),
        InventoryItem(gen, "Generator", (a,), True),
    ])
    rules = [
        ProtectionRule(id="observe", component="Bus", action="trace_only",
                       match=MatchConfig(field="v_nom", op="ge", value=500.0)),
        ProtectionRule(id="gen", component="Generator",
                       action="preserve_component_identity"),
        ProtectionRule(id="terminal", component="Generator",
                       action="preserve_terminal_bus"),
        ProtectionRule(id="singleton", component="Bus", action="preserve_bus_as_singleton",
                       match=MatchConfig(field="v_nom", op="ge", value=500.0)),
        ProtectionRule(id="barrier", component="Line", action="cannot_link"),
    ]

    protected = ProtectionEngine().compile(inventory, rules)

    assert protected.traced == frozenset({a})
    assert gen in protected.preserved_components
    assert a in protected.terminal_buses
    assert a in protected.singleton_buses
    assert frozenset({a, b}) in protected.cannot_link


def test_ac_pf_strict_adds_builtin_control_and_transformer_protection() -> None:
    high = StableId("n", "Bus", "high")
    low = StableId("n", "Bus", "low")
    transformer = StableId("n", "Transformer", "tx")
    generator = StableId("n", "Generator", "slack")
    inventory = GridInventory([
        InventoryItem(high, "Bus"), InventoryItem(low, "Bus"),
        InventoryItem(transformer, "Transformer", (high, low)),
        InventoryItem(generator, "Generator", (high,), True,
                      {"control": "Slack"}),
    ])

    protected = ProtectionEngine().compile(inventory, [], profile="ac_pf_strict")

    assert transformer in protected.preserved_components
    assert generator in protected.preserved_components
    assert high in protected.singleton_buses
    assert low in protected.terminal_buses


def test_cannot_link_bus_group_from_stable_id_file(tmp_path: Path) -> None:
    a = StableId("n", "Bus", "a")
    b = StableId("n", "Bus", "b")
    path = tmp_path / "barrier.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["source_id"])
        writer.writeheader()
        writer.writerows([{"source_id": a.encode()}, {"source_id": b.encode()}])
    inventory = GridInventory([InventoryItem(a, "Bus"), InventoryItem(b, "Bus")])
    rule = ProtectionRule(id="barrier", component="Bus", ids_file="barrier.csv",
                          action="cannot_link")

    protected = ProtectionEngine().compile(inventory, [rule], base_dir=tmp_path)

    assert frozenset({a, b}) in protected.cannot_link
