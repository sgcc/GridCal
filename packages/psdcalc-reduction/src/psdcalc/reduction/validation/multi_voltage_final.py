"""Final structural evidence evaluated against an explicitly reloaded quotient."""
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import math
from numbers import Real
from typing import Literal

import pandas as pd
import pypsa
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.topology.parallel_corridors import ProtectedParallelPlan
from psdcalc.reduction.topology.parallel_provenance import ParallelSourceLedger
from psdcalc.reduction.exceptions import ConfigSemanticError, IdentityError, MappingError
from psdcalc.reduction.policies.voltage_cutoff import (
    ResolvedVoltagePolicy, VoltageCutoffPolicy, voltage_policy_evidence,
)
from psdcalc.reduction.validation.multi_voltage import AggregationInvariantSpec

from psdcalc.reduction.validation.multi_voltage import MultiVoltageStructuralValidator
from psdcalc.reduction.validation.protected_parallel import (
    LineSemanticHash,
    ProtectedParallelStructuralValidator,
)


@dataclass(frozen=True)
class MultiVoltageStructuralResult:
    """Final structural status and individual persisted-artifact checks."""

    status: Literal['MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID', 'MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID']
    checks: Mapping[str, bool]
    details: Mapping[str, str]
    parallel_corridor_policy: str = "aggregate_corridor"
    protected_object_results: Mapping[str, Mapping[str, bool]] = field(default_factory=dict)


def _value(value: object) -> object:
    """Normalize one public PyPSA table value across NetCDF dtype changes."""
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, (pd.Timestamp, pd.Timedelta)):
        return value.isoformat()
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, Real):
        number = float(value)
        return ("number", number if math.isfinite(number) else str(number))
    if isinstance(value, str):
        return ("string", value)
    return (type(value).__name__, str(value))


def _frame_values(frame: pd.DataFrame) -> tuple[object, ...]:
    """Compare cells and labels while ignoring storage-only Pandas dtypes."""
    ordered = frame.sort_index().sort_index(axis=1)
    return (
        tuple(map(str, ordered.index)), tuple(map(str, ordered.columns)),
        tuple(tuple(_value(cell) for cell in row)
              for row in ordered.itertuples(index=False, name=None)),
    )


def _detached_dense_view(network: pypsa.Network) -> pypsa.Network:
    """Clone public network state and install an independent snapshot index."""
    view = network.copy()
    snapshots = network.snapshots.copy(deep=True)
    view.set_snapshots(snapshots)
    view.snapshot_weightings = network.snapshot_weightings.copy(deep=True)
    view.investment_period_weightings = (
        network.investment_period_weightings.copy(deep=True))
    return view


def network_persistence_difference(left: pypsa.Network,
                                   right: pypsa.Network) -> str | None:
    """Find the first semantic change in supported NetCDF-persisted input data.

    Regenerated SubNetwork objects and output time series are excluded. Input
    dynamic attributes use PyPSA's public dense accessor so static fallback
    and sparse dynamic columns have the same meaning after clean reload.
    """
    if not left.snapshots.equals(right.snapshots):
        return "snapshots"
    if not left.snapshot_weightings.equals(right.snapshot_weightings):
        return "snapshot_weightings"
    if not left.investment_periods.equals(right.investment_periods):
        return "investment_periods"
    if not left.investment_period_weightings.equals(
            right.investment_period_weightings):
        return "investment_period_weightings"
    ignored_static = {"sub_network", "obj"}
    left_view: pypsa.Network | None = None
    right_view: pypsa.Network | None = None
    for component in sorted(left.components, key=lambda item: item.name):
        name = component.name
        if name == "SubNetwork":
            continue
        other = right.components[name]
        a = component.static.drop(columns=list(ignored_static), errors="ignore")
        b = other.static.drop(columns=list(ignored_static), errors="ignore")
        if _frame_values(a) != _frame_values(b):
            return f"{name}.static"
        attributes = {
            attribute for view in (component, other)
            for attribute, frame in view.dynamic.items()
            if not frame.empty and len(frame.columns)
            and attribute in view.defaults.index
            and str(view.defaults.loc[attribute, "status"]).startswith("Input")
        }
        names = pd.Index(sorted(map(str, a.index)))
        for attribute in sorted(attributes):
            if attribute not in a.columns or attribute not in b.columns:
                return f"{name}.{attribute} static fallback"
            if left_view is None:
                left_view = _detached_dense_view(left)
                right_view = _detached_dense_view(right)
            try:
                dense_a = left_view.get_switchable_as_dense(
                    name, attribute, snapshots=left.snapshots, inds=names)
                dense_b = right_view.get_switchable_as_dense(
                    name, attribute, snapshots=right.snapshots, inds=names)
            except (KeyError, ValueError, AttributeError) as exc:
                return f"{name}.{attribute} dense accessor: {exc}"
            dense_a = dense_a.reindex(index=left.snapshots, columns=names).fillna(
                a.loc[names, attribute])
            dense_b = dense_b.reindex(index=right.snapshots, columns=names).fillna(
                b.loc[names, attribute])
            if _frame_values(dense_a) != _frame_values(dense_b):
                return f"{name}.{attribute} dynamic"
    return None


