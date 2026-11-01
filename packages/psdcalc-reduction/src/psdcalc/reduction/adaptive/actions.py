"""Canonical reversible reduction actions and their mapping transitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Mapping

from psdcalc.reduction.core.ids import StableId


class ActionOperation(StrEnum):
    """Operations currently supported by the reduction implementation."""

    RADIAL_ABSORPTION = "RADIAL_ABSORPTION"
    BUS_CLUSTER = "BUS_CLUSTER"
    PARALLEL_CORRIDOR_AGGREGATION = "PARALLEL_CORRIDOR_AGGREGATION"


class ActionContractError(ValueError):
    """Raised when an action cannot be replayed or has invalid identity data."""


@dataclass(frozen=True, order=True)
class MappingTransition:
    """Stable source-to-target mapping row captured by one reduction action."""

    source_id: StableId
    target_id: StableId | None
    mapping_type: str

    def __post_init__(self) -> None:
        if not self.mapping_type:
            raise ActionContractError("mapping_type must be non-empty")

    def canonical(self) -> dict[str, str | None]:
        """Return the backend-name-free mapping payload used in fingerprints."""
        return {"source_id": self.source_id.encode(),
                "target_id": self.target_id.encode() if self.target_id else None,
                "mapping_type": self.mapping_type}

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> "MappingTransition":
        """Restore a transition only from its exact StableId-based schema."""
        if set(value) != {"source_id", "target_id", "mapping_type"}:
            raise ActionContractError("mapping transition schema mismatch")
        source, target, mapping_type = value["source_id"], value["target_id"], value["mapping_type"]
        if not isinstance(source, str) or target is not None and not isinstance(target, str) or not isinstance(mapping_type, str):
            raise ActionContractError("mapping transition values have invalid types")
        return cls(StableId.decode(source), StableId.decode(target) if target is not None else None,
                   mapping_type)


@dataclass(frozen=True)
class ReductionAction:
    """One explicitly supported reduction operation with exact inverse metadata."""

    action_id: str
    fingerprint: str
    operation: ActionOperation
    source_ids: tuple[StableId, ...]
    target_ids: tuple[StableId, ...]
    mapping_delta: tuple[MappingTransition, ...]
    voltage_layer: str
    component_id: str
    region_fingerprint: str
    dependencies: tuple[str, ...]
    parent_action_ids: tuple[str, ...]
    protection_reasons: tuple[str, ...]
    protected: bool
    reversible: bool
    inverse_semantics: str
    parameters: Mapping[str, object]
    audit_reason: str

    @property
    def expansion_eligible(self) -> bool:
        """Whether this action may appear in an adaptive expansion candidate."""
        return self.reversible and not self.protected

    @classmethod
    def create(
        cls,
        *,
        action_id: str,
        operation: ActionOperation,
        source_ids: tuple[StableId, ...],
        target_ids: tuple[StableId, ...],
        mapping_delta: tuple[MappingTransition, ...],
        voltage_layer: str,
        component_id: str,
        region_fingerprint: str,
        dependencies: tuple[str, ...] = (),
        parent_action_ids: tuple[str, ...] = (),
        protection_reasons: tuple[str, ...] = (),
        protected: bool = False,
        reversible: bool = True,
        inverse_semantics: str = "rebuild_from_s1_with_action_deactivated",
        parameters: Mapping[str, object] | None = None,
        audit_reason: str,
    ) -> "ReductionAction":
        """Create an action with sorted identity collections and a canonical digest."""
        if not action_id or not voltage_layer or not component_id or not region_fingerprint:
            raise ActionContractError("action, layer, component, and region identifiers are required")
        if not audit_reason:
            raise ActionContractError("audit_reason must be explicit")
        if not reversible and inverse_semantics:
            raise ActionContractError("non-reversible actions cannot claim inverse semantics")
        sources = tuple(sorted(set(source_ids), key=StableId.encode))
        targets = tuple(sorted(set(target_ids), key=StableId.encode))
        mappings = tuple(sorted(set(mapping_delta), key=lambda row: (
            row.source_id.encode(), row.target_id.encode() if row.target_id else "", row.mapping_type)))
        if {row.source_id for row in mappings} != set(sources):
            raise ActionContractError("mapping delta must account for every action source StableId")
        if {row.target_id for row in mappings if row.target_id is not None} != set(targets):
            raise ActionContractError("target StableIds must exactly match mapping delta targets")
        deps = tuple(sorted(set(dependencies)))
        parents = tuple(sorted(set(parent_action_ids)))
        protection = tuple(sorted(set(protection_reasons)))
        params = dict(parameters or {})
        payload = {
            "schema_version": "1.0", "action_id": action_id, "operation": operation.value,
            "source_ids": [item.encode() for item in sources],
            "target_ids": [item.encode() for item in targets],
            "mapping_delta": [row.canonical() for row in mappings],
            "voltage_layer": voltage_layer, "component_id": component_id,
            "region_fingerprint": region_fingerprint, "dependencies": deps,
            "parent_action_ids": parents, "protection_reasons": protection,
            "protected": protected, "reversible": reversible,
            "inverse_semantics": inverse_semantics, "parameters": _canonical(params),
            "audit_reason": audit_reason,
        }
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                                  ensure_ascii=False).encode("utf-8")).hexdigest()
        return cls(action_id, fingerprint, operation, sources, targets, mappings,
                   voltage_layer, component_id, region_fingerprint, deps, parents,
                   protection, protected, reversible, inverse_semantics, params, audit_reason)

    def canonical_payload(self) -> dict[str, object]:
        """Return the canonical action payload, including its verified fingerprint."""
        return {
            "schema_version": "1.0", "action_id": self.action_id,
            "fingerprint": self.fingerprint, "operation": self.operation.value,
            "source_ids": [item.encode() for item in self.source_ids],
            "target_ids": [item.encode() for item in self.target_ids],
            "mapping_delta": [row.canonical() for row in self.mapping_delta],
            "voltage_layer": self.voltage_layer, "component_id": self.component_id,
            "region_fingerprint": self.region_fingerprint, "dependencies": list(self.dependencies),
            "parent_action_ids": list(self.parent_action_ids),
            "protection_reasons": list(self.protection_reasons), "protected": self.protected,
            "reversible": self.reversible, "inverse_semantics": self.inverse_semantics,
            "parameters": _canonical(self.parameters), "audit_reason": self.audit_reason,
        }

    def verify_fingerprint(self) -> bool:
        """Recompute the action fingerprint without trusting the stored digest."""
        rebuilt = ReductionAction.create(
            action_id=self.action_id, operation=self.operation, source_ids=self.source_ids,
            target_ids=self.target_ids, mapping_delta=self.mapping_delta,
            voltage_layer=self.voltage_layer, component_id=self.component_id,
            region_fingerprint=self.region_fingerprint, dependencies=self.dependencies,
            parent_action_ids=self.parent_action_ids, protection_reasons=self.protection_reasons,
            protected=self.protected, reversible=self.reversible,
            inverse_semantics=self.inverse_semantics, parameters=self.parameters,
            audit_reason=self.audit_reason,
        )
        return rebuilt.fingerprint == self.fingerprint

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> "ReductionAction":
        """Restore an action by rebuilding its fingerprint from canonical content."""
        expected = {
            "schema_version", "action_id", "fingerprint", "operation", "source_ids",
            "target_ids", "mapping_delta", "voltage_layer", "component_id",
            "region_fingerprint", "dependencies", "parent_action_ids",
            "protection_reasons", "protected", "reversible", "inverse_semantics",
            "parameters", "audit_reason",
        }
        if set(value) != expected or value.get("schema_version") != "1.0":
            raise ActionContractError("reduction action schema mismatch")
        try:
            source_ids = tuple(StableId.decode(item) for item in value["source_ids"])
            target_ids = tuple(StableId.decode(item) for item in value["target_ids"])
            mapping_delta = tuple(MappingTransition.from_payload(item) for item in value["mapping_delta"])
            action = cls.create(
                action_id=str(value["action_id"]),
                operation=ActionOperation(str(value["operation"])),
                source_ids=source_ids, target_ids=target_ids, mapping_delta=mapping_delta,
                voltage_layer=str(value["voltage_layer"]), component_id=str(value["component_id"]),
                region_fingerprint=str(value["region_fingerprint"]),
                dependencies=tuple(str(item) for item in value["dependencies"]),
                parent_action_ids=tuple(str(item) for item in value["parent_action_ids"]),
                protection_reasons=tuple(str(item) for item in value["protection_reasons"]),
                protected=bool(value["protected"]), reversible=bool(value["reversible"]),
                inverse_semantics=str(value["inverse_semantics"]),
                parameters=value["parameters"], audit_reason=str(value["audit_reason"]),
            )
        except (TypeError, ValueError, KeyError) as exc:
            if isinstance(exc, ActionContractError):
                raise
            raise ActionContractError(f"invalid persisted action: {exc}") from exc
        if action.fingerprint != value["fingerprint"]:
            raise ActionContractError("persisted action fingerprint mismatch")
        return action


def _canonical(value: object) -> object:
    if isinstance(value, StableId):
        return value.encode()
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise ActionContractError(f"unsupported action parameter type: {type(value).__name__}")
