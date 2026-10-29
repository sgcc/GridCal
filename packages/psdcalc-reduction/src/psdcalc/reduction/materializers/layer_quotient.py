"""Public-API quotient construction within one nominal-voltage layer."""

from collections.abc import Mapping
from dataclasses import dataclass

import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import AggregationConfig
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import (
    BackendCapabilityError, MaterializationError, MappingError,
)
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.materializers.quotient import (
    QuotientMaterializer, QuotientResult,
)
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.planners.multi_voltage_busmap import ValidatedLayerBusmaps
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.voltage_layers import VoltageLayer


_SOURCES = frozenset({"Bus", "Line", "Transformer", "Link", "Generator",
                      "Load", "StorageUnit", "Store", "ShuntImpedance"})
_RENAMED = ("Line", "Link", "Generator", "Load", "StorageUnit", "Store",
            "ShuntImpedance")


@dataclass(frozen=True)
class LayerQuotient:
    """One detached layer quotient and its parent-relative mapping."""

    layer: VoltageLayer
    network: pypsa.Network
    mapping: MappingBundle
    target_names: Mapping[StableId, tuple[str, str]]


class LayerQuotientMaterializer:
    """Extract each layer and call the public PyPSA spatial quotient."""

    @staticmethod
    def _layer_copy(network: pypsa.Network, sidecar: IdentitySidecar,
                    layer: VoltageLayer) -> pypsa.Network:
        """Remove other-layer components from a detached public-API copy."""
        child = PypsaAdapter().clone(network)
        allowed = {sidecar.backend_name(source) for source in layer.bus_ids}
        for component in child.components:
            if component.name in {"Bus", "Carrier", "LineType",
                                  "TransformerType", "Shape", "SubNetwork"}:
                continue
            if component.name == "Transformer":
                remove = list(component.static.index)
            elif component.name in _SOURCES:
                terminal_columns = [column for column in component.static.columns
                                    if str(column) == "bus" or
                                    str(column).startswith("bus") and
                                    str(column)[3:].isdigit()]
                remove = [name for name, row in component.static.iterrows()
                          if any(str(row[column]) not in allowed for column in
                                 terminal_columns if str(row[column]) not in {"", "nan"})]
            else:
                continue
            if remove:
                child.remove(component.name, remove)
        other_buses = [name for name in child.buses.index if str(name) not in allowed]
        if other_buses:
            child.remove("Bus", other_buses)
        if not child.sub_networks.empty:
            child.remove("SubNetwork", list(child.sub_networks.index))
        child.buses.loc[:, "v_nom"] = layer.v_nom_kv
        return child

    @staticmethod
    def _sidecar_subset(parent: IdentitySidecar,
                        network: pypsa.Network) -> IdentitySidecar:
        """Keep the authoritative parent IDs for extracted component rows."""
        names = {component.name: {str(name) for name in component.static.index}
                 for component in network.components if component.name in _SOURCES}
        return IdentitySidecar.from_rows(
            row for row in parent.to_rows()
            if row["backend_name"] in names.get(row["component"], set())
        )

    @staticmethod
    def _protection_subset(source: ProtectionSet,
                           sidecar: IdentitySidecar) -> ProtectionSet:
        """Project source-ID protections onto one extracted layer."""
        present = {StableId.decode(row["source_id"]) for row in sidecar.to_rows()}
        return ProtectionSet(
            source.traced & present,
            source.preserved_components & present,
            source.terminal_buses & present,
            source.singleton_buses & present,
            frozenset(pair for pair in source.cannot_link if pair <= present),
        )

    @staticmethod
    def _target_names(bundle: MappingBundle, layer_network: pypsa.Network,
                      partition: PartitionPlan, operation_id: str,
                      translated: Mapping[tuple[str, str], str],
                      sidecar: IdentitySidecar) -> dict[StableId, tuple[str, str]]:
        """Resolve every mapped target ID to its renamed backend object."""
        names: dict[StableId, tuple[str, str]] = {}
        for row in (bundle.bus_map + bundle.branch_map + bundle.device_map):
            target = row.target_id
            if target is None:
                continue
            if row.source_id.component == "Bus":
                old = partition.backend_busmap[sidecar.backend_name(row.source_id)]
                component = "Bus"
            else:
                component = row.source_id.component
                old = (sidecar.backend_name(row.source_id)
                       if target == row.source_id else
                       target.source_key.removeprefix(f"{operation_id}:"))
            backend = translated.get((component, old), old)
            value = (component, backend)
            if target in names and names[target] != value:
                raise MappingError(f"Target ID has conflicting names: {target.encode()}")
            names[target] = value
        return names

    @staticmethod
    def _rename_local(network: pypsa.Network, component_name: str,
                      names: Mapping[str, str]) -> None:
        """Rename detached static/dynamic rows with public component tables.

        PyPSA 1.2.4's public rename helper assumes every component with ports
        has a column named after the renamed component. That is false for Line
        names on Load rows. Update only foreign-key columns that actually
        exist, along with the component's static and dynamic row names.
        """
        reference_columns = {component_name.lower(),
                             f"{component_name.lower()}0",
                             f"{component_name.lower()}1"}
        for component in network.components:
            if component.name == component_name or component.static.empty:
                continue
            for column in reference_columns & set(component.static.columns):
                component.static[column] = component.static[column].replace(
                    dict(names)
                )
        component = network.components[component_name]
        component.static = component.static.rename(index=dict(names))
        for attribute, frame in component.dynamic.items():
            component.dynamic[attribute] = frame.rename(columns=dict(names))

    def materialize(self, simplified_network: pypsa.Network,
                    parent_sidecar: IdentitySidecar,
                    layers: tuple[VoltageLayer, ...],
                    busmaps: ValidatedLayerBusmaps,
                    aggregation: AggregationConfig,
                    protection: ProtectionSet) -> tuple[LayerQuotient, ...]:
        """Return detached per-layer quotients with globally unique names."""
        output: list[LayerQuotient] = []
        for layer in layers:
            child = self._layer_copy(simplified_network, parent_sidecar, layer)
            child_sidecar = self._sidecar_subset(parent_sidecar, child)
            stable = {source: busmaps.global_busmap[source] for source in layer.bus_ids}
            backend = dict(busmaps.backend_by_layer[layer.key])
            planned, actual = busmaps.per_layer_counts[layer.key]
            partition = PartitionPlan(stable, backend, planned, actual, "reject")
            local_config = aggregation.model_copy(update={
                "preserve_components": [name for name in aggregation.preserve_components
                                        if name != "Transformer"]
            })
            subset = self._protection_subset(protection, child_sidecar)
            # The global validator already checked singleton cardinality. The
            # single-voltage helper additionally requires the *source* ID to
            # equal the target ID, which is not true for qualified layer names.
            local_protection = ProtectionSet(
                subset.traced, subset.preserved_components,
                frozenset(), frozenset(), subset.cannot_link,
            )
            if child.lines.empty:
                # PyPSA 1.2.4's aggregatelines divides empty Arrow string
                # arrays by a voltage float. Every Bus is already singleton
                # here, so a public Bus rename is the exact quotient.
                if len(set(backend.values())) != len(backend):
                    raise BackendCapabilityError(
                        "Line-free layer cannot aggregate disconnected buses"
                    )
                for component_name in local_config.aggregate_one_ports:
                    frame = child.components[component_name].static
                    if (not frame.empty and "bus" in frame.columns and
                            frame.groupby("bus").size().max() > 1):
                        raise BackendCapabilityError(
                            f"Line-free layer cannot aggregate co-located "
                            f"{component_name} devices"
                        )
                renamed = child.copy()
                renamed.rename_component_names("Bus", **backend)
                quotient = QuotientResult(renamed, {})
            else:
                quotient = QuotientMaterializer().materialize(
                    child, child_sidecar, partition, local_config, local_protection
                )
            operation_id = f"mvq-layer-{layer.key.replace('.', '_')}"
            bundle = MappingBundle.from_quotient(
                child, child_sidecar, partition, quotient, operation_id=operation_id
            )
            translated: dict[tuple[str, str], str] = {}
            for component_name in _RENAMED:
                component = quotient.network.components[component_name]
                names = {str(name): f"{layer.key}:{name}"
                         for name in component.static.index}
                if names:
                    self._rename_local(quotient.network, component_name, names)
                    translated.update({(component_name, old): new
                                       for old, new in names.items()})
            for source in layer.bus_ids:
                backend_name = backend[parent_sidecar.backend_name(source)]
                if backend_name not in quotient.network.buses.index:
                    raise MaterializationError("Layer quotient lost a Bus target")
                quotient.network.buses.loc[backend_name, "v_nom"] = layer.v_nom_kv
            target_names = self._target_names(
                bundle, quotient.network, partition, operation_id,
                translated, child_sidecar,
            )
            output.append(LayerQuotient(layer, quotient.network, bundle,
                                        target_names))
        return tuple(output)
