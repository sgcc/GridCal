"""Bind audited BPA L/L+ cards to the existing external provenance ledger."""

from collections.abc import Mapping
from math import isfinite
from pathlib import Path
from typing import Any, Literal, Protocol

import pypsa

from psdcalc.reduction.core.ids import IdentitySidecar
from psdcalc.reduction.exceptions import BackendCapabilityError, IdentityError
from psdcalc.reduction.topology.parallel_provenance import (
    LPlusAssociation, LineCircuitProvenance, ParallelSourceLedger,
    SourceProfileContract,
)


class _SourceLine(Protocol):
    key: str
    source_identity: Any
    attributes: Mapping[str, Any]


class _SourceCase(Protocol):
    ac_lines: Mapping[str, _SourceLine]


def _reactor(value: object) -> float:
    """Preserve one finite source-card value without electrical conversion."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BackendCapabilityError("L+ source reactor value is not numeric")
    result = float(value)
    if not isfinite(result):
        raise BackendCapabilityError("L+ source reactor value is nonfinite")
    return result


def build_bpa_source_ledger(
    case: _SourceCase, network: pypsa.Network, sidecar: IdentitySidecar,
    dat_path: str | Path, *,
    case_id: Literal["BPA10056814", "BPA10056819"],
) -> ParallelSourceLedger:
    """Return strict L/L+ provenance without copying raw cards into PyPSA."""
    profile = SourceProfileContract.audited_bpa_case(case_id)
    raw_lines = Path(dat_path).read_bytes().splitlines()
    plus_by_key: dict[tuple[bytes, bytes, bytes], tuple[int, bytes]] = {}
    for number, raw in enumerate(raw_lines, 1):
        if raw.startswith(b"L+"):
            key = (raw[6:18], raw[19:31], raw[31:32])
            if key in plus_by_key:
                raise BackendCapabilityError("Duplicate L+ raw-card key")
            plus_by_key[key] = number, raw
    lines: list[LineCircuitProvenance] = []
    associations: list[LPlusAssociation] = []
    matched: set[tuple[bytes, bytes, bytes]] = set()
    for backend_name, row in network.lines.sort_index().iterrows():
        line_id = sidecar.source_id("Line", str(backend_name))
        source = case.ac_lines.get(line_id.source_key)
        if source is None or source.key != line_id.source_key:
            raise BackendCapabilityError("PyPSA Line has no PSDCase source")
        origin = source.source_identity
        numbers = getattr(origin, "dat_line_numbers", ())
        if (getattr(origin, "dat_card_type", None) != "L" or
                len(numbers) != 1 or not isinstance(numbers[0], int) or
                isinstance(numbers[0], bool) or not 1 <= numbers[0] <= len(raw_lines)):
            raise BackendCapabilityError("Line lacks one valid DAT L-card identity")
        number = numbers[0]
        raw = raw_lines[number - 1]
        if not raw.startswith(b"L "):
            raise BackendCapabilityError("Line source card is not L")
        circuit = raw[31:32].decode("ascii", errors="strict").strip()
        if not circuit or circuit != source.attributes.get("circuit_id"):
            raise BackendCapabilityError("Line source circuit ID differs from DAT")
        key = (raw[6:18], raw[19:31], raw[31:32])
        lines.append(LineCircuitProvenance(
            line_id, f"L:{number}", circuit, raw.decode("latin-1")))
        plus = plus_by_key.get(key)
        has_plus = ("from_reactor_mvar" in source.attributes or
                    "to_reactor_mvar" in source.attributes)
        if bool(plus) != has_plus:
            raise BackendCapabilityError("L+ source association differs from PSDCase")
        if plus is None:
            continue
        matched.add(key)
        if not all(field in source.attributes for field in (
                "from_reactor_mvar", "to_reactor_mvar")):
            raise BackendCapabilityError("L+ terminal source values are incomplete")
        for side, bus_column, attribute in (
                ("FROM", "bus0", "from_reactor_mvar"),
                ("TO", "bus1", "to_reactor_mvar")):
            shunt_name = f"{line_id.source_key}:L+:{side}"
            try:
                shunt_id = sidecar.source_id("ShuntImpedance", shunt_name)
            except IdentityError as exc:
                raise BackendCapabilityError("Derived L+ Shunt is absent") from exc
            associations.append(LPlusAssociation(
                line_id, f"L+:{plus[0]}", side,
                sidecar.source_id("Bus", str(row[bus_column])), shunt_id,
                _reactor(source.attributes[attribute])))
    if matched != set(plus_by_key):
        raise BackendCapabilityError("Unbound L+ source card remains")
    ledger = ParallelSourceLedger(profile, tuple(lines), tuple(associations))
    ledger.validate(network, sidecar)
    return ledger
