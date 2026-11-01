"""Synthetic contracts for resolved adaptive protection and action readiness."""

from pathlib import Path
import hashlib

import pypsa
import pytest

from psdcalc.reduction.adaptive.actions import (
    ActionOperation, MappingTransition, ReductionAction,
)
from psdcalc.reduction.adaptive.candidates import (
    CandidateReconstructionError, PypsaAdaptiveCandidateBuilder,
)
from psdcalc.reduction.adaptive.pypsa_runtime import PypsaAdaptiveRuntime
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.loader import load_config
from psdcalc.reduction.config.models import AggregationConfig, ParallelCorridorConfig
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.fidelity.contracts import (
    PowerFlowMetricPolicy, PowerFlowOperatingPointContract,
)
from psdcalc.reduction.fidelity.persistence import FrozenThresholdSet
from psdcalc.reduction.topology.parallel_provenance import (
    LPlusAssociation, ParallelSourceLedger, SourceProfileContract,
)


def _fixture(tmp_path: Path):
    network = pypsa.Network()
    for name, voltage in (("anchor", 110), ("radial", 110),
                          ("peer", 110), ("low", 10)):
        network.add("Bus", name, v_nom=voltage)
    network.add("Line", "feeder", bus0="anchor", bus1="radial", r=0.01, x=0.1)
    network.add("Line", "tail", bus0="radial", bus1="peer", r=0.02, x=0.2)
    network.add("Transformer", "tx", bus0="anchor", bus1="low", x=0.1)
    sidecar = PypsaAdapter().identity_sidecar(network, "pr025r")
    config = load_config({
        "schema_version": "0.1.0",
        "run": {"id": "pr025r", "seed": 7, "output_dir": str(tmp_path / "runs")},
        "input": {"backend": "pypsa", "path": str(tmp_path / "source.nc"),
                  "format": "netcdf", "source_namespace": "pr025r"},
        "task": {"type": "planning", "profile": "planning_aggregation",
                 "reduction_role": "candidate", "final_authority": "original"},
        "pipeline": {"materialization_family": "multi_voltage_quotient"},
        "simplification": {"operations": [{"id": "topology", "type": "voltage_aware_topology",
                                           "enabled": True, "mode": "keep_transformer_interface"}]},
        "partition": {"planner": "pypsa_kmeans", "target": {"n_clusters": 3},
                      "connectivity_policy": "reject"},
        "aggregation": {"backend": "pypsa"},
    })
    anchor = sidecar.source_id("Bus", "anchor")
    radial = sidecar.source_id("Bus", "radial")
    feeder = sidecar.source_id("Line", "feeder")
    action = ReductionAction.create(
        action_id="radial-to-anchor", operation=ActionOperation.RADIAL_ABSORPTION,
        source_ids=(anchor, radial, feeder), target_ids=(anchor,),
        mapping_delta=(MappingTransition(anchor, anchor, "preserved"),
                       MappingTransition(radial, anchor, "clustered"),
                       MappingTransition(feeder, None, "absorbed_internal")),
        voltage_layer="110", component_id="synthetic", region_fingerprint="synthetic",
        audit_reason="Transformer terminal anchor acceptance fixture",
    )
    empty = ProtectionSet(frozenset(), frozenset(), frozenset(), frozenset(), frozenset())
    return network, sidecar, config, action, empty


def test_anchor_action_is_catalog_ready_and_reconstructable(tmp_path: Path) -> None:
    """An ordinary Bus may merge into its Transformer-terminal identity anchor."""
    network, sidecar, config, action, empty = _fixture(tmp_path)
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=network, source_sidecar=sidecar, actions=(action,),
        reduction_config=config, protection=empty,
    )

    readiness = builder.action_catalog_readiness((action.action_id,))
    assert readiness.ready
    assert readiness.rejections == ()
    built = builder.build((action.action_id,))
    assert built.network.transformers.loc["tx", "bus0"] == "anchor"


