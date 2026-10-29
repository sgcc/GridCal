"""Exact nominal-voltage classification for in-memory multi-voltage quotients."""

from collections.abc import Mapping
from dataclasses import dataclass
import math

import pypsa

from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import BackendCapabilityError


@dataclass(frozen=True)
class VoltageLayer:
    """One canonical nominal-voltage layer and its stable source Bus IDs."""

    key: str
    v_nom_kv: float
    bus_ids: tuple[StableId, ...]


class VoltageLayerClassifier:
    """Classify buses without implicit voltage tolerance."""

    def classify(self, network: pypsa.Network,
                 sidecar: IdentitySidecar,
                 aliases: Mapping[float, float] | None = None
                 ) -> tuple[VoltageLayer, ...]:
        """Return exact voltage layers or raise a capability error."""
        normalized_aliases: dict[float, float] = {}
        for source, target in (aliases or {}).items():
            try:
                source_value = float(source)
                target_value = float(target)
            except (TypeError, ValueError) as exc:
                raise BackendCapabilityError(
                    "Voltage aliases must contain numeric kV values"
                ) from exc
            if not (math.isfinite(source_value) and source_value > 0 and
                    math.isfinite(target_value) and target_value > 0):
                raise BackendCapabilityError("Voltage aliases must be finite positive kV")
            normalized_aliases[source_value] = target_value
        if network.buses.empty:
            raise BackendCapabilityError("Cannot classify a network without buses")
        raw: dict[str, float] = {}
        canonical: dict[str, float] = {}
        grouped: dict[float, list[StableId]] = {}
        for name, row in network.buses.sort_index().iterrows():
            try:
                voltage = float(row.v_nom)
            except (TypeError, ValueError) as exc:
                raise BackendCapabilityError(f"Invalid Bus v_nom: {name}") from exc
            if not math.isfinite(voltage) or voltage <= 0:
                raise BackendCapabilityError(f"Invalid Bus v_nom: {name}")
            bus_name = str(name)
            raw[bus_name] = voltage
            canonical[bus_name] = normalized_aliases.get(voltage, voltage)
            grouped.setdefault(canonical[bus_name], []).append(
                sidecar.source_id("Bus", bus_name)
            )
        for name, row in network.lines.iterrows():
            bus0, bus1 = str(row.bus0), str(row.bus1)
            if bus0 not in raw or bus1 not in raw:
                raise BackendCapabilityError(f"Dangling Line terminal: {name}")
            if raw[bus0] != raw[bus1]:
                raise BackendCapabilityError(f"cross-voltage Line is unsupported: {name}")
        for name, row in network.transformers.iterrows():
            bus0, bus1 = str(row.bus0), str(row.bus1)
            if bus0 not in canonical or bus1 not in canonical:
                raise BackendCapabilityError(f"Dangling Transformer terminal: {name}")
            if canonical[bus0] == canonical[bus1]:
                raise BackendCapabilityError(
                    f"Transformer terminals must remain in distinct voltage layers: {name}"
                )
        return tuple(VoltageLayer(format(voltage, ".15g"), voltage,
                                  tuple(sorted(buses)))
                     for voltage, buses in sorted(grouped.items()))
