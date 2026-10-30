"""External source-profile, circuit and L+ association identities.

These tables complement the PR-017 BPA audit. They do not infer card identity
or circuit IDs from PyPSA backend names and remain independent of NetCDF.
"""

from dataclasses import dataclass
from collections.abc import Mapping
import json
import math
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq
import pypsa

from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import IdentityError
from psdcalc.reduction.topology.voltage_layers import VoltageLayerClassifier


@dataclass(frozen=True)
class SourceProfileContract:
    """Declare whether an authoritative source supplies circuit identities."""

    name: str
    circuit_ids: Literal["unavailable", "required"]

    def __post_init__(self) -> None:
        if not self.name or self.circuit_ids not in {"unavailable", "required"}:
            raise IdentityError("Invalid source-profile circuit ID contract")
        if (self.name in {"BPA10056814", "BPA10056819"} and
                self.circuit_ids != "required"):
            raise IdentityError("Audited BPA source profiles require circuit IDs")

    @classmethod
    def audited_bpa_case(cls, case_id: Literal["BPA10056814", "BPA10056819"]
                         ) -> "SourceProfileContract":
        """Declare the two audited BPA L-card circuit contracts."""
        if case_id not in {"BPA10056814", "BPA10056819"}:
            raise IdentityError("Unknown audited BPA source profile")
        return cls(case_id, "required")


@dataclass(frozen=True)
class LineCircuitProvenance:
    """External raw-card and parsed circuit evidence for one Line StableId."""

    line_id: StableId
    source_card_identity: str | None
    circuit_id: str | None
    raw_card: str | None


@dataclass(frozen=True)
class LPlusAssociation:
    """One source L+ card terminal and its derived ShuntImpedance identity."""

    base_line_id: StableId
    source_card_identity: str
    terminal_side: Literal["FROM", "TO"]
    terminal_bus_id: StableId
    shunt_id: StableId
    signed_reactor_value: float


