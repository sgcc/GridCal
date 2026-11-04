"""Read-only, StableId-aligned AC fidelity diagnostics for retained networks."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pypsa

from psdcalc.reduction.backends.pypsa.adapter import IdentitySidecar
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.exceptions import IdentityError
from psdcalc.reduction.fidelity.contracts import PowerFlowErrorSummary
from psdcalc.reduction.fidelity.metrics import align_component_angles, summarize_errors
from psdcalc.reduction.fidelity.pypsa_executor import PowerFlowExecutionResult
from psdcalc.reduction.mapping.bundle import MappingBundle, MapEntry
from psdcalc.reduction.reporting.comprehensive import (
    CrossVoltageInterfaceReport,
    ErrorStatistics,
    EvidenceStatus,
    EvidenceValue,
    MetricGroup,
    RetainedGroupComparison,
    RetainedNetworkFidelityReport,
    RetainedVoltageLayerComparison,
    ValueStatistics,
    not_recorded,
)


class RetainedFidelityError(ValueError):
    """Raised when source/target identities or mapping semantics conflict."""


@dataclass(frozen=True)
class _BranchMember:
    """One mapped source branch and its canonical target branch endpoints."""

    entry: MapEntry
    component: str
    source_name: str
    target_name: str
    source_bus0: StableId
    source_bus1: StableId
    target_bus0: StableId
    target_bus1: StableId
    active: bool


@dataclass
class _ErrorBuckets:
    """Mutable internal error samples; public output is immutable."""

    values: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    def add(self, metric: str, value: float) -> None:
        if not math.isfinite(value):
            raise RetainedFidelityError("nonfinite retained-network residual")
        self.values[metric].append(float(value))


def _finite(value: object) -> float | None:
    """Return a finite numeric scalar, rejecting bool and non-scalars."""
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    return numeric


def _available(value: int | float | str, unit: str, *, sample_count: int | None = None) -> EvidenceValue:
    """Create one finite, typed available observation."""
    return EvidenceValue(value=value, state=EvidenceStatus.AVAILABLE, unit=unit,
                         sample_count=sample_count)


def _error_statistics(values: Sequence[float], unit: str) -> ErrorStatistics:
    """Summarize signed residuals using the package's frozen RMSE/P95 convention."""
    if not values:
        return ErrorStatistics()
    summary: PowerFlowErrorSummary = summarize_errors([0.0] * len(values), values)
    return ErrorStatistics(
        maximum=_available(summary.max_absolute, unit, sample_count=summary.sample_count),
        rmse=_available(summary.rmse, unit, sample_count=summary.sample_count),
        p95=_available(summary.p95_absolute, unit, sample_count=summary.sample_count),
        sample_count=_available(summary.sample_count, "samples"),
    )


def _value_statistics(values: Sequence[float], unit: str) -> ValueStatistics:
    """Summarize signed directional P/Q values without converting to errors."""
    if not values:
        return ValueStatistics()
    finite = [float(value) for value in values]
    if any(not math.isfinite(value) for value in finite):
        raise RetainedFidelityError("nonfinite interface power value")
    return ValueStatistics(
        minimum=_available(min(finite), unit, sample_count=len(finite)),
        maximum=_available(max(finite), unit, sample_count=len(finite)),
        mean=_available(float(np.mean(finite)), unit, sample_count=len(finite)),
        max_absolute=_available(max(abs(value) for value in finite), unit,
                                sample_count=len(finite)),
        sample_count=_available(len(finite), "samples"),
    )


def _digest_result(result: PowerFlowExecutionResult | None) -> str | None:
    """Hash solver outputs and status with StableIds, without exposing identities."""
    if result is None:
        return None
    components = (
        "bus_vm_pu", "bus_va_deg", "bus_p_mw", "bus_q_mvar",
        "branch_p0", "branch_q0", "branch_p1", "branch_q1",
    )
    payload: dict[str, object] = {"status": result.status.value, "converged": result.converged}
    for component in components:
        values: Mapping[StableId, float] = getattr(result, component)
        payload[component] = sorted((key.encode(), float(value)) for key, value in values.items())
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _component_frames(network: pypsa.Network) -> dict[str, object]:
    """Return public PyPSA tables keyed by StableId component names."""
    return {
        "Bus": network.buses,
        "Line": network.lines,
        "Transformer": network.transformers,
        "Link": network.links,
        "Generator": network.generators,
        "Load": network.loads,
        "ShuntImpedance": network.shunt_impedances,
    }


def _backend_exists(sidecar: IdentitySidecar, network: pypsa.Network, stable_id: StableId) -> str:
    """Resolve and verify a StableId against a public component table."""
    if stable_id.component not in _component_frames(network):
        raise RetainedFidelityError("mapping references an unsupported component")
    try:
        backend_name = sidecar.backend_name(stable_id)
    except (IdentityError, KeyError, ValueError) as exc:
        raise RetainedFidelityError("mapping identity is absent from its sidecar") from exc
    frame = _component_frames(network)[stable_id.component]
    if backend_name not in frame.index:  # type: ignore[attr-defined]
        raise RetainedFidelityError("sidecar identity is absent from its network")
    return backend_name


