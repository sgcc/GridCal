"""Isolated single-snapshot AC PF execution through PyPSA's public API."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

import networkx as nx
import numpy as np
import pandas as pd
import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.fidelity.contracts import PowerFlowOperatingPointContract


class PowerFlowExecutionStatus(StrEnum):
    """Terminal state of one isolated PyPSA AC power-flow attempt."""

    PF_CONVERGED = "PF_CONVERGED"
    PF_NOT_CONVERGED = "PF_NOT_CONVERGED"
    PF_EXECUTION_UNSUPPORTED = "PF_EXECUTION_UNSUPPORTED"
    FAILED = "FAILED"


class PowerFlowExecutionError(RuntimeError):
    """Raised when a PF result cannot be safely attributed to its input network."""


@dataclass(frozen=True)
class PowerFlowSubnetworkStatus:
    """Convergence summary for one anonymous connected subnetwork."""

    subnetwork_ordinal: int
    converged: bool
    iterations: int | None
    error: float | None

    def __post_init__(self) -> None:
        if isinstance(self.subnetwork_ordinal, bool) or self.subnetwork_ordinal < 0:
            raise PowerFlowExecutionError("subnetwork_ordinal must be nonnegative")
        if not isinstance(self.converged, bool):
            raise PowerFlowExecutionError("subnetwork converged state must be bool")
        if self.iterations is not None and (
                isinstance(self.iterations, bool) or self.iterations < 0):
            raise PowerFlowExecutionError("iterations must be nonnegative or None")
        if self.error is not None and (not math.isfinite(self.error) or self.error < 0.0):
            raise PowerFlowExecutionError("subnetwork error must be finite and nonnegative")


@dataclass(frozen=True)
class PowerFlowExecutionResult:
    """Detached solved data indexed by authoritative StableIds, not PyPSA names."""

    status: PowerFlowExecutionStatus
    converged: bool
    snapshot: object | None
    subnetwork_status: tuple[PowerFlowSubnetworkStatus, ...]
    bus_vm_pu: Mapping[StableId, float] = field(default_factory=dict)
    bus_va_deg: Mapping[StableId, float] = field(default_factory=dict)
    bus_p_mw: Mapping[StableId, float] = field(default_factory=dict)
    bus_q_mvar: Mapping[StableId, float] = field(default_factory=dict)
    branch_p0: Mapping[StableId, float] = field(default_factory=dict)
    branch_q0: Mapping[StableId, float] = field(default_factory=dict)
    branch_p1: Mapping[StableId, float] = field(default_factory=dict)
    branch_q1: Mapping[StableId, float] = field(default_factory=dict)
    solver_metadata: Mapping[str, object] = field(default_factory=dict)
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, PowerFlowExecutionStatus):
            raise PowerFlowExecutionError("status must be PowerFlowExecutionStatus")
        if self.converged != (self.status is PowerFlowExecutionStatus.PF_CONVERGED):
            raise PowerFlowExecutionError("converged flag disagrees with execution status")
        for name in ("bus_vm_pu", "bus_va_deg", "bus_p_mw", "bus_q_mvar",
                     "branch_p0", "branch_q0", "branch_p1", "branch_q1"):
            values = getattr(self, name)
            for key, value in values.items():
                if not isinstance(key, StableId):
                    raise PowerFlowExecutionError(f"{name} keys must be StableId values")
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise PowerFlowExecutionError(f"{name} must contain finite scalar values")
        if not isinstance(self.subnetwork_status, tuple):
            raise PowerFlowExecutionError("subnetwork_status must be a tuple")
        if self.status is not PowerFlowExecutionStatus.PF_CONVERGED and any((
                self.bus_vm_pu, self.bus_va_deg, self.bus_p_mw, self.bus_q_mvar,
                self.branch_p0, self.branch_q0, self.branch_p1, self.branch_q1)):
            raise PowerFlowExecutionError("nonconverged results cannot include solved quantities")
        if self.status in {PowerFlowExecutionStatus.PF_EXECUTION_UNSUPPORTED,
                           PowerFlowExecutionStatus.FAILED} and not self.failure_reason:
            raise PowerFlowExecutionError("unsupported or failed execution needs a reason")


def structural_input_signature(network: pypsa.Network) -> str:
    """Hash PyPSA input attributes and snapshot metadata while omitting outputs."""
    digest = hashlib.sha256()

    def add_frame(frame: pd.DataFrame) -> None:
        digest.update(pd.util.hash_pandas_object(frame.index, index=False).values.tobytes())
        digest.update(repr(tuple(frame.columns)).encode("utf-8"))
        digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())

    add_frame(network.snapshot_weightings)
    add_frame(network.investment_period_weightings)
    digest.update(pd.util.hash_pandas_object(network.investment_periods, index=False).values.tobytes())
    digest.update(pd.util.hash_pandas_object(network.snapshots, index=False).values.tobytes())
    for component in sorted(network.components, key=lambda item: item.name):
        defaults = component.defaults
        if not isinstance(defaults, pd.DataFrame) or "status" not in defaults.columns:
            raise PowerFlowExecutionError(
                f"PyPSA component input metadata is unavailable: {component.name}")
        input_attributes = tuple(
            str(attribute) for attribute, row in defaults.iterrows()
            if str(row["status"]).startswith("Input")
        )
        digest.update(component.name.encode("utf-8"))
        static_columns = [name for name in input_attributes if name in component.static.columns]
        add_frame(component.static.loc[:, static_columns])
        for attribute, frame in sorted(component.dynamic.items()):
            if attribute in input_attributes:
                add_frame(frame)
    return digest.hexdigest()


class PypsaAcPowerFlowExecutor:
    """Run a supported PyPSA 1.2.x AC PF on a detached working copy only."""

    def __init__(self, adapter: PypsaAdapter | None = None) -> None:
        self._adapter = adapter or PypsaAdapter()

    def solve(
        self,
        network: pypsa.Network,
        sidecar: IdentitySidecar,
        contract: PowerFlowOperatingPointContract,
    ) -> PowerFlowExecutionResult:
        """Solve one selected snapshot and return identity-indexed scalar results.

        PyPSA 1.2.x exposes no public ``max_iterations`` or solver-selection
        parameter on ``Network.pf``. Such requests therefore return
        ``PF_EXECUTION_UNSUPPORTED`` instead of silently ignoring the contract.
        """
        unsupported_reason = self._unsupported_reason(network, contract)
        snapshot = contract.snapshots[0] if len(contract.snapshots) == 1 else None
        if unsupported_reason is not None:
            return self._empty_result(
                PowerFlowExecutionStatus.PF_EXECUTION_UNSUPPORTED,
                snapshot, unsupported_reason)
        try:
            self._adapter.validate_model(network, sidecar)
        except Exception as exc:
            return self._empty_result(
                PowerFlowExecutionStatus.FAILED, snapshot,
                f"input identity or capability validation failed ({type(exc).__name__})")

        original_signature = structural_input_signature(network)
        try:
            working = self._adapter.clone(network)
            result = self._run_pf(working, snapshot, contract)
        except _NoSlackIslandError:
            self._assert_original_unchanged(network, original_signature)
            island_count = self._connected_island_count(network)
            statuses = tuple(PowerFlowSubnetworkStatus(i, False, None, None)
                             for i in range(island_count))
            return PowerFlowExecutionResult(
                PowerFlowExecutionStatus.PF_NOT_CONVERGED, False, snapshot,
                statuses, solver_metadata=self._solver_metadata(contract, island_count),
                failure_reason="one or more subnetworks have no Slack generator")
        except Exception as exc:
            self._assert_original_unchanged(network, original_signature)
            return self._empty_result(
                PowerFlowExecutionStatus.FAILED, snapshot,
                f"PyPSA AC PF execution failed ({type(exc).__name__})")
        self._assert_original_unchanged(network, original_signature)

        subnetwork_status = self._parse_subnetwork_status(result, snapshot)
        metadata = self._solver_metadata(contract, len(subnetwork_status))
        if not subnetwork_status or not all(item.converged for item in subnetwork_status):
            return PowerFlowExecutionResult(
                PowerFlowExecutionStatus.PF_NOT_CONVERGED, False, snapshot,
                subnetwork_status, solver_metadata=metadata,
                failure_reason="one or more subnetworks did not converge")
        values = self._extract_solved_values(working, sidecar, snapshot)
        return PowerFlowExecutionResult(
            PowerFlowExecutionStatus.PF_CONVERGED, True, snapshot,
            subnetwork_status, **values, solver_metadata=metadata)

    def _unsupported_reason(
        self, network: pypsa.Network, contract: PowerFlowOperatingPointContract,
    ) -> str | None:
        """Return a fail-closed reason for unsupported execution scope/settings."""
        version = str(pypsa.__version__).split(".")
        if len(version) < 2 or tuple(version[:2]) != ("1", "2"):
            return "PF_EXECUTION_UNSUPPORTED: only PyPSA 1.2.x is supported"
        if len(contract.snapshots) != 1:
            return "PF_EXECUTION_UNSUPPORTED: exactly one snapshot is required"
        if len(network.snapshots) != 1:
            return "PF_EXECUTION_UNSUPPORTED: time-series network inputs are unsupported"
        if contract.solver != "pypsa.newton_raphson":
            return "PF_EXECUTION_UNSUPPORTED: only pypsa.newton_raphson is supported"
        if contract.max_iterations is not None:
            return "PF_EXECUTION_UNSUPPORTED: Network.pf has no public max_iterations argument"
        if contract.control_policy != "preserve_source_controls":
            return "PF_EXECUTION_UNSUPPORTED: control recovery or modification is unsupported"
        if contract.tap_policy != "fixed_at_source_schedule" or contract.phase_shift_policy != "fixed_at_source_schedule":
            return "PF_EXECUTION_UNSUPPORTED: taps and phase shifts must remain at source schedule"
        snapshot = contract.snapshots[0]
        if snapshot not in network.snapshots:
            return "PF_EXECUTION_UNSUPPORTED: requested snapshot is absent from network"
        if not network.links.empty:
            return "PF_EXECUTION_UNSUPPORTED: Link reactive branch flows are unavailable"
        if any(str(value) != "AC" for value in network.buses.carrier.dropna().unique()):
            return "PF_EXECUTION_UNSUPPORTED: only AC bus carriers are supported"
        return None

    def _run_pf(
        self, network: pypsa.Network, snapshot: object,
        contract: PowerFlowOperatingPointContract,
    ) -> Mapping[str, pd.DataFrame]:
        """Call the public nonlinear ``Network.pf`` accessor once."""
        self._require_slack_per_island(network)
        weights = contract.slack_weights or "p_set"
        return network.pf(
            snapshots=[snapshot], x_tol=contract.tolerance,
            use_seed=contract.use_seed, distribute_slack=contract.distribute_slack,
            slack_weights=weights,
        )

    def _require_slack_per_island(self, network: pypsa.Network) -> None:
        """Return a solver nonconvergence result before PF if an island lacks Slack."""
        graph = self._active_ac_graph(network)
        slack_buses = set(
            network.generators.loc[network.generators.control == "Slack", "bus"].astype(str)
        ) if not network.generators.empty else set()
        missing = [nodes for nodes in nx.connected_components(graph)
                   if not nodes.intersection(slack_buses)]
        if missing:
            raise _NoSlackIslandError("at least one connected component has no Slack generator")

    @staticmethod
    def _active_ac_graph(network: pypsa.Network) -> nx.Graph:
        """Construct the active AC topology from public static component tables."""
        graph = nx.Graph()
        graph.add_nodes_from(str(name) for name in network.buses.index)
        for component in (network.lines, network.transformers):
            for _, row in component.iterrows():
                if bool(row.get("active", True)):
                    graph.add_edge(str(row.bus0), str(row.bus1))
        return graph

    @classmethod
    def _connected_island_count(cls, network: pypsa.Network) -> int:
        """Return the number of active AC islands without exposing their names."""
        return nx.number_connected_components(cls._active_ac_graph(network))

    def _parse_subnetwork_status(
        self, result: Mapping[str, pd.DataFrame], snapshot: object,
    ) -> tuple[PowerFlowSubnetworkStatus, ...]:
        """Validate PyPSA convergence tables and redact backend subnetwork names."""
        required = ("converged", "n_iter", "error")
        if any(name not in result or not isinstance(result[name], pd.DataFrame) for name in required):
            raise PowerFlowExecutionError("PyPSA PF result lacks convergence tables")
        converged, iterations, errors = (result[name] for name in required)
        if snapshot not in converged.index:
            raise PowerFlowExecutionError("PyPSA PF result omitted the selected snapshot")
        columns = tuple(converged.columns)
        if not columns or any(column not in iterations.columns or column not in errors.columns
                              for column in columns):
            raise PowerFlowExecutionError("PyPSA PF convergence subnetwork columns are inconsistent")
        statuses: list[PowerFlowSubnetworkStatus] = []
        for ordinal, column in enumerate(columns):
            converged_value = converged.at[snapshot, column]
            if not isinstance(converged_value, (bool, np.bool_)):
                raise PowerFlowExecutionError("PyPSA convergence value is not boolean")
            raw_iteration = iterations.at[snapshot, column]
            iteration = int(raw_iteration) if pd.notna(raw_iteration) else None
            raw_error = errors.at[snapshot, column]
            error = float(raw_error) if pd.notna(raw_error) and math.isfinite(float(raw_error)) else None
            statuses.append(PowerFlowSubnetworkStatus(
                ordinal, bool(converged_value), iteration, error))
        return tuple(statuses)

    def _extract_solved_values(
        self, network: pypsa.Network, sidecar: IdentitySidecar, snapshot: object,
    ) -> dict[str, dict[StableId, float]]:
        """Copy selected-snapshot bus/branch scalar outputs into sidecar IDs."""
        result: dict[str, dict[StableId, float]] = {
            name: {} for name in (
                "bus_vm_pu", "bus_va_deg", "bus_p_mw", "bus_q_mvar",
                "branch_p0", "branch_q0", "branch_p1", "branch_q1")
        }
        for name in network.buses.index:
            stable_id = sidecar.source_id("Bus", str(name))
            result["bus_vm_pu"][stable_id] = self._read_dynamic(network.buses_t.v_mag_pu, snapshot, name)
            result["bus_va_deg"][stable_id] = math.degrees(
                self._read_dynamic(network.buses_t.v_ang, snapshot, name))
            result["bus_p_mw"][stable_id] = self._read_dynamic(network.buses_t.p, snapshot, name)
            result["bus_q_mvar"][stable_id] = self._read_dynamic(network.buses_t.q, snapshot, name)
        for component_name, component in (("Line", network.lines),
                                          ("Transformer", network.transformers)):
            time_series = network.lines_t if component_name == "Line" else network.transformers_t
            for name in component.index:
                stable_id = sidecar.source_id(component_name, str(name))
                for port in (0, 1):
                    for quantity, output in (("p", "p"), ("q", "q")):
                        key = f"branch_{quantity}{port}"
                        table = getattr(time_series, f"{output}{port}")
                        result[key][stable_id] = self._read_dynamic(table, snapshot, name)
        return result

    @staticmethod
    def _read_dynamic(frame: pd.DataFrame, snapshot: object, component_name: object) -> float:
        """Read and validate one solved scalar from a PyPSA time-series table."""
        try:
            value = float(frame.at[snapshot, component_name])
        except (KeyError, TypeError, ValueError) as exc:
            raise PowerFlowExecutionError("PyPSA solved-output table is incomplete") from exc
        if not math.isfinite(value):
            raise PowerFlowExecutionError("PyPSA solved output is not finite")
        return value

    @staticmethod
    def _solver_metadata(
        contract: PowerFlowOperatingPointContract, subnetwork_count: int,
    ) -> dict[str, object]:
        """Return non-identifying solver settings and dependency version."""
        return {
            "pypsa_version": pypsa.__version__,
            "solver": contract.solver,
            "tolerance": contract.tolerance,
            "max_iterations_requested": contract.max_iterations,
            "max_iterations_enforced": False,
            "iteration_limit_source": "PyPSA public API default",
            "distribute_slack": contract.distribute_slack,
            "slack_weights": contract.slack_weights or "p_set",
            "use_seed": contract.use_seed,
            "subnetwork_count": subnetwork_count,
        }

    @staticmethod
    def _empty_result(
        status: PowerFlowExecutionStatus, snapshot: object | None,
        reason: str,
    ) -> PowerFlowExecutionResult:
        """Build a no-metrics result for an unsupported, failed, or prechecked case."""
        return PowerFlowExecutionResult(
            status=status, converged=False, snapshot=snapshot,
            subnetwork_status=(), failure_reason=reason,
        )

    @staticmethod
    def _assert_original_unchanged(network: pypsa.Network, before: str) -> None:
        """Ensure the caller's structural network stayed unchanged by solving its copy."""
        if structural_input_signature(network) != before:
            raise PowerFlowExecutionError("PF executor modified the caller's structural network")


class _NoSlackIslandError(RuntimeError):
    """Internal marker mapped to the explicit nonconvergence state."""
