"""Tests for the independent AC model readiness boundary."""

from __future__ import annotations

import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import AggregationConfig
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.materializers.multi_voltage import MultiVoltageQuotientBuilder
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.protection.engine import ProtectionEngine
from psdcalc.reduction.readiness.ac_model import (
    AcModelReadinessStatus,
    AcModelReadinessValidator,
)


def _ready_case() -> tuple[pypsa.Network, object, object]:
    """Build a small one-layer network and its identity-busmap quotient."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110.0)
    network.add("Bus", "b", v_nom=110.0)
    network.add("Line", "line", bus0="a", bus1="b", r=0.02, x=0.08,
                g=0.0001, b=0.0002, s_nom=100.0)
    network.add("Generator", "slack", bus="a", control="Slack", p_nom=100.0)
    network.add("Load", "load", bus="b", p_set=10.0, q_set=2.0)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "readiness")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    stable = {
        sidecar.source_id("Bus", str(name)): StableId("readiness", "Bus", str(name))
        for name in network.buses.index
    }
    backend = {str(name): stable[sidecar.source_id("Bus", str(name))].encode()
               for name in network.buses.index}
    plan = PartitionPlan(stable, backend, len(stable), len(stable), "reject")
    quotient = MultiVoltageQuotientBuilder().build(
        network, sidecar, plan,
        AggregationConfig(aggregate_one_ports=["Load"]), protection,
    )
    return network, sidecar, quotient


def test_ac_readiness_accepts_equivalent_materialized_line() -> None:
    """A finite, nonzero equivalent model passes its named checks."""
    source, source_sidecar, quotient = _ready_case()

    result = AcModelReadinessValidator().validate(
        source, source_sidecar, quotient.network, quotient.target_sidecar,
        quotient.mappings,
    )

    assert result.status is AcModelReadinessStatus.AC_MODEL_READY
    assert all(result.checks.values())
    assert result.checks["line_parameters_finite"]
    assert result.checks["line_series_impedance_nonzero"]
    assert result.checks["line_equivalent_admittance_consistency"]
    assert result.checks["line_shunt_admittance_consistency"]


def test_ac_readiness_rejects_materialized_zero_series_impedance() -> None:
    """A nonzero source Line cannot silently become an active zero-Z target."""
    source, source_sidecar, quotient = _ready_case()
    target_map = next(row for row in quotient.mappings.branch_map
                      if row.source_id.component == "Line")
    assert target_map.target_id is not None
    target_name = quotient.target_sidecar.backend_name(target_map.target_id)
    quotient.network.lines.loc[target_name, ["r", "x"]] = [0.0, 0.0]

    result = AcModelReadinessValidator().validate(
        source, source_sidecar, quotient.network, quotient.target_sidecar,
        quotient.mappings,
    )

    assert result.status is AcModelReadinessStatus.AC_MODEL_NOT_READY
    assert result.checks["line_parameters_finite"]
    assert not result.checks["line_series_impedance_nonzero"]
    assert not result.checks["line_equivalent_admittance_consistency"]


def test_ac_readiness_rejects_nonfinite_active_line_parameters() -> None:
    """The readiness boundary reports nonfinite target electrical values."""
    source, source_sidecar, quotient = _ready_case()
    target_map = next(row for row in quotient.mappings.branch_map
                      if row.source_id.component == "Line")
    assert target_map.target_id is not None
    target_name = quotient.target_sidecar.backend_name(target_map.target_id)
    quotient.network.lines.loc[target_name, "x"] = float("nan")

    result = AcModelReadinessValidator().validate(
        source, source_sidecar, quotient.network, quotient.target_sidecar,
        quotient.mappings,
    )

    assert result.status is AcModelReadinessStatus.AC_MODEL_NOT_READY
    assert not result.checks["line_parameters_finite"]


def test_ac_readiness_checks_shunt_admittance_separately() -> None:
    """A shunt mismatch fails its own named readiness check."""
    source, source_sidecar, quotient = _ready_case()
    target_map = next(row for row in quotient.mappings.branch_map
                      if row.source_id.component == "Line")
    assert target_map.target_id is not None
    target_name = quotient.target_sidecar.backend_name(target_map.target_id)
    quotient.network.lines.loc[target_name, "b"] += 0.01

    result = AcModelReadinessValidator().validate(
        source, source_sidecar, quotient.network, quotient.target_sidecar,
        quotient.mappings,
    )

    assert result.status is AcModelReadinessStatus.AC_MODEL_NOT_READY
    assert result.checks["line_parameters_finite"]
    assert result.checks["line_equivalent_admittance_consistency"]
    assert not result.checks["line_shunt_admittance_consistency"]