def _validate_maps(
    source_network: pypsa.Network,
    target_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    target_sidecar: IdentitySidecar,
    mappings: MappingBundle | None,
) -> tuple[dict[StableId, MapEntry], dict[StableId, MapEntry]]:
    """Validate every provided Bus/branch row and retain partial coverage."""
    if mappings is None:
        return {}, {}
    bus_entries: dict[StableId, MapEntry] = {}
    branch_entries: dict[StableId, MapEntry] = {}
    for rows, destination in ((mappings.bus_map, bus_entries),
                              (mappings.branch_map, branch_entries)):
        for row in rows:
            if row.source_id in destination:
                raise RetainedFidelityError("duplicate source identity in MappingBundle")
            _backend_exists(source_sidecar, source_network, row.source_id)
            if row.source_id.component not in ({"Bus"} if destination is bus_entries else
                                               {"Line", "Transformer", "Link"}):
                raise RetainedFidelityError("mapping row is in the wrong component map")
            if row.target_id is not None:
                if row.target_id.component != row.source_id.component:
                    raise RetainedFidelityError("source and target mapping components differ")
                _backend_exists(target_sidecar, target_network, row.target_id)
            destination[row.source_id] = row
    return bus_entries, branch_entries


def _active(row: object) -> bool:
    """Read a static active flag; absent means active and missing values fail closed."""
    value = row.get("active", True)  # type: ignore[attr-defined]
    if value is None or isinstance(value, float) and math.isnan(value):
        raise RetainedFidelityError("branch active state is missing")
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    raise RetainedFidelityError("branch active state is not boolean")


def _voltage(network: pypsa.Network, sidecar: IdentitySidecar, bus_id: StableId) -> float:
    """Resolve a Bus.v_nom value by StableId with no tolerance or rounding."""
    name = _backend_exists(sidecar, network, bus_id)
    value = _finite(network.buses.at[name, "v_nom"])
    if value is None or value <= 0.0:
        raise RetainedFidelityError("Bus.v_nom must be positive and finite")
    return value


def _branch_members(
    source_network: pypsa.Network,
    target_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    target_sidecar: IdentitySidecar,
    source_bus_map: Mapping[StableId, MapEntry],
    branch_entries: Mapping[StableId, MapEntry],
) -> tuple[_BranchMember, ...]:
    """Resolve active source branches to target endpoint identities and direction."""
    output: list[_BranchMember] = []
    for entry in branch_entries.values():
        if entry.target_id is None:
            continue
        source_name = _backend_exists(source_sidecar, source_network, entry.source_id)
        target_name = _backend_exists(target_sidecar, target_network, entry.target_id)
        source_frame = _component_frames(source_network)[entry.source_id.component]
        target_frame = _component_frames(target_network)[entry.target_id.component]
        source_row = source_frame.loc[source_name]  # type: ignore[attr-defined]
        target_row = target_frame.loc[target_name]  # type: ignore[attr-defined]
        if not _active(source_row) or not _active(target_row):
            continue
        try:
            source_bus0 = source_sidecar.source_id("Bus", str(source_row.bus0))
            source_bus1 = source_sidecar.source_id("Bus", str(source_row.bus1))
            bus_mapping0 = source_bus_map.get(source_bus0)
            bus_mapping1 = source_bus_map.get(source_bus1)
            if bus_mapping0 is None or bus_mapping1 is None:
                continue
            mapped0 = bus_mapping0.target_id
            mapped1 = bus_mapping1.target_id
        except (IdentityError, KeyError, ValueError) as exc:
            raise RetainedFidelityError("branch endpoint lacks a source-to-target Bus identity") from exc
        if mapped0 is None or mapped1 is None:
            continue
        try:
            target_bus0 = target_sidecar.source_id("Bus", str(target_row.bus0))
            target_bus1 = target_sidecar.source_id("Bus", str(target_row.bus1))
        except (IdentityError, KeyError, ValueError) as exc:
            raise RetainedFidelityError("target branch endpoint lacks a sidecar identity") from exc
        if (mapped0, mapped1) not in ((target_bus0, target_bus1), (target_bus1, target_bus0)):
            raise RetainedFidelityError("mapped source branch terminals do not match target branch")
        output.append(_BranchMember(
            entry=entry,
            component=entry.source_id.component,
            source_name=source_name,
            target_name=target_name,
            source_bus0=source_bus0,
            source_bus1=source_bus1,
            target_bus0=target_bus0,
            target_bus1=target_bus1,
            active=True,
        ))
    return tuple(output)


def _component_labels(network: pypsa.Network, sidecar: IdentitySidecar) -> dict[StableId, str]:
    """Build deterministic active connected-component labels for angle alignment."""
    graph = nx.Graph()
    ids = {str(name): sidecar.source_id("Bus", str(name)) for name in network.buses.index}
    graph.add_nodes_from(ids)
    for frame in (network.lines, network.transformers, network.links):
        for _, row in frame.iterrows():
            if _active(row):
                graph.add_edge(str(row.bus0), str(row.bus1))
    result: dict[StableId, str] = {}
    for component in nx.connected_components(graph):
        label = min(ids[name].encode() for name in component)
        result.update({ids[name]: label for name in component})
    return result


def _branch_terminal_value(
    result: PowerFlowExecutionResult,
    component: str,
    stable_id: StableId,
    side: int,
    quantity: str,
) -> float | None:
    """Read one PF branch terminal from the detached StableId result."""
    suffix = "0" if side == 0 else "1"
    values = getattr(result, f"branch_{quantity}{suffix}")
    return _finite(values.get(stable_id))