def network_semantic_signature(network: pypsa.Network) -> str:
    """Fingerprint public static/dynamic tables and snapshot metadata."""
    digest = hashlib.sha256()
    def add(frame: pd.DataFrame) -> None:
        digest.update(pd.util.hash_pandas_object(frame.index, index=False).values.tobytes())
        if isinstance(frame, pd.DataFrame):
            digest.update(repr(frame.columns).encode())
        digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    add(network.snapshot_weightings)
    add(network.investment_period_weightings)
    digest.update(pd.util.hash_pandas_object(network.investment_periods, index=False).values.tobytes())
    digest.update(pd.util.hash_pandas_object(network.snapshots, index=False).values.tobytes())
    for component in sorted(network.components, key=lambda c: c.name):
        digest.update(component.name.encode())
        add(component.static.drop(columns=["obj"], errors="ignore"))
        for attribute, frame in sorted(component.dynamic.items()):
            digest.update(attribute.encode())
            add(frame)
    return digest.hexdigest()


class MultiVoltageFinalValidator:
    """Keep Builder prechecks distinct from post-persistence final evidence."""

    def validate(self, parent_network: pypsa.Network, quotient_network: pypsa.Network,
                 parent_sidecar: IdentitySidecar, target_sidecar: IdentitySidecar,
                 mappings: MappingBundle, invariants: tuple[AggregationInvariantSpec, ...],
                 protection: ProtectionSet, *,
                 voltage_layer_map: Mapping[StableId, tuple[float, float]],
                 original_network: pypsa.Network, original_sidecar: IdentitySidecar,
                 composed_mappings: MappingBundle,
                 original_input_signature: str,
                 built_network: pypsa.Network | None = None,
                 parallel_plan: ProtectedParallelPlan | None = None,
                 source_ledger: ParallelSourceLedger | None = None,
                 persisted_ledger: ParallelSourceLedger | None = None,
                 parallel_mapping_round_trip: bool = True,
                 parallel_sidecar_round_trip: bool = True,
                 parallel_corridor_policy: str = "aggregate_corridor",
                 voltage_policy_request: VoltageCutoffPolicy | None = None,
                 voltage_policy_persisted: Mapping[str, object] | None = None,
                 voltage_policy_source_network: pypsa.Network | None = None,
                 voltage_policy_source_sidecar: IdentitySidecar | None = None,
                 voltage_policy_round_trip_ready: bool = False,
                 ) -> MultiVoltageStructuralResult:
        """Validate reloaded structure; propagate unsupported configuration errors."""
        checks = {}
        details = {}
        try:
            preliminary = MultiVoltageStructuralValidator().precheck(
                parent_network, quotient_network, parent_sidecar, target_sidecar,
                mappings, invariants, protection,
                voltage_layer_map=voltage_layer_map,
                allowed_line_dynamic_names=frozenset(
                    parent_sidecar.backend_name(line_id)
                    for line_id in parallel_plan.protected_lines)
                if parallel_plan is not None else frozenset())
            checks.update(preliminary.checks)
            details.update(preliminary.details)
        except (IdentityError, MappingError, KeyError) as exc:
            checks['reloaded_precheck'] = False
            details['reloaded_precheck'] = f'{type(exc).__name__}: {exc}'
        protected_object_results: dict[str, dict[str, bool]] = {}
        if parallel_plan is not None:
            busmap = {row.source_id: row.target_id
                      for row in mappings.bus_map if row.target_id is not None}
            parallel_checks, parallel_details = (
                ProtectedParallelStructuralValidator().check(
                    parent_network, quotient_network, parent_sidecar,
                    target_sidecar, mappings, parallel_plan, busmap,
                    source_ledger=source_ledger,
                    persisted_ledger=persisted_ledger,
                    protection=protection,
                    mapping_round_trip=parallel_mapping_round_trip,
                    sidecar_round_trip=parallel_sidecar_round_trip,
                    object_results=protected_object_results))
            checks.update(parallel_checks)
            details.update(parallel_details)
        checks['input_semantic_immutability'] = (
            network_semantic_signature(original_network) == original_input_signature)
        if built_network is not None:
            difference = network_persistence_difference(
                built_network, quotient_network)
            checks['netcdf_round_trip'] = difference is None
            if difference is not None:
                details['netcdf_round_trip'] = difference
        checks['investment_period_weightings'] = original_network.investment_period_weightings.equals(quotient_network.investment_period_weightings)
        checks['investment_periods'] = original_network.investment_periods.equals(quotient_network.investment_periods)
        checks['snapshots'] = original_network.snapshots.equals(quotient_network.snapshots)
        checks['snapshot_weightings'] = original_network.snapshot_weightings.equals(
            quotient_network.snapshot_weightings)
        supported = {'Bus', 'Line', 'Transformer', 'Link', 'Generator', 'Load',
                     'StorageUnit', 'Store', 'ShuntImpedance'}
        try:
            expected = {original_sidecar.source_id(c.name, str(name))
                        for c in original_network.components if c.name in supported
                        for name in c.static.index}
            rows = (composed_mappings.bus_map + composed_mappings.branch_map +
                    composed_mappings.device_map)
            checks['original_mapping_coverage'] = (
                len(rows) == len(expected) and {r.source_id for r in rows} == expected)
            actual = {target_sidecar.source_id(c.name, str(name))
                      for c in quotient_network.components if c.name in supported
                      for name in c.static.index}
            bindings = {r['source_id'] for r in target_sidecar.to_rows()
                        if r['component'] in supported}
            checks['target_sidecar_coverage'] = bindings == {sid.encode() for sid in actual}
            checks['composed_target_existence'] = all(
                r.target_id is None or r.target_id in actual for r in rows)
            checks['target_provenance_coverage'] = actual <= {
                row.target_id for row in rows if row.target_id is not None
            }
            checks['composed_bus_targets'] = all(
                r.target_id in actual for r in composed_mappings.bus_map)
            original_controls = {
                item.source_id for item in PypsaAdapter().inventory(
                    original_network, original_sidecar).items if item.controllable
            }
            controls = composed_mappings.control_map
            owners = {row.source_id: row for row in composed_mappings.device_map}
            checks['composed_control_coverage'] = (
                len(controls) == len(original_controls)
                and {row.source_id for row in controls} == original_controls)
            checks['composed_control_owner_consistency'] = all(
                row.source_id in owners
                and row.target_id == owners[row.source_id].target_id
                and row.mapping_type == owners[row.source_id].mapping_type
                and row.target_id in actual
                for row in controls)
            original_transformers = {
                original_sidecar.source_id('Transformer', str(name))
                for name in original_network.transformers.index
            }
            transformer_rows = composed_mappings.transformer_map
            checks['composed_transformer_identity'] = (
                len(transformer_rows) == len(original_transformers)
                and {row.source_id for row in transformer_rows}
                == original_transformers
                and all(row.target_id == row.source_id
                        and row.mapping_type == 'preserved'
                        for row in transformer_rows))
        except (IdentityError, MappingError, KeyError) as exc:
            checks['original_mapping_coverage'] = False
            details['original_mapping_coverage'] = f'{type(exc).__name__}: {exc}'
        policy_resolved: ResolvedVoltagePolicy | None = None
        policy_source = voltage_policy_source_network
        policy_sidecar = voltage_policy_source_sidecar
        try:
            if (voltage_policy_request is None or voltage_policy_persisted is None
                    or policy_source is None or policy_sidecar is None):
                raise ValueError("Persisted voltage-policy inputs are missing")
            policy_resolved = voltage_policy_request.resolve(policy_source, policy_sidecar)
            checks['voltage_policy_resolved'] = _resolved_policy_evidence_matches(
                voltage_policy_request, policy_source, policy_sidecar,
                voltage_policy_persisted)
            if not checks['voltage_policy_resolved']:
                details['voltage_policy_resolved'] = (
                    "Persisted request, source layers, StableIds, counts, or "
                    "recomputed fingerprint do not match authoritative source")
            checks['voltage_policy_voltage_membership'] = (
                _voltage_policy_membership_is_valid(
                    voltage_policy_request, policy_resolved,
                    policy_source, policy_sidecar))
            checks['voltage_policy_singleton_buses'] = (
                _retained_bus_mappings_are_identity(
                    policy_resolved, composed_mappings, quotient_network,
                    target_sidecar))
            checks['voltage_policy_line_identity'] = (
                _retained_line_mappings_are_identity(
                    policy_resolved, policy_source, policy_sidecar,
                    quotient_network, target_sidecar, composed_mappings))
        except (ConfigSemanticError, IdentityError, MappingError, KeyError,
                TypeError, ValueError, AttributeError) as exc:
            checks['voltage_policy_resolved'] = False
            checks['voltage_policy_voltage_membership'] = False
            checks['voltage_policy_singleton_buses'] = False
            checks['voltage_policy_line_identity'] = False
            details['voltage_policy_resolved'] = f'{type(exc).__name__}: {exc}'
        checks['voltage_policy_round_trip'] = (
            voltage_policy_round_trip_ready
            and checks.get('voltage_policy_resolved', False)
            and checks.get('voltage_policy_voltage_membership', False)
            and checks.get('voltage_policy_singleton_buses', False)
            and checks.get('voltage_policy_line_identity', False)
        )
        if not checks['voltage_policy_round_trip']:
            details.setdefault(
                'voltage_policy_round_trip',
                "Persisted config, source, sidecars, mappings, policy evidence, "
                "and final NetCDF were not reconstructed consistently")
        return MultiVoltageStructuralResult(
            'MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID' if all(checks.values()) else 'MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID',
            checks, details, parallel_corridor_policy, protected_object_results)


