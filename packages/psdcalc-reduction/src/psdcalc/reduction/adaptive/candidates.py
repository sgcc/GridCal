"""Deterministic candidate reconstruction from a frozen S1 network."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING

import pypsa

from psdcalc.reduction.adaptive.actions import ActionOperation, MappingTransition, ReductionAction
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import ReductionConfig
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.fidelity.persistence import canonical_model_digest
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.materializers.multi_voltage import MultiVoltageQuotientBuilder
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.protection.engine import ProtectionEngine, ProtectionSet
from psdcalc.reduction.protection.effective import (
    EffectiveProtectionResolver, ResolvedEffectiveProtection,
)
from psdcalc.reduction.topology.parallel_provenance import (
    ParallelSourceLedger,
)
from psdcalc.reduction.adaptive.readiness import (
    ActionCatalogReadinessResult, ActionCatalogReadinessValidator,
)
from psdcalc.reduction.readiness.ac_model import AcModelReadinessResult
from psdcalc.reduction.validation.multi_voltage import (
    AggregationInvariantSpec,
    MultiVoltageStructuralValidator,
    PreliminaryStructuralEvidence,
)
from psdcalc.reduction.adaptive.validation import CandidateValidationOrchestrator
from psdcalc.reduction.topology.voltage_layers import VoltageLayerClassifier

if TYPE_CHECKING:
    from psdcalc.reduction.core.records import ReductionRecord


class CandidateReconstructionError(RuntimeError):
    """Raised when an active action set cannot be faithfully rebuilt from S1."""


class ActionCatalogNotReady(CandidateReconstructionError):
    """Raised before materialization when active actions violate the frozen contract."""

    def __init__(self, result: ActionCatalogReadinessResult) -> None:
        self.result = result
        codes = ", ".join(sorted({row.failure_code for row in result.rejections}))
        super().__init__(f"ACTION_CATALOG_READINESS failed: {codes}")


@dataclass(frozen=True)
class CandidateBuildIdentity:
    """Canonical content identity and execution trace for one candidate build."""

    candidate_fingerprint: str
    active_action_ids: tuple[str, ...]
    execution_order: tuple[str, ...]
    partition_digest: str
    action_set_fingerprint: str
    model_digest: str
    mapping_digest: str
    structural_digest: str
    ac_readiness_digest: str
    source_model_digest: str
    source_sidecar_digest: str
    reduction_config_digest: str
    effective_protection_digest: str
    source_ledger_digest: str


@dataclass(frozen=True)
class BuiltCandidate:
    """Runtime candidate network with authoritative maps and reused Gate results."""

    network: pypsa.Network
    sidecar: IdentitySidecar
    mappings: MappingBundle
    identity: CandidateBuildIdentity
    structural_result: PreliminaryStructuralEvidence
    ac_readiness_result: AcModelReadinessResult
    records: tuple[ReductionRecord, ...]


class _PypsaActionReplay:
    """Translate a supported StableId action set into one explicit quotient plan."""

    supported_operations = frozenset({
        ActionOperation.BUS_CLUSTER,
        ActionOperation.RADIAL_ABSORPTION,
    })

    def __init__(self, protection: ProtectionSet,
                 source_ledger: ParallelSourceLedger,
                 effective: ResolvedEffectiveProtection) -> None:
        self._protection = protection
        self._source_ledger = source_ledger
        self._effective = effective

    def plan(
        self,
        source_network: pypsa.Network,
        source_sidecar: IdentitySidecar,
        active_actions: tuple[ReductionAction, ...],
        reduction_config: ReductionConfig,
    ) -> tuple[PartitionPlan, dict[StableId, MappingTransition]]:
        """Build a complete explicit partition without consulting any planner."""
        partition_config = reduction_config.partition
        if partition_config is None:
            raise CandidateReconstructionError("frozen reduction config has no partition section")
        stable_busmap = {
            source_sidecar.source_id("Bus", str(name)):
            source_sidecar.source_id("Bus", str(name))
            for name in source_network.buses.index
        }
        explicit: dict[StableId, StableId] = {}
        active_transitions: dict[StableId, MappingTransition] = {}
        for action in active_actions:
            self._validate_action_shape(action, source_sidecar)
            for transition in action.mapping_delta:
                if transition.source_id.component == "Bus":
                    target = transition.target_id
                    if target is None or target.component != "Bus":
                        raise CandidateReconstructionError(
                            f"Bus action must target a Bus StableId: {action.action_id}"
                        )
                    if transition.mapping_type not in {"preserved", "clustered"}:
                        raise CandidateReconstructionError(
                            f"Bus action has unsupported mapping type: {action.action_id}"
                        )
                    previous = explicit.get(transition.source_id)
                    if previous is not None and previous != target:
                        raise CandidateReconstructionError(
                            f"conflicting active bus transitions: {transition.source_id.encode()}"
                        )
                    explicit[transition.source_id] = target
                previous_transition = active_transitions.get(transition.source_id)
                if previous_transition is not None and previous_transition != transition:
                    raise CandidateReconstructionError(
                        f"conflicting active mapping transitions: {transition.source_id.encode()}"
                    )
                active_transitions[transition.source_id] = transition
        stable_busmap.update(explicit)
        if not stable_busmap:
            raise CandidateReconstructionError("S1 has no Bus objects")
        actual_count = len(set(stable_busmap.values()))
        backend_busmap: dict[str, str] = {}
        target_by_name: dict[str, StableId] = {}
        for source_id, target_id in sorted(stable_busmap.items()):
            try:
                source_name = source_sidecar.backend_name(source_id)
            except Exception as exc:
                raise CandidateReconstructionError(
                    f"action references missing source StableId: {source_id.encode()}"
                ) from exc
            if target_id in stable_busmap:
                try:
                    target_name = source_sidecar.backend_name(target_id)
                except Exception:
                    target_name = _generated_bus_name(target_id)
            else:
                target_name = _generated_bus_name(target_id)
            prior = target_by_name.get(target_name)
            if prior is not None and prior != target_id:
                raise CandidateReconstructionError("deterministic target Bus name collision")
            target_by_name[target_name] = target_id
            backend_busmap[source_name] = target_name
        partition = PartitionPlan(
            stable_busmap=stable_busmap,
            backend_busmap=backend_busmap,
            requested_cluster_count=actual_count,
            actual_cluster_count=actual_count,
            connectivity_policy=partition_config.connectivity_policy,
        )
        expected = {source_sidecar.source_id("Bus", str(name))
                    for name in source_network.buses.index}
        if set(partition.stable_busmap) != expected or set(backend_busmap) != {
            str(name) for name in source_network.buses.index
        }:
            raise CandidateReconstructionError(
                "explicit PartitionPlan must account for every source Bus exactly once"
            )
        return partition, active_transitions

    def rebuild(
        self,
        source_network: pypsa.Network,
        source_sidecar: IdentitySidecar,
        active_actions: tuple[ReductionAction, ...],
        reduction_config: ReductionConfig,
        partition: PartitionPlan,
        active_transitions: Mapping[StableId, MappingTransition],
    ) -> tuple[pypsa.Network, IdentitySidecar, MappingBundle,
               PreliminaryStructuralEvidence, tuple[ReductionRecord, ...]]:
        """Materialize the supplied explicit partition without replanning."""
        if not active_actions:
            mapping = _identity_mapping(source_network, source_sidecar)
            target = PypsaAdapter().clone(source_network)
            aliases = {float(source): float(target_value)
                       for source, target_value in reduction_config.voltage_layers.aliases.items()}
            layers = VoltageLayerClassifier().classify(
                source_network, source_sidecar, aliases=aliases)
            voltage_map = {
                source_id: (float(source_network.buses.loc[
                    source_sidecar.backend_name(source_id), "v_nom"]), layer.v_nom_kv)
                for layer in layers for source_id in layer.bus_ids
            }
            invariants = _invariants(reduction_config)
            structural = MultiVoltageStructuralValidator().precheck(
                source_network,
                target,
                source_sidecar,
                source_sidecar,
                mapping,
                invariants,
                self._protection,
                voltage_layer_map=voltage_map,
                allowed_line_dynamic_names=frozenset(map(str, source_network.lines.index)),
            )
            return target, source_sidecar, mapping, structural, ()
        aliases = {float(source): float(target)
                   for source, target in reduction_config.voltage_layers.aliases.items()}
        invariants = _invariants(reduction_config)
        try:
            result = MultiVoltageQuotientBuilder().build(
                simplified_network=source_network,
                parent_sidecar=source_sidecar,
                partition=partition,
                aggregation=reduction_config.aggregation,
                protection=self._protection,
                voltage_aliases=aliases,
                invariants=invariants,
                source_ledger=self._source_ledger,
                resolved_parallel_plan=self._effective.parallel_plan,
            )
        except Exception as exc:
            if isinstance(exc, CandidateReconstructionError):
                raise
            raise CandidateReconstructionError(
                f"active action set could not be materialized: {exc}"
            ) from exc
        self._validate_bus_action_map(partition.stable_busmap, result.mappings)
        self._validate_derived_reductions(active_transitions, result.mappings)
        return (result.network, result.target_sidecar, result.mappings,
                result.preliminary, result.records)

    @staticmethod
    def _validate_action_shape(action: ReductionAction, sidecar: IdentitySidecar) -> None:
        """Require every action source and target to exist in the declared StableId domain."""
        for source_id in action.source_ids:
            try:
                sidecar.backend_name(source_id)
            except Exception as exc:
                raise CandidateReconstructionError(
                    f"action references missing StableId: {source_id.encode()}"
                ) from exc
        for transition in action.mapping_delta:
            try:
                sidecar.backend_name(transition.source_id)
            except Exception as exc:
                raise CandidateReconstructionError(
                    f"action mapping references missing StableId: {transition.source_id.encode()}"
                ) from exc
            if transition.target_id is not None and transition.target_id.component not in {
                "Bus", "Line", "Transformer", "Link", "Generator", "Load",
                "StorageUnit", "Store", "ShuntImpedance",
            }:
                raise CandidateReconstructionError(
                    f"action target has unsupported component: {transition.target_id.component}"
                )
        if not any(
            row.source_id.component == "Bus" and row.target_id != row.source_id
            for row in action.mapping_delta
        ):
            raise CandidateReconstructionError(f"ineffective reduction action: {action.action_id}")
        if action.operation == ActionOperation.RADIAL_ABSORPTION and not any(
            row.source_id.component == "Line" and row.mapping_type == "absorbed_internal"
            for row in action.mapping_delta
        ):
            raise CandidateReconstructionError(
                f"radial absorption action lacks an absorbed Line: {action.action_id}"
            )

    @staticmethod
    def _validate_bus_action_map(
        expected: Mapping[StableId, StableId], mappings: MappingBundle,
    ) -> None:
        """Ensure the materializer honored the exact active bus partition."""
        actual = {row.source_id: row.target_id for row in mappings.bus_map}
        if actual != dict(expected):
            raise CandidateReconstructionError(
                "materialized Bus MappingBundle differs from active action set"
            )

    @staticmethod
    def _validate_derived_reductions(
        active_transitions: Mapping[StableId, MappingTransition],
        mappings: MappingBundle,
    ) -> None:
        """Reject unjournaled branch absorption or many-to-one device aggregation."""
        for row in mappings.branch_map:
            if row.mapping_type == "absorbed_internal" and row.source_id not in active_transitions:
                raise CandidateReconstructionError(
                    f"absorbed branch is absent from active action mapping: {row.source_id.encode()}"
                )
        for name in ("branch_map", "device_map"):
            groups: dict[StableId, list[MapEntry]] = defaultdict(list)
            for row in getattr(mappings, name):
                if row.target_id is not None:
                    groups[row.target_id].append(row)
            for target_id, rows in groups.items():
                if len(rows) > 1 and any(row.source_id not in active_transitions for row in rows):
                    raise CandidateReconstructionError(
                        f"many-to-one {name} effect is absent from active action mapping: "
                        f"{target_id.encode()}"
                    )


class PypsaAdaptiveCandidateBuilder:
    """Rebuild each supported candidate from frozen S1 using explicit actions.

    The builder never accepts a prior candidate. Action IDs are canonicalized,
    translated into an explicit StableId bus partition, and passed to the
    existing multi-voltage quotient materializer. Structural and AC-readiness
    evidence are reused from the existing validators; no PF is executed here.
    """

    def __init__(
        self,
        *,
        source_network: pypsa.Network,
        source_sidecar: IdentitySidecar,
        actions: tuple[ReductionAction, ...],
        reduction_config: ReductionConfig,
        protection: ProtectionSet | None = None,
        protection_base_dir: Path | None = None,
        protection_is_resolved: bool = False,
        source_ledger: ParallelSourceLedger | None = None,
    ) -> None:
        self._source = source_network
        self._sidecar = source_sidecar
        self._actions = tuple(sorted(actions, key=lambda item: item.action_id))
        self._by_id = {action.action_id: action for action in self._actions}
        if len(self._by_id) != len(self._actions):
            raise CandidateReconstructionError("duplicate action ID in candidate catalog")
        self._adapter = PypsaAdapter()
        self._adapter.validate_model(source_network, source_sidecar)
        self._config = reduction_config
        self._config_digest = _config_digest(reduction_config)
        self._semantic_config_digest = _semantic_config_digest(reduction_config)
        if protection_is_resolved:
            if protection is None:
                raise CandidateReconstructionError(
                    "resolved protection replay requires an explicit ProtectionSet"
                )
            compiled = protection
        else:
            compiled_protection = ProtectionEngine().compile(
                self._adapter.inventory(source_network, source_sidecar),
                reduction_config.protection.rules,
                profile=reduction_config.task.profile,
                base_dir=(protection_base_dir or Path(reduction_config.input.path).resolve().parent),
            )
            compiled = (compiled_protection.union(protection)
                        if protection is not None else compiled_protection)
        if source_ledger is None:
            raise CandidateReconstructionError(
                "source ledger must be explicit for adaptive candidate replay")
        self._source_ledger = source_ledger
        self._effective = EffectiveProtectionResolver().resolve(
            network=source_network, sidecar=source_sidecar,
            config=reduction_config, protection=compiled,
            source_ledger=source_ledger,
        )
        self._protection = self._effective.protection
        self._execution_order = _catalog_topological_order(self._actions)
        for action in self._actions:
            if not action.verify_fingerprint():
                raise CandidateReconstructionError(
                    f"invalid action fingerprint: {action.action_id}"
                )
        self._source_digest = canonical_model_digest(source_network, source_sidecar)
        self._source_sidecar_digest = _sidecar_digest(source_sidecar)
        self._source_output_digest = _pf_output_digest(source_network)

    @property
    def rebuild_from_s1(self) -> bool:
        """Return true to expose the builder's fixed reconstruction contract."""
        return True

    @property
    def supported_operations(self) -> frozenset[ActionOperation]:
        """Return the exact action types currently replayed by the PyPSA adapter."""
        return _PypsaActionReplay.supported_operations

    @property
    def source_model_digest(self) -> str:
        """Return the frozen S1 semantic digest."""
        return self._source_digest

    @property
    def source_sidecar_digest(self) -> str:
        """Return the frozen S1 sidecar digest."""
        return self._source_sidecar_digest

    @property
    def reduction_config_digest(self) -> str:
        """Return the frozen resolved reduction configuration digest."""
        return self._config_digest

    @property
    def full_config_digest(self) -> str:
        """Return the complete config digest used for provenance and drift checks."""
        return self._config_digest

    @property
    def semantic_config_digest(self) -> str:
        """Return path-independent configuration semantics for attempt identity."""
        return self._semantic_config_digest

    @property
    def action_catalog_digest(self) -> str:
        """Return the canonical digest of the builder's complete action catalog."""
        return _digest({
            "schema_version": "1.0",
            "actions": [action.canonical_payload() for action in self._actions],
        })

    @property
    def reduction_config(self) -> ReductionConfig:
        """Return the immutable resolved config used to rebuild every candidate."""
        return self._config

    @property
    def protection_set(self) -> ProtectionSet:
        """Return the fully compiled protection overlay used by candidate replay."""
        return self._protection

    @property
    def effective_protection_digest(self) -> str:
        """Return the complete pre-materialization protection semantic digest."""
        return self._effective.digest

    @property
    def source_ledger_digest(self) -> str:
        """Return the bound source-card and L+ ledger semantic digest."""
        return self._effective.source_ledger_digest

    @property
    def source_ledger(self) -> ParallelSourceLedger:
        """Return the caller-supplied authoritative source ledger."""
        return self._source_ledger

    def build_attempt_fingerprint(self, active_action_ids: tuple[str, ...]) -> str:
        """Identify an attempted build using frozen semantics, never diagnostics."""
        active = tuple(sorted(active_action_ids))
        return _digest({
            "schema_version": "2.0",
            "source_model_digest": self._source_digest,
            "source_sidecar_digest": self._source_sidecar_digest,
            "semantic_config_digest": self._semantic_config_digest,
            "effective_protection_digest": self._effective.digest,
            "source_ledger_digest": self.source_ledger_digest,
            "action_catalog_digest": self.action_catalog_digest,
            "active_action_set_fingerprint": _digest({
                "schema_version": "1.0", "active_action_ids": active,
            }),
        })

    def action_catalog_readiness(
        self, active_action_ids: tuple[str, ...],
    ) -> ActionCatalogReadinessResult:
        """Check all active actions before candidate materialization."""
        partition = self._config.partition
        return ActionCatalogReadinessValidator().validate(
            network=self._source, sidecar=self._sidecar,
            actions=self._actions, active_action_ids=active_action_ids,
            effective=self._effective,
            voltage_aliases=self._config.voltage_layers.aliases,
            connectivity_policy=(partition.connectivity_policy
                                 if partition is not None else "reject"),
        )

    def build(self, active_action_ids: tuple[str, ...]) -> BuiltCandidate:
        """Rebuild a candidate from the frozen S1 and the explicit active action set."""
        readiness = self.action_catalog_readiness(active_action_ids)
        if not readiness.ready:
            raise ActionCatalogNotReady(readiness)
        if len(active_action_ids) != len(set(active_action_ids)):
            raise CandidateReconstructionError("active action IDs must be unique")
        unknown = set(active_action_ids) - set(self._by_id)
        if unknown:
            raise CandidateReconstructionError(
                "active action set contains unknown action IDs: " + ", ".join(sorted(unknown))
            )
        active_ids = tuple(sorted(active_action_ids))
        active = set(active_ids)
        for action in self._actions:
            if action.protected or not action.reversible:
                if action.action_id not in active:
                    raise CandidateReconstructionError(
                        f"protected action cannot be omitted: {action.action_id}"
                    )
        for action_id in active_ids:
            action = self._by_id[action_id]
            if not set(action.dependencies) <= active:
                raise CandidateReconstructionError(
                    f"active action set violates dependency closure: {action_id}"
                )
        unsupported = {self._by_id[action_id].operation for action_id in active_ids} - self.supported_operations
        if unsupported:
            raise CandidateReconstructionError(
                "ACTION_TYPE_NOT_STAGE2_READY: " +
                ", ".join(sorted(item.value for item in unsupported))
            )
        if _config_digest(self._config) != self._config_digest:
            raise CandidateReconstructionError("frozen reduction config changed after builder creation")
        before_digest = canonical_model_digest(self._source, self._sidecar)
        before_sidecar_digest = _sidecar_digest(self._sidecar)
        before_output_digest = _pf_output_digest(self._source)
        detached_source = self._adapter.clone(self._source)
        replay = _PypsaActionReplay(self._protection, self._source_ledger,
                                    self._effective)
        execution_order = _active_topological_order(active_ids, self._by_id, self._execution_order)
        active_actions = tuple(self._by_id[action_id] for action_id in execution_order)
        partition, active_transitions = replay.plan(
            detached_source, self._sidecar, active_actions, self._config,
        )
        partition_digest = _partition_digest(partition)
        candidate_network, candidate_sidecar, mappings, structural, records = replay.rebuild(
            detached_source,
            self._sidecar,
            active_actions,
            self._config,
            partition,
            active_transitions,
        )
        self._assert_source_unchanged(before_digest, before_sidecar_digest, before_output_digest)
        self._adapter.validate_model(candidate_network, candidate_sidecar)
        _validate_mapping_coverage(self._source, self._sidecar, mappings)
        _validate_action_mapping(active_ids, self._by_id, mappings)
        validation = CandidateValidationOrchestrator().validate(
            source_network=self._source,
            source_sidecar=self._sidecar,
            candidate_network=candidate_network,
            candidate_sidecar=candidate_sidecar,
            mappings=mappings,
            structural=structural,
        )
        model_digest = canonical_model_digest(candidate_network, candidate_sidecar)
        mapping_digest = mappings.semantic_digest()
        structural_digest = _digest({
            "stage": validation.structural.stage,
            "finalized": validation.structural.finalized,
            "checks": dict(sorted(validation.structural.checks.items())),
            "details": dict(sorted(validation.structural.details.items())),
        })
        readiness = validation.ac_readiness
        readiness_digest = _digest({
            "status": readiness.status.value,
            "checks": dict(sorted(readiness.checks.items())),
            "details": dict(sorted(readiness.details.items())),
        })
        action_set_fingerprint = _digest({
            "schema_version": "1.0",
            "active_action_ids": active_ids,
            "execution_order": execution_order,
        })
        candidate_fingerprint = _digest({
            "schema_version": "1.0",
            "s1_model_digest": self._source_digest,
            "s1_sidecar_digest": self._source_sidecar_digest,
            "reduction_config_digest": self._config_digest,
            "effective_protection_digest": self._effective.digest,
            "source_ledger_digest": self.source_ledger_digest,
            "action_set_fingerprint": action_set_fingerprint,
            "partition_digest": partition_digest,
            "model_digest": model_digest,
            "mapping_digest": mapping_digest,
            "structural_digest": structural_digest,
            "ac_readiness_digest": readiness_digest,
        })
        identity = CandidateBuildIdentity(
            candidate_fingerprint=candidate_fingerprint,
            active_action_ids=active_ids,
            execution_order=execution_order,
            partition_digest=partition_digest,
            action_set_fingerprint=action_set_fingerprint,
            model_digest=model_digest,
            mapping_digest=mapping_digest,
            structural_digest=structural_digest,
            ac_readiness_digest=readiness_digest,
            source_model_digest=self._source_digest,
            source_sidecar_digest=self._source_sidecar_digest,
            reduction_config_digest=self._config_digest,
            effective_protection_digest=self._effective.digest,
            source_ledger_digest=self.source_ledger_digest,
        )
        self._assert_source_unchanged(before_digest, before_sidecar_digest, before_output_digest)
        return BuiltCandidate(
            candidate_network,
            candidate_sidecar,
            mappings,
            identity,
            validation.structural,
            readiness,
            records,
        )

    def _assert_source_unchanged(
        self, before_digest: str, before_sidecar_digest: str, before_output_digest: str,
    ) -> None:
        """Fail if any source semantic, identity, or solved-output state changed."""
        after_digest = canonical_model_digest(self._source, self._sidecar)
        after_sidecar_digest = _sidecar_digest(self._sidecar)
        after_output_digest = _pf_output_digest(self._source)
        if (before_digest != after_digest or after_digest != self._source_digest or
                before_sidecar_digest != after_sidecar_digest or
                after_sidecar_digest != self._source_sidecar_digest or
                before_output_digest != after_output_digest or
                after_output_digest != self._source_output_digest):
            raise CandidateReconstructionError(
                "frozen S1 semantics, source identity facts, or PF outputs changed during rebuild"
            )