@dataclass(frozen=True)
class ParallelSourceLedger:
    """Persistable external provenance with fail-closed ownership checks."""

    profile: SourceProfileContract
    lines: tuple[LineCircuitProvenance, ...] = ()
    l_plus: tuple[LPlusAssociation, ...] = ()

    def validate(self, network: pypsa.Network,
                 sidecar: IdentitySidecar, *,
                 voltage_aliases: Mapping[float, float] | None = None) -> None:
        """Validate exact source-card, circuit, and L+ owner identities."""
        source_lines = {sidecar.source_id("Line", str(name))
                        for name in network.lines.index}
        canonical_voltage = {
            bus: layer.v_nom_kv
            for layer in VoltageLayerClassifier().classify(
                network, sidecar, aliases=voltage_aliases)
            for bus in layer.bus_ids
        }
        line_ids: set[StableId] = set()
        cards: set[str] = set()
        circuits: set[tuple[float, StableId, StableId, str]] = set()
        for item in self.lines:
            if item.line_id.component != "Line" or item.line_id not in source_lines:
                raise IdentityError("Circuit provenance references an absent Line")
            if item.line_id in line_ids:
                raise IdentityError("Duplicate Line circuit provenance")
            line_ids.add(item.line_id)
            if item.source_card_identity is not None:
                if not item.source_card_identity or item.source_card_identity in cards:
                    raise IdentityError("Duplicate or blank source Line card identity")
                cards.add(item.source_card_identity)
            if self.profile.circuit_ids == "required":
                if (not item.source_card_identity or not item.raw_card or
                        not item.circuit_id or not item.circuit_id.strip()):
                    raise IdentityError("Required Line circuit/card evidence is missing")
                if (self.profile.name in {"BPA10056814", "BPA10056819"}
                        and not item.raw_card.startswith("L ")):
                    raise IdentityError("Audited BPA circuit must bind to an L card")
                row = network.lines.loc[sidecar.backend_name(item.line_id)]
                bus0 = sidecar.source_id("Bus", str(row.bus0))
                bus1 = sidecar.source_id("Bus", str(row.bus1))
                left, right = sorted((bus0, bus1))
                voltage = canonical_voltage[bus0]
                key = (voltage, left, right, item.circuit_id)
                if key in circuits:
                    raise IdentityError("Duplicate circuit ID within one corridor")
                circuits.add(key)
            elif item.circuit_id is not None:
                raise IdentityError("Circuit ID contradicts unavailable source profile")
        if self.profile.circuit_ids == "required" and line_ids != source_lines:
            raise IdentityError("Required circuit ledger does not cover every Line")

        card_owners: dict[str, StableId] = {}
        card_sides: set[tuple[str, str]] = set()
        base_sides: set[tuple[StableId, str]] = set()
        shunts: set[StableId] = set()
        for item in self.l_plus:
            if (not item.source_card_identity or
                    item.terminal_side not in {"FROM", "TO"} or
                    item.base_line_id.component != "Line" or
                    item.shunt_id.component != "ShuntImpedance" or
                    item.terminal_bus_id.component != "Bus" or
                    not math.isfinite(item.signed_reactor_value)):
                raise IdentityError("Invalid L+ association fields")
            if item.base_line_id not in source_lines:
                raise IdentityError("L+ base Line is absent")
            previous = card_owners.setdefault(
                item.source_card_identity, item.base_line_id)
            if previous != item.base_line_id:
                raise IdentityError("L+ source card has multiple base Lines")
            card_side = (item.source_card_identity, item.terminal_side)
            base_side = (item.base_line_id, item.terminal_side)
            if (card_side in card_sides or base_side in base_sides or
                    item.shunt_id in shunts):
                raise IdentityError("Duplicate L+ card side, base side, or Shunt")
            card_sides.add(card_side)
            base_sides.add(base_side)
            shunts.add(item.shunt_id)
            line = network.lines.loc[sidecar.backend_name(item.base_line_id)]
            terminal = "bus0" if item.terminal_side == "FROM" else "bus1"
            expected_bus = sidecar.source_id("Bus", str(line[terminal]))
            if item.terminal_bus_id != expected_bus:
                raise IdentityError("L+ terminal Bus disagrees with ordered Line terminal")
            shunt_name = sidecar.backend_name(item.shunt_id)
            shunt = network.shunt_impedances.loc[shunt_name]
            if sidecar.source_id("Bus", str(shunt.bus)) != expected_bus:
                raise IdentityError("L+ Shunt is attached to a different Bus")
            if float(shunt.b) != item.signed_reactor_value:
                raise IdentityError("L+ signed reactor value differs from Shunt")

    def save_parquet(self, directory: Path) -> tuple[Path, ...]:
        """Write deterministic external ledgers, including empty typed tables."""
        directory.mkdir(parents=True, exist_ok=True)
        profile_path = directory / "source_profile.json"
        profile_path.write_text(json.dumps({
            "name": self.profile.name, "circuit_ids": self.profile.circuit_ids,
        }, sort_keys=True) + "\n", encoding="utf-8")
        line_schema = pa.schema([
            ("line_id", pa.string()), ("source_card_identity", pa.string()),
            ("circuit_id", pa.string()), ("raw_card", pa.string()),
        ])
        lines_path = directory / "line_provenance.parquet"
        pq.write_table(pa.Table.from_pylist([{
            "line_id": item.line_id.encode(),
            "source_card_identity": item.source_card_identity,
            "circuit_id": item.circuit_id, "raw_card": item.raw_card,
        } for item in sorted(self.lines, key=lambda row: row.line_id)],
            schema=line_schema), lines_path)
        lplus_schema = pa.schema([
            ("base_line_id", pa.string()), ("source_card_identity", pa.string()),
            ("terminal_side", pa.string()), ("terminal_bus_id", pa.string()),
            ("shunt_id", pa.string()), ("signed_reactor_value", pa.float64()),
        ])
        lplus_path = directory / "l_plus_associations.parquet"
        pq.write_table(pa.Table.from_pylist([{
            "base_line_id": item.base_line_id.encode(),
            "source_card_identity": item.source_card_identity,
            "terminal_side": item.terminal_side,
            "terminal_bus_id": item.terminal_bus_id.encode(),
            "shunt_id": item.shunt_id.encode(),
            "signed_reactor_value": item.signed_reactor_value,
        } for item in sorted(self.l_plus,
                             key=lambda row: (row.base_line_id, row.terminal_side))],
            schema=lplus_schema), lplus_path)
        return profile_path, lines_path, lplus_path

    @classmethod
    def load_parquet(cls, directory: Path) -> "ParallelSourceLedger":
        """Reload source profile and both external ledgers from disk."""
        profile = json.loads((directory / "source_profile.json").read_text(
            encoding="utf-8"))
        lines = tuple(LineCircuitProvenance(
            StableId.decode(row["line_id"]), row["source_card_identity"],
            row["circuit_id"], row["raw_card"])
            for row in pq.read_table(directory / "line_provenance.parquet").to_pylist())
        l_plus = tuple(LPlusAssociation(
            StableId.decode(row["base_line_id"]), row["source_card_identity"],
            row["terminal_side"], StableId.decode(row["terminal_bus_id"]),
            StableId.decode(row["shunt_id"]), row["signed_reactor_value"])
            for row in pq.read_table(directory / "l_plus_associations.parquet").to_pylist())
        return cls(SourceProfileContract(**profile), lines, l_plus)