def _voltage_policy_request_is_valid(policy: VoltageCutoffPolicy) -> bool:
    """Check that the persisted request has valid BASE or cutoff semantics."""
    if policy.enabled:
        return (policy.mode == "freeze_at_or_above"
                and policy.cutoff_kv is not None
                and math.isfinite(policy.cutoff_kv)
                and policy.cutoff_kv > 0)
    return policy.mode == "freeze_at_or_above" and policy.cutoff_kv is None


def _resolved_policy_evidence_matches(
    policy: VoltageCutoffPolicy,
    source_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    persisted: Mapping[str, object],
) -> bool:
    """Recompute and compare the entire request, resolution, and fingerprint."""
    if not _voltage_policy_request_is_valid(policy):
        return False
    resolved = policy.resolve(source_network, source_sidecar)
    return dict(persisted) == voltage_policy_evidence(policy, resolved)


def _voltage_policy_membership_is_valid(
    policy: VoltageCutoffPolicy,
    resolved: ResolvedVoltagePolicy,
    network: pypsa.Network,
    sidecar: IdentitySidecar,
) -> bool:
    """Verify complete, disjoint membership from authoritative source v_nom."""
    source_buses = {
        sidecar.source_id("Bus", str(name)): float(row.v_nom)
        for name, row in network.buses.iterrows()
    }
    retained = set(resolved.retained_bus_ids)
    compressible = set(source_buses) - retained
    if retained & compressible or retained | compressible != set(source_buses):
        return False
    if not policy.enabled:
        return (not retained and not resolved.retained_line_ids
                and resolved.retained_bus_count == 0
                and resolved.retained_line_count == 0)
    cutoff = float(policy.cutoff_kv)
    return (all(source_buses[sid] >= cutoff for sid in retained)
            and all(source_buses[sid] < cutoff for sid in compressible)
            and tuple(sorted(set(resolved.source_voltage_layers)))
            == resolved.source_voltage_layers
            and tuple(sorted(set(resolved.retained_voltage_layers)))
            == resolved.retained_voltage_layers
            and tuple(sorted(set(resolved.compressible_voltage_layers)))
            == resolved.compressible_voltage_layers)


