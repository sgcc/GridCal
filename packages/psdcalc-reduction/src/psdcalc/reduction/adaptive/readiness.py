"""Deterministic static legality Gate for an adaptive ReductionAction set."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping

import pypsa

from psdcalc.reduction.adaptive.actions import ActionOperation, ReductionAction
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import BackendCapabilityError, PartitionError
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.planners.multi_voltage_busmap import MultiVoltageBusmapValidator
from psdcalc.reduction.protection.effective import ResolvedEffectiveProtection
from psdcalc.reduction.topology.voltage_layers import VoltageLayerClassifier


def _digest(value: object) -> str:
    """Hash canonical, path-free readiness evidence."""
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _anonymous(source_id: StableId) -> str:
    """Expose a StableId fingerprint without exposing its source key."""
    return hashlib.sha256(source_id.encode().encode("utf-8")).hexdigest()


@dataclass(frozen=True, order=True)
class ActionReadinessRejection:
    """One action-scoped, source-name-free legality failure."""

    action_id: str
    failure_code: str
    offending_identity_fingerprints: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return stable JSON evidence for a rejected action."""
        return {
            "action_id": self.action_id,
            "failure_code": self.failure_code,
            "offending_identity_fingerprints": list(self.offending_identity_fingerprints),
        }


@dataclass(frozen=True)
class ActionCatalogReadinessResult:
    """Pre-materialization Gate with deterministic per-action rejections."""

    ready: bool
    rejections: tuple[ActionReadinessRejection, ...]
    digest: str
    effective_protection_digest: str


