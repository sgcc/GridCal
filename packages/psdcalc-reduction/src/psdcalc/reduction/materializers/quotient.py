"""PyPSA quotient construction with preflight capability and identity gates."""

from dataclasses import dataclass
from typing import Mapping

import pypsa

from psdcalc.reduction.backends.base import require_capability
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.backends.pypsa.compat import get_clustering_from_busmap
from psdcalc.reduction.config.models import AggregationConfig
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import (
    BackendCapabilityError, MaterializationError,
)
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.protection.engine import ProtectionSet


@dataclass(frozen=True)
class QuotientResult:
    """Constructed quotient and PyPSA's surviving-line mapping."""

    network: pypsa.Network
    linemap: Mapping[str, str]


class QuotientMaterializer:
    """Construct a PyPSA quotient only where the backend can preserve contracts."""

    def __init__(self) -> None:
        self.adapter = PypsaAdapter()

    def materialize(
        self,
        original: pypsa.Network,
        sidecar: IdentitySidecar,
        partition: PartitionPlan,
        config: AggregationConfig,
        protection: ProtectionSet,
    ) -> QuotientResult:
        """Build a detached quotient or raise a capability/materialization error."""
        self.adapter.validate_model(original, sidecar)
        require_capability(self.adapter.capabilities, "public_spatial_clustering")
        if not original.transformers.empty:
            raise BackendCapabilityError("transformer quotient is not supported in the first batch")
        if original.buses.v_nom.nunique(dropna=False) > 1:
            raise BackendCapabilityError("multi-voltage quotient is not supported in the first batch")
        for column in original.links.columns:
            if str(column).startswith("bus") and str(column) not in {"bus0", "bus1"}:
                values = original.links[column]
                if (values.notna() & values.astype(str).ne("")).any():
                    raise BackendCapabilityError("multi-terminal Link quotient is not supported")
        if set(partition.backend_busmap) != set(original.buses.index):
            raise MaterializationError("Partition does not cover every source bus")
        self._check_protection(partition, protection)
        working = self.adapter.clone(original)
        options = {
            "line_length_factor": config.line_length_factor,
            "aggregate_one_ports": {name: {} for name in config.aggregate_one_ports},
            "generator_strategies": config.strategies.generators.model_dump(exclude_none=True),
            "one_port_strategies": config.strategies.one_ports.model_dump(exclude_none=True),
            # Parallel source Lines can differ in active state. The target
            # branch is active when any source member is active; electrical
            # equivalencing then includes active members only.
            "line_strategies": {"active": "any"},
        }
        try:
            cluster = get_clustering_from_busmap(
                working, dict(partition.backend_busmap), options=options
            )
        except Exception as exc:
            raise MaterializationError(f"PyPSA quotient construction failed: {exc}") from exc
        quotient = cluster.n
        self._check_preserved_devices(original, quotient, sidecar,
                                      config.preserve_components, protection)
        return QuotientResult(quotient, cluster.linemap.to_dict())

    @staticmethod
    def _check_protection(partition: PartitionPlan,
                          protection: ProtectionSet) -> None:
        """Reject a partition that violates hard bus constraints."""
        mapping = partition.stable_busmap
        counts: dict[StableId, int] = {}
        for target in mapping.values():
            counts[target] = counts.get(target, 0) + 1
        for source in protection.singleton_buses:
            if mapping.get(source) != source or counts[source] != 1:
                raise MaterializationError(f"Protected singleton bus was merged: {source.encode()}")
        for source in protection.terminal_buses:
            if mapping.get(source) != source:
                raise MaterializationError(f"Protected terminal bus lost identity: {source.encode()}")
        for pair in protection.cannot_link:
            left, right = tuple(pair)
            if mapping.get(left) == mapping.get(right):
                raise MaterializationError("cannot_link endpoints were merged")

    @staticmethod
    def _check_preserved_devices(
        original: pypsa.Network,
        quotient: pypsa.Network,
        sidecar: IdentitySidecar,
        preserve_components: list[str],
        protection: ProtectionSet,
    ) -> None:
        """Verify protected device names survive the quotient unchanged."""
        by_component: dict[str, set[str]] = {}
        for component in quotient.components:
            by_component[component.name] = set(component.static.index)
        source_components = {component.name: set(component.static.index)
                             for component in original.components}
        for source_id in protection.preserved_components:
            if source_id.component == "Bus":
                continue
            backend_name = sidecar.backend_name(source_id)
            if backend_name not in by_component.get(source_id.component, set()):
                raise MaterializationError(f"Protected device missing: {source_id.encode()}")
        for component in preserve_components:
            if component not in source_components:
                raise MaterializationError(f"Unknown preserve component: {component}")
            source_names = source_components[component]
            if not source_names.issubset(by_component.get(component, set())):
                raise MaterializationError(f"Preserved {component} identity missing after quotient")
