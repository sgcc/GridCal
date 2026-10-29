"""Byte-exact BPA source audit of active parallel AC Line corridors.

This diagnostic never relaxes the Phase 1.5 cycle-corridor Gate. Raw DAT
cards are written only to a caller-selected local audit directory.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Protocol

import networkx as nx
import pandas as pd
import pypsa

from psdcalc.reduction.core.ids import IdentitySidecar
from psdcalc.reduction.exceptions import BackendCapabilityError
from psdcalc.reduction.topology.active import branch_is_active
from psdcalc.reduction.topology.cycle_corridors import analyze_cycle_corridors


class _SourceLine(Protocol):
    key: str
    active: bool
    source_identity: Any
    attributes: Mapping[str, Any]


class _SourceCase(Protocol):
    ac_lines: Mapping[str, _SourceLine]


@dataclass(frozen=True)
class ParallelCorridorAuditPaths:
    """Locations of private, source-derived audit artifacts."""

    parquet_path: Path
    summary_path: Path


@dataclass(frozen=True)
class ParallelCorridorAuditResult:
    """One row per source Line within each parallel corridor."""

    rows: tuple[dict[str, Any], ...]
    summary: Mapping[str, int | str]

    def write(self, directory: str | Path) -> ParallelCorridorAuditPaths:
        """Write exact raw-card evidence and aggregate counts locally."""
        output = Path(directory).resolve()
        output.mkdir(parents=True, exist_ok=True)
        parquet_path = output / "parallel_corridor_audit.parquet"
        summary_path = output / "parallel_corridor_summary.json"
        pd.DataFrame(self.rows).to_parquet(parquet_path, index=False)
        summary_path.write_text(json.dumps(dict(self.summary), indent=2,
                                           sort_keys=True) + "\n", encoding="utf-8")
        return ParallelCorridorAuditPaths(parquet_path, summary_path)


class ParallelCorridorAudit:
    """Join PSDCase L identities, DAT bytes, PyPSA Lines and quotient targets."""

    def run(
        self, *, network: pypsa.Network, sidecar: IdentitySidecar,
        case: _SourceCase, dat_path: str | Path,
        quotient_targets: Mapping[str, str | None] | None = None,
    ) -> ParallelCorridorAuditResult:
        """Audit source Line provenance without interpreting device names."""
        path = Path(dat_path).resolve()
        if not path.is_file():
            raise BackendCapabilityError(f"BPA DAT file does not exist: {path}")
        dat_bytes = path.read_bytes()
        raw_lines = dat_bytes.splitlines()
        plus_by_key: dict[tuple[bytes, bytes, bytes], tuple[int, bytes]] = {}
        for number, raw in enumerate(raw_lines, 1):
            if raw.startswith(b"L+"):
                key = (raw[6:18], raw[19:31], raw[31:32])
                if key in plus_by_key:
                    raise BackendCapabilityError("Duplicate L+ source card for one L key")
                plus_by_key[key] = (number, raw)

        analysis = analyze_cycle_corridors(network)
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        targets = quotient_targets or {}
        matched_plus_keys: set[tuple[bytes, bytes, bytes]] = set()
        for backend_name, line in network.lines.iterrows():
            backend_name = str(backend_name)
            sid = sidecar.source_id("Line", backend_name)
            source = case.ac_lines.get(sid.source_key)
            if source is None or source.key != sid.source_key:
                raise BackendCapabilityError(f"Missing PSDCase L identity: {sid.encode()}")
            origin = source.source_identity
            numbers = getattr(origin, "dat_line_numbers", ())
            if getattr(origin, "dat_card_type", None) != "L" or len(numbers) != 1:
                raise BackendCapabilityError(f"Missing original DAT L card identity: {sid.encode()}")
            number = numbers[0]
            if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= len(raw_lines):
                raise BackendCapabilityError(f"Invalid DAT L card line number: {sid.encode()}")
            raw = raw_lines[number - 1]
            if not raw.startswith(b"L "):
                raise BackendCapabilityError(f"Source Line does not reference a DAT L card: {sid.encode()}")
            circuit = raw[31:32].decode("ascii", errors="strict").strip()
            if circuit != str(source.attributes.get("circuit_id", "")):
                raise BackendCapabilityError(f"DAT L circuit ID differs from PSDCase: {sid.encode()}")
            raw_key = (raw[6:18], raw[19:31], raw[31:32])
            source_left = source.attributes.get("from")
            source_right = source.attributes.get("to")
            if source_left is not None or source_right is not None:
                if ((source_left, source_right) !=
                    (raw_key[0].hex().upper(), raw_key[1].hex().upper())):
                    raise BackendCapabilityError(f"DAT L endpoints differ from PSDCase: {sid.encode()}")
            plus = plus_by_key.get(raw_key)
            has_plus = ("from_reactor_mvar" in source.attributes or
                        "to_reactor_mvar" in source.attributes)
            if bool(plus) != has_plus:
                raise BackendCapabilityError(f"L+ association differs from PSDCase: {sid.encode()}")
            if plus is not None:
                matched_plus_keys.add(raw_key)
            left, right = str(line.bus0), str(line.bus1)
            if source_left is not None and (left, right) != (source_left, source_right):
                raise BackendCapabilityError(f"PyPSA Line endpoints differ from PSDCase: {sid.encode()}")
            active = bool(branch_is_active(line))
            if active != bool(source.active):
                raise BackendCapabilityError(f"PyPSA Line active state differs from PSDCase: {sid.encode()}")
            voltage = float(network.buses.at[left, "v_nom"])
            row = {
                "corridor_bus0": min(left, right),
                "corridor_bus1": max(left, right),
                "source_stable_id": sid.encode(),
                "backend_name": backend_name,
                "dat_line_number": number,
                "dat_raw_card": raw,
                "card_type": "L",
                "circuit_id": circuit,
                "active": active,
                "bus0": left, "bus1": right,
                "voltage_kv": voltage,
                "r": float(line.r), "x": float(line.x),
                "g": float(line.g), "b": float(line.b),
                "length_km": float(line.length),
                "rating_mva": float(line.s_nom),
                "rating_current_a": source.attributes.get("rating_current_a"),
                "l_plus_associated": plus is not None,
                "l_plus_line_number": plus[0] if plus else None,
                "l_plus_raw_card": plus[1] if plus else None,
                "current_quotient_target": targets.get(sid.encode()),
                "source_raw_endpoint0": raw_key[0],
                "source_raw_endpoint1": raw_key[1],
            }
            groups[tuple(sorted((left, right)))].append(row)
        if matched_plus_keys != set(plus_by_key):
            raise BackendCapabilityError("Unbound L+ source card remains after Line projection")

        all_rows: list[dict[str, Any]] = []
        classifications: Counter[str] = Counter()
        cycle_types: Counter[str] = Counter()
        double_count = 0
        plus_count = 0
        quotient_collapsed_count = 0
        quotient_collapsed_parallel_only_count = 0
        simple_bridges = {tuple(sorted(edge)) for edge in nx.bridges(analysis.simple_graph)}
        for corridor, members in sorted(groups.items()):
            active = [row for row in members if row["active"]]
            if len(active) < 2:
                continue
            raw_endpoints = {
                tuple(sorted((row["source_raw_endpoint0"], row["source_raw_endpoint1"])))
                for row in active
            }
            line_numbers = {row["dat_line_number"] for row in active}
            source_ids = {row["source_stable_id"] for row in active}
            circuit_ids = {row["circuit_id"] for row in active}
            if len(line_numbers) != len(active) or len(source_ids) != len(active):
                classification = "adapter_duplicate"
            elif len(raw_endpoints) == 1 and len(circuit_ids) == len(active):
                classification = "genuine_parallel_circuits"
            else:
                classification = "unresolved"
            cycle_type = "parallel_only" if corridor in simple_bridges else "both"
            classifications[classification] += 1
            cycle_types[cycle_type] += 1
            if classification == "genuine_parallel_circuits" and len(active) == 2:
                double_count += 1
            if any(row["l_plus_associated"] for row in active):
                plus_count += 1
            mapped_targets = {row["current_quotient_target"] for row in active}
            if None not in mapped_targets and len(mapped_targets) == 1:
                quotient_collapsed_count += 1
                if cycle_type == "parallel_only":
                    quotient_collapsed_parallel_only_count += 1
            for row in members:
                row["cycle_type"] = cycle_type if row["active"] else None
                row["classification"] = classification
                del row["source_raw_endpoint0"]
                del row["source_raw_endpoint1"]
                all_rows.append(row)
        all_rows.sort(key=lambda row: (row["corridor_bus0"], row["corridor_bus1"],
                                       row["dat_line_number"]))
        summary: dict[str, int | str] = {
            "source_case": path.stem,
            "dat_sha256": hashlib.sha256(dat_bytes).hexdigest(),
            "l_plus_card_count": len(plus_by_key),
            "parallel_corridor_count": sum(classifications.values()),
            "parallel_line_row_count": len(all_rows),
            "quotient_target_line_count": sum(
                row["current_quotient_target"] is not None for row in all_rows
            ),
            "quotient_target_unique_count": len({
                row["current_quotient_target"] for row in all_rows
                if row["current_quotient_target"] is not None
            }),
            "parallel_corridors_with_single_quotient_target_count": quotient_collapsed_count,
            "parallel_only_corridors_with_single_quotient_target_count": quotient_collapsed_parallel_only_count,
            "genuine_parallel_circuit_corridor_count": classifications["genuine_parallel_circuits"],
            "genuine_double_circuit_count": double_count,
            "l_plus_extension_corridor_count": classifications["l_plus_extension"],
            "l_plus_associated_parallel_corridor_count": plus_count,
            "l_plus_false_parallel_count": classifications["l_plus_extension"],
            "adapter_duplicate_corridor_count": classifications["adapter_duplicate"],
            "unresolved_corridor_count": classifications["unresolved"],
            "parallel_only_cycle_corridor_count": cycle_types["parallel_only"],
            "simple_graph_cycle_corridor_count": cycle_types["simple_graph_cycle"],
            "both_cycle_corridor_count": cycle_types["both"],
        }
        return ParallelCorridorAuditResult(tuple(all_rows), summary)