def test_many_radial_actions_into_one_transformer_anchor(tmp_path: Path) -> None:
    """A synthetic Stage2A analogue may absorb many free Buses into one anchor."""
    network, _sidecar, config, _action, empty = _fixture(tmp_path)
    network.remove("Line", "feeder")
    network.remove("Line", "tail")
    network.remove("Bus", "radial")
    network.remove("Bus", "peer")
    for index in range(8):
        network.add("Bus", f"radial-{index}", v_nom=110)
        network.add("Line", f"feeder-{index}", bus0="anchor",
                    bus1=f"radial-{index}", r=0.01, x=0.1)
    sidecar = PypsaAdapter().identity_sidecar(network, "pr025r-many")
    anchor = sidecar.source_id("Bus", "anchor")
    actions = []
    for index in range(8):
        radial = sidecar.source_id("Bus", f"radial-{index}")
        feeder = sidecar.source_id("Line", f"feeder-{index}")
        actions.append(ReductionAction.create(
            action_id=f"absorb-{index}", operation=ActionOperation.RADIAL_ABSORPTION,
            source_ids=(anchor, radial, feeder), target_ids=(anchor,),
            mapping_delta=(MappingTransition(anchor, anchor, "preserved"),
                           MappingTransition(radial, anchor, "clustered"),
                           MappingTransition(feeder, None, "absorbed_internal")),
            voltage_layer="110", component_id="synthetic",
            region_fingerprint="synthetic", audit_reason="radial into anchor",
        ))
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=network, source_sidecar=sidecar, actions=tuple(actions),
        reduction_config=config, protection=empty,
    )
    active = tuple(action.action_id for action in actions)
    assert builder.action_catalog_readiness(active).ready
    # This regression targets catalog legality; the one-action test above
    # separately exercises materialization without changing PyPSA quotient.
    assert len(active) == 8


def test_strict_singleton_action_is_rejected_before_materialization(tmp_path: Path) -> None:
    """The same merge is illegal when the anchor has an independent strict rule."""
    network, sidecar, config, action, empty = _fixture(tmp_path)
    anchor = sidecar.source_id("Bus", "anchor")
    strict = ProtectionSet(empty.traced, empty.preserved_components,
                           empty.terminal_buses, frozenset({anchor}), empty.cannot_link)
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=network, source_sidecar=sidecar, actions=(action,),
        reduction_config=config, protection=strict,
    )

    readiness = builder.action_catalog_readiness((action.action_id,))
    assert not readiness.ready
    assert any(row.failure_code == "STRICT_SINGLETON_CONFLICT"
               for row in readiness.rejections)


def test_preserve_policy_requires_an_explicit_source_ledger(tmp_path: Path) -> None:
    """A real-profile replay cannot silently acquire an empty programmatic ledger."""
    network, sidecar, config, action, empty = _fixture(tmp_path)
    config = config.model_copy(update={
        "aggregation": AggregationConfig(
            parallel_corridors=ParallelCorridorConfig(policy="preserve_protected_members")),
    })
    with pytest.raises(CandidateReconstructionError, match="source ledger"):
        PypsaAdaptiveCandidateBuilder(
            source_network=network, source_sidecar=sidecar, actions=(action,),
            reduction_config=config, protection=empty,
        )

    ledger = ParallelSourceLedger(SourceProfileContract("programmatic_pypsa", "unavailable"))
    first = PypsaAdaptiveCandidateBuilder(
        source_network=network, source_sidecar=sidecar, actions=(action,),
        reduction_config=config, protection=empty, source_ledger=ledger,
    )
    second = PypsaAdaptiveCandidateBuilder(
        source_network=network, source_sidecar=sidecar, actions=(action,),
        reduction_config=config, protection=empty, source_ledger=ledger,
    )
    assert first.effective_protection_digest == second.effective_protection_digest
    assert first.source_ledger_digest == second.source_ledger_digest