def _branch_group_samples(
    members: Sequence[_BranchMember],
    source_pf: PowerFlowExecutionResult,
    target_pf: PowerFlowExecutionResult,
    *,
    selected: set[StableId] | None = None,
    allowed_voltage: float | None = None,
    source_network: pypsa.Network,
    target_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    target_sidecar: IdentitySidecar,
    source_bus_map: Mapping[StableId, MapEntry],
) -> tuple[dict[str, list[float]], int, int, int, int]:
    """Compare many-to-one branch maps by aligned endpoint, deduplicating targets."""
    target_groups: dict[StableId, list[_BranchMember]] = defaultdict(list)
    source_member_count = 0
    unmatched_count = 0
    for member in members:
        if selected is not None and member.entry.source_id not in selected:
            continue
        if allowed_voltage is not None:
            v0 = _voltage(source_network, source_sidecar, member.source_bus0)
            v1 = _voltage(source_network, source_sidecar, member.source_bus1)
            if v0 != allowed_voltage and v1 != allowed_voltage:
                continue
        target_id = member.entry.target_id
        if target_id is None:
            continue
        source_member_count += 1
        target_groups[target_id].append(member)
    errors: dict[str, list[float]] = defaultdict(list)
    compared_member_count = 0
    for target_id, group in target_groups.items():
        sums = {f"{quantity}{port}": 0.0 for quantity in ("p", "q") for port in (0, 1)}
        valid = True
        for member in group:
            mapped0 = source_bus_map[member.source_bus0].target_id
            mapped1 = source_bus_map[member.source_bus1].target_id
            if mapped0 is None or mapped1 is None:
                valid = False
                break
            direct = (mapped0, mapped1) == (member.target_bus0, member.target_bus1)
            source_side_for_target0 = 0 if direct else 1
            source_side_for_target1 = 1 - source_side_for_target0
            for quantity in ("p", "q"):
                value0 = _branch_terminal_value(source_pf, member.component,
                                                member.entry.source_id, source_side_for_target0,
                                                quantity)
                value1 = _branch_terminal_value(source_pf, member.component,
                                                member.entry.source_id, source_side_for_target1,
                                                quantity)
                if value0 is None or value1 is None:
                    valid = False
                    break
                sums[f"{quantity}0"] += value0
                sums[f"{quantity}1"] += value1
            if not valid:
                break
        if not valid:
            unmatched_count += len(group)
            continue
        target_name = _backend_exists(target_sidecar, target_network, target_id)
        target_row = _component_frames(target_network)[target_id.component].loc[target_name]  # type: ignore[attr-defined]
        if not _active(target_row):
            unmatched_count += len(group)
            continue
        for quantity in ("p", "q"):
            values = getattr(target_pf, f"branch_{quantity}0")
            values1 = getattr(target_pf, f"branch_{quantity}1")
            if target_id not in values or target_id not in values1:
                valid = False
                break
            for port, target_values in ((0, values), (1, values1)):
                target_value = _finite(target_values[target_id])
                if target_value is None:
                    valid = False
                    break
                residual = sums[f"{quantity}{port}"] - target_value
                errors[f"branch_{quantity}"].append(residual)
                errors[f"branch_{quantity}_{target_id.component.casefold()}"].append(residual)
        if not valid:
            unmatched_count += len(group)
            for quantity in ("branch_p", "branch_q"):
                if len(errors[quantity]) >= 2:
                    del errors[quantity][-2:]
            continue
        compared_member_count += len(group)
    return errors, source_member_count, compared_member_count, unmatched_count, len(target_groups)


def _bus_samples(
    entries: Sequence[MapEntry],
    source_pf: PowerFlowExecutionResult,
    target_pf: PowerFlowExecutionResult,
    source_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    *,
    preserved_voltage_ids: set[StableId] | None = None,
) -> tuple[dict[str, list[float]], int, int]:
    """Compare mapped Bus Vm/Va and aggregate net P/Q by target Bus."""
    errors: dict[str, list[float]] = defaultdict(list)
    vm_rows: list[MapEntry] = []
    angle_rows: list[MapEntry] = []
    for entry in entries:
        if entry.target_id is None:
            continue
        if preserved_voltage_ids is not None and entry.source_id not in preserved_voltage_ids:
            continue
        source_vm = _finite(source_pf.bus_vm_pu.get(entry.source_id))
        target_vm = _finite(target_pf.bus_vm_pu.get(entry.target_id))
        if source_vm is not None and target_vm is not None:
            errors["bus_vm"].append(source_vm - target_vm)
            vm_rows.append(entry)
        source_va = _finite(source_pf.bus_va_deg.get(entry.source_id))
        target_va = _finite(target_pf.bus_va_deg.get(entry.target_id))
        if source_va is not None and target_va is not None:
            angle_rows.append(entry)
    labels = _component_labels(source_network, source_sidecar)
    source_angles = {entry.source_id: float(source_pf.bus_va_deg[entry.source_id])
                     for entry in angle_rows}
    target_angles = {entry.target_id: float(target_pf.bus_va_deg[entry.target_id])
                     for entry in angle_rows if entry.target_id is not None}
    alignment = {entry.source_id: (entry.target_id, labels[entry.source_id])
                 for entry in angle_rows if entry.target_id is not None}
    if alignment:
        aligned, _ = align_component_angles(source_angles, target_angles, alignment)
        errors["bus_va"].extend(aligned.values())
    source_injection_p: dict[StableId, float] = defaultdict(float)
    source_injection_q: dict[StableId, float] = defaultdict(float)
    valid_p: dict[StableId, int] = defaultdict(int)
    valid_q: dict[StableId, int] = defaultdict(int)
    expected: dict[StableId, int] = defaultdict(int)
    for entry in entries:
        if entry.target_id is None:
            continue
        expected[entry.target_id] += 1
        p_value = _finite(source_pf.bus_p_mw.get(entry.source_id))
        q_value = _finite(source_pf.bus_q_mvar.get(entry.source_id))
        if p_value is not None:
            source_injection_p[entry.target_id] += p_value
            valid_p[entry.target_id] += 1
        if q_value is not None:
            source_injection_q[entry.target_id] += q_value
            valid_q[entry.target_id] += 1
    for target_id, expected_count in expected.items():
        target_p = _finite(target_pf.bus_p_mw.get(target_id))
        target_q = _finite(target_pf.bus_q_mvar.get(target_id))
        if target_p is not None and valid_p[target_id] == expected_count:
            errors["injection_p"].append(source_injection_p[target_id] - target_p)
        if target_q is not None and valid_q[target_id] == expected_count:
            errors["injection_q"].append(source_injection_q[target_id] - target_q)
    return errors, len(entries), len(vm_rows)


