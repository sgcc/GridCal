"""ADR-009 parallel corridor policy configuration contract."""

import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.loader import load_config
from psdcalc.reduction.config.models import AggregationConfig, ParallelCorridorConfig
from psdcalc.reduction.exceptions import ConfigSchemaError, UnsupportedFeatureError
from psdcalc.reduction.materializers.multi_voltage import MultiVoltageQuotientBuilder
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.protection.engine import ProtectionSet


def _config(policy: str) -> dict:
    """Build a minimal multi-voltage configuration for policy validation."""
    return {
        "schema_version": "0.1.0",
        "run": {"id": "parallel", "seed": 1},
        "input": {"backend": "pypsa", "path": "small.nc", "format": "netcdf",
                  "source_namespace": "test"},
        "task": {"type": "ac_pf", "profile": "ac_pf_strict",
                 "reduction_role": "warm_start",
                 "final_authority": "full_grid_independent_pf"},
        "pipeline": {"materialization_family": "multi_voltage_quotient"},
        "partition": {"planner": "pypsa_modularity", "target": {"n_clusters": 2}},
        "simplification": {"operations": [
            {"id": "topology", "type": "voltage_aware_topology", "enabled": True,
             "mode": "keep_transformer_interface"},
        ]},
        "aggregation": {"parallel_corridors": {"policy": policy}},
    }


def test_parallel_policy_defaults_to_aggregate() -> None:
    """The 0.2.0 behavior remains the implicit default."""
    assert AggregationConfig().parallel_corridors.policy == "aggregate_corridor"


def test_protected_parallel_policy_is_explicitly_selectable() -> None:
    """The first authorized strategy parses through the strict config."""
    assert (load_config(_config("preserve_protected_members"))
            .aggregation.parallel_corridors.policy == "preserve_protected_members")


def test_preserve_all_members_is_explicitly_unsupported() -> None:
    """A known future policy cannot silently use the default."""
    with pytest.raises(UnsupportedFeatureError, match="preserve_all_members"):
        load_config(_config("preserve_all_members"))


def test_unknown_parallel_policy_is_schema_error() -> None:
    """Strict policy enumeration does not accept aliases or guesses."""
    with pytest.raises(ConfigSchemaError):
        load_config(_config("preserve_some_members"))


def test_direct_builder_cannot_bypass_preserve_all_feature_gate() -> None:
    """A constructed Pydantic model still cannot select an absent handler."""
    n = pypsa.Network()
    n.add("Bus", "a", v_nom=110)
    sidecar = PypsaAdapter().identity_sidecar(n, "test")
    sid = sidecar.source_id("Bus", "a")
    plan = PartitionPlan({sid: sid}, {"a": "a"}, 1, 1, "reject")
    aggregation = AggregationConfig(parallel_corridors=ParallelCorridorConfig(
        policy="preserve_all_members"))
    protection = ProtectionSet(*[frozenset() for _ in range(5)])
    with pytest.raises(UnsupportedFeatureError, match="preserve_all_members"):
        MultiVoltageQuotientBuilder().build(
            n, sidecar, plan, aggregation, protection)
