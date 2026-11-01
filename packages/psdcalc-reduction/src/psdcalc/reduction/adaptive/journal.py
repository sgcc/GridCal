"""Append-only canonical action-set journal with tamper-evident replay checks."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable, Mapping

from psdcalc.reduction.adaptive.actions import ReductionAction


class JournalIntegrityError(ValueError):
    """Raised when an action catalog or historical action-set snapshot is invalid."""


def deactivation_closure(
    actions: Mapping[str, ReductionAction],
    active_action_ids: Iterable[str],
    requested_action_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return requested actions and all transitively active dependents to deactivate.

    An action's ``dependencies`` are prerequisites that must remain active while
    that action is active. Removing a prerequisite therefore also removes every
    active action that depends on it, directly or transitively.

    Raises:
        JournalIntegrityError: If requested actions are not active or the active
            catalog has missing prerequisites.
    """
    active = set(active_action_ids)
    requested = set(requested_action_ids)
    if not requested or not requested <= active:
        raise JournalIntegrityError("deactivation must name currently active actions")
    if not active <= set(actions):
        raise JournalIntegrityError("active action set references an unknown catalog action")
    dependents: dict[str, set[str]] = {}
    for action_id in sorted(active):
        dependencies = set(actions[action_id].dependencies)
        if not dependencies <= active:
            raise JournalIntegrityError("active action dependency closure is incomplete")
        for dependency in dependencies:
            dependents.setdefault(dependency, set()).add(action_id)
    closure = set(requested)
    pending = sorted(requested)
    while pending:
        current = pending.pop()
        for dependent in sorted(dependents.get(current, ())):
            if dependent not in closure:
                closure.add(dependent)
                pending.append(dependent)
    return tuple(sorted(closure))


