"""Reassemble detached voltage layers and reconnect supported interfaces."""

from dataclasses import dataclass
from typing import Protocol

import pypsa

from psdcalc.reduction.core.ids import IdentityBinding, IdentitySidecar, StableId
from psdcalc.reduction.exceptions import BackendCapabilityError, MaterializationError
from psdcalc.reduction.materializers.layer_quotient import LayerQuotient
from psdcalc.reduction.planners.multi_voltage_busmap import ValidatedLayerBusmaps


@dataclass(frozen=True)
class ReconnectedNetwork:
    """Detached assembled network and authoritative target identity sidecar."""

    network: pypsa.Network
    target_sidecar: IdentitySidecar


class CrossLayerHandler(Protocol):
    """Interface for a future cross-layer component reconnection rule."""

    def reconnect(self, source: pypsa.Network, target: pypsa.Network,
                  parent_sidecar: IdentitySidecar,
                  busmaps: ValidatedLayerBusmaps) -> None:
        """Copy one supported component type with mapped terminals."""
        ...


class TransformerHandler:
    """Copy every Transformer and translate its ordered identity anchors."""

    def reconnect(self, source: pypsa.Network, target: pypsa.Network,
                  parent_sidecar: IdentitySidecar,
                  busmaps: ValidatedLayerBusmaps) -> None:
        """Add each source Transformer once with all static and dynamic data."""
        for name, row in source.transformers.sort_index().iterrows():
            attrs = row.to_dict()
            for terminal in ("bus0", "bus1"):
                bus_name = str(attrs[terminal])
                source_id = parent_sidecar.source_id("Bus", bus_name)
                if source_id not in busmaps.transformer_terminals:
                    raise MaterializationError(
                        f"Transformer terminal was not marked an identity anchor: {name}/{terminal}"
                    )
                projected = [backend[source_id_name]
                             for backend in busmaps.backend_by_layer.values()
                             for source_id_name in (bus_name,)
                             if source_id_name in backend]
                if len(projected) != 1:
                    raise MaterializationError(
                        f"Transformer terminal has no unique target: {name}/{terminal}"
                    )
                attrs[terminal] = projected[0]
                if attrs[terminal] not in target.buses.index:
                    raise MaterializationError("Mapped Transformer terminal is missing")
            if attrs["bus0"] == attrs["bus1"]:
                raise MaterializationError(f"Transformer self-loop is forbidden: {name}")
            if str(name) in target.transformers.index:
                raise MaterializationError(f"Transformer name collision: {name}")
            target.add("Transformer", str(name), **attrs)
            source_dynamic = source.components["Transformer"].dynamic
            target_dynamic = target.components["Transformer"].dynamic
            for attribute, frame in source_dynamic.items():
                if not frame.empty and str(name) in frame.columns:
                    if attribute not in target_dynamic:
                        raise BackendCapabilityError(
                            f"Unsupported Transformer dynamic attribute: {attribute}"
                        )
                    target_dynamic[attribute][str(name)] = frame[str(name)].reindex(
                        target.snapshots
                    )


class CrossLayerComponentReconnector:
    """Merge layer quotients and apply registered cross-layer handlers."""

    def __init__(self) -> None:
        """Register only the Transformer handler for this phase."""
        self._handlers: dict[str, CrossLayerHandler] = {}
        self.register("Transformer", TransformerHandler())

    def register(self, component_type: str, handler: CrossLayerHandler) -> None:
        """Register the supported Transformer handler extension point."""
        if component_type != "Transformer":
            raise BackendCapabilityError(
                f"Cross-layer {component_type} handler is not supported in PR-015"
            )
        self._handlers[component_type] = handler

    def reconnect(self, simplified_network: pypsa.Network,
                  parent_sidecar: IdentitySidecar,
                  layers: tuple[LayerQuotient, ...],
                  busmaps: ValidatedLayerBusmaps) -> ReconnectedNetwork:
        """Build a detached whole-network quotient and target sidecar."""
        if not layers:
            raise MaterializationError("No voltage layer quotient was supplied")
        result = layers[0].network.copy()
        metadata = ("Carrier", "LineType", "TransformerType", "SubNetwork", "Shape")
        for layer in layers[1:]:
            try:
                result.merge(layer.network, components_to_skip=metadata,
                             inplace=True, with_time=True)
            except Exception as exc:
                raise MaterializationError(
                    f"Cannot merge voltage layer {layer.layer.key}: {exc}"
                ) from exc
        for component_name in ("Carrier", "LineType", "TransformerType"):
            result.components[component_name].static = (
                simplified_network.components[component_name].static.copy(deep=True)
            )
        for name, handler in self._handlers.items():
            if name == "Transformer":
                handler.reconnect(simplified_network, result, parent_sidecar, busmaps)
        result.determine_network_topology()
        bindings: list[IdentityBinding] = []
        resolved: dict[StableId, tuple[str, str]] = {}
        for layer in layers:
            for source_id, target in layer.target_names.items():
                if source_id in resolved and resolved[source_id] != target:
                    raise MaterializationError("Target StableId has conflicting bindings")
                resolved[source_id] = target
        for name in simplified_network.transformers.index:
            source_id = parent_sidecar.source_id("Transformer", str(name))
            resolved[source_id] = ("Transformer", str(name))
        for source_id, (component, backend) in sorted(resolved.items()):
            if backend not in result.components[component].static.index:
                raise MaterializationError(
                    f"Mapped target is absent after reassembly: {source_id.encode()}"
                )
            bindings.append(IdentityBinding(component, backend, source_id))
        return ReconnectedNetwork(result, IdentitySidecar(bindings))
