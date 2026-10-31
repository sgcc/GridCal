"""Resolve voltage cutoffs into stable source-identity protection constraints."""

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Literal, TYPE_CHECKING

from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import ConfigSemanticError
from psdcalc.reduction.protection.engine import ProtectionSet

if TYPE_CHECKING:
    import pypsa


@dataclass(frozen=True)
class ResolvedVoltagePolicy:
    """Canonical voltage-layer and source-ID resolution for one network."""

    policy_id: str
    requested_cutoff_kv: float | None
    source_voltage_layers: tuple[float, ...]
    retained_voltage_layers: tuple[float, ...]
    compressible_voltage_layers: tuple[float, ...]
    retained_bus_ids: frozenset[StableId]
    retained_line_ids: frozenset[StableId]
    retained_bus_count: int
    retained_line_count: int
    resolved_policy_fingerprint: str


def voltage_policy_evidence(
    policy: "VoltageCutoffPolicy", resolved: ResolvedVoltagePolicy,
) -> dict[str, object]:
    """Serialize policy request and resolved StableIds for internal Gate evidence."""
    return {
        "request": {
            "enabled": policy.enabled,
            "mode": policy.mode,
            "cutoff_kv": policy.cutoff_kv,
        },
        "resolved": {
            "policy_id": resolved.policy_id,
            "requested_cutoff_kv": resolved.requested_cutoff_kv,
            "source_voltage_layers": list(resolved.source_voltage_layers),
            "retained_voltage_layers": list(resolved.retained_voltage_layers),
            "compressible_voltage_layers": list(resolved.compressible_voltage_layers),
            "retained_bus_ids": sorted(item.encode() for item in resolved.retained_bus_ids),
            "retained_line_ids": sorted(item.encode() for item in resolved.retained_line_ids),
            "retained_bus_count": resolved.retained_bus_count,
            "retained_line_count": resolved.retained_line_count,
            "resolved_policy_fingerprint": resolved.resolved_policy_fingerprint,
        },
    }


@dataclass(frozen=True)
class VoltageCutoffPolicy:
    """Freeze buses and same-voltage lines at or above an actual kV cutoff."""

    enabled: bool
    mode: Literal["freeze_at_or_above"] = "freeze_at_or_above"
    cutoff_kv: float | None = None

    def __post_init__(self) -> None:
        if self.mode != "freeze_at_or_above":
            raise ConfigSemanticError(f"Unsupported voltage cutoff mode: {self.mode}")
        if self.enabled:
            if isinstance(self.cutoff_kv, bool) or self.cutoff_kv is None:
                raise ConfigSemanticError("Enabled voltage policy requires a positive finite cutoff_kv")
            if not math.isfinite(self.cutoff_kv) or self.cutoff_kv <= 0:
                raise ConfigSemanticError("cutoff_kv must be positive and finite")
        elif self.cutoff_kv is not None:
            raise ConfigSemanticError("Disabled voltage policy must not specify cutoff_kv")

    def resolve(self, network: "pypsa.Network", sidecar: IdentitySidecar) -> ResolvedVoltagePolicy:
        """Resolve actual Bus.v_nom values and source IDs without voltage substitution."""
        layers = tuple(sorted({float(value) for value in network.buses.v_nom}))
        retained_names = {
            str(name) for name, row in network.buses.iterrows()
            if self.enabled and float(row.v_nom) >= float(self.cutoff_kv)
        }
        retained_layers = tuple(v for v in layers if self.enabled and v >= float(self.cutoff_kv))
        compressible_layers = tuple(v for v in layers if v not in retained_layers)
        retained_buses = frozenset(sidecar.source_id("Bus", name) for name in retained_names)
        retained_lines: set[StableId] = set()
        if self.enabled:
            for name, row in network.lines.iterrows():
                if str(row.bus0) in retained_names and str(row.bus1) in retained_names:
                    v0 = float(network.buses.at[row.bus0, "v_nom"])
                    v1 = float(network.buses.at[row.bus1, "v_nom"])
                    if v0 == v1:
                        retained_lines.add(sidecar.source_id("Line", str(name)))
        line_ids = frozenset(retained_lines)
        payload = {
            "mode": self.mode,
            "retained_voltage_layers": retained_layers,
            "compressible_voltage_layers": compressible_layers,
            "retained_bus_ids": sorted(item.encode() for item in retained_buses),
            "retained_line_ids": sorted(item.encode() for item in line_ids),
        }
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return ResolvedVoltagePolicy(
            policy_id="BASE" if not self.enabled else f"CUT_{self.cutoff_kv:g}",
            requested_cutoff_kv=self.cutoff_kv if self.enabled else None,
            source_voltage_layers=layers,
            retained_voltage_layers=retained_layers,
            compressible_voltage_layers=compressible_layers,
            retained_bus_ids=retained_buses,
            retained_line_ids=line_ids,
            retained_bus_count=len(retained_buses),
            retained_line_count=len(line_ids),
            resolved_policy_fingerprint=fingerprint,
        )


def voltage_policy_protection(resolved: ResolvedVoltagePolicy) -> ProtectionSet:
    """Return singleton Bus and identity-preserved Line constraints for a policy."""
    return ProtectionSet(
        traced=frozenset(), preserved_components=resolved.retained_line_ids,
        terminal_buses=frozenset(), singleton_buses=resolved.retained_bus_ids,
        cannot_link=frozenset(),
    )