def _retained_bus_mappings_are_identity(
    resolved: ResolvedVoltagePolicy,
    mappings: MappingBundle,
    final_network: pypsa.Network,
    target_sidecar: IdentitySidecar,
) -> bool:
    """Require retained source Buses to map one-to-one to themselves."""
    targets: dict[StableId, list[StableId]] = {}
    for row in mappings.bus_map:
        if row.target_id is not None:
            targets.setdefault(row.target_id, []).append(row.source_id)
    final_ids = {
        target_sidecar.source_id("Bus", str(name))
        for name in final_network.buses.index
    }
    for source_id in resolved.retained_bus_ids:
        rows = [row for row in mappings.bus_map if row.source_id == source_id]
        if (len(rows) != 1 or rows[0].mapping_type != "preserved"
                or rows[0].target_id != source_id
                or rows[0].target_id not in final_ids
                or targets.get(source_id) != [source_id]):
            return False
    return True


def _retained_line_mappings_are_identity(
    resolved: ResolvedVoltagePolicy,
    source_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    final_network: pypsa.Network,
    target_sidecar: IdentitySidecar,
    mappings: MappingBundle,
) -> bool:
    """Require retained source Lines to map and hash-identically after reload."""
    line_hasher = LineSemanticHash()
    final_ids = {
        target_sidecar.source_id("Line", str(name))
        for name in final_network.lines.index
    }
    for source_id in resolved.retained_line_ids:
        rows = [row for row in mappings.branch_map if row.source_id == source_id]
        if (len(rows) != 1 or rows[0].mapping_type != "preserved"
                or rows[0].target_id != source_id
                or rows[0].target_id not in final_ids):
            return False
        if (line_hasher.digest(source_network, source_sidecar, source_id)
                != line_hasher.digest(final_network, target_sidecar, source_id)):
            return False
    return True