@dataclass(frozen=True)
class ActionSetSnapshot:
    """One immutable action-set version and its ordered deactivation delta."""

    iteration_index: int
    parent_action_set_fingerprint: str | None
    deactivated_action_ids: tuple[str, ...]
    active_action_ids: tuple[str, ...]
    dependency_closure: tuple[str, ...]
    mapping_delta: tuple[dict[str, str | None], ...]
    mapping_fingerprint: str
    decision_reference: str
    fingerprint: str

    @classmethod
    def create(cls, *, iteration_index: int, parent_action_set_fingerprint: str | None,
               deactivated_action_ids: tuple[str, ...], active_action_ids: tuple[str, ...],
               dependency_closure: tuple[str, ...], mapping_delta: tuple[dict[str, str | None], ...],
               mapping_fingerprint: str, decision_reference: str) -> "ActionSetSnapshot":
        """Create a snapshot fingerprint from all content, including its mapping delta."""
        payload: dict[str, object] = {
            "schema_version": "1.0", "iteration_index": iteration_index,
            "parent_action_set_fingerprint": parent_action_set_fingerprint,
            "deactivated_action_ids": list(deactivated_action_ids),
            "active_action_ids": list(active_action_ids),
            "dependency_closure": list(dependency_closure),
            "mapping_delta": list(mapping_delta), "mapping_fingerprint": mapping_fingerprint,
            "decision_reference": decision_reference,
        }
        return cls(iteration_index, parent_action_set_fingerprint, deactivated_action_ids,
                   active_action_ids, dependency_closure, mapping_delta, mapping_fingerprint,
                   decision_reference, _digest(payload))

    def canonical_payload(self) -> dict[str, object]:
        """Return the canonical persisted representation of this snapshot."""
        return {
            "schema_version": "1.0", "iteration_index": self.iteration_index,
            "parent_action_set_fingerprint": self.parent_action_set_fingerprint,
            "deactivated_action_ids": list(self.deactivated_action_ids),
            "active_action_ids": list(self.active_action_ids),
            "dependency_closure": list(self.dependency_closure),
            "mapping_delta": list(self.mapping_delta),
            "mapping_fingerprint": self.mapping_fingerprint,
            "decision_reference": self.decision_reference, "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_payload(cls, value: Mapping[str, object]) -> "ActionSetSnapshot":
        """Restore one immutable snapshot and reject unknown persisted fields."""
        expected = {
            "schema_version", "iteration_index", "parent_action_set_fingerprint",
            "deactivated_action_ids", "active_action_ids", "dependency_closure",
            "mapping_delta", "mapping_fingerprint", "decision_reference", "fingerprint",
        }
        if set(value) != expected or value.get("schema_version") != "1.0":
            raise JournalIntegrityError("action-set snapshot schema mismatch")
        parent = value["parent_action_set_fingerprint"]
        if parent is not None and not isinstance(parent, str):
            raise JournalIntegrityError("snapshot parent fingerprint has invalid type")
        try:
            mapping_delta = tuple(dict(row) for row in value["mapping_delta"])
            snapshot = cls.create(
                iteration_index=int(value["iteration_index"]),
                parent_action_set_fingerprint=parent,
                deactivated_action_ids=tuple(str(item) for item in value["deactivated_action_ids"]),
                active_action_ids=tuple(str(item) for item in value["active_action_ids"]),
                dependency_closure=tuple(str(item) for item in value["dependency_closure"]),
                mapping_delta=mapping_delta,
                mapping_fingerprint=str(value["mapping_fingerprint"]),
                decision_reference=str(value["decision_reference"]),
            )
        except (TypeError, ValueError) as exc:
            raise JournalIntegrityError(f"invalid action-set snapshot: {exc}") from exc
        if snapshot.fingerprint != value["fingerprint"]:
            raise JournalIntegrityError("persisted snapshot fingerprint mismatch")
        return snapshot


@dataclass(frozen=True)
class ReductionActionJournal:
    """Immutable action catalog plus snapshots; extensions return a new journal."""

    actions: tuple[ReductionAction, ...]
    snapshots: tuple[ActionSetSnapshot, ...]
    catalog_digest: str
    digest: str

    @classmethod
    def start(cls, actions: tuple[ReductionAction, ...]) -> "ReductionActionJournal":
        """Create an initial active-action snapshot after catalog validation."""
        ordered = tuple(sorted(actions, key=lambda item: item.action_id))
        by_id = _validate_catalog(ordered)
        catalog_digest = _digest([action.canonical_payload() for action in ordered])
        initial_ids = tuple(sorted(by_id))
        mapping_fingerprint = _mapping_fingerprint(ordered)
        snapshot = ActionSetSnapshot.create(
            iteration_index=0, parent_action_set_fingerprint=None,
            deactivated_action_ids=(), active_action_ids=initial_ids,
            dependency_closure=(), mapping_delta=(), mapping_fingerprint=mapping_fingerprint,
            decision_reference="initial",
        )
        return _journal(ordered, (snapshot,), catalog_digest)

    @property
    def active_action_ids(self) -> tuple[str, ...]:
        """Return the latest sorted active-action set."""
        return self.snapshots[-1].active_action_ids

    @property
    def action_set_fingerprint(self) -> str:
        """Return the current active-action-set fingerprint."""
        return _action_set_fingerprint(self.active_action_ids)

    def deactivate(self, action_ids: tuple[str, ...], *, reason: str,
                   decision_reference: str | None = None) -> "ReductionActionJournal":
        """Append a dependency-closed deactivation snapshot without rewriting history."""
        if not reason:
            raise JournalIntegrityError("deactivation reason must be explicit")
        by_id = _validate_catalog(self.actions)
        current = set(self.active_action_ids)
        requested = set(action_ids)
        if not requested or not requested <= current:
            raise JournalIntegrityError("deactivation must name currently active actions")
        if any(not by_id[action_id].expansion_eligible for action_id in requested):
            raise JournalIntegrityError("protected or irreversible actions cannot be deactivated")
        ordered_closure = deactivation_closure(by_id, current, requested)
        closure = set(ordered_closure)
        if any(not by_id[action_id].expansion_eligible for action_id in closure):
            raise JournalIntegrityError("dependency closure includes a protected or irreversible action")
        active = tuple(sorted(current - closure))
        delta = tuple(
            transition.canonical()
            for action_id in ordered_closure
            for transition in by_id[action_id].mapping_delta
        )
        delta = tuple(sorted(delta, key=lambda row: (row["source_id"] or "", row["target_id"] or "", row["mapping_type"] or "")))
        snapshot = ActionSetSnapshot.create(
            iteration_index=len(self.snapshots),
            parent_action_set_fingerprint=self.snapshots[-1].fingerprint,
            deactivated_action_ids=tuple(sorted(requested)), active_action_ids=active,
            dependency_closure=tuple(sorted(closure - requested)),
            mapping_delta=delta, mapping_fingerprint=_mapping_fingerprint(
                tuple(by_id[action_id] for action_id in active)),
            decision_reference=decision_reference or reason,
        )
        return _journal(self.actions, self.snapshots + (snapshot,), self.catalog_digest)

    def verify(self) -> bool:
        """Recompute catalog, set, mapping and snapshot digests; raise on tampering."""
        by_id = _validate_catalog(self.actions)
        expected_catalog = _digest([action.canonical_payload() for action in self.actions])
        if expected_catalog != self.catalog_digest:
            raise JournalIntegrityError("action catalog digest mismatch")
        if not self.snapshots:
            raise JournalIntegrityError("journal must contain its initial snapshot")
        active = set(by_id)
        previous_fingerprint: str | None = None
        for index, snapshot in enumerate(self.snapshots):
            if snapshot.iteration_index != index:
                raise JournalIntegrityError("snapshot indexes are not contiguous")
            if snapshot.parent_action_set_fingerprint != previous_fingerprint:
                raise JournalIntegrityError("snapshot parent fingerprint mismatch")
            if index == 0:
                if snapshot.deactivated_action_ids or snapshot.active_action_ids != tuple(sorted(active)):
                    raise JournalIntegrityError("invalid initial action set")
                if snapshot.mapping_delta:
                    raise JournalIntegrityError("initial snapshot cannot contain a mapping delta")
            else:
                requested = set(snapshot.deactivated_action_ids)
                if not requested or not requested <= active:
                    raise JournalIntegrityError("invalid snapshot action deactivation")
                closure = set(deactivation_closure(by_id, active, requested))
                if set(snapshot.dependency_closure) != closure - requested:
                    raise JournalIntegrityError("snapshot dependency closure mismatch")
                deactivated = closure
                for action_id in deactivated:
                    if not by_id[action_id].expansion_eligible:
                        raise JournalIntegrityError("snapshot deactivated protected or irreversible action")
                active -= deactivated
                if tuple(sorted(active)) != snapshot.active_action_ids:
                    raise JournalIntegrityError("snapshot active action set mismatch")
            expected_mapping = _mapping_fingerprint(tuple(by_id[action_id] for action_id in sorted(active)))
            if expected_mapping != snapshot.mapping_fingerprint:
                raise JournalIntegrityError("snapshot mapping fingerprint mismatch")
            expected_snapshot = ActionSetSnapshot.create(
                iteration_index=snapshot.iteration_index,
                parent_action_set_fingerprint=snapshot.parent_action_set_fingerprint,
                deactivated_action_ids=snapshot.deactivated_action_ids,
                active_action_ids=snapshot.active_action_ids,
                dependency_closure=snapshot.dependency_closure,
                mapping_delta=snapshot.mapping_delta,
                mapping_fingerprint=snapshot.mapping_fingerprint,
                decision_reference=snapshot.decision_reference,
            )
            if expected_snapshot.fingerprint != snapshot.fingerprint:
                raise JournalIntegrityError("snapshot content fingerprint mismatch")
            previous_fingerprint = snapshot.fingerprint
        if _journal_digest(self.catalog_digest, self.snapshots) != self.digest:
            raise JournalIntegrityError("journal digest mismatch")
        return True

    def to_dict(self) -> dict[str, object]:
        """Serialize the full catalog and history in stable order."""
        return {"schema_version": "1.0", "actions": [item.canonical_payload() for item in self.actions],
                "snapshots": [item.canonical_payload() for item in self.snapshots],
                "catalog_digest": self.catalog_digest, "digest": self.digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ReductionActionJournal":
        """Reload and fully verify an action journal without trusting digest fields."""
        if set(value) != {"schema_version", "actions", "snapshots", "catalog_digest", "digest"}:
            raise JournalIntegrityError("journal schema mismatch")
        if value.get("schema_version") != "1.0":
            raise JournalIntegrityError("unsupported journal schema version")
        actions = tuple(ReductionAction.from_payload(item) for item in value["actions"])
        snapshots = tuple(ActionSetSnapshot.from_payload(item) for item in value["snapshots"])
        journal = cls(actions, snapshots, str(value["catalog_digest"]), str(value["digest"]))
        journal.verify()
        return journal


def _validate_catalog(actions: tuple[ReductionAction, ...]) -> dict[str, ReductionAction]:
    by_id = {action.action_id: action for action in actions}
    if len(by_id) != len(actions):
        raise JournalIntegrityError("duplicate action ID")
    if tuple(sorted(by_id)) != tuple(action.action_id for action in actions):
        raise JournalIntegrityError("action catalog must be sorted by action ID")
    for action in actions:
        if not action.verify_fingerprint():
            raise JournalIntegrityError(f"action fingerprint mismatch: {action.action_id}")
        if not set(action.dependencies) <= set(by_id):
            raise JournalIntegrityError(f"unknown dependency for action: {action.action_id}")
        if action.action_id in action.dependencies:
            raise JournalIntegrityError("an action cannot depend on itself")
    _validate_acyclic(by_id)
    return by_id


def _validate_acyclic(by_id: Mapping[str, ReductionAction]) -> None:
    """Reject dependency cycles so canonical action execution is well-defined."""
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(action_id: str) -> None:
        if action_id in visiting:
            raise JournalIntegrityError("action dependency graph contains a cycle")
        if action_id in visited:
            return
        visiting.add(action_id)
        for dependency in by_id[action_id].dependencies:
            visit(dependency)
        visiting.remove(action_id)
        visited.add(action_id)

    for action_id in sorted(by_id):
        visit(action_id)


def _mapping_fingerprint(actions: tuple[ReductionAction, ...]) -> str:
    rows = [transition.canonical() for action in actions for transition in action.mapping_delta]
    rows.sort(key=lambda row: (row["source_id"] or "", row["target_id"] or "", row["mapping_type"] or ""))
    return _digest(rows)


def _action_set_fingerprint(action_ids: tuple[str, ...]) -> str:
    return _digest({"schema_version": "1.0", "active_action_ids": list(action_ids)})


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _journal_digest(catalog_digest: str, snapshots: tuple[ActionSetSnapshot, ...]) -> str:
    return _digest({"schema_version": "1.0", "catalog_digest": catalog_digest,
                    "snapshots": [item.canonical_payload() for item in snapshots]})


def _journal(actions: tuple[ReductionAction, ...], snapshots: tuple[ActionSetSnapshot, ...],
             catalog_digest: str) -> ReductionActionJournal:
    return ReductionActionJournal(actions, snapshots, catalog_digest,
                                  _journal_digest(catalog_digest, snapshots))
