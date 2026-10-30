"""Read-only BPA DAT card inventory for the AC-only reduction boundary."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from psdcalc.reduction.exceptions import BackendCapabilityError


KNOWN_DC_FAMILIES: frozenset[bytes] = frozenset({
    b"BD", b"BM", b"BZ", b"BA", b"BB", b"DC", b"LD", b"LM", b"LZ", b"LY",
})
"""DC card families recognized by the existing PSDCase DAT parser."""

_AC_FAMILIES = frozenset({b"B ", b"BQ", b"BS", b"BE", b"BG", b"L ", b"L+", b"T "})


@dataclass(frozen=True)
class BpaAcInventory:
    """Card counts and explicit AC-only/unsupported-card evidence."""

    dat_path: Path
    line_count: int
    card_counts: Mapping[str, int]
    dc_cards: Mapping[str, int]
    unsupported_cards: Mapping[str, int]
    annotation_counts: Mapping[str, int]

    @property
    def known_dc_total(self) -> int:
        """Return the number of known DC records, including extended forms."""
        return sum(self.dc_cards.values())

    @property
    def ac_only(self) -> bool:
        """Report whether the known DC-card zero-count Gate passed."""
        return self.known_dc_total == 0

    def require_supported_ac(self) -> None:
        """Reject DC or unsupported active cards before PSDCase adaptation."""
        if not self.ac_only:
            raise BackendCapabilityError(f"AC-only Gate failed: {self.known_dc_total} known DC cards")
        if self.unsupported_cards:
            raise BackendCapabilityError(f"Unsupported BPA card prefixes: {dict(self.unsupported_cards)}")


def scan_bpa_dat(path: str | Path) -> BpaAcInventory:
    """Inventory DAT prefixes without importing or modifying the source model."""
    dat_path = Path(path).resolve()
    if not dat_path.is_file():
        raise BackendCapabilityError(f"BPA DAT file does not exist: {dat_path}")
    cards: Counter[str] = Counter()
    dc: Counter[str] = Counter()
    unsupported: Counter[str] = Counter()
    annotations: Counter[str] = Counter()
    lines = dat_path.read_bytes().splitlines()
    for raw in lines:
        prefix = raw[:2]
        label = prefix.decode("ascii", errors="backslashreplace")
        cards[label] += 1
        if prefix in KNOWN_DC_FAMILIES:
            if raw.startswith(b"BZ+"):
                dc["BZ+"] += 1
            elif raw.startswith(b"BA1"):
                dc["BA1"] += 1
            elif raw.startswith(b"BA2"):
                dc["BA2"] += 1
            else:
                dc[label] += 1
        elif prefix in _AC_FAMILIES:
            continue
        elif raw.startswith((b"+H1", b"+H2")):
            if raw[20:21] != b"W":
                unsupported["+H_non_W"] += 1
        elif not raw or prefix[:1] in {b".", b"/", b"(", b">"}:
            annotations[label] += 1
        else:
            unsupported[label] += 1
    return BpaAcInventory(dat_path, len(lines), dict(sorted(cards.items())),
                          dict(sorted(dc.items())), dict(sorted(unsupported.items())),
                          dict(sorted(annotations.items())))
