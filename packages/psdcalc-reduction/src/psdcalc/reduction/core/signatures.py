"""Content and topology signatures based only on stable source IDs."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable

from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.exceptions import SignatureError


@dataclass(frozen=True)
class TopologyBus:
    """Bus contribution to a canonical topology signature."""

    source_id: StableId
    v_nom: float


@dataclass(frozen=True)
class TopologyBranch:
    """Branch contribution to a canonical topology signature."""

    source_id: StableId
    bus0: StableId
    bus1: StableId
    active: bool = True


def hash_file(path: str | Path) -> str:
    """Compute the SHA-256 digest of source bytes in bounded memory."""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise SignatureError(f"Cannot hash input file {path}: {exc}") from exc
    return digest.hexdigest()


def topology_signature(
    buses: Iterable[TopologyBus], branches: Iterable[TopologyBranch]
) -> str:
    """Hash a canonical bus/branch topology independent of iteration order."""
    bus_rows = sorted((bus.source_id.encode(), bus.v_nom) for bus in buses)
    branch_rows = sorted((
        branch.source_id.encode(), branch.bus0.encode(), branch.bus1.encode(),
        branch.active,
    ) for branch in branches)
    if len({row[0] for row in bus_rows}) != len(bus_rows):
        raise SignatureError("Duplicate bus StableId in topology")
    if len({row[0] for row in branch_rows}) != len(branch_rows):
        raise SignatureError("Duplicate branch StableId in topology")
    payload = json.dumps({"buses": bus_rows, "branches": branch_rows},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
