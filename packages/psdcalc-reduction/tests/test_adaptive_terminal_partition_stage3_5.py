"""Production Stage-3/4 terminal partition tests over synthetic PyPSA cases."""

from __future__ import annotations

from pathlib import Path

import pypsa
from psdcalc.reduction.adaptive.actions import (
    ActionOperation,
    MappingTransition,
    ReductionAction,
)
from psdcalc.reduction.adaptive.contracts import (
    AdaptiveReductionConfig,
    AdaptiveStrategy,
    AdaptiveStoppingReason,
    AttributionStatus,
    SCHEMA_VERSION,
)
from psdcalc.reduction.adaptive.candidates import PypsaAdaptiveCandidateBuilder
from psdcalc.reduction.adaptive.controller import AdaptiveReductionController
from psdcalc.reduction.adaptive.pypsa_runtime import PypsaAdaptiveRuntime
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.loader import load_config
from psdcalc.reduction.core.ids import IdentitySidecar
from psdcalc.reduction.fidelity.contracts import (
    PowerFlowMetricPolicy,
    PowerFlowOperatingPointContract,
)
from psdcalc.reduction.fidelity.persistence import FrozenThresholdSet
from psdcalc.reduction.protection.engine import ProtectionSet


def _case(
    tmp_path: Path,
    *,
    active_impedance: tuple[float, float],
    load_p: float,
    load_q: float,
    protected: bool = False,
) -> tuple[PypsaAdaptiveRuntime, tuple[ReductionAction, ...], tuple[str, ...]]:
    """Build a real PyPSA source, replay action, fidelity runtime, and controller config."""
    network = pypsa.Network()
    for bus in ("a", "b", "c"):
        network.add("Bus", bus, v_nom=110.0)
    network.add("Line", "ab", bus0="a", bus1="b", r=active_impedance[0],
                x=active_impedance[1], s_nom=1000.0)
    network.add("Line", "bc", bus0="b", bus1="c", r=active_impedance[0],
                x=active_impedance[1], s_nom=1000.0)
    network.add("Generator", "slack", bus="c", control="Slack", p_nom=1000.0)
    network.add("Load", "load", bus="a", p_set=load_p, q_set=load_q)
    sidecar = PypsaAdapter().identity_sidecar(network, "stage3-5")

    bus_a = sidecar.source_id("Bus", "a")
    bus_b = sidecar.source_id("Bus", "b")
    line_ab = sidecar.source_id("Line", "ab")
    action = ReductionAction.create(
        action_id="collapse-ab",
        operation=ActionOperation.RADIAL_ABSORPTION,
        source_ids=(bus_a, bus_b, line_ab),
        target_ids=(bus_a,),
        mapping_delta=(
            MappingTransition(bus_a, bus_a, "preserved"),
            MappingTransition(bus_b, bus_a, "clustered"),
            MappingTransition(line_ab, None, "absorbed_internal"),
        ),
        voltage_layer="110",
        component_id="synthetic-component",
        region_fingerprint="synthetic-region",
        audit_reason="programmatic radial-resolution reduction",
        protected=protected,
    )
    actions = (action,)
    reduction_config = load_config({
        "schema_version": "0.1.0",
        "run": {"id": "stage3-5", "seed": 7, "output_dir": str(tmp_path / "runs")},
        "input": {"backend": "pypsa", "path": str(tmp_path / "s1.nc"),
                  "format": "netcdf", "source_namespace": "stage3-5"},
        "task": {"type": "planning", "profile": "planning_aggregation",
                 "reduction_role": "candidate", "final_authority": "original"},
        "pipeline": {"materialization_family": "multi_voltage_quotient"},
        "simplification": {"operations": [{
            "id": "topology", "type": "voltage_aware_topology",
            "enabled": True, "mode": "keep_transformer_interface",
        }]},
        "partition": {"planner": "pypsa_kmeans", "target": {"n_clusters": 2},
                      "connectivity_policy": "reject"},
        "aggregation": {"backend": "pypsa"},
    })
    protection = ProtectionSet(
        frozenset(), frozenset(), frozenset(), frozenset(), frozenset())
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=network,
        source_sidecar=sidecar,
        actions=actions,
        reduction_config=reduction_config,
        protection=protection,
    )
    thresholds = FrozenThresholdSet.load()
    operating_point = PowerFlowOperatingPointContract(
        snapshots=tuple(network.snapshots),
        solver="pypsa.newton_raphson",
        tolerance=1e-8,
        max_iterations=None,
        distribute_slack=False,
        slack_weights=None,
        use_seed=False,
        control_policy="preserve_source_controls",
        tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest="0" * 64,
        device_schedule_digest="0" * 64,
    )
    runtime = PypsaAdaptiveRuntime(
        source_network=network,
        source_sidecar=sidecar,
        builder=builder,
        actions=actions,
        reduction_config_digest=builder.reduction_config_digest,
        operating_point=operating_point,
        metric_policy=PowerFlowMetricPolicy(**thresholds.payload["metric_policy"]),
        threshold_set=thresholds,
    )
    return runtime, actions, (action.action_id,)