def _group_comparison(
    group: MetricGroup,
    source_count: int,
    rows: Sequence[MapEntry],
    branch_members: Sequence[_BranchMember],
    source_pf: PowerFlowExecutionResult | None,
    target_pf: PowerFlowExecutionResult | None,
    source_network: pypsa.Network,
    target_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    target_sidecar: IdentitySidecar,
    source_bus_map: Mapping[StableId, MapEntry],
    *,
    retained_bus_count: int,
    source_branch_count: int,
    retained_branch_count: int,
    preserved_voltage_ids: set[StableId],
) -> tuple[RetainedGroupComparison, dict[str, list[float]], dict[str, int]]:
    """Build one diagnostic group without invoking PF or threshold evaluation."""
    bus_errors: dict[str, list[float]] = defaultdict(list)
    branch_errors: dict[str, list[float]] = defaultdict(list)
    bus_compared = 0
    branch_sources = branch_compared = branch_unmatched = target_branch_count = 0
    available = source_pf is not None and target_pf is not None \
        and source_pf.converged and target_pf.converged
    if available:
        bus_errors, _, bus_compared = _bus_samples(
            rows, source_pf, target_pf, source_network, source_sidecar,
            preserved_voltage_ids=preserved_voltage_ids)
        branch_errors, branch_sources, branch_compared, branch_unmatched, target_branch_count = (
            _branch_group_samples(
                branch_members, source_pf, target_pf,
                source_network=source_network, target_network=target_network,
                source_sidecar=source_sidecar, target_sidecar=target_sidecar,
                source_bus_map=source_bus_map,
            ))
    branch_expected = source_branch_count if group is MetricGroup.WHOLE_NETWORK else retained_branch_count
    bus_coverage_denominator = source_count
    branch_coverage_denominator = branch_expected
    mapped_bus_count = sum(row.target_id is not None for row in rows)
    mapped_branch_count = len({member.entry.source_id for member in branch_members})
    bus_coverage = (mapped_bus_count / bus_coverage_denominator
                    if bus_coverage_denominator else None)
    branch_coverage = (mapped_branch_count / branch_coverage_denominator
                       if branch_coverage_denominator else None)
    overall_coverage_values = [value for value in (bus_coverage, branch_coverage)
                               if value is not None]
    mapping_coverage = (sum(overall_coverage_values) / len(overall_coverage_values)
                        if overall_coverage_values else None)
    mapped_bus_sources = {row.source_id for row in rows if row.target_id is not None}
    missing_bus = max(0, source_count - len(mapped_bus_sources))
    status = EvidenceStatus.AVAILABLE if available else EvidenceStatus.NOT_RECORDED
    report = RetainedGroupComparison(
        group=group,
        status=status,
        source_bus_count=_available(source_count, "buses"),
        retained_bus_count=_available(retained_bus_count, "buses"),
        compared_bus_count=_available(bus_compared, "buses") if available else not_recorded(),
        missing_bus_count=_available(missing_bus, "buses"),
        unmatched_bus_count=_available(max(0, len(mapped_bus_sources) - bus_compared), "buses")
            if available else not_recorded(),
        source_branch_count=_available(source_branch_count, "branches"),
        retained_branch_count=_available(retained_branch_count, "branches"),
        compared_branch_count=_available(branch_compared, "branch members")
            if available else not_recorded(),
        missing_branch_count=_available(max(0, branch_expected - branch_sources), "branches"),
        unmatched_branch_count=_available(branch_unmatched, "branches") if available else not_recorded(),
        mapping_coverage=_available(mapping_coverage, "fraction")
            if mapping_coverage is not None else not_recorded(),
        bus_vm=_error_statistics(bus_errors.get("bus_vm", ()), "pu"),
        bus_va=_error_statistics(bus_errors.get("bus_va", ()), "degree"),
        line_p=_error_statistics(branch_errors.get("branch_p_line", ()), "MW"),
        line_q=_error_statistics(branch_errors.get("branch_q_line", ()), "MVAr"),
        transformer_p=_error_statistics(branch_errors.get("branch_p_transformer", ()), "MW"),
        transformer_q=_error_statistics(branch_errors.get("branch_q_transformer", ()), "MVAr"),
        injection_p=_error_statistics(bus_errors.get("injection_p", ()), "MW"),
        injection_q=_error_statistics(bus_errors.get("injection_q", ()), "MVAr"),
    )
    counts = {"bus_compared": bus_compared, "branch_sources": branch_sources,
              "branch_compared": branch_compared, "branch_unmatched": branch_unmatched,
              "target_branches": target_branch_count}
    return report, {**bus_errors, **branch_errors}, counts