class ActionCatalogReadinessValidator:
    """Check active actions against the complete resolved protection contract."""

    supported_operations = frozenset({
        ActionOperation.BUS_CLUSTER, ActionOperation.RADIAL_ABSORPTION,
    })

    def validate(
        self, *, network: pypsa.Network, sidecar: IdentitySidecar,
        actions: tuple[ReductionAction, ...], active_action_ids: tuple[str, ...],
        effective: ResolvedEffectiveProtection,
        voltage_aliases: Mapping[float, float] | None = None,
        connectivity_policy: str = "reject",
    ) -> ActionCatalogReadinessResult:
        """Return all independently detectable action violations without building."""
        by_id = {action.action_id: action for action in actions}
        active = tuple(sorted(active_action_ids))
        active_set = set(active)
        rejected: set[ActionReadinessRejection] = set()

        def fail(action_id: str, code: str, *ids: StableId) -> None:
            rejected.add(ActionReadinessRejection(
                action_id, code, tuple(sorted({_anonymous(item) for item in ids})),
            ))

        for action_id in active:
            if action_id not in by_id:
                fail(action_id, "UNKNOWN_ACTION")
        if len(active) != len(active_set):
            for action_id in active:
                if active.count(action_id) > 1:
                    fail(action_id, "DUPLICATE_ACTIVE_ACTION")
        for action in actions:
            if (action.protected or not action.reversible) and action.action_id not in active_set:
                fail(action.action_id, "REQUIRED_ACTION_OMITTED")

        bus_ids = {sidecar.source_id("Bus", str(name)) for name in network.buses.index}
        source_ids = {StableId.decode(row["source_id"])
                      for row in sidecar.to_rows()}
        ledger_identity_ids = (
            {item.base_line_id for item in effective.source_ledger.l_plus} |
            {item.shunt_id for item in effective.source_ledger.l_plus}
        )
        busmap = {bus: bus for bus in bus_ids}
        owners: dict[StableId, set[str]] = {}
        transitions: dict[StableId, object] = {}
        for action_id in active:
            action = by_id.get(action_id)
            if action is None:
                continue
            if action.operation not in self.supported_operations:
                fail(action_id, "UNSUPPORTED_ACTION_TYPE", *action.source_ids)
            if not set(action.dependencies) <= active_set:
                fail(action_id, "INVALID_DEPENDENCY_CLOSURE")
            if not action.verify_fingerprint():
                fail(action_id, "ACTION_FINGERPRINT_INVALID")
            if {row.source_id for row in action.mapping_delta} != set(action.source_ids):
                fail(action_id, "ACTION_MAPPING_INCONSISTENT", *action.source_ids)
            if {row.target_id for row in action.mapping_delta if row.target_id is not None} != set(action.target_ids):
                fail(action_id, "ACTION_MAPPING_INCONSISTENT", *action.source_ids)
            if not any(row.source_id.component == "Bus" and
                       row.target_id != row.source_id for row in action.mapping_delta):
                fail(action_id, "ACTION_MAPPING_INCONSISTENT", *action.source_ids)
            if (action.operation == ActionOperation.RADIAL_ABSORPTION and
                    not any(row.source_id.component == "Line" and
                            row.mapping_type == "absorbed_internal"
                            for row in action.mapping_delta)):
                fail(action_id, "ACTION_MAPPING_INCONSISTENT", *action.source_ids)
            for row in action.mapping_delta:
                if row.source_id not in source_ids:
                    fail(action_id, "ACTION_SOURCE_MISSING", row.source_id)
                    continue
                previous = transitions.get(row.source_id)
                if previous is not None and previous != row:
                    fail(action_id, "ACTION_MAPPING_INCONSISTENT", row.source_id)
                transitions[row.source_id] = row
                owners.setdefault(row.source_id, set()).add(action_id)
                if row.source_id.component == "Bus":
                    if row.target_id is None or row.target_id.component != "Bus":
                        fail(action_id, "ACTION_MAPPING_INCONSISTENT", row.source_id)
                    elif row.source_id in bus_ids:
                        busmap[row.source_id] = row.target_id
                    if row.mapping_type not in {"preserved", "clustered"}:
                        fail(action_id, "ACTION_MAPPING_INCONSISTENT", row.source_id)
                if (row.source_id in effective.protected_lines and
                        (row.mapping_type != "preserved" or row.target_id != row.source_id)):
                    fail(action_id, "PROTECTED_PARALLEL_IDENTITY_CONFLICT", row.source_id)
                if (row.source_id in ledger_identity_ids and
                        (row.mapping_type != "preserved" or row.target_id != row.source_id)):
                    fail(action_id, "SOURCE_LEDGER_IDENTITY_CONFLICT", row.source_id)

        for anchor in sorted(effective.identity_anchors):
            if busmap.get(anchor) != anchor:
                for action_id in owners.get(anchor, {"<catalog>"}):
                    fail(action_id, "ANCHOR_MAPPED_AWAY", anchor)
        for singleton in sorted(effective.strict_singletons):
            members = tuple(sorted(bus for bus, target in busmap.items()
                                   if target == busmap.get(singleton)))
            if busmap.get(singleton) != singleton or members != (singleton,):
                related = set(owners.get(singleton, ()))
                for bus in members:
                    related.update(owners.get(bus, ()))
                for action_id in related or {"<catalog>"}:
                    fail(action_id, "STRICT_SINGLETON_CONFLICT", singleton, *members)
        for pair in sorted(effective.protection.cannot_link,
                           key=lambda group: tuple(sorted(group))):
            left, right = sorted(pair)
            if busmap.get(left) == busmap.get(right):
                related = owners.get(left, set()) | owners.get(right, set())
                for action_id in related or {"<catalog>"}:
                    fail(action_id, "CANNOT_LINK_CONFLICT", left, right)
        for action_id in active:
            action = by_id.get(action_id)
            if action is None:
                continue
            for row in action.mapping_delta:
                if row.source_id.component != "Line" or row.mapping_type != "absorbed_internal":
                    continue
                try:
                    line = network.lines.loc[sidecar.backend_name(row.source_id)]
                    left = sidecar.source_id("Bus", str(line.bus0))
                    right = sidecar.source_id("Bus", str(line.bus1))
                except (KeyError, ValueError):
                    fail(action_id, "ACTION_MAPPING_INCONSISTENT", row.source_id)
                    continue
                if busmap.get(left) != busmap.get(right):
                    fail(action_id, "ACTION_MAPPING_INCONSISTENT", row.source_id)

        if not rejected and bus_ids:
            backend = {}
            for source, target in sorted(busmap.items()):
                backend[sidecar.backend_name(source)] = (
                    sidecar.backend_name(target) if target in bus_ids else target.encode())
            count = len(set(busmap.values()))
            partition = PartitionPlan(busmap, backend, count, count,
                                      connectivity_policy)
            try:
                layers = VoltageLayerClassifier().classify(
                    network, sidecar, aliases=voltage_aliases)
                MultiVoltageBusmapValidator().validate(
                    network, sidecar, partition, layers, effective.protection)
            except (BackendCapabilityError, PartitionError):
                for action_id in active or ("<catalog>",):
                    fail(action_id, "PARTITION_INVALID")
        rows = tuple(sorted(rejected))
        digest = _digest({
            "schema": "action-catalog-readiness/v1",
            "active_action_ids": active,
            "effective_protection_digest": effective.digest,
            "rejections": [row.to_dict() for row in rows],
        })
        return ActionCatalogReadinessResult(not rows, rows, digest,
                                            effective.digest)
