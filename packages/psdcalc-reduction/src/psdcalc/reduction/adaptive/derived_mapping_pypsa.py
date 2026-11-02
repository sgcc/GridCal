"""PyPSA 1.2.4 grouping adapter for backend-neutral derived mapping contracts."""

from __future__ import annotations

from collections import Counter, defaultdict
from importlib.metadata import version
from typing import TYPE_CHECKING

import pypsa

from psdcalc.reduction.adaptive.derived_mapping import (
    DerivedBranchEffect, DerivedClosureError, DerivedMappingClosure, _digest,
)
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.protection.effective import ResolvedEffectiveProtection
from psdcalc.reduction.topology.voltage_layers import VoltageLayerClassifier

if TYPE_CHECKING:
    from psdcalc.reduction.config.models import ReductionConfig


GROUPING_CONTRACT = "pypsa-1.2.4-line-endpoint-v1"


def _ordered_effects(effects: list[DerivedBranchEffect]) -> tuple[DerivedBranchEffect, ...]:
    """Sort effects by their complete canonical content."""
    return tuple(sorted(effects, key=lambda item: (
        item.effect_type, item.component_type,
        tuple(source.encode() for source in item.source_ids),
        tuple(bus.encode() for bus in item.mapped_endpoints),
    )))


class PypsaDerivedMappingAdapter:
    """Predict and normalize secondary effects for the frozen spatial quotient."""

    def __init__(self, *, source: pypsa.Network, sidecar: IdentitySidecar,
                 partition: PartitionPlan, config: ReductionConfig,
                 effective: ResolvedEffectiveProtection, source_model_digest: str,
                 source_sidecar_digest: str, partition_digest: str) -> None:
        """Bind only frozen S1 inputs and an authoritative active Bus partition."""
        if version("pypsa") != "1.2.4":
            raise DerivedClosureError("DERIVED_GROUPING_CONTRACT_UNSUPPORTED")
        if config.aggregation.parallel_corridors.policy == "preserve_all_members":
            raise DerivedClosureError("DERIVED_GROUPING_CONTRACT_UNSUPPORTED")
        self.source = source
        self.sidecar = sidecar
        self.partition = partition
        self.config = config
        self.effective = effective
        self.policy = config.aggregation.parallel_corridors.policy
        self._protected = frozenset(effective.protected_lines) | frozenset(
            item for item in effective.protection.preserved_components
            if item.component in {"Line", "Link"}
        ) | frozenset(item.base_line_id for item in effective.source_ledger.l_plus)
        self._ledger_lines = frozenset(item.line_id for item in effective.source_ledger.lines)
        layers = VoltageLayerClassifier().classify(
            source, sidecar, aliases={float(a): float(b) for a, b in
                                      config.voltage_layers.aliases.items()},
        )
        self._voltage_by_bus = {bus: layer.v_nom_kv for layer in layers
                                for bus in layer.bus_ids}
        parallel = effective.parallel_plan
        parallel_payload = [] if parallel is None else [
            {
                "members": sorted(item.encode() for item in corridor.members),
                "active": sorted(item.encode() for item in corridor.active_members),
                "protected": corridor.protected,
                "cycle_type": corridor.cycle_type,
            }
            for corridor in sorted(parallel.corridors, key=lambda row:
                                   tuple(sorted(item.encode() for item in row.members)))
        ]
        self.basis = tuple(sorted({
            "source_model_digest": source_model_digest,
            "source_sidecar_digest": source_sidecar_digest,
            "partition_digest": partition_digest,
            "aggregation_policy_digest": _digest(config.aggregation.model_dump(mode="json")),
            "effective_protection_digest": effective.digest,
            "source_ledger_digest": effective.source_ledger_digest,
            "protected_parallel_plan_digest": _digest(parallel_payload),
            "grouping_contract": GROUPING_CONTRACT,
        }.items()))

    def _branch(self, source_id: StableId) -> tuple[tuple[StableId, StableId], float]:
        """Return canonical mapped terminals and layer for one frozen branch."""
        name = self.sidecar.backend_name(source_id)
        frame = {"Line": self.source.lines, "Link": self.source.links,
                 "Transformer": self.source.transformers}.get(source_id.component)
        if frame is None or name not in frame.index:
            raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT")
        row = frame.loc[name]
        original = (self.sidecar.source_id("Bus", str(row.bus0)),
                    self.sidecar.source_id("Bus", str(row.bus1)))
        if any(bus not in self.partition.stable_busmap for bus in original):
            raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT")
        if source_id.component == "Link":
            for column in frame.columns:
                if str(column).startswith("bus") and str(column) not in {"bus0", "bus1"}:
                    value = row[column]
                    if value is not None and str(value) not in {"", "nan", "<NA>"}:
                        raise DerivedClosureError("DERIVED_GROUPING_CONTRACT_UNSUPPORTED")
        if self._voltage_by_bus[original[0]] != self._voltage_by_bus[original[1]]:
            if source_id.component != "Transformer":
                raise DerivedClosureError("DERIVED_GROUPING_CONTRACT_UNSUPPORTED")
        mapped = tuple(sorted(self.partition.stable_busmap[bus] for bus in original))
        return mapped, self._voltage_by_bus[original[0]]

    def _effect(self, effect_type: str, component: str,
                members: tuple[StableId, ...], endpoints: tuple[StableId, StableId],
                voltage: float) -> DerivedBranchEffect:
        """Construct an immutable effect from canonical source membership."""
        sources = tuple(sorted(members))
        eligibility = tuple((item.encode(), item in self._ledger_lines,
                             item in self._protected) for item in sources)
        return DerivedBranchEffect(effect_type, component, sources, endpoints,
                                   voltage, (), self.policy, eligibility)

    def expected(self) -> DerivedMappingClosure:
        """Predict all legal branch effects independently of materialized mappings."""
        effects: list[DerivedBranchEffect] = []
        ordinary: dict[tuple[float, StableId, StableId], list[StableId]] = defaultdict(list)
        for component, frame in (("Line", self.source.lines), ("Link", self.source.links)):
            for name in sorted(map(str, frame.index)):
                source_id = self.sidecar.source_id(component, name)
                endpoints, voltage = self._branch(source_id)
                if endpoints[0] == endpoints[1]:
                    if source_id in self._protected:
                        raise DerivedClosureError("UNAUTHORIZED_PROTECTED_BRANCH_AGGREGATION")
                    effects.append(self._effect("ABSORBED_INTERNAL", component,
                                                (source_id,), endpoints, voltage))
                elif component == "Line" and source_id not in self._protected:
                    ordinary[(voltage, *endpoints)].append(source_id)
        for (voltage, left, right), members in sorted(ordinary.items()):
            if len(members) > 1:
                effects.append(self._effect("MANY_TO_ONE_BRANCH_AGGREGATION", "Line",
                                            tuple(members), (left, right), voltage))
        for name in self.source.transformers.index:
            endpoints, _ = self._branch(self.sidecar.source_id("Transformer", str(name)))
            if endpoints[0] == endpoints[1]:
                raise DerivedClosureError("UNAUTHORIZED_PROTECTED_BRANCH_AGGREGATION")
        return DerivedMappingClosure(self.basis, _ordered_effects(effects))

    def actual(self, mappings: MappingBundle, target: pypsa.Network,
               target_sidecar: IdentitySidecar) -> DerivedMappingClosure:
        """Normalize actual branch rows without trusting generated target names."""
        expected_ids = {
            self.sidecar.source_id(component, str(name))
            for component, frame in (("Line", self.source.lines),
                                     ("Link", self.source.links),
                                     ("Transformer", self.source.transformers))
            for name in frame.index
        }
        rows = tuple(mappings.branch_map)
        actual_ids = [row.source_id for row in rows]
        counts = Counter(actual_ids)
        protected_missing_or_duplicate = tuple(sorted(
            item for item in self._protected if item in expected_ids and counts[item] != 1
        ))
        if protected_missing_or_duplicate:
            raise DerivedClosureError("UNAUTHORIZED_PROTECTED_BRANCH_AGGREGATION",
                                      source_groups=(protected_missing_or_duplicate,))
        if len(actual_ids) != len(counts) or set(actual_ids) != expected_ids:
            wrong = tuple(sorted(set(actual_ids) ^ expected_ids))
            duplicates = tuple(sorted(item for item, count in counts.items() if count > 1))
            raise DerivedClosureError("DERIVED_CLOSURE_MEMBERSHIP_MISMATCH",
                                      source_groups=(wrong + duplicates,))
        effects: list[DerivedBranchEffect] = []
        groups: dict[StableId, list[StableId]] = defaultdict(list)
        for row in rows:
            endpoints, voltage = self._branch(row.source_id)
            if row.mapping_type not in {"preserved", "aggregated", "absorbed_internal"}:
                raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT",
                                          source_groups=((row.source_id,),))
            if row.source_id in self._protected and (
                row.mapping_type != "preserved" or row.target_id != row.source_id
            ):
                raise DerivedClosureError("UNAUTHORIZED_PROTECTED_BRANCH_AGGREGATION",
                                          source_groups=((row.source_id,),))
            if row.mapping_type == "absorbed_internal":
                if row.target_id is not None or endpoints[0] != endpoints[1] or row.source_id in self._protected:
                    raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT")
                effects.append(self._effect("ABSORBED_INTERNAL", row.source_id.component,
                                            (row.source_id,), endpoints, voltage))
            elif row.target_id is None:
                raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT")
            else:
                if row.target_id.component != row.source_id.component:
                    raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT",
                                              source_groups=((row.source_id,),))
                if row.mapping_type == "preserved" and row.target_id != row.source_id:
                    raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT",
                                              source_groups=((row.source_id,),))
                if endpoints[0] == endpoints[1]:
                    raise DerivedClosureError("DERIVED_CLOSURE_MISSING_EFFECT")
                try:
                    target_name = target_sidecar.backend_name(row.target_id)
                except Exception as exc:
                    raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT",
                                              source_groups=((row.source_id,),)) from exc
                target_frame = {"Line": target.lines, "Link": target.links,
                                "Transformer": target.transformers}[row.target_id.component]
                if target_name not in target_frame.index:
                    raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT",
                                              source_groups=((row.source_id,),))
                groups[row.target_id].append(row.source_id)
        for target_id, members in groups.items():
            if len(members) < 2:
                continue
            if target_id.component != "Line" or any(item.component != "Line" for item in members):
                raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT")
            source_rows = [row for row in rows if row.source_id in members]
            if any(row.mapping_type != "aggregated" for row in source_rows):
                raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT")
            if any(item in self._protected for item in members):
                raise DerivedClosureError("UNAUTHORIZED_PROTECTED_BRANCH_AGGREGATION",
                                          source_groups=(tuple(sorted(members)),))
            mapped = {self._branch(item) for item in members}
            if len(mapped) != 1:
                raise DerivedClosureError("DERIVED_CLOSURE_MEMBERSHIP_MISMATCH")
            endpoints, voltage = next(iter(mapped))
            try:
                target_name = target_sidecar.backend_name(target_id)
            except Exception as exc:
                raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT") from exc
            if target_name not in target.lines.index:
                raise DerivedClosureError("DERIVED_CLOSURE_UNEXPECTED_EFFECT")
            target_row = target.lines.loc[target_name]
            target_endpoints = tuple(sorted((
                target_sidecar.source_id("Bus", str(target_row.bus0)),
                target_sidecar.source_id("Bus", str(target_row.bus1)),
            )))
            if target_endpoints != endpoints:
                raise DerivedClosureError("DERIVED_CLOSURE_MEMBERSHIP_MISMATCH")
            effects.append(self._effect("MANY_TO_ONE_BRANCH_AGGREGATION", "Line",
                                        tuple(members), endpoints, voltage))
        return DerivedMappingClosure(self.basis, _ordered_effects(effects))
