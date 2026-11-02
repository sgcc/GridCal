"""Resolve every adaptive protection source before catalog validation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

import pypsa

from psdcalc.reduction.config.models import ReductionConfig
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.planners.multi_voltage_busmap import MultiVoltageBusmapValidator
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.parallel_corridors import (
    ParallelCorridorAnalyzer, ProtectedParallelPlan,
)
from psdcalc.reduction.topology.parallel_provenance import ParallelSourceLedger


def _digest(payload: object) -> str:
    """Hash one canonical JSON protection payload."""
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def source_ledger_digest(ledger: ParallelSourceLedger) -> str:
    """Bind source-card and L+ semantics without backend names or file paths."""
    return _digest({
        "profile": {
            "name": ledger.profile.name,
            "circuit_ids": ledger.profile.circuit_ids,
            "blank_circuit_ids": ledger.profile.blank_circuit_ids,
        },
        "coverage_scope": ledger.coverage_scope,
        "lines": [{
            "line_id": item.line_id.encode(),
            "source_card_identity": item.source_card_identity,
            "circuit_id": item.circuit_id,
            "raw_card": item.raw_card,
        } for item in sorted(ledger.lines, key=lambda row: row.line_id)],
        "l_plus": [{
            "base_line_id": item.base_line_id.encode(),
            "source_card_identity": item.source_card_identity,
            "terminal_side": item.terminal_side,
            "terminal_bus_id": item.terminal_bus_id.encode(),
            "shunt_id": item.shunt_id.encode(),
            "source_value_state": item.source_value_state,
            "source_reactor_value": item.source_reactor_value,
        } for item in sorted(ledger.l_plus, key=lambda row: (
            row.base_line_id, row.terminal_side, row.source_card_identity))],
    })


def _protection_payload(protection: ProtectionSet) -> dict[str, object]:
    """Serialize all five declarative protection dimensions deterministically."""
    return {
        "traced": sorted(item.encode() for item in protection.traced),
        "preserved_components": sorted(item.encode() for item in protection.preserved_components),
        "terminal_buses": sorted(item.encode() for item in protection.terminal_buses),
        "singleton_buses": sorted(item.encode() for item in protection.singleton_buses),
        "cannot_link": sorted(sorted(item.encode() for item in pair)
                              for pair in protection.cannot_link),
    }


@dataclass(frozen=True)
class ResolvedEffectiveProtection:
    """One digest-bound anchor, singleton, line, and source-ledger contract."""

    protection: ProtectionSet
    identity_anchors: frozenset[StableId]
    strict_singletons: frozenset[StableId]
    protected_lines: frozenset[StableId]
    source_ledger: ParallelSourceLedger
    source_ledger_digest: str
    parallel_plan: ProtectedParallelPlan | None
    digest: str


class EffectiveProtectionResolver:
    """Compile the materializer's complete protection view before replay."""

    def resolve(
        self, *, network: pypsa.Network, sidecar: IdentitySidecar,
        config: ReductionConfig, protection: ProtectionSet,
        source_ledger: ParallelSourceLedger,
    ) -> ResolvedEffectiveProtection:
        """Return stable roles and digest or raise on invalid source evidence."""
        aliases: Mapping[float, float] = config.voltage_layers.aliases
        source_ledger.validate(network, sidecar, voltage_aliases=aliases)
        parallel_plan: ProtectedParallelPlan | None = None
        effective = protection
        protected_lines = frozenset(
            item for item in protection.preserved_components
            if item.component == "Line"
        )
        if config.aggregation.parallel_corridors.policy == "preserve_protected_members":
            lplus_base = frozenset(item.base_line_id for item in source_ledger.l_plus)
            parallel_plan = ParallelCorridorAnalyzer().analyze(
                network, sidecar, protection, voltage_aliases=aliases,
                lplus_base_lines=lplus_base,
            )
            protected_lines |= parallel_plan.protected_lines | lplus_base
            effective = protection.union(ProtectionSet(
                traced=frozenset(),
                preserved_components=(parallel_plan.protected_lines | lplus_base |
                                      frozenset(item.shunt_id for item in source_ledger.l_plus)),
                terminal_buses=frozenset(),
                singleton_buses=parallel_plan.mandatory_singletons,
                cannot_link=frozenset(),
            ))
        validator = MultiVoltageBusmapValidator()
        anchors = validator.identity_anchors(network, sidecar, effective)
        strict = validator.mandatory_singletons(network, sidecar, effective)
        ledger_digest = source_ledger_digest(source_ledger)
        digest = _digest({
            "schema": "resolved-effective-protection/v1",
            "protection": _protection_payload(effective),
            "identity_anchors": sorted(item.encode() for item in anchors),
            "strict_singletons": sorted(item.encode() for item in strict),
            "protected_lines": sorted(item.encode() for item in protected_lines),
            "source_ledger_digest": ledger_digest,
        })
        return ResolvedEffectiveProtection(
            effective, anchors, strict, protected_lines, source_ledger,
            ledger_digest, parallel_plan, digest,
        )
