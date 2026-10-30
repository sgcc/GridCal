"""Detach and reconnect identity-preserved Line members in one layer."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from typing import Any

import pandas as pd
import pypsa

from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import BackendCapabilityError, MaterializationError
from psdcalc.reduction.topology.parallel_corridors import ProtectedParallelPlan
from psdcalc.reduction.topology.parallel_provenance import ParallelSourceLedger


@dataclass(frozen=True)
class DetachedLine:
    """Complete in-memory Line row and supported dynamic columns."""

    source_id: StableId
    backend_name: str
    attributes: Mapping[str, Any]
    dynamic: Mapping[str, pd.Series]


@dataclass(frozen=True)
class DetachedShunt:
    """Full in-memory L+ derived Shunt row and dynamic input columns."""

    source_id: StableId
    backend_name: str
    attributes: Mapping[str, Any]
    dynamic: Mapping[str, pd.Series]


class ProtectedParallelLineHandler:
    """Keep protected Line identities outside the ordinary PyPSA quotient."""

    def detach(
        self, child: pypsa.Network, sidecar: IdentitySidecar,
        plan: ProtectedParallelPlan,
    ) -> tuple[DetachedLine, ...]:
        """Remove every protected member from a detached layer clone."""
        rows: list[DetachedLine] = []
        for name in sorted(child.lines.index):
            sid = sidecar.source_id("Line", str(name))
            if sid not in plan.protected_lines:
                continue
            dynamic = {
                attribute: frame[str(name)].copy(deep=True)
                for attribute, frame in child.components["Line"].dynamic.items()
                if str(name) in frame.columns
            }
            rows.append(DetachedLine(
                sid, str(name), child.lines.loc[name].to_dict(), dynamic))
        if rows:
            child.remove("Line", [row.backend_name for row in rows])
        if any(sidecar.source_id("Line", str(name)) in plan.protected_lines
               for name in child.lines.index):
            raise MaterializationError("Protected Line remained in quotient input")
        return tuple(rows)

    def detach_l_plus_shunts(
        self, child: pypsa.Network, sidecar: IdentitySidecar,
        ledger: ParallelSourceLedger, detached_lines: tuple[DetachedLine, ...],
    ) -> tuple[DetachedShunt, ...]:
        """Detach every L+ Shunt whose base Line was detached in this layer."""
        line_ids = {item.source_id for item in detached_lines}
        shunt_ids = {item.shunt_id for item in ledger.l_plus
                     if item.base_line_id in line_ids}
        rows: list[DetachedShunt] = []
        for source_id in sorted(shunt_ids):
            name = sidecar.backend_name(source_id)
            if name not in child.shunt_impedances.index:
                raise MaterializationError("L+ Shunt is absent from extracted layer")
            dynamic = {
                attribute: frame[name].copy(deep=True)
                for attribute, frame in child.components["ShuntImpedance"].dynamic.items()
                if name in frame.columns
            }
            rows.append(DetachedShunt(
                source_id, name, child.shunt_impedances.loc[name].to_dict(),
                dynamic))
        if rows:
            child.remove("ShuntImpedance", [item.backend_name for item in rows])
        return tuple(rows)

    @staticmethod
    def _name(layer_key: str, source_id: StableId) -> str:
        """Derive a stable backend name from the authoritative StableId."""
        suffix = hashlib.sha256(source_id.encode().encode()).hexdigest()[:24]
        return f"{layer_key}:protected:{suffix}"

    def reconnect(
        self, target: pypsa.Network, detached: tuple[DetachedLine, ...],
        backend_busmap: Mapping[str, str], layer_key: str,
    ) -> dict[StableId, tuple[str, str]]:
        """Restore complete Line rows with ordered translated terminals."""
        names: dict[StableId, tuple[str, str]] = {}
        for item in detached:
            name = self._name(layer_key, item.source_id)
            if name in target.lines.index:
                raise MaterializationError(f"Protected Line name collision: {name}")
            attributes = dict(item.attributes)
            for terminal in ("bus0", "bus1"):
                source_bus = str(attributes[terminal])
                if source_bus not in backend_busmap:
                    raise MaterializationError(
                        f"Protected Line terminal has no busmap: {item.source_id.encode()}")
                attributes[terminal] = backend_busmap[source_bus]
                if attributes[terminal] not in target.buses.index:
                    raise MaterializationError("Protected Line target Bus is missing")
            if attributes["bus0"] == attributes["bus1"]:
                raise MaterializationError("Protected Line self-loop is forbidden")
            target.add("Line", name, **attributes)
            for attribute, series in item.dynamic.items():
                frame = target.components["Line"].dynamic.get(attribute)
                if frame is None:
                    raise BackendCapabilityError(
                        f"Unsupported protected Line dynamic attribute: {attribute}")
                frame[name] = series.reindex(target.snapshots)
            names[item.source_id] = ("Line", name)
        return names

    def reconnect_l_plus_shunts(
        self, target: pypsa.Network, detached: tuple[DetachedShunt, ...],
        backend_busmap: Mapping[str, str], layer_key: str,
    ) -> dict[StableId, tuple[str, str]]:
        """Reconnect identity-preserved terminal Shunts after quotient."""
        names: dict[StableId, tuple[str, str]] = {}
        for item in detached:
            name = self._name(layer_key, item.source_id)
            if name in target.shunt_impedances.index:
                raise MaterializationError(f"Protected Shunt name collision: {name}")
            attributes = dict(item.attributes)
            source_bus = str(attributes["bus"])
            if source_bus not in backend_busmap:
                raise MaterializationError("L+ Shunt terminal has no busmap")
            attributes["bus"] = backend_busmap[source_bus]
            if attributes["bus"] not in target.buses.index:
                raise MaterializationError("L+ Shunt target Bus is missing")
            target.add("ShuntImpedance", name, **attributes)
            for attribute, series in item.dynamic.items():
                frame = target.components["ShuntImpedance"].dynamic.get(attribute)
                if frame is None:
                    raise BackendCapabilityError(
                        f"Unsupported protected Shunt dynamic attribute: {attribute}")
                frame[name] = series.reindex(target.snapshots)
            names[item.source_id] = ("ShuntImpedance", name)
        return names