def test_protected_parallel_action_rejected_before_materialization(tmp_path: Path) -> None:
    """A protected parallel member cannot be absorbed by an adaptive action."""
    network, _sidecar, config, _action, empty = _fixture(tmp_path)
    network.add("Line", "feeder-second", bus0="anchor", bus1="radial",
                r=0.03, x=0.3)
    sidecar = PypsaAdapter().identity_sidecar(network, "pr025r")
    anchor = sidecar.source_id("Bus", "anchor")
    radial = sidecar.source_id("Bus", "radial")
    feeder = sidecar.source_id("Line", "feeder")
    action = ReductionAction.create(
        action_id="absorb-protected-member", operation=ActionOperation.RADIAL_ABSORPTION,
        source_ids=(anchor, radial, feeder), target_ids=(anchor,),
        mapping_delta=(MappingTransition(anchor, anchor, "preserved"),
                       MappingTransition(radial, anchor, "clustered"),
                       MappingTransition(feeder, None, "absorbed_internal")),
        voltage_layer="110", component_id="synthetic", region_fingerprint="synthetic",
        audit_reason="parallel member identity conflict",
    )
    config = config.model_copy(update={
        "aggregation": AggregationConfig(
            parallel_corridors=ParallelCorridorConfig(policy="preserve_protected_members")),
    })
    ledger = ParallelSourceLedger(SourceProfileContract("programmatic_pypsa", "unavailable"))
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=network, source_sidecar=sidecar, actions=(action,),
        reduction_config=config, protection=empty, source_ledger=ledger,
    )
    failures = {row.failure_code for row in builder.action_catalog_readiness(
        (action.action_id,)).rejections}
    assert "PROTECTED_PARALLEL_IDENTITY_CONFLICT" in failures


def test_l_plus_source_ledger_identity_rejected_before_materialization(
    tmp_path: Path,
) -> None:
    """An L+ base Line cannot be silently absorbed into an adaptive candidate."""
    network, _sidecar, config, action, empty = _fixture(tmp_path)
    network.add("ShuntImpedance", "reactor", bus="anchor", g=0.0, b=-0.01)
    sidecar = PypsaAdapter().identity_sidecar(network, "pr025r")
    ledger = ParallelSourceLedger(
        SourceProfileContract("programmatic_pypsa", "unavailable"),
        l_plus=(LPlusAssociation(
            base_line_id=sidecar.source_id("Line", "feeder"),
            source_card_identity="synthetic-l-plus-card", terminal_side="FROM",
            terminal_bus_id=sidecar.source_id("Bus", "anchor"),
            shunt_id=sidecar.source_id("ShuntImpedance", "reactor"),
            source_reactor_value=4.0,
        ),),
    )
    config = config.model_copy(update={
        "aggregation": AggregationConfig(
            parallel_corridors=ParallelCorridorConfig(policy="preserve_protected_members")),
    })
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=network, source_sidecar=sidecar, actions=(action,),
        reduction_config=config, protection=empty, source_ledger=ledger,
    )
    failures = {row.failure_code for row in builder.action_catalog_readiness(
        (action.action_id,)).rejections}
    assert "SOURCE_LEDGER_IDENTITY_CONFLICT" in failures


def test_failed_build_has_stable_attempt_identity_and_no_candidate(tmp_path: Path) -> None:
    """A rejected catalog produces no fictitious materialized candidate identity."""
    network, sidecar, config, action, empty = _fixture(tmp_path)
    anchor = sidecar.source_id("Bus", "anchor")
    strict = ProtectionSet(empty.traced, empty.preserved_components,
                           empty.terminal_buses, frozenset({anchor}), empty.cannot_link)
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=network, source_sidecar=sidecar, actions=(action,),
        reduction_config=config, protection=strict,
    )
    identity = hashlib.sha256(b"synthetic").hexdigest()
    operating = PowerFlowOperatingPointContract(
        snapshots=(network.snapshots[0],), solver="pypsa.newton_raphson",
        tolerance=1e-8, max_iterations=None, distribute_slack=False,
        slack_weights=None, use_seed=False,
        control_policy="preserve_source_controls",
        tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest=identity, device_schedule_digest=identity,
    )
    runtime = PypsaAdaptiveRuntime(
        source_network=network, source_sidecar=sidecar, builder=builder,
        actions=(action,), reduction_config_digest=builder.reduction_config_digest,
        operating_point=operating, metric_policy=PowerFlowMetricPolicy(1e-6, 1e-6, 1e-6),
        threshold_set=FrozenThresholdSet.load(),
    )

    first = runtime.build((action.action_id,))
    second = runtime.build((action.action_id,))
    assert first.build_attempt_fingerprint == second.build_attempt_fingerprint
    assert first.build_attempt_fingerprint is not None
    assert first.candidate_fingerprint is None
    assert first.materialized_candidate_fingerprint is None
    assert first.failure_stage == "ACTION_CATALOG_READINESS"
    assert first.failure_code == "STRICT_SINGLETON_CONFLICT"
    assert first.offending_identity_fingerprints == second.offending_identity_fingerprints
    assert first.structural_status == "STRUCTURAL_INVALID"
