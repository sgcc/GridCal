"""Seeded energy planning independently within canonical voltage layers."""

from collections.abc import Mapping, Sequence
from collections import Counter
from dataclasses import replace
from typing import Literal

import networkx as nx
import pypsa

from psdcalc.reduction.config.models import FeatureBuilder, PartitionOptions
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import PartitionError
from psdcalc.reduction.materializers.layer_quotient import LayerQuotientMaterializer
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.planners.energy import EnergyPartitionPlanner, PlannerName
from psdcalc.reduction.planners.multi_voltage_busmap import MultiVoltageBusmapValidator
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.voltage_layers import VoltageLayerClassifier


class VoltageLayerEnergyPlanner:
    """Allocate and plan independent seeded energy clusters per voltage layer."""

    @staticmethod
    def _component_targets(
        graph: nx.Graph, groups: tuple[tuple[StableId, ...], ...],
        mandatory: frozenset[StableId], total: int,
        cannot_link: frozenset[frozenset[StableId]],
    ) -> tuple[int, ...]:
        """Allocate a layer budget across active, same-domain components."""
        floors = []
        for group in groups:
            fixed = set(group) & mandatory
            free_components = nx.connected_components(
                graph.subgraph(set(group) - fixed))
            floors.append(len(fixed) + sum(
                2 if any(pair <= component for pair in cannot_link) else 1
                for component in free_components))
        capacities = [len(group) for group in groups]
        if total < sum(floors) or total > sum(capacities):
            raise PartitionError("Layer target is outside connected-component bounds")
        counts = floors[:]
        remaining = total - sum(counts)
        headroom = [capacity - floor for capacity, floor in zip(capacities, floors)]
        if remaining:
            overall = sum(headroom)
            quotas = [remaining * room / overall for room in headroom]
            whole = [int(quota) for quota in quotas]
            counts = [count + amount for count, amount in zip(counts, whole)]
            residues = remaining - sum(whole)
            for index in sorted(range(len(groups)),
                                key=lambda item: (-(quotas[item] - whole[item]),
                                                  groups[item][0]))[:residues]:
                counts[index] += 1
        return tuple(counts)

    def plan(
        self,
        network: pypsa.Network,
        sidecar: IdentitySidecar,
        protection: ProtectionSet,
        *,
        planner: PlannerName,
        n_clusters: int,
        seed: int,
        builders: Sequence[FeatureBuilder],
        options: PartitionOptions,
        connectivity_policy: Literal["reject", "split_disconnected"] = "reject",
        aliases: Mapping[float, float] | None = None,
        per_layer: Mapping[str, int] | None = None,
        min_cluster_size: int = 1,
        max_cluster_size: int | None = None,
    ) -> PartitionPlan:
        """Return a stable-ID global partition without cross-layer clusters."""
        layers = VoltageLayerClassifier().classify(network, sidecar, aliases=aliases)
        validator = MultiVoltageBusmapValidator()
        allocated = validator.allocate_targets(
            network, sidecar, layers, protection, n_clusters, per_layer,
        )
        # Energy planners currently reserve anchors as their own clusters.
        # Explicit adaptive action replay may absorb free Buses into an anchor;
        # this conservative seeded planner does not synthesize such actions.
        mandatory = (validator.mandatory_singletons(network, sidecar, protection) |
                     validator.identity_anchors(network, sidecar, protection))
        materializer = LayerQuotientMaterializer()
        stable: dict[StableId, StableId] = {}
        backend: dict[str, str] = {}
        for layer in layers:
            graph = validator._graph(network, sidecar, layer.bus_ids)
            groups = tuple(sorted((tuple(sorted(group)) for group in
                                   nx.connected_components(graph)),
                                  key=lambda group: group[0]))
            budgets = self._component_targets(
                graph, groups, mandatory, allocated[layer.key],
                protection.cannot_link)
            for component_index, (group, budget) in enumerate(zip(groups, budgets)):
                component_layer = replace(layer, bus_ids=group)
                extracted = materializer._layer_copy(
                    network, sidecar, component_layer)
                extracted_sidecar = materializer._sidecar_subset(
                    sidecar, extracted)
                local = materializer._protection_subset(
                    protection, extracted_sidecar)
                guarded = replace(
                    local,
                    singleton_buses=frozenset(
                        set(local.singleton_buses) | (mandatory & set(group))
                    ),
                )
                planned = EnergyPartitionPlanner().plan(
                    extracted, extracted_sidecar, guarded,
                    planner=planner, n_clusters=budget, seed=seed,
                    builders=builders, options=options,
                    connectivity_policy=connectivity_policy,
                )
                for source, target in planned.stable_busmap.items():
                    qualified = (target if target == source else
                                 StableId(target.namespace, "BusCluster",
                                          f"{layer.key}:{component_index}:"
                                          f"{target.source_key}"))
                    stable[source] = qualified
                    backend[sidecar.backend_name(source)] = qualified.encode()
        plan = PartitionPlan(
            stable, backend, n_clusters, len(set(stable.values())),
            connectivity_policy,
        )
        checked = validator.validate(network, sidecar, plan, layers, protection)
        sizes = Counter(checked.global_busmap.values())
        if any(size < min_cluster_size or
               max_cluster_size is not None and size > max_cluster_size
               for size in sizes.values()):
            raise PartitionError(
                "Multi-voltage partition violates configured cluster-size bounds")
        return plan
