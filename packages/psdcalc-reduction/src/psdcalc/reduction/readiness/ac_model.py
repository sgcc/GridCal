"""AC model-readiness checks kept separate from structural validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Mapping

import pypsa

from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.exceptions import IdentityError, MaterializationError
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.materializers.line_equivalence import (
    EffectiveLineParameters,
    effective_line_parameters,
)


class AcModelReadinessStatus(str, Enum):
    """Result states for input readiness to a PyPSA AC power flow."""

    AC_MODEL_READY = "AC_MODEL_READY"
    AC_MODEL_NOT_READY = "AC_MODEL_NOT_READY"


@dataclass(frozen=True)
class AcModelReadinessResult:
    """Named AC model readiness checks, independent of structural status."""

    status: AcModelReadinessStatus
    checks: Mapping[str, bool]
    details: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible anonymous readiness summary."""
        return {
            "status": self.status.value,
            "checks": dict(self.checks),
            "details": dict(self.details),
        }


class AcModelReadinessValidator:
    """Check finite and lineage-consistent Line parameters before AC PF.

    This validator does not run power flow and does not change the structural
    Gate. It resolves LineType values on detached network copies through the
    supported PyPSA API, then compares each target branch with its mapped
    active source members.
    """

    _REL_TOL = 1e-9
    _ABS_TOL = 1e-12

    def validate(
        self,
        source_network: pypsa.Network,
        source_sidecar: IdentitySidecar,
        target_network: pypsa.Network,
        target_sidecar: IdentitySidecar,
        mappings: MappingBundle,
    ) -> AcModelReadinessResult:
        """Return named readiness checks for mapped Line electrical models."""
        checks = {
            "line_parameters_finite": True,
            "line_series_impedance_nonzero": True,
            "line_equivalent_admittance_consistency": True,
            "line_shunt_admittance_consistency": True,
            "line_target_provenance_coverage": True,
        }
        details: dict[str, str] = {}
        try:
            source_parameters = effective_line_parameters(source_network)
            target_parameters = effective_line_parameters(target_network)
        except MaterializationError as exc:
            checks.update({name: False for name in checks})
            details["parameter_resolution"] = str(exc)
            return self._result(checks, details)

        source_groups: dict[StableId, list[EffectiveLineParameters]] = {}
        target_group_names: dict[StableId, list[str]] = {}
        for row in mappings.branch_map:
            if row.source_id.component != "Line" or row.target_id is None:
                continue
            try:
                source_name = source_sidecar.backend_name(row.source_id)
                target_name = target_sidecar.backend_name(row.target_id)
            except IdentityError:
                checks["line_target_provenance_coverage"] = False
                continue
            if source_name not in source_parameters or target_name not in target_parameters:
                checks["line_target_provenance_coverage"] = False
                continue
            source_groups.setdefault(row.target_id, []).append(
                source_parameters[source_name]
            )
            target_group_names.setdefault(row.target_id, []).append(target_name)

        mapped_targets: set[StableId] = set()
        for name in target_network.lines.index:
            try:
                mapped_targets.add(target_sidecar.source_id("Line", str(name)))
            except IdentityError:
                checks["line_target_provenance_coverage"] = False
        if mapped_targets != set(target_group_names):
            checks["line_target_provenance_coverage"] = False

        active_target = [
            values for values in target_parameters.values() if values.active
        ]
        checks["line_parameters_finite"] &= all(
            self._finite(values) for values in active_target
        )
        checks["line_series_impedance_nonzero"] &= all(
            values.series_impedance != 0.0j for values in active_target
        )

        for target_id, source_members in source_groups.items():
            target_names = set(target_group_names[target_id])
            if len(target_names) != 1:
                checks["line_target_provenance_coverage"] = False
                continue
            target = target_parameters[next(iter(target_names))]
            active_source = [member for member in source_members if member.active]
            if not active_source:
                if target.active:
                    checks["line_target_provenance_coverage"] = False
                continue
            checks["line_parameters_finite"] &= all(
                self._finite(member) for member in active_source
            )
            source_impedances = [member.series_impedance for member in active_source]
            if any(value == 0.0j for value in source_impedances):
                checks["line_series_impedance_nonzero"] = False
                checks["line_equivalent_admittance_consistency"] = False
                checks["line_shunt_admittance_consistency"] = False
                continue
            expected_series_admittance = sum(
                (1.0 / value for value in source_impedances), 0.0j
            )
            target_series_admittance = (
                1.0 / target.series_impedance
                if target.series_impedance != 0.0j else complex(math.inf, math.inf)
            )
            checks["line_series_impedance_nonzero"] &= (
                target.active and target.series_impedance != 0.0j
            )
            checks["line_equivalent_admittance_consistency"] &= (
                target.active
                and self._close(expected_series_admittance, target_series_admittance)
            )
            expected_shunt = sum(
                (complex(member.g, member.b) for member in active_source), 0.0j
            )
            target_shunt = complex(target.g, target.b)
            checks["line_shunt_admittance_consistency"] &= (
                target.active and self._close(expected_shunt, target_shunt)
            )

        details.update({
            "source_active_line_count": str(sum(value.active for value in source_parameters.values())),
            "target_active_line_count": str(len(active_target)),
            "mapped_target_line_count": str(len(target_group_names)),
            "unmapped_target_line_count": str(len(mapped_targets - set(target_group_names))),
        })
        return self._result(checks, details)

    @staticmethod
    def _finite(values: EffectiveLineParameters) -> bool:
        """Return whether all supported electrical values are finite."""
        return all(math.isfinite(value) for value in (values.r, values.x, values.g, values.b))

    @classmethod
    def _close(cls, actual: complex, expected: complex) -> bool:
        """Compare complex admittances with a fixed numerical readiness tolerance."""
        if not all(math.isfinite(value) for value in (
            actual.real, actual.imag, expected.real, expected.imag,
        )):
            return False
        return abs(actual - expected) <= cls._ABS_TOL + cls._REL_TOL * max(
            abs(actual), abs(expected)
        )

    @staticmethod
    def _result(
        checks: Mapping[str, bool], details: Mapping[str, str],
    ) -> AcModelReadinessResult:
        """Create a result whose status reflects only named AC readiness checks."""
        status = (
            AcModelReadinessStatus.AC_MODEL_READY
            if all(checks.values())
            else AcModelReadinessStatus.AC_MODEL_NOT_READY
        )
        return AcModelReadinessResult(status, dict(checks), dict(details))
