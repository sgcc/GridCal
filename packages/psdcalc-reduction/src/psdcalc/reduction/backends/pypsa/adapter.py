"""Read-only PyPSA source adapter with authoritative identity sidecars."""

from pathlib import Path
import re
from typing import Literal

import pandas as pd
import pypsa

from psdcalc.reduction.backends.base import BackendCapabilities
from psdcalc.reduction.core.ids import IdentityBinding, IdentitySidecar, StableId
from psdcalc.reduction.core.inventory import GridInventory, InventoryItem
from psdcalc.reduction.core.signatures import (
    TopologyBranch, TopologyBus, topology_signature as canonical_topology_signature,
)
from psdcalc.reduction.exceptions import BackendIOError, IdentityError, RoundTripError


_SOURCE_COMPONENTS = frozenset({
    "Bus", "Line", "Transformer", "Link", "Generator", "Load", "StorageUnit",
    "Store", "ShuntImpedance",
})
_BRANCH_COMPONENTS = frozenset({"Line", "Transformer", "Link"})
_TERMINAL_COLUMN = re.compile(r"^bus(?:\d+)?$")


class PypsaAdapter:
    """Implement the structural backend protocol using public PyPSA APIs."""

    name = "pypsa"
    capabilities = BackendCapabilities(
        public_spatial_clustering=True, netcdf_roundtrip=True,
        csv_roundtrip=True, multi_voltage_quotient=False,
        transformer_quotient=False,
    )

    def load(self, path: Path, format: Literal["netcdf", "csv"]) -> pypsa.Network:
        """Load a NetCDF file or CSV folder into a new network."""
        if not path.exists():
            raise BackendIOError(f"PyPSA input does not exist: {path}")
        network = pypsa.Network()
        try:
            if format == "netcdf":
                network.import_from_netcdf(path)
            elif format == "csv":
                network.import_from_csv_folder(path)
            else:
                raise BackendIOError(f"Unsupported PyPSA format: {format}")
        except BackendIOError:
            raise
        except Exception as exc:
            raise BackendIOError(f"Cannot import PyPSA {format} from {path}: {exc}") from exc
        return network

    def clone(self, network: pypsa.Network) -> pypsa.Network:
        """Deep-copy a PyPSA network through its public copy method."""
        return network.copy()

    def export(self, network: pypsa.Network, path: Path,
               format: Literal["netcdf", "csv"]) -> None:
        """Export a working network without altering its source object."""
        try:
            if format == "netcdf":
                network.export_to_netcdf(path)
            elif format == "csv":
                network.export_to_csv_folder(path)
            else:
                raise BackendIOError(f"Unsupported PyPSA format: {format}")
        except BackendIOError:
            raise
        except Exception as exc:
            raise BackendIOError(f"Cannot export PyPSA {format} to {path}: {exc}") from exc

    def identity_sidecar(self, network: pypsa.Network,
                         namespace: str) -> IdentitySidecar:
        """Build a source identity table independent of custom network columns."""
        bindings: list[IdentityBinding] = []
        for component in network.components:
            if component.name not in _SOURCE_COMPONENTS:
                continue
            for backend_name in component.static.index:
                name = str(backend_name)
                bindings.append(IdentityBinding(
                    component.name, name, StableId(namespace, component.name, name)
                ))
        return IdentitySidecar(bindings)

    def source_id(self, sidecar: IdentitySidecar, component: str,
                  backend_name: str) -> StableId:
        """Read stable identity solely from the external sidecar."""
        return sidecar.source_id(component, backend_name)

    def inventory(self, network: pypsa.Network,
                  sidecar: IdentitySidecar) -> GridInventory:
        """Inventory devices and terminals by stable source identities."""
        items: list[InventoryItem] = []
        for component in network.components:
            if component.name not in _SOURCE_COMPONENTS:
                continue
            terminal_cols = [column for column in component.static.columns
                             if _TERMINAL_COLUMN.fullmatch(str(column))]
            for backend_name, row in component.static.iterrows():
                source_id = sidecar.source_id(component.name, str(backend_name))
                terminals: list[StableId] = []
                for column in terminal_cols:
                    bus_name = row[column]
                    if pd.isna(bus_name) or bus_name == "":
                        continue
                    bus_name = str(bus_name)
                    if bus_name not in network.buses.index:
                        raise IdentityError(f"Dangling {component.name}/{backend_name} terminal: {bus_name}")
                    terminals.append(sidecar.source_id("Bus", bus_name))
                control = str(row.get("control", ""))
                controllable = (control in {"Slack", "PV"} or
                                bool(row.get("p_nom_extendable", False)) or
                                bool(row.get("committable", False)))
                items.append(InventoryItem(source_id, component.name,
                                           tuple(terminals), controllable))
        return GridInventory(items)

    def topology_signature(self, network: pypsa.Network,
                           sidecar: IdentitySidecar) -> str:
        """Hash buses and branch endpoints with source IDs and voltage levels."""
        buses = [TopologyBus(sidecar.source_id("Bus", str(name)), float(row.v_nom))
                 for name, row in network.buses.iterrows()]
        branches: list[TopologyBranch] = []
        for component in network.components:
            if component.name not in _BRANCH_COMPONENTS:
                continue
            for name, row in component.static.iterrows():
                branches.append(TopologyBranch(
                    sidecar.source_id(component.name, str(name)),
                    sidecar.source_id("Bus", str(row.bus0)),
                    sidecar.source_id("Bus", str(row.bus1)),
                    bool(row.get("active", True)),
                ))
        return canonical_topology_signature(buses, branches)

    def validate_model(self, network: pypsa.Network,
                       sidecar: IdentitySidecar) -> None:
        """Reject missing buses, sidecar bindings, or dangling terminals."""
        if network.buses.empty:
            raise IdentityError("PyPSA network has no buses")
        self.inventory(network, sidecar)

    def round_trip(self, network: pypsa.Network, sidecar: IdentitySidecar,
                   path: Path, format: Literal["netcdf", "csv"]) -> pypsa.Network:
        """Export, reload, and verify topology while retaining sidecar authority."""
        before = self.topology_signature(network, sidecar)
        self.export(network, path, format)
        restored = self.load(path, format)
        self.validate_model(restored, sidecar)
        after = self.topology_signature(restored, sidecar)
        if before != after:
            raise RoundTripError(f"PyPSA {format} round-trip changed topology signature")
        if self.topology_signature(network, sidecar) != before:
            raise RoundTripError("PyPSA export modified source network topology")
        return restored