def _run(
    runtime: PypsaAdaptiveRuntime,
    actions: tuple[ReductionAction, ...],
    active_action_ids: tuple[str, ...],
    *,
    improvement_tolerance: float = 1e-12,
):
    """Run the real Stage-4 controller with the runtime's frozen evidence bindings."""
    config = AdaptiveReductionConfig(
        schema_version=SCHEMA_VERSION,
        reduction_config_digest=runtime.reduction_config_digest,
        operating_point_digest=runtime.operating_point_digest,
        solver_contract_digest=runtime.solver_contract_digest,
        metric_policy_digest=runtime.metric_policy_digest,
        threshold_set_id=runtime.threshold_set_id,
        threshold_digest=runtime.threshold_digest,
        max_iterations=4,
        cost_bus_weight=1.0,
        cost_branch_weight=1.0,
        strategy=AdaptiveStrategy.HIERARCHICALLY_CONSTRAINED_GREEDY_LOCAL_EXPANSION,
        improvement_tolerance=improvement_tolerance,
    )
    return AdaptiveReductionController().run(
        runtime=runtime,
        actions=actions,
        initial_active_action_ids=active_action_ids,
        config=config,
    )


def test_real_pypsa_hard_violation_without_supported_relation_is_unattributable(
    tmp_path: Path,
) -> None:
    """A global active-loss HARD violation terminates as attribution failure."""
    runtime, actions, active = _case(
        tmp_path, active_impedance=(3.0, 30.0), load_p=10.0, load_q=3.0)
    report = _run(runtime, actions, active)

    assert report.terminal_reason is AdaptiveStoppingReason.UNATTRIBUTABLE_HARD_VIOLATION
    hard_rows = report.iterations[-1].hard_violations
    assert hard_rows
    assert any(row.status is AttributionStatus.UNATTRIBUTABLE for row in hard_rows)
    assert any(row.status is AttributionStatus.ATTRIBUTABLE for row in hard_rows)


def test_real_pypsa_protected_related_action_reaches_no_expansion(
    tmp_path: Path,
) -> None:
    """A protected action remains attributable evidence but never becomes a candidate."""
    runtime, actions, active = _case(
        tmp_path, active_impedance=(1e-6, 30.0), load_p=0.01, load_q=10.0,
        protected=True)
    report = _run(runtime, actions, active)

    assert report.terminal_reason is AdaptiveStoppingReason.NO_EXPANSION_CANDIDATE
    iteration = report.iterations[-1]
    assert iteration.hard_violations
    assert all(row.status is AttributionStatus.ATTRIBUTABLE for row in iteration.hard_violations)
    assert all(row.related_action_ids == ("collapse-ab",) for row in iteration.hard_violations)
    assert all(row.candidate_action_ids == () for row in iteration.hard_violations)
    assert iteration.expansion_decision is not None
    assert iteration.expansion_decision.candidate_evaluations == ()


def test_real_pypsa_viable_candidate_without_sufficient_improvement_reaches_no_further(
    tmp_path: Path,
) -> None:
    """A real expanded candidate passes all prerequisite Gates and reaches scoring."""
    runtime, actions, active = _case(
        tmp_path, active_impedance=(1e-6, 30.0), load_p=0.01, load_q=10.0)
    report = _run(runtime, actions, active, improvement_tolerance=1e9)

    assert report.terminal_reason is AdaptiveStoppingReason.NO_FURTHER_COMPRESSION_BENEFIT
    evaluations = report.iterations[-1].expansion_decision.candidate_evaluations
    assert len(evaluations) == 1
    row = evaluations[0]
    assert row.structural_status == "STRUCTURALLY_VALID"
    assert row.readiness_status == "AC_MODEL_READY"
    assert row.pf_status == "PF_CONVERGED"
    assert row.fidelity_status == "PF_FIDELITY_WITHIN_TOLERANCE"
    assert row.metrics_digest is not None
    assert row.hard_violation_improvement is not None
    assert row.hard_violation_improvement <= 1e9
    assert row.score is None
