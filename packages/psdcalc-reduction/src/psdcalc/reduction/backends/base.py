"""Backend-neutral interfaces and explicit capability gates."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypeVar, runtime_checkable

from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.core.inventory import GridInventory
from psdcalc.reduction.exceptions import BackendCapabilityError


NetworkT = TypeVar("NetworkT")
CapabilityName = Literal[
    "public_spatial_clustering", "netcdf_roundtrip", "csv_roundtrip",
    "multi_voltage_quotient", "transformer_quotient", "reduced_power_flow",
]


@dataclass(frozen=True)
class BackendCapabilities:
    """Explicitly advertised, default-deny backend abilities."""

    public_spatial_clustering: bool = False
    netcdf_roundtrip: bool = False
    csv_roundtrip: bool = False
    multi_voltage_quotient: bool = False
    transformer_quotient: bool = False
    reduced_power_flow: bool = False


def require_capability(capabilities: BackendCapabilities, name: CapabilityName) -> None:
    """Reject any operation for which the backend has no declared support."""
    if not getattr(capabilities, name, False):
        raise BackendCapabilityError(f"Backend lacks required capability: {name}")


@runtime_checkable
class ReductionBackend(Protocol[NetworkT]):
    """Minimum backend contract for the first-batch structural pipeline."""

    name: str
    capabilities: BackendCapabilities

    def load(self, path: Path, format: Literal["netcdf", "csv"]) -> NetworkT:
        """Load one source artifact without changing it."""
        ...

    def clone(self, network: NetworkT) -> NetworkT:
        """Create a detached working network."""
        ...

    def export(self, network: NetworkT, path: Path,
               format: Literal["netcdf", "csv"]) -> None:
        """Persist a working network for round-trip verification."""
        ...

    def identity_sidecar(self, network: NetworkT, namespace: str) -> IdentitySidecar:
        """Build authoritative source-ID bindings outside the network."""
        ...

    def inventory(self, network: NetworkT, sidecar: IdentitySidecar) -> GridInventory:
        """List components and control terminals by StableId."""
        ...

    def source_id(self, sidecar: IdentitySidecar, component: str,
                  backend_name: str) -> StableId:
        """Read source identity from the sidecar authority."""
        ...

    def topology_signature(self, network: NetworkT,
                           sidecar: IdentitySidecar) -> str:
        """Compute a backend-independent structural signature."""
        ...

    def validate_model(self, network: NetworkT,
                       sidecar: IdentitySidecar) -> None:
        """Raise on unsupported or malformed structural input."""
        ...


@runtime_checkable
class ReducedPowerFlowBackend(Protocol[NetworkT]):
    """Optional later capability; it does not grant full-grid acceptance."""

    def solve_pf(self, network: NetworkT) -> object:
        """Run a reduced-network PF for diagnostics only."""
        ...
