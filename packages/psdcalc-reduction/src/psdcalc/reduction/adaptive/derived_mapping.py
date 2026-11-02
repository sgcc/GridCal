"""Backend-neutral, immutable contracts for secondary branch mapping effects."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Literal

from psdcalc.reduction.core.ids import StableId


EffectType = Literal["ABSORBED_INTERNAL", "MANY_TO_ONE_BRANCH_AGGREGATION"]
CLOSURE_SCHEMA = "derived-mapping-closure/v1"


def _digest(payload: object) -> str:
    """Hash a versioned, order-stable JSON payload."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class DerivedBranchEffect:
    """One canonical branch absorption or ordinary many-to-one Line group."""

    effect_type: EffectType
    component_type: str
    source_ids: tuple[StableId, ...]
    mapped_endpoints: tuple[StableId, StableId]
    voltage_kv: float
    grouping_values: tuple[str, ...]
    policy: str
    eligibility: tuple[tuple[str, bool, bool], ...]

    def __post_init__(self) -> None:
        """Reject ambiguous or order-dependent effect rows."""
        if self.effect_type not in {"ABSORBED_INTERNAL", "MANY_TO_ONE_BRANCH_AGGREGATION"}:
            raise ValueError("unsupported derived branch effect type")
        if self.component_type not in {"Line", "Link"}:
            raise ValueError("unsupported derived branch component")
        if not self.source_ids or tuple(sorted(set(self.source_ids))) != self.source_ids:
            raise ValueError("derived source IDs must be sorted and unique")
        if len(self.mapped_endpoints) != 2 or self.mapped_endpoints != tuple(sorted(self.mapped_endpoints)):
            raise ValueError("mapped endpoints must be a sorted Bus pair")
        if not math.isfinite(self.voltage_kv) or self.voltage_kv <= 0:
            raise ValueError("canonical voltage must be finite and positive")
        if self.effect_type == "MANY_TO_ONE_BRANCH_AGGREGATION":
            if self.component_type != "Line" or len(self.source_ids) < 2:
                raise ValueError("many-to-one effect requires at least two Lines")
            if self.mapped_endpoints[0] == self.mapped_endpoints[1]:
                raise ValueError("many-to-one Line endpoints must differ")
        elif len(self.source_ids) != 1 or self.mapped_endpoints[0] != self.mapped_endpoints[1]:
            raise ValueError("absorption requires one internal source branch")
        if tuple(sorted(self.eligibility)) != self.eligibility or len(self.eligibility) != len(self.source_ids):
            raise ValueError("eligibility must cover sorted source members")

    def canonical_payload(self) -> dict[str, object]:
        """Return a target-name-free semantic effect payload."""
        return {
            "effect_type": self.effect_type,
            "component_type": self.component_type,
            "source_ids": [item.encode() for item in self.source_ids],
            "mapped_endpoints": [item.encode() for item in self.mapped_endpoints],
            "voltage_kv": self.voltage_kv,
            "grouping_values": list(self.grouping_values),
            "policy": self.policy,
            "eligibility": [list(row) for row in self.eligibility],
        }

    @property
    def fingerprint(self) -> str:
        """Return a deterministic anonymous fingerprint for public evidence."""
        return _digest(self.canonical_payload())

    @property
    def group_key(self) -> tuple[object, ...]:
        """Identify a quotient group without using its generated target name."""
        return (self.component_type, self.mapped_endpoints, self.voltage_kv,
                self.grouping_values, self.policy)


@dataclass(frozen=True)
class DerivedMappingClosure:
    """Sorted secondary effects bound to frozen source and policy semantics."""

    basis: tuple[tuple[str, str], ...]
    effects: tuple[DerivedBranchEffect, ...]

    def __post_init__(self) -> None:
        """Require one canonical basis and a deterministic effect order."""
        if tuple(sorted(self.basis)) != self.basis or len({k for k, _ in self.basis}) != len(self.basis):
            raise ValueError("closure basis must be sorted with unique keys")
        ordered = tuple(sorted(self.effects, key=lambda item: (
            item.effect_type, item.component_type,
            tuple(source.encode() for source in item.source_ids),
            tuple(bus.encode() for bus in item.mapped_endpoints),
        )))
        if ordered != self.effects or len(set(ordered)) != len(ordered):
            raise ValueError("closure effects must be sorted and unique")

    def canonical_payload(self) -> dict[str, object]:
        """Return the schema-tagged closure including its frozen basis."""
        return {"schema": CLOSURE_SCHEMA, "basis": dict(self.basis),
                "effects": [item.canonical_payload() for item in self.effects]}

    @property
    def digest(self) -> str:
        """Return the path-independent canonical SHA-256 closure digest."""
        return _digest(self.canonical_payload())


class DerivedClosureError(RuntimeError):
    """Fail-closed mismatch with deterministic public-safe evidence fields."""

    def __init__(self, code: str, *, expected: DerivedMappingClosure | None = None,
                 actual: DerivedMappingClosure | None = None,
                 effects: tuple[DerivedBranchEffect, ...] = (),
                 source_groups: tuple[tuple[StableId, ...], ...] = ()) -> None:
        """Record stable code and anonymous effect fingerprints without raw IDs."""
        self.code = code
        self.expected_digest = expected.digest if expected is not None else None
        self.actual_digest = actual.digest if actual is not None else None
        fingerprints = {item.fingerprint for item in effects}
        fingerprints.update(_digest(sorted(item.encode() for item in group))
                            for group in source_groups)
        self.offending_identity_fingerprints = tuple(sorted(fingerprints))
        super().__init__(code)


def validate_derived_closures(expected: DerivedMappingClosure,
                              actual: DerivedMappingClosure) -> None:
    """Require exact bidirectional equality with specific fail-closed codes."""
    if expected.basis != actual.basis:
        raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT",
                                  expected=expected, actual=actual)
    expected_set = set(expected.effects)
    actual_set = set(actual.effects)
    if expected_set == actual_set:
        return
    missing = expected_set - actual_set
    extra = actual_set - expected_set
    for left in missing:
        for right in extra:
            if (left.effect_type == right.effect_type and left.group_key == right.group_key
                    and left.source_ids != right.source_ids):
                raise DerivedClosureError("DERIVED_CLOSURE_MEMBERSHIP_MISMATCH",
                                          expected=expected, actual=actual,
                                          effects=(left, right))
    if missing:
        raise DerivedClosureError("DERIVED_CLOSURE_MISSING_EFFECT",
                                  expected=expected, actual=actual,
                                  effects=tuple(sorted(missing, key=lambda item: item.fingerprint)))
    raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT",
                              expected=expected, actual=actual,
                              effects=tuple(sorted(extra, key=lambda item: item.fingerprint)))
