"""Electrical parameter preservation and parallel-line equivalencing."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math

import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import MaterializationError
from psdcalc.reduction.mapping.bundle import MappingBundle

_ONE_TO_ONE_LINE_FIELDS = (
    "length", "carrier", "terrain_factor", "s_nom", "s_nom_extendable",
    "s_nom_min", "s_nom_max", "s_min_pu", "s_max_pu", "capital_cost",
    "v_ang_min", "v_ang_max",
)


@dataclass(frozen=True)
class EffectiveLineParameters:
    """PyPSA-effective static electrical values for one Line member."""

    r: float
    x: float
    g: float
    b: float
    num_parallel: float
    line_type: str
    length: float
    active: bool

    @property
    def series_impedance(self) -> complex:
        """Return the effective series impedance in PyPSA Line units."""
        return complex(self.r, self.x)


def effective_line_parameters(network: pypsa.Network) -> Mapping[str, EffectiveLineParameters]:
    """Resolve type-backed Line values on a detached copy through PyPSA's public API.

    PyPSA 1.2.x applies ``LineType`` values using ``length`` and
    ``num_parallel`` during ``calculate_dependent_values``. Explicit values on
    untyped Lines are already effective and are left as supplied by PyPSA.
    """
    if network.lines.empty:
        return {}
    resolved = PypsaAdapter().clone(network)
    try:
        resolved.calculate_dependent_values()
    except Exception as exc:
        raise MaterializationError(
            "AC_LINE_EQUIVALENCE_UNSUPPORTED: PyPSA could not resolve LineType values"
        ) from exc
    output: dict[str, EffectiveLineParameters] = {}
    for name, row in resolved.lines.iterrows():
        try:
            output[str(name)] = EffectiveLineParameters(
                r=float(row.r), x=float(row.x), g=float(row.g), b=float(row.b),
                num_parallel=float(row.num_parallel), line_type=str(row.type),
                length=float(row.length), active=bool(row.active),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise MaterializationError(
                "AC_LINE_EQUIVALENCE_UNSUPPORTED: Line electrical values are invalid"
            ) from exc
    return output


def equivalent_parallel_line_parameters(
    members: Sequence[EffectiveLineParameters],
) -> EffectiveLineParameters:
    """Return an explicit equivalent for active parallel Line members.

    Series admittances and shunt admittances are summed. ``num_parallel`` has
    already been applied by PyPSA for type-backed Lines; explicit untyped
    ``r/x/g/b`` values are treated as effective values. The equivalent is
    emitted as an untyped, single Line to avoid applying member multiplicity a
    second time.
    """
    active = tuple(member for member in members if member.active)
    if not active:
        raise MaterializationError(
            "AC_LINE_EQUIVALENCE_UNSUPPORTED: an all-inactive corridor has no active equivalent"
        )
    if len(active) != len(members):
        # Inactive members remain in source lineage but cannot contribute to the
        # active target's electrical equivalent.
        pass
    admittance = 0.0j
    shunt = 0.0j
    for member in active:
        impedance = member.series_impedance
        values = (member.r, member.x, member.g, member.b)
        if not all(math.isfinite(value) for value in values):
            raise MaterializationError(
                "AC_LINE_EQUIVALENCE_UNSUPPORTED: member parameters are nonfinite"
            )
        if impedance == 0.0j:
            raise MaterializationError(
                "AC_LINE_EQUIVALENCE_UNSUPPORTED: active member has zero series impedance"
            )
        admittance += 1.0 / impedance
        shunt += complex(member.g, member.b)
    if not (math.isfinite(admittance.real) and math.isfinite(admittance.imag)) or admittance == 0.0j:
        raise MaterializationError(
            "AC_LINE_EQUIVALENCE_UNSUPPORTED: parallel series admittance is invalid"
        )
    impedance = 1.0 / admittance
    if not (math.isfinite(impedance.real) and math.isfinite(impedance.imag)):
        raise MaterializationError(
            "AC_LINE_EQUIVALENCE_UNSUPPORTED: equivalent series impedance is nonfinite"
        )
    return EffectiveLineParameters(
        r=impedance.real, x=impedance.imag, g=shunt.real, b=shunt.imag,
        num_parallel=1.0, line_type="", length=0.0, active=True,
    )


def materialize_line_electrical_parameters(
    source_network: pypsa.Network,
    source_sidecar: IdentitySidecar,
    target_network: pypsa.Network,
    mappings: MappingBundle,
    target_names: Mapping[StableId, tuple[str, str]],
) -> None:
    """Restore one-to-one Line semantics and equivalence mapped corridors.

    ``mappings`` is the just-created per-layer quotient map. Source Lines with
    no target are internal absorbed branches and do not contribute to a target
    corridor. Detached protected Lines are intentionally absent here and are
    reconnected by their dedicated identity-preservation handler.
    """
    if source_network.lines.empty or target_network.lines.empty:
        return
    effective = effective_line_parameters(source_network)
    groups: dict[StableId, list[str]] = defaultdict(list)
    for entry in mappings.branch_map:
        if entry.source_id.component != "Line" or entry.target_id is None:
            continue
        if entry.target_id not in target_names:
            raise MaterializationError(
                "AC_LINE_EQUIVALENCE_UNSUPPORTED: mapped Line target has no backend name"
            )
        component, target_name = target_names[entry.target_id]
        if component != "Line" or target_name not in target_network.lines.index:
            raise MaterializationError(
                "AC_LINE_EQUIVALENCE_UNSUPPORTED: mapped Line target does not exist"
            )
        source_name = source_sidecar.backend_name(entry.source_id)
        if source_name not in effective:
            raise MaterializationError(
                "AC_LINE_EQUIVALENCE_UNSUPPORTED: mapped source Line does not exist"
            )
        groups[entry.target_id].append(source_name)

    for target_id, source_names in groups.items():
        _, target_name = target_names[target_id]
        members = [effective[name] for name in source_names]
        if len(members) == 1:
            equivalent = members[0]
            source_row = source_network.lines.loc[source_names[0]]
            for field in _ONE_TO_ONE_LINE_FIELDS:
                if (field in source_network.lines.columns
                        and field in target_network.lines.columns):
                    target_network.lines.at[target_name, field] = source_row[field]
        else:
            equivalent = equivalent_parallel_line_parameters(members)
        target_network.lines.loc[target_name, [
            "r", "x", "g", "b", "num_parallel", "type", "active",
        ]] = [
            equivalent.r, equivalent.x, equivalent.g, equivalent.b,
            equivalent.num_parallel, equivalent.line_type, equivalent.active,
        ]
