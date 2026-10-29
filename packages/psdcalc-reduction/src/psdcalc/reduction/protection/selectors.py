"""Safe selectors for stable-ID inventory rows."""

import csv
from pathlib import Path

from psdcalc.reduction.config.models import ProtectionRule
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.core.inventory import InventoryItem
from psdcalc.reduction.exceptions import ProtectionError


def _read_ids(path: Path) -> frozenset[StableId]:
    """Read source IDs from a CSV with a required source_id column."""
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or "source_id" not in reader.fieldnames:
                raise ProtectionError(f"IDs file lacks source_id column: {path}")
            return frozenset(StableId.decode(row["source_id"]) for row in reader)
    except OSError as exc:
        raise ProtectionError(f"Cannot read protected IDs file {path}: {exc}") from exc


def matches(item: InventoryItem, rule: ProtectionRule,
            *, base_dir: Path = Path(".")) -> bool:
    """Evaluate an allowlisted declarative rule against one inventory row."""
    if rule.component not in {"*", item.component}:
        return False
    if rule.ids_file is not None:
        path = base_dir / rule.ids_file
        if item.source_id not in _read_ids(path):
            return False
    if rule.match is None:
        return True
    field = rule.match.field
    if field == "controllable":
        actual: object = item.controllable
    elif field == "source_id":
        actual = item.source_id.encode()
    elif field == "control_role":
        actual = item.attributes.get("control")
    elif field in item.attributes:
        actual = item.attributes[field]
    else:
        raise ProtectionError(f"Unknown selector field {field!r} for {item.component}")
    expected = rule.match.value
    op = rule.match.op
    try:
        if op == "eq":
            return bool(actual == expected)
        if op == "ne":
            return bool(actual != expected)
        if op == "in":
            return actual in expected
        if op == "ge":
            return bool(actual >= expected)
        if op == "gt":
            return bool(actual > expected)
        if op == "le":
            return bool(actual <= expected)
        if op == "lt":
            return bool(actual < expected)
    except (TypeError, ValueError) as exc:
        raise ProtectionError(f"Invalid selector comparison for {field!r}") from exc
    raise ProtectionError(f"Unsupported selector operation: {op}")