def _preserved_bus_ids(bus_entries: Mapping[StableId, MapEntry]) -> set[StableId]:
    """Return only exact preserved Singleton Bus mappings."""
    return {source_id for source_id, row in bus_entries.items()
            if row.target_id == source_id and row.mapping_type == "preserved"}


def _preserved_branch_ids(branch_entries: Mapping[StableId, MapEntry],
                          preserved_buses: set[StableId],
                          source_network: pypsa.Network,
                          source_sidecar: IdentitySidecar) -> set[StableId]:
    """Return exact preserved branches whose two terminal Buses are preserved."""
    result: set[StableId] = set()
    for source_id, row in branch_entries.items():
        if row.target_id != source_id or row.mapping_type != "preserved":
            continue
        name = _backend_exists(source_sidecar, source_network, source_id)
        frame = _component_frames(source_network)[source_id.component]
        branch = frame.loc[name]  # type: ignore[attr-defined]
        bus0 = source_sidecar.source_id("Bus", str(branch.bus0))
        bus1 = source_sidecar.source_id("Bus", str(branch.bus1))
        if bus0 in preserved_buses and bus1 in preserved_buses:
            result.add(source_id)
    return result


def _layer_rows(
    source_network: pypsa.Network,
    target_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    target_sidecar: IdentitySidecar,
    bus_entries: Mapping[StableId, MapEntry],
    branch_entries: Mapping[StableId, MapEntry],
    branch_members: Sequence[_BranchMember],
    preserved_buses: set[StableId],
    preserved_branches: set[StableId],
    source_pf: PowerFlowExecutionResult | None,
    target_pf: PowerFlowExecutionResult | None,
    cutoffs: Sequence[float],
) -> tuple[RetainedVoltageLayerComparison, ...]:
    """Summarize each actual source voltage layer under each requested cutoff."""
    source_voltage = {source_sidecar.source_id("Bus", str(name)): float(value)
                      for name, value in source_network.buses.v_nom.items()}
    target_voltage = {target_sidecar.source_id("Bus", str(name)): float(value)
                      for name, value in target_network.buses.v_nom.items()}
    layers = sorted(set(source_voltage.values()))
    result: list[RetainedVoltageLayerComparison] = []
    solved = (source_pf is not None and target_pf is not None
              and source_pf.converged and target_pf.converged)
    for cutoff in cutoffs:
        for voltage in layers:
            source_buses = {sid for sid, value in source_voltage.items() if value == voltage}
            required_buses = {sid for sid in source_buses if voltage >= cutoff}
            retained_buses = source_buses & preserved_buses
            source_lines: set[StableId] = set()
            for name, row in source_network.lines.iterrows():
                v0 = float(source_network.buses.at[row.bus0, "v_nom"])
                v1 = float(source_network.buses.at[row.bus1, "v_nom"])
                if v0 == voltage and v1 == voltage:
                    source_lines.add(source_sidecar.source_id("Line", str(name)))
            required_lines = {sid for sid in source_lines if (
                float(source_network.buses.at[source_network.lines.at[source_sidecar.backend_name(sid), "bus0"], "v_nom"]) >= cutoff
            )}
            retained_lines = source_lines & preserved_branches
            source_transformers = {
                source_sidecar.source_id("Transformer", str(name))
                for name, row in source_network.transformers.iterrows()
                if float(source_network.buses.at[row.bus0, "v_nom"]) == voltage
                or float(source_network.buses.at[row.bus1, "v_nom"]) == voltage
            }
            retained_transformers = source_transformers & preserved_branches
            target_buses = sum(value == voltage for value in target_voltage.values())
            target_lines = sum(
                float(target_network.buses.at[row.bus0, "v_nom"]) == voltage
                and float(target_network.buses.at[row.bus1, "v_nom"]) == voltage
                for _, row in target_network.lines.iterrows())
            target_transformers = sum(
                float(target_network.buses.at[row.bus0, "v_nom"]) == voltage
                or float(target_network.buses.at[row.bus1, "v_nom"]) == voltage
                for _, row in target_network.transformers.iterrows())
            eligible_bus_coverage = (len(required_buses & preserved_buses) / len(required_buses)
                                     if required_buses else None)
            eligible_line_coverage = (len(required_lines & preserved_branches) / len(required_lines)
                                      if required_lines else None)
            transformer_coverage = (len(source_transformers & preserved_branches) / len(source_transformers)
                                    if source_transformers else None)
            bus_rows = [row for row in bus_entries.values()
                        if row.source_id in retained_buses and voltage >= cutoff]
            branch_rows = [member for member in branch_members
                           if member.entry.source_id in retained_lines and voltage >= cutoff]
            transformer_rows = [member for member in branch_members
                                if member.entry.source_id in retained_transformers
                                and member.component == "Transformer"]
            bus_error_data: dict[str, list[float]] = defaultdict(list)
            if solved and bus_rows:
                bus_error_data, _, _ = _bus_samples(
                    bus_rows, source_pf, target_pf, source_network, source_sidecar,
                    preserved_voltage_ids=preserved_buses)
            line_error_data: dict[str, list[float]] = defaultdict(list)
            if solved and branch_rows:
                line_error_data, *_ = _branch_group_samples(
                    branch_rows, source_pf, target_pf,
                    source_network=source_network, target_network=target_network,
                    source_sidecar=source_sidecar, target_sidecar=target_sidecar,
                    source_bus_map=bus_entries)
            transformer_error_data: dict[str, list[float]] = defaultdict(list)
            if solved and transformer_rows:
                transformer_error_data, *_ = _branch_group_samples(
                    transformer_rows, source_pf, target_pf,
                    source_network=source_network, target_network=target_network,
                    source_sidecar=source_sidecar, target_sidecar=target_sidecar,
                    source_bus_map=bus_entries)
            result.append(RetainedVoltageLayerComparison(
                cutoff_kv=cutoff,
                voltage_kv=voltage,
                source_bus_count=_available(len(source_buses), "buses"),
                policy_protected_bus_count=_available(len(required_buses), "buses"),
                retained_bus_count=_available(len(retained_buses), "buses"),
                reduced_bus_count=_available(len(source_buses) - len(retained_buses), "buses"),
                target_bus_count=_available(target_buses, "buses"),
                source_line_count=_available(len(source_lines), "lines"),
                policy_protected_line_count=_available(len(required_lines), "lines"),
                retained_line_count=_available(len(retained_lines), "lines"),
                reduced_line_count=_available(len(source_lines) - len(retained_lines), "lines"),
                target_line_count=_available(target_lines, "lines"),
                source_transformer_count=_available(len(source_transformers), "backend transformer rows"),
                retained_transformer_count=_available(len(retained_transformers), "backend transformer rows"),
                reduced_transformer_count=_available(
                    len(source_transformers) - len(retained_transformers), "backend transformer rows"),
                target_transformer_count=_available(target_transformers, "backend transformer rows"),
                preserved_identity_coverage=(
                    _available(eligible_bus_coverage, "fraction")
                    if eligible_bus_coverage is not None else not_recorded()),
                preserved_bus_identity_coverage=(
                    _available(eligible_bus_coverage, "fraction")
                    if eligible_bus_coverage is not None else not_recorded()),
                preserved_line_identity_coverage=(
                    _available(eligible_line_coverage, "fraction")
                    if eligible_line_coverage is not None else not_recorded()),
                preserved_transformer_identity_coverage=(
                    _available(transformer_coverage, "fraction")
                    if transformer_coverage is not None else not_recorded()),
                bus_vm=_error_statistics(bus_error_data.get("bus_vm", ()), "pu"),
                bus_va=_error_statistics(bus_error_data.get("bus_va", ()), "degree"),
                line_p=_error_statistics(line_error_data.get("branch_p_line", ()), "MW"),
                line_q=_error_statistics(line_error_data.get("branch_q_line", ()), "MVAr"),
                transformer_p=_error_statistics(
                    transformer_error_data.get("branch_p_transformer", ()), "MW"),
                transformer_q=_error_statistics(
                    transformer_error_data.get("branch_q_transformer", ()), "MVAr"),
                evaluation_status=(EvidenceStatus.AVAILABLE if solved else EvidenceStatus.NOT_RECORDED),
            ))
    return tuple(result)