def _validate_action_mapping(
    active_ids: tuple[str, ...],
    actions: Mapping[str, ReductionAction],
    mappings: MappingBundle,
) -> None:
    """Require every active journal transition to occur in the authoritative map."""
    actual = {
        (row.source_id, row.target_id, row.mapping_type)
        for name in ("bus_map", "branch_map", "device_map", "control_map")
        for row in getattr(mappings, name)
    }
    for action_id in active_ids:
        for transition in actions[action_id].mapping_delta:
            target_id = transition.target_id
            if transition.source_id.component == "Bus" and target_id is not None:
                target_id = StableId(target_id.namespace, "Bus", target_id.source_key)
            expected = (transition.source_id, target_id, transition.mapping_type)
            if expected not in actual:
                raise CandidateReconstructionError(
                    f"active action mapping differs from MappingBundle: {action_id}"
                )


def _validate_mapping_coverage(
    network: pypsa.Network, sidecar: IdentitySidecar, mappings: MappingBundle,
) -> None:
    """Require complete source identity coverage for all quotient map families."""
    expected_buses = {sidecar.source_id("Bus", str(name)) for name in network.buses.index}
    expected_branches = {
        sidecar.source_id(component, str(name))
        for component, frame in (("Line", network.lines), ("Transformer", network.transformers),
                                 ("Link", network.links))
        for name in frame.index
    }
    expected_devices = {
        sidecar.source_id(component.name, str(name))
        for component in network.components
        if component.name in {"Generator", "Load", "StorageUnit", "Store", "ShuntImpedance"}
        for name in component.static.index
    }
    expected_controls = {
        item.source_id for item in PypsaAdapter().inventory(network, sidecar).items
        if item.controllable
    }
    groups = (
        ("Bus", expected_buses, tuple(row.source_id for row in mappings.bus_map)),
        ("branch", expected_branches, tuple(row.source_id for row in mappings.branch_map)),
        ("device", expected_devices, tuple(row.source_id for row in mappings.device_map)),
        ("control", expected_controls, tuple(row.source_id for row in mappings.control_map)),
    )
    for label, expected, actual in groups:
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise CandidateReconstructionError(
                f"candidate {label} MappingBundle coverage is incomplete or ambiguous"
            )


