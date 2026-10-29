"""Deterministic post-topology PyPSA spatial planner wrappers."""

from collections.abc import Sequence
from dataclasses import replace
from typing import Literal

import pandas as pd
import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.backends.pypsa.compat import (
    busmap_by_greedy_modularity, busmap_by_hac, busmap_by_kmeans,
)
from psdcalc.reduction.config.models import FeatureBuilder, PartitionOptions
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import BackendCapabilityError, PartitionError
from psdcalc.reduction.features.energy import EnergyFeatureBuilder
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.planners.custom_busmap import CustomBusmapPlanner
from psdcalc.reduction.protection.engine import ProtectionSet


PlannerName = Literal["pypsa_kmeans", "pypsa_hac", "pypsa_modularity"]


class EnergyPartitionPlanner:
    """Use public n.cluster.spatial accessors after the topology stage."""

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
        ring_edges: Sequence[str] = (),
    ) -> PartitionPlan:
        """Return a deterministic partition or raise an explicit capability error."""
        if n_clusters < 1 or n_clusters > len(network.buses):
            raise PartitionError("Requested cluster count is outside 1..bus_count")
        if network.buses.v_nom.nunique(dropna=False) > 1:
            raise BackendCapabilityError("Energy clustering cannot cross voltage levels")
        if not network.transformers.empty:
            raise BackendCapabilityError("Energy quotient cannot preserve Transformer yet")
        adapter = PypsaAdapter()
        adapter.validate_model(network, sidecar)
        fixed = set(protection.singleton_buses | protection.terminal_buses)
        for item in adapter.inventory(network, sidecar).items:
            if item.controllable:
                fixed.update(item.terminals)
        for name in ring_edges:
            if name not in network.lines.index:
                raise PartitionError(f"Protected ring edge is missing: {name}")
            row = network.lines.loc[name]
            fixed.add(sidecar.source_id("Bus", str(row.bus0)))
            fixed.add(sidecar.source_id("Bus", str(row.bus1)))
        free_names = sorted(name for name in network.buses.index
                            if sidecar.source_id("Bus", str(name)) not in fixed)
        free_clusters = n_clusters - len(fixed)
        if free_names and (free_clusters < 1 or free_clusters > len(free_names)):
            raise PartitionError(
                "Requested clusters cannot preserve ring/control/transformer bus identities"
            )
        if not free_names and free_clusters != 0:
            raise PartitionError("Requested cluster count differs from fixed bus count")
        mapping = {source: source for source in fixed}
        if free_names:
            if free_clusters == len(free_names):
                mapping.update({sidecar.source_id("Bus", str(name)):
                                sidecar.source_id("Bus", str(name))
                                for name in free_names})
            else:
                labels = self._labels(network, planner, free_names, free_clusters,
                                      seed, builders, options)
                if set(labels.index) != set(free_names):
                    raise PartitionError("PyPSA planner omitted one or more buses")
                namespace = sidecar.source_id("Bus", str(free_names[0])).namespace
                mapping.update({
                    sidecar.source_id("Bus", str(name)):
                    StableId(namespace, "BusCluster", f"{planner}-{label}")
                    for name, label in labels.items()
                })
        guarded = replace(protection,
                          singleton_buses=frozenset(set(protection.singleton_buses) | fixed))
        result = CustomBusmapPlanner().plan_mapping(
            network, sidecar, mapping, guarded,
            connectivity_policy=connectivity_policy,
        )
        return PartitionPlan(result.stable_busmap, result.backend_busmap,
                             n_clusters, result.actual_cluster_count,
                             result.connectivity_policy)

    @staticmethod
    def _labels(network: pypsa.Network, planner: PlannerName,
                free_names: list[str], n_clusters: int, seed: int,
                builders: Sequence[FeatureBuilder],
                options: PartitionOptions) -> pd.Series:
        """Call only public spatial accessors on a detached network copy."""
        working = network.copy()
        bus_index = pd.Index(free_names)
        enabled = [builder for builder in builders if builder.enabled]
        if planner == "pypsa_kmeans":
            energy_types = [builder.type for builder in enabled
                            if builder.type in {"load_generation", "renewable_time"}]
            if energy_types:
                raw = EnergyFeatureBuilder().build(working, energy_types)
                scales = {}
                for column in raw.columns:
                    feature_type = ("renewable_time" if column.startswith("renewable_")
                                    else "load_generation")
                    builder = next(item for item in enabled
                                   if item.type == feature_type)
                    scales[column] = (builder.weight if builder.weight is not None
                                      else 1.0)
                magnitude = (raw.abs() * pd.Series(scales)).sum(axis=1)
                magnitude = magnitude.reindex(bus_index).fillna(0.0)
                maximum = max(1.0, float(magnitude.max()))
                weights = (1 + (magnitude / maximum * 9).round()).astype(int)
            else:
                weights = pd.Series(1, index=bus_index, dtype=int)
            return busmap_by_kmeans(
                working, weights, n_clusters, seed,
                buses_i=bus_index, n_init=options.n_init,
            )
        if planner == "pypsa_hac":
            feature_types = [builder.type for builder in enabled]
            if not feature_types:
                raise PartitionError("HAC requires at least one enabled feature builder")
            raw = EnergyFeatureBuilder().build(working, feature_types).loc[bus_index]
            feature = pd.DataFrame(index=raw.index)
            for column in raw.columns:
                values = raw[column]
                deviation = float(values.std(ddof=0))
                normalized = (values - values.mean()) / (deviation if deviation else 1.0)
                builder = next((item for item in enabled
                                if column in {"x", "y"} and item.type == "geographic_xy" or
                                column in {"load_mw", "generation_mw"} and
                                item.type == "load_generation" or
                                column.startswith("renewable_") and
                                item.type == "renewable_time"), None)
                weight = builder.weight if builder and builder.weight is not None else 1.0
                feature[column] = normalized * weight
            return busmap_by_hac(
                working, n_clusters, buses_i=bus_index,
                branch_components=["Line"],
                feature=feature, affinity=options.affinity, linkage=options.linkage,
            )
        if planner == "pypsa_modularity":
            return busmap_by_greedy_modularity(
                working, n_clusters, buses_i=bus_index,
            )
        raise PartitionError(f"Unknown energy planner: {planner}")
