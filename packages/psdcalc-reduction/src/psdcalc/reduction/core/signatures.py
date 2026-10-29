"""Content and topology signatures based only on stable source IDs."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable

import pypsa

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


def hash_path(path: str | Path) -> str:
    """Hash one input file or an entire CSV folder in canonical path order."""
    source = Path(path)
    if source.is_file():
        return hash_file(source)
    if not source.is_dir():
        raise SignatureError(f"Input path does not exist: {source}")
    files = sorted(item for item in source.rglob("*") if item.is_file())
    if not files:
        raise SignatureError(f"Input directory has no files: {source}")
    digest = hashlib.sha256()
    for item in files:
        relative = item.relative_to(source).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hash_file(item).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def network_content_hash(network: pypsa.Network) -> str:
    """Hash all static/dynamic component data in deterministic table order."""
    digest = hashlib.sha256()
    digest.update(json.dumps([str(item) for item in network.snapshots],
                             separators=(",", ":")).encode("utf-8"))
    for component in sorted(network.components, key=lambda item: item.name):
        digest.update(component.name.encode("utf-8"))
        digest.update(b"\0")
        frame = component.static.sort_index().sort_index(axis=1)
        digest.update(frame.to_json(orient="split", date_format="iso",
                                    double_precision=15, default_handler=str).encode("utf-8"))
        for name, dynamic in sorted(component.dynamic.items()):
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            ordered = dynamic.sort_index().sort_index(axis=1)
            digest.update(ordered.to_json(orient="split", date_format="iso",
                                          double_precision=15,
                                          default_handler=str).encode("utf-8"))
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
