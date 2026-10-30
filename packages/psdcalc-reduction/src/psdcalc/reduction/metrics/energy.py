"""Grouped aggregation/inventory statistics; no power-flow interpretation."""

from collections import defaultdict
from hashlib import sha256
from math import isfinite

import networkx as nx
import pandas as pd
import pypsa

from psdcalc.reduction.exceptions import BackendCapabilityError
from psdcalc.reduction.reporting.comparison import EnergyAggregateSummary
from psdcalc.reduction.metrics.stage_inventory import CompressionStage
from psdcalc.reduction.topology.active import branch_is_active


def _number(value: object, context: str) -> float | None:
    """Return None for an unspecified optional scalar; reject invalid values."""
    if pd.isna(value):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BackendCapabilityError(f"Non-numeric energy inventory: {context}")
    result = float(value)
    if not isfinite(result):
        raise BackendCapabilityError(f"Non-finite energy inventory: {context}")
    return result


def collect_energy_aggregates(
    network: pypsa.Network, stage: CompressionStage,
) -> tuple[EnergyAggregateSummary, ...]:
    """Collect global, canonical layer, active-component and anonymous-region sums."""
    if stage == CompressionStage.PSD_BPA_SOURCE:
        raise BackendCapabilityError("S0 energy requires source-model semantics")
    graph = nx.Graph()
    graph.add_nodes_from(str(bus) for bus in network.buses.index)
    for _, line in network.lines.iterrows():
        if branch_is_active(line):
            graph.add_edge(str(line.bus0), str(line.bus1))
    components = {bus: index for index, buses in enumerate(
        sorted(nx.connected_components(graph), key=lambda group: sorted(group)[0]))
        for bus in buses}
    view = network.copy()
    view.set_snapshots(network.snapshots.copy(deep=True))
    view.snapshot_weightings = network.snapshot_weightings.copy(deep=True)
    weights = network.snapshot_weightings["objective"].reindex(network.snapshots)
    if weights.isna().any():
        raise BackendCapabilityError("Missing snapshot objective weight")
    fields = (
        "load_count", "load_p_set", "load_q_set", "generator_count",
        "generator_p_nom", "generator_p_set", "shunt_count", "shunt_g",
        "shunt_b", "weighted_load_p", "weighted_load_q",
        "weighted_generation_p",
    )
    groups: dict[tuple[str, float | None, int | None, str | None],
                 dict[str, float]] = defaultdict(lambda: {name: 0.0 for name in fields})
    missing: dict[tuple[str, float | None, int | None, str | None], set[str]] = defaultdict(set)
    has_weighted: dict[str, bool] = {name: False for name in fields if name.startswith("weighted_")}

    def weighted(component: str, attribute: str, name: str) -> float | None:
        frame = network.components[component].dynamic.get(attribute)
        if frame is None or frame.empty:
            return None
        if name not in frame.columns:
            static = _number(network.components[component].static.at[name, attribute],
                             f"{component}.{attribute}")
            if static is None:
                raise BackendCapabilityError("Unknown static energy fallback")
            return float(static * weights.astype(float).sum())
        try:
            dense = view.get_switchable_as_dense(
                component, attribute, snapshots=view.snapshots,
                inds=pd.Index([name]))[name]
        except (KeyError, ValueError, AttributeError) as exc:
            raise BackendCapabilityError(
                f"Cannot read {component}.{attribute} energy input") from exc
        effective = dense.reindex(network.snapshots)
        fallback = network.components[component].static.at[name, attribute]
        effective = effective.fillna(fallback)
        if effective.isna().any():
            raise BackendCapabilityError("Unknown dynamic energy input")
        return float((effective.astype(float) * weights.astype(float)).sum())

    for component, frame, count_field, attributes in (
        ("Load", network.loads, "load_count",
         {"p_set": "load_p_set", "q_set": "load_q_set"}),
        ("Generator", network.generators, "generator_count",
         {"p_nom": "generator_p_nom", "p_set": "generator_p_set"}),
        ("ShuntImpedance", network.shunt_impedances, "shunt_count",
         {"g": "shunt_g", "b": "shunt_b"}),
    ):
        for backend_name, row in frame.iterrows():
            name = str(backend_name)
            bus = str(row.bus)
            if bus not in components:
                raise BackendCapabilityError("Energy device refers to absent Bus")
            voltage = _number(network.buses.at[bus, "v_nom"], "Bus.v_nom")
            if voltage is None or voltage <= 0:
                raise BackendCapabilityError("Bus.v_nom is absent or nonpositive")
            raw_region = (str(network.buses.at[bus, "country"])
                          if "country" in network.buses.columns else "")
            region = "region_" + sha256(raw_region.encode()).hexdigest()[:12]
            keys = (("global", None, None, None),
                    ("voltage_layer", voltage, None, None),
                    ("source_component", None, components[bus], None),
                    ("region", None, None, region))
            numeric = {field: _number(row[attribute],
                                      f"{component}.{attribute}")
                       for attribute, field in attributes.items()}
            dynamic = {}
            for attribute, field in (("p_set", "weighted_load_p"),
                                     ("q_set", "weighted_load_q")) if component == "Load" else (
                                     (("p_set", "weighted_generation_p"),)
                                     if component == "Generator" else ()):
                value = weighted(component, attribute, name)
                if value is not None:
                    dynamic[field] = value
                    has_weighted[field] = True
            for key in keys:
                group = groups[key]
                group[count_field] += 1
                for field, value in numeric.items():
                    if value is None:
                        missing[key].add(field)
                    else:
                        group[field] += value
                for field, value in dynamic.items():
                    group[field] += value
    if not groups:
        groups[("global", None, None, None)]
    return tuple(EnergyAggregateSummary(
        stage=stage, scope=scope, voltage_kv=voltage,
        source_component=component, region=region,
        load_count=int(values["load_count"]),
        load_p_set=(None if "load_p_set" in missing[(scope, voltage, component, region)]
                    else values["load_p_set"]),
        load_q_set=(None if "load_q_set" in missing[(scope, voltage, component, region)]
                    else values["load_q_set"]),
        generator_count=int(values["generator_count"]),
        generator_p_nom=(None if "generator_p_nom" in missing[(scope, voltage, component, region)]
                         else values["generator_p_nom"]),
        generator_p_set=(None if "generator_p_set" in missing[(scope, voltage, component, region)]
                         else values["generator_p_set"]),
        shunt_count=int(values["shunt_count"]),
        shunt_g=(None if "shunt_g" in missing[(scope, voltage, component, region)]
                 else values["shunt_g"]),
        shunt_b=(None if "shunt_b" in missing[(scope, voltage, component, region)]
                 else values["shunt_b"]),
        weighted_load_p=(values["weighted_load_p"] if has_weighted["weighted_load_p"]
                         else None),
        weighted_load_q=(values["weighted_load_q"] if has_weighted["weighted_load_q"]
                         else None),
        weighted_generation_p=(values["weighted_generation_p"]
                               if has_weighted["weighted_generation_p"] else None),
    ) for (scope, voltage, component, region), values in sorted(
        groups.items(), key=lambda item: str(item[0])))