def _catalog_topological_order(actions: tuple[ReductionAction, ...]) -> tuple[str, ...]:
    """Return a deterministic topological catalog order or reject invalid dependencies."""
    by_id = {action.action_id: action for action in actions}
    for action in actions:
        references = set(action.dependencies) | set(action.parent_action_ids)
        unknown = references - set(by_id)
        if unknown:
            raise CandidateReconstructionError(
                f"action dependency references unknown catalog IDs: {action.action_id}"
            )
    indegree = {action_id: len(set(action.dependencies)) for action_id, action in by_id.items()}
    children: dict[str, set[str]] = defaultdict(set)
    for action_id, action in by_id.items():
        for dependency in set(action.dependencies):
            children[dependency].add(action_id)
    ready = sorted(action_id for action_id, count in indegree.items() if count == 0)
    output: list[str] = []
    while ready:
        current = ready.pop(0)
        output.append(current)
        for child in sorted(children[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(output) != len(actions):
        raise CandidateReconstructionError("action dependency graph contains a cycle")
    return tuple(output)


def _active_topological_order(
    active_ids: tuple[str, ...],
    actions: Mapping[str, ReductionAction],
    catalog_order: tuple[str, ...],
) -> tuple[str, ...]:
    """Project the canonical catalog topological order onto active IDs."""
    active = set(active_ids)
    return tuple(action_id for action_id in catalog_order if action_id in active)


def _generated_bus_name(target_id: StableId) -> str:
    """Return a deterministic backend-only target name for a new StableId Bus."""
    suffix = hashlib.sha256(target_id.encode().encode("utf-8")).hexdigest()[:20]
    return f"adaptive-bus-{suffix}"


def _empty_protection() -> ProtectionSet:
    """Return an empty protection set without sharing mutable collections."""
    return ProtectionSet(frozenset(), frozenset(), frozenset(), frozenset(), frozenset())


def _invariants(config: ReductionConfig) -> tuple[AggregationInvariantSpec, ...]:
    """Convert resolved invariant configuration to the existing Gate contract."""
    return tuple(
        AggregationInvariantSpec(**item.model_dump(mode="python"))
        for item in config.aggregation.invariants
    )


def _identity_mapping(
    network: pypsa.Network, sidecar: IdentitySidecar,
) -> MappingBundle:
    """Return complete one-to-one source ownership for the no-reduction state."""
    operation_id = "adaptive-no-active-actions"
    bus_rows = tuple(
        MapEntry(source, source, "preserved", operation_id)
        for source in sorted(
            sidecar.source_id("Bus", str(name)) for name in network.buses.index
        )
    )
    branch_rows = tuple(
        MapEntry(source, source, "preserved", operation_id)
        for component, frame in (("Line", network.lines),
                                 ("Transformer", network.transformers),
                                 ("Link", network.links))
        for source in sorted(sidecar.source_id(component, str(name)) for name in frame.index)
    )
    device_rows = tuple(
        MapEntry(source, source, "preserved", operation_id)
        for component in network.components
        if component.name in {"Generator", "Load", "StorageUnit", "Store", "ShuntImpedance"}
        for source in sorted(sidecar.source_id(component.name, str(name))
                             for name in component.static.index)
    )
    owners = {row.source_id: row for row in device_rows}
    control_rows = tuple(
        MapEntry(item.source_id, owners[item.source_id].target_id,
                 owners[item.source_id].mapping_type, operation_id)
        for item in PypsaAdapter().inventory(network, sidecar).items
        if item.controllable
    )
    return MappingBundle(bus_rows, branch_rows, device_rows, control_rows)


def _partition_digest(partition: PartitionPlan) -> str:
    """Hash every source Bus target, backend projection, and count policy canonically."""
    payload = {
        "stable_busmap": [
            {"source": source.encode(), "target": target.encode()}
            for source, target in sorted(partition.stable_busmap.items())
        ],
        "backend_busmap": [
            {"source_name": source, "target_name": target}
            for source, target in sorted(partition.backend_busmap.items())
        ],
        "requested_cluster_count": partition.requested_cluster_count,
        "actual_cluster_count": partition.actual_cluster_count,
        "connectivity_policy": partition.connectivity_policy,
    }
    return _digest(payload)


def _sidecar_digest(sidecar: IdentitySidecar) -> str:
    """Hash authoritative identity rows in canonical sidecar order."""
    return _digest(sidecar.to_rows())


def _config_digest(config: ReductionConfig) -> str:
    """Hash a resolved Pydantic configuration in canonical JSON mode."""
    return _digest(config.model_dump(mode="json"))


def _semantic_config_digest(config: ReductionConfig) -> str:
    """Hash algorithm configuration while omitting artifact location paths."""
    payload = config.model_dump(mode="json")
    payload["input"].pop("path", None)
    payload["run"].pop("output_dir", None)
    return _digest(payload)


def _pf_output_digest(network: pypsa.Network) -> str:
    """Hash all populated dynamic PyPSA output tables to detect source leakage."""
    output: list[dict[str, object]] = []
    for component in network.components:
        for attribute, frame in component.dynamic.items():
            defaults = component.defaults
            if attribute not in defaults.index or not str(defaults.loc[attribute, "status"]).startswith("Output"):
                continue
            output.append({
                "component": component.name,
                "attribute": attribute,
                "columns": list(map(str, frame.columns)),
                "index": [str(value) for value in frame.index],
                "values": frame.astype(object).where(frame.notna(), None).values.tolist(),
            })
    return _digest(sorted(output, key=lambda item: (str(item["component"]), str(item["attribute"]))))


def _digest(payload: object) -> str:
    """Compute a stable SHA-256 over JSON-compatible canonical content."""
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=_json_default,
    ).encode("utf-8")).hexdigest()


def _json_default(value: object) -> object:
    """Convert Pydantic scalar and pandas timestamp values into stable JSON."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, float) and math.isnan(value):
        return "NaN"
    raise TypeError(f"Unsupported canonical digest value: {type(value).__name__}")