def compare_retained_network_fidelity(
    *,
    source_network: pypsa.Network,
    target_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    target_sidecar: IdentitySidecar,
    mappings: MappingBundle | None,
    source_pf: PowerFlowExecutionResult | None,
    target_pf: PowerFlowExecutionResult | None,
    cutoff_kv: Sequence[float] = (500.0, 220.0, 100.0),
) -> RetainedNetworkFidelityReport:
    """Compare existing source/candidate PF outputs by retained identities.

    The function performs no power-flow solve, threshold evaluation, admission,
    mapping construction, or source/target mutation. All branch ports are
    matched through endpoint StableIds, and only explicit ``preserved`` rows
    enter the retained-backbone group.
    """
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           for value in cutoff_kv):
        raise RetainedFidelityError("cutoff_kv values must be numeric, not boolean")
    cutoffs = tuple(float(value) for value in cutoff_kv)
    if any(not math.isfinite(value) or value <= 0.0 for value in cutoffs):
        raise RetainedFidelityError("cutoff_kv values must be positive and finite")
    if len(set(cutoffs)) != len(cutoffs):
        raise RetainedFidelityError("duplicate cutoff_kv value")
    bus_entries, branch_entries = _validate_maps(
        source_network, target_network, source_sidecar, target_sidecar, mappings)
    if mappings is None:
        group_rows = tuple(RetainedGroupComparison(group=group) for group in MetricGroup)
        return RetainedNetworkFidelityReport(
            source_pf_status=(_available(source_pf.status.value, "status") if source_pf else not_recorded()),
            target_pf_status=(_available(target_pf.status.value, "status") if target_pf else not_recorded()),
            source_pf_digest=_digest_result(source_pf),
            target_pf_digest=_digest_result(target_pf),
            groups=group_rows,
        )
    source_bus_ids = {source_sidecar.source_id("Bus", str(name)) for name in source_network.buses.index}
    source_branch_ids = {
        source_sidecar.source_id(component, str(name))
        for component, frame in (("Line", source_network.lines),
                                 ("Transformer", source_network.transformers),
                                 ("Link", source_network.links))
        for name in frame.index
    }
    if set(bus_entries) - source_bus_ids or set(branch_entries) - source_branch_ids:
        raise RetainedFidelityError("MappingBundle has source identities outside source inventory")
    preserved_buses = _preserved_bus_ids(bus_entries)
    preserved_branches = _preserved_branch_ids(
        branch_entries, preserved_buses, source_network, source_sidecar)
    branch_members = _branch_members(
        source_network, target_network, source_sidecar, target_sidecar,
        bus_entries, branch_entries)
    source_pf_ready = source_pf is not None and source_pf.converged
    target_pf_ready = target_pf is not None and target_pf.converged
    pf_ready = source_pf_ready and target_pf_ready

    source_all_buses = len(source_bus_ids)
    retained_entries = [row for row in bus_entries.values() if row.source_id in preserved_buses]
    retained_cutoff = max(cutoffs, default=0.0)
    expected_retained_buses = {
        sid for sid in source_bus_ids
        if _voltage(source_network, source_sidecar, sid) >= retained_cutoff
    } | preserved_buses
    retained_expected_count = len(expected_retained_buses)
    aggregated_entries = [row for row in bus_entries.values()
                          if row.target_id is not None and row.source_id not in preserved_buses]
    mapped_whole_entries = list(bus_entries.values())
    all_branch_members = list(branch_members)
    retained_branch_members = [member for member in branch_members
                               if member.entry.source_id in preserved_branches]
    retained_source_bus_backend = {
        source_sidecar.backend_name(sid) for sid in preserved_buses
    }

    def region_branch(member: _BranchMember) -> bool:
        source0 = source_sidecar.backend_name(member.source_bus0)
        source1 = source_sidecar.backend_name(member.source_bus1)
        return source0 not in retained_source_bus_backend and source1 not in retained_source_bus_backend

    aggregated_branch_members = [member for member in branch_members if region_branch(member)]
    boundary_members = [member for member in branch_members
                        if (source_sidecar.backend_name(member.source_bus0) in retained_source_bus_backend)
                        != (source_sidecar.backend_name(member.source_bus1) in retained_source_bus_backend)]
    total_retained_branch_count = len(preserved_branches)
    group_rows: list[RetainedGroupComparison] = []
    group_errors: dict[MetricGroup, dict[str, list[float]]] = {}
    group_counts: dict[MetricGroup, dict[str, int]] = {}
    specs = (
        (MetricGroup.WHOLE_NETWORK, source_all_buses, mapped_whole_entries,
         all_branch_members, len(source_branch_ids), total_retained_branch_count),
        (MetricGroup.RETAINED_BACKBONE, retained_expected_count, retained_entries,
         retained_branch_members, len(retained_branch_members), len(retained_branch_members)),
        (MetricGroup.AGGREGATED_REGION, len(aggregated_entries), aggregated_entries,
         aggregated_branch_members, len(aggregated_branch_members), 0),
        (MetricGroup.CROSS_VOLTAGE_INTERFACES, 0, [], boundary_members,
         len(boundary_members), 0),
    )
    for group, source_count, rows, branches, source_branch_count, retained_branch_count in specs:
        retained_count = len(rows) if group is MetricGroup.RETAINED_BACKBONE else (
            len(preserved_buses) if group is MetricGroup.WHOLE_NETWORK else 0)
        row, errors, counts = _group_comparison(
            group, source_count, rows, branches,
            source_pf if pf_ready else None, target_pf if pf_ready else None,
            source_network, target_network, source_sidecar, target_sidecar,
            bus_entries, retained_bus_count=retained_count,
            source_branch_count=source_branch_count,
            retained_branch_count=retained_branch_count,
            preserved_voltage_ids=(set() if group is MetricGroup.AGGREGATED_REGION
                                   else preserved_buses))
        group_rows.append(row)
        group_errors[group] = errors
        group_counts[group] = counts

    # Boundary metrics are grouped by one retained terminal and one mapped region bus.
    interface_members: dict[tuple[StableId, StableId], list[_BranchMember]] = defaultdict(list)
    for member in boundary_members:
        mapped0 = bus_entries[member.source_bus0].target_id
        mapped1 = bus_entries[member.source_bus1].target_id
        if mapped0 is None or mapped1 is None:
            continue
        source0_preserved = member.source_bus0 in preserved_buses
        source1_preserved = member.source_bus1 in preserved_buses
        owner = mapped0 if source0_preserved else mapped1
        region = mapped1 if source0_preserved else mapped0
        interface_members[(owner, region)].append(member)
    interface_source_p: list[float] = []
    interface_target_p: list[float] = []
    interface_source_q: list[float] = []
    interface_target_q: list[float] = []
    interface_p_error: list[float] = []
    interface_q_error: list[float] = []
    matched_interfaces = 0
    source_interface_members = 0
    target_interface_ids: set[StableId] = set()
    unmatched_interfaces = 0
    if pf_ready:
        for (retained_bus, _), members in interface_members.items():
            source_p = source_q = 0.0
            target_by_id: dict[StableId, tuple[float, float]] = {}
            valid = True
            for member in members:
                mapped0 = bus_entries[member.source_bus0].target_id
                mapped1 = bus_entries[member.source_bus1].target_id
                retained_is_bus0 = member.source_bus0 in preserved_buses
                retained_target = mapped0 if retained_is_bus0 else mapped1
                if retained_target != retained_bus:
                    valid = False
                    break
                # Compare power at the same retained endpoint. PyPSA terminal
                # values use each branch's own bus0/bus1 order, so a reversed
                # target orientation changes the target terminal, not the source.
                source_terminal = 0 if retained_is_bus0 else 1
                p_value = _branch_terminal_value(source_pf, member.component,
                                                 member.entry.source_id, source_terminal, "p")
                q_value = _branch_terminal_value(source_pf, member.component,
                                                 member.entry.source_id, source_terminal, "q")
                if p_value is None or q_value is None:
                    valid = False
                    break
                source_p += p_value
                source_q += q_value
                target_id = member.entry.target_id
                if target_id is None:
                    valid = False
                    break
                target_terminal = 0 if member.target_bus0 == retained_bus else 1
                target_pv = _finite((target_pf.branch_p0 if target_terminal == 0
                                     else target_pf.branch_p1).get(target_id))
                target_qv = _finite((target_pf.branch_q0 if target_terminal == 0
                                     else target_pf.branch_q1).get(target_id))
                if target_pv is None or target_qv is None:
                    valid = False
                    break
                previous = target_by_id.get(target_id)
                if previous is not None and previous != (target_pv, target_qv):
                    raise RetainedFidelityError("duplicate target interface values disagree")
                target_by_id[target_id] = (target_pv, target_qv)
            source_interface_members += len(members)
            if not valid or not target_by_id:
                unmatched_interfaces += 1
                continue
            # A parallel corridor has one target identity, so count its value once.
            target_p = sum(value[0] for value in target_by_id.values())
            target_q = sum(value[1] for value in target_by_id.values())
            interface_source_p.append(source_p)
            interface_target_p.append(target_p)
            interface_source_q.append(source_q)
            interface_target_q.append(target_q)
            interface_p_error.append(source_p - target_p)
            interface_q_error.append(source_q - target_q)
            target_interface_ids.update(target_by_id)
            matched_interfaces += 1

    region_p_errors: list[float] = []
    region_q_errors: list[float] = []
    if pf_ready:
        region_source_p: dict[StableId, float] = defaultdict(float)
        region_source_q: dict[StableId, float] = defaultdict(float)
        region_expected: dict[StableId, int] = defaultdict(int)
        region_valid_p: dict[StableId, int] = defaultdict(int)
        region_valid_q: dict[StableId, int] = defaultdict(int)
        for entry in aggregated_entries:
            if entry.target_id is None:
                continue
            region_expected[entry.target_id] += 1
            p_value = _finite(source_pf.bus_p_mw.get(entry.source_id))
            q_value = _finite(source_pf.bus_q_mvar.get(entry.source_id))
            if p_value is not None:
                region_source_p[entry.target_id] += p_value
                region_valid_p[entry.target_id] += 1
            if q_value is not None:
                region_source_q[entry.target_id] += q_value
                region_valid_q[entry.target_id] += 1
        for target_id, expected_count in region_expected.items():
            p_value = _finite(target_pf.bus_p_mw.get(target_id))
            q_value = _finite(target_pf.bus_q_mvar.get(target_id))
            if p_value is not None and region_valid_p[target_id] == expected_count:
                region_p_errors.append(region_source_p[target_id] - p_value)
            if q_value is not None and region_valid_q[target_id] == expected_count:
                region_q_errors.append(region_source_q[target_id] - q_value)

    interface_count = len(interface_members)
    interfaces = CrossVoltageInterfaceReport(
        interface_count=_available(interface_count, "interfaces"),
        source_branch_member_count=_available(source_interface_members if pf_ready
                                                else sum(len(v) for v in interface_members.values()),
                                                "source branch members"),
        target_branch_count=_available(len(target_interface_ids), "target branches")
            if pf_ready else not_recorded(),
        matched_interface_count=_available(matched_interfaces, "interfaces") if pf_ready else not_recorded(),
        unmatched_interface_count=_available(unmatched_interfaces, "interfaces") if pf_ready else not_recorded(),
        mapping_coverage=(_available(matched_interfaces / interface_count, "fraction")
                          if interface_count and pf_ready else not_recorded()),
        source_p_mw=_value_statistics(interface_source_p, "MW"),
        target_p_mw=_value_statistics(interface_target_p, "MW"),
        p_error_mw=_error_statistics(interface_p_error, "MW"),
        source_q_mvar=_value_statistics(interface_source_q, "MVAr"),
        target_q_mvar=_value_statistics(interface_target_q, "MVAr"),
        q_error_mvar=_error_statistics(interface_q_error, "MVAr"),
        region_net_injection_p_error_mw=_error_statistics(region_p_errors, "MW"),
        region_net_injection_q_error_mvar=_error_statistics(region_q_errors, "MVAr"),
    )
    groups = []
    for row in group_rows:
        if row.group is MetricGroup.CROSS_VOLTAGE_INTERFACES:
            row = row.model_copy(update={
                "line_p": _error_statistics(interface_p_error, "MW"),
                "line_q": _error_statistics(interface_q_error, "MVAr"),
                "injection_p": _error_statistics(region_p_errors, "MW"),
                "injection_q": _error_statistics(region_q_errors, "MVAr"),
                "compared_branch_count": _available(matched_interfaces, "interfaces")
                    if pf_ready else not_recorded(),
                "unmatched_branch_count": _available(unmatched_interfaces, "interfaces")
                    if pf_ready else not_recorded(),
                "mapping_coverage": interfaces.mapping_coverage,
            })
        groups.append(row)

    layers = _layer_rows(
        source_network, target_network, source_sidecar, target_sidecar,
        bus_entries, branch_entries, branch_members, preserved_buses,
        preserved_branches, source_pf if pf_ready else None,
        target_pf if pf_ready else None, cutoffs)
    return RetainedNetworkFidelityReport(
        source_pf_status=(_available(source_pf.status.value, "status")
                          if source_pf is not None else not_recorded()),
        target_pf_status=(_available(target_pf.status.value, "status")
                          if target_pf is not None else not_recorded()),
        mapping_digest=mappings.semantic_digest() if mappings is not None else None,
        source_pf_digest=_digest_result(source_pf),
        target_pf_digest=_digest_result(target_pf),
        groups=tuple(groups),
        interfaces=interfaces,
        layers=layers,
    )
