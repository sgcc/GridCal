"""Stage-2 reconstruction acceptance using only a synthetic PyPSA network."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pypsa
import pytest

from psdcalc.reduction.adaptive.actions import (
    ActionOperation,
    MappingTransition,
    ReductionAction,
)
from psdcalc.reduction.adaptive.candidates import (
    CandidateReconstructionError,
    PypsaAdaptiveCandidateBuilder,
)
from psdcalc.reduction.adaptive.journal import ReductionActionJournal
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.loader import load_config
from psdcalc.reduction.config.models import MatchConfig, ProtectionConfig, ProtectionRule
from psdcalc.reduction.core.ids import IdentitySidecar, StableId
from psdcalc.reduction.fidelity.persistence import canonical_model_digest
from psdcalc.reduction.protection.engine import ProtectionSet
from psdcalc.reduction.planners.energy import EnergyPartitionPlanner


def _source() -> tuple[pypsa.Network, IdentitySidecar]:
    """Return a stable-ID two-edge radial fixture with a removable middle bus."""
    network = pypsa.Network()
    for name in ("a", "b", "c", "d", "e", "f", "g"):
        network.add("Bus", name, v_nom=110.0)
    network.add("Line", "ab", bus0="a", bus1="b", r=0.01, x=0.1)
    network.add("Line", "bc", bus0="b", bus1="c", r=0.02, x=0.2)
    network.add("Line", "de", bus0="d", bus1="e", r=0.03, x=0.3)
    network.add("Line", "fg", bus0="f", bus1="g", r=0.04, x=0.4)
    network.add("Generator", "slack", bus="c", control="Slack", p_nom=100.0)
    network.add("Load", "load-c", bus="c", p_set=5.0)
    sidecar = PypsaAdapter().identity_sidecar(network, "stage2")
    return network, sidecar


def _config(tmp_path: Path, *, n_clusters: int = 2):
    """Return a valid frozen config whose cluster target is action-resolved."""
    return load_config({
        "schema_version": "0.1.0",
        "run": {"id": "stage2", "seed": 7, "output_dir": str(tmp_path / "runs")},
        "input": {"backend": "pypsa", "path": str(tmp_path / "s1.nc"),
                  "format": "netcdf", "source_namespace": "stage2"},
        "task": {"type": "planning", "profile": "planning_aggregation",
                 "reduction_role": "candidate", "final_authority": "original"},
        "pipeline": {"materialization_family": "multi_voltage_quotient"},
        "simplification": {"operations": [{
            "id": "topology", "type": "voltage_aware_topology",
            "enabled": True, "mode": "keep_transformer_interface",
        }]},
        "partition": {"planner": "pypsa_kmeans", "target": {"n_clusters": n_clusters},
                      "connectivity_policy": "reject"},
        "aggregation": {"backend": "pypsa"},
    })


def _radial_action(sidecar: IdentitySidecar) -> ReductionAction:
    """Declare the exact Bus contraction and internal Line absorption."""
    bus_a = sidecar.source_id("Bus", "a")
    bus_b = sidecar.source_id("Bus", "b")
    line_ab = sidecar.source_id("Line", "ab")
    return ReductionAction.create(
        action_id="absorb-ab",
        operation=ActionOperation.RADIAL_ABSORPTION,
        source_ids=(bus_a, bus_b, line_ab),
        target_ids=(bus_a,),
        mapping_delta=(
            MappingTransition(bus_a, bus_a, "preserved"),
            MappingTransition(bus_b, bus_a, "clustered"),
            MappingTransition(line_ab, None, "absorbed_internal"),
        ),
        voltage_layer="110",
        component_id="component-a",
        region_fingerprint="region-a",
        audit_reason="synthetic radial contraction",
    )


def _second_radial_action(sidecar: IdentitySidecar) -> ReductionAction:
    """Declare an independent radial contraction in a separate component."""
    bus_d = sidecar.source_id("Bus", "d")
    bus_e = sidecar.source_id("Bus", "e")
    line_de = sidecar.source_id("Line", "de")
    return ReductionAction.create(
        action_id="absorb-de",
        operation=ActionOperation.RADIAL_ABSORPTION,
        source_ids=(bus_d, bus_e, line_de),
        target_ids=(bus_d,),
        mapping_delta=(
            MappingTransition(bus_d, bus_d, "preserved"),
            MappingTransition(bus_e, bus_d, "clustered"),
            MappingTransition(line_de, None, "absorbed_internal"),
        ),
        voltage_layer="110",
        component_id="component-d",
        region_fingerprint="region-d",
        audit_reason="independent synthetic radial contraction",
    )


def _third_radial_action(sidecar: IdentitySidecar) -> ReductionAction:
    """Declare a third independent contraction for permutation coverage."""
    bus_f = sidecar.source_id("Bus", "f")
    bus_g = sidecar.source_id("Bus", "g")
    line_fg = sidecar.source_id("Line", "fg")
    return ReductionAction.create(
        action_id="absorb-fg",
        operation=ActionOperation.RADIAL_ABSORPTION,
        source_ids=(bus_f, bus_g, line_fg),
        target_ids=(bus_f,),
        mapping_delta=(
            MappingTransition(bus_f, bus_f, "preserved"),
            MappingTransition(bus_g, bus_f, "clustered"),
            MappingTransition(line_fg, None, "absorbed_internal"),
        ),
        voltage_layer="110",
        component_id="component-f",
        region_fingerprint="region-f",
        audit_reason="third independent synthetic radial contraction",
    )


def _builder(tmp_path: Path, actions: tuple[ReductionAction, ...]):
    """Construct the required concrete Stage-2 builder from frozen S1."""
    network, sidecar = _source()
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=network,
        source_sidecar=sidecar,
        actions=actions,
        reduction_config=_config(tmp_path),
        protection=ProtectionSet(frozenset(), frozenset(), frozenset(),
                                 frozenset(), frozenset()),
    )
    return network, sidecar, builder


def test_candidate_builder_has_no_previous_candidate_input(tmp_path: Path) -> None:
    """Candidate reconstruction only accepts frozen S1 and action-set IDs."""
    parameters = inspect.signature(PypsaAdaptiveCandidateBuilder.build).parameters
    assert tuple(parameters) == ("self", "active_action_ids")
    _source_network, sidecar = _source()
    builder = _builder(tmp_path, (_radial_action(sidecar),))[2]
    assert builder.rebuild_from_s1 is True


def test_reconstruction_is_repeatable_and_source_is_immutable(tmp_path: Path) -> None:
    """Fresh S1 rebuilds produce identical model and mapping digests."""
    source, sidecar = _source()
    action = _radial_action(sidecar)
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=source,
        source_sidecar=sidecar,
        actions=(action,),
        reduction_config=_config(tmp_path),
        protection=ProtectionSet(frozenset(), frozenset(), frozenset(),
                                 frozenset(), frozenset()),
    )
    source_digest = canonical_model_digest(source, sidecar)
    sidecar_rows = sidecar.to_rows()

    first = builder.build((action.action_id,))
    second = builder.build((action.action_id,))

    assert first.identity.model_digest == second.identity.model_digest
    assert first.identity.mapping_digest == second.identity.mapping_digest
    assert first.identity.action_set_fingerprint == second.identity.action_set_fingerprint
    assert first.structural_result.checks == second.structural_result.checks
    assert first.structural_result.stage == "builder"
    assert first.structural_result.finalized is False
    assert all(first.structural_result.checks.values())
    assert first.ac_readiness_result.status.value == "AC_MODEL_READY"
    assert len(first.network.buses) == 6
    assert len(first.network.lines) == 3
    assert {row.source_id for row in first.mappings.control_map} == {
        sidecar.source_id("Generator", "slack")
    }
    assert first.identity.execution_order == second.identity.execution_order
    assert first.network is not second.network
    assert canonical_model_digest(source, sidecar) == source_digest
    assert sidecar.to_rows() == sidecar_rows
    assert source.lines_t.p0.empty
    assert source.generators_t.p.empty
    assert source.buses_t.v_mag_pu.empty


def test_action_order_and_journal_reload_rebuild_identically(tmp_path: Path) -> None:
    """Input ordering and JSON journal round-trip do not change reconstruction."""
    source, sidecar = _source()
    action_a = _radial_action(sidecar)
    action_b = _second_radial_action(sidecar)
    action_c = _third_radial_action(sidecar)
    actions = (action_a, action_b, action_c)
    journal = ReductionActionJournal.start(actions)
    loaded = ReductionActionJournal.from_dict(json.loads(json.dumps(journal.to_dict())))
    loaded.verify()
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=source,
        source_sidecar=sidecar,
        actions=actions,
        reduction_config=_config(tmp_path),
        protection=ProtectionSet(frozenset(), frozenset(), frozenset(),
                                 frozenset(), frozenset()),
    )
    direct = builder.build(tuple(action.action_id for action in actions))
    permutation_results = [builder.build(ids) for ids in (
        (action_c.action_id, action_a.action_id, action_b.action_id),
        (action_b.action_id, action_c.action_id, action_a.action_id),
    )]
    replay = PypsaAdaptiveCandidateBuilder(
        source_network=source,
        source_sidecar=sidecar,
        actions=tuple(reversed(loaded.actions)),
        reduction_config=_config(tmp_path),
        protection=ProtectionSet(frozenset(), frozenset(), frozenset(),
                                 frozenset(), frozenset()),
    ).build(tuple(reversed(loaded.active_action_ids)))
    for candidate in (replay, *permutation_results):
        assert direct.identity.partition_digest == candidate.identity.partition_digest
        assert direct.identity.model_digest == candidate.identity.model_digest
        assert direct.identity.mapping_digest == candidate.identity.mapping_digest
        assert direct.identity.action_set_fingerprint == candidate.identity.action_set_fingerprint
        assert direct.identity.execution_order == candidate.identity.execution_order


def test_active_action_set_controls_exact_bus_and_line_effects(tmp_path: Path) -> None:
    """The explicit action set, not a planner, determines the quotient partition."""
    source, sidecar = _source()
    actions = (_radial_action(sidecar), _second_radial_action(sidecar),
               _third_radial_action(sidecar))
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=source,
        source_sidecar=sidecar,
        actions=actions,
        reduction_config=_config(tmp_path),
        protection=ProtectionSet(frozenset(), frozenset(), frozenset(),
                                 frozenset(), frozenset()),
    )

    candidate = builder.build(tuple(action.action_id for action in actions))
    identity = builder.build(())

    assert (len(source.buses), len(source.lines)) == (7, 4)
    assert (len(candidate.network.buses), len(candidate.network.lines)) == (4, 1)
    assert (len(identity.network.buses), len(identity.network.lines)) == (7, 4)
    rows = {row.source_id: row for row in candidate.mappings.branch_map}
    assert rows[sidecar.source_id("Line", "ab")].mapping_type == "absorbed_internal"
    assert rows[sidecar.source_id("Line", "de")].mapping_type == "absorbed_internal"
    assert rows[sidecar.source_id("Line", "fg")].mapping_type == "absorbed_internal"
    assert candidate.identity.active_action_ids == ("absorb-ab", "absorb-de", "absorb-fg")


def test_empty_action_set_is_exact_s1_and_does_not_invent_reductions(
    tmp_path: Path,
) -> None:
    """No active actions returns an identity candidate, not a planner quotient."""
    source, sidecar = _source()
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar, actions=(),
        reduction_config=_config(tmp_path),
    )

    candidate = builder.build(())

    assert candidate.identity.model_digest == canonical_model_digest(source, sidecar)
    assert candidate.identity.mapping_digest == candidate.mappings.semantic_digest()
    assert len(candidate.network.buses) == len(source.buses)
    assert len(candidate.network.lines) == len(source.lines)
    assert all(row.source_id == row.target_id for row in candidate.mappings.bus_map)
    assert all(row.source_id == row.target_id for row in candidate.mappings.branch_map)


def test_frozen_config_protection_is_enforced_during_action_replay(
    tmp_path: Path,
) -> None:
    """Config-compiled singleton protection cannot be bypassed by an action."""
    source, sidecar = _source()
    bus_b = sidecar.source_id("Bus", "b")
    config = _config(tmp_path).model_copy(update={
        "protection": ProtectionConfig(rules=[ProtectionRule(
            id="protect-b", component="Bus",
            match=MatchConfig(field="source_id", op="eq", value=bus_b.encode()),
            action="preserve_bus_as_singleton",
        )]),
    })
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar,
        actions=(_radial_action(sidecar),), reduction_config=config,
        protection=ProtectionSet(frozenset(), frozenset(), frozenset(),
                                 frozenset(), frozenset()),
    )

    with pytest.raises(CandidateReconstructionError, match="STRICT_SINGLETON_CONFLICT"):
        builder.build(("absorb-ab",))


def test_adaptive_replay_never_invokes_energy_planner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary planner must stay outside explicit action reconstruction."""
    def forbidden_plan(*args, **kwargs):
        raise AssertionError("PR024A_STAGE2_PLANNER_LEAK")

    monkeypatch.setattr(EnergyPartitionPlanner, "plan", forbidden_plan)
    source, sidecar = _source()
    action = _radial_action(sidecar)
    candidate = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar, actions=(action,),
        reduction_config=_config(tmp_path),
    ).build((action.action_id,))
    assert len(candidate.network.buses) == len(source.buses) - 1


def test_planner_target_config_does_not_change_active_action_partition(
    tmp_path: Path,
) -> None:
    """A planner-only target count cannot add or remove action-selected merges."""
    source, sidecar = _source()
    action = _radial_action(sidecar)
    low = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar, actions=(action,),
        reduction_config=_config(tmp_path, n_clusters=2),
    ).build((action.action_id,))
    high = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar, actions=(action,),
        reduction_config=_config(tmp_path, n_clusters=6),
    ).build((action.action_id,))
    assert low.identity.partition_digest == high.identity.partition_digest
    assert low.identity.mapping_digest == high.identity.mapping_digest
    assert low.identity.model_digest == high.identity.model_digest
    assert len(low.network.buses) == len(high.network.buses) == len(source.buses) - 1


def test_corrupted_prior_candidate_is_not_used_for_rebuild(tmp_path: Path) -> None:
    """Mutating one returned candidate cannot affect a later fresh S1 rebuild."""
    _source_network, sidecar, builder = _builder(tmp_path, (_radial_action(_source()[1]),))
    action = _radial_action(sidecar)
    clean = builder.build((action.action_id,))
    expected_model = clean.identity.model_digest
    expected_mapping = clean.identity.mapping_digest
    clean.network.buses.loc[:, "v_nom"] = 999.0
    clean.network.lines.loc[:, "x"] = 0.0

    rebuilt = builder.build((action.action_id,))

    assert rebuilt.identity.model_digest == expected_model
    assert rebuilt.identity.mapping_digest == expected_mapping


def test_missing_ids_invalid_dependencies_and_unsupported_actions_fail_closed(
    tmp_path: Path,
) -> None:
    """Unknown source IDs, dependency gaps and unsupported operations are rejected."""
    source, sidecar = _source()
    config = _config(tmp_path)
    protection = ProtectionSet(frozenset(), frozenset(), frozenset(),
                               frozenset(), frozenset())
    action = _radial_action(sidecar)
    missing = StableId("stage2", "Bus", "absent")
    bad_action = ReductionAction.create(
        action_id="missing",
        operation=ActionOperation.BUS_CLUSTER,
        source_ids=(missing,),
        target_ids=(missing,),
        mapping_delta=(MappingTransition(missing, missing, "preserved"),),
        voltage_layer="110", component_id="component-a",
        region_fingerprint="region-a", audit_reason="missing StableId",
    )
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar,
        actions=(bad_action,), reduction_config=config, protection=protection,
    )
    with pytest.raises(CandidateReconstructionError, match="ACTION_SOURCE_MISSING"):
        builder.build((bad_action.action_id,))

    dependent = ReductionAction.create(
        action_id="dependent",
        operation=ActionOperation.RADIAL_ABSORPTION,
        source_ids=action.source_ids,
        target_ids=action.target_ids,
        mapping_delta=action.mapping_delta,
        voltage_layer="110", component_id="component-a",
        region_fingerprint="region-a", dependencies=("not-in-catalog",),
        audit_reason="invalid dependency",
    )
    with pytest.raises(CandidateReconstructionError, match="dependency"):
        PypsaAdaptiveCandidateBuilder(
            source_network=source, source_sidecar=sidecar,
            actions=(dependent,), reduction_config=config, protection=protection,
        )

    base_action = ReductionAction.create(
        action_id="base", operation=ActionOperation.BUS_CLUSTER,
        source_ids=action.source_ids, target_ids=action.target_ids,
        mapping_delta=action.mapping_delta, voltage_layer="110",
        component_id="component-a", region_fingerprint="region-a",
        audit_reason="dependency prerequisite",
    )
    dependent_action = ReductionAction.create(
        action_id="dependent-valid", operation=ActionOperation.RADIAL_ABSORPTION,
        source_ids=action.source_ids, target_ids=action.target_ids,
        mapping_delta=action.mapping_delta, voltage_layer="110",
        component_id="component-a", region_fingerprint="region-a",
        dependencies=("base",), audit_reason="dependency closure fixture",
    )
    closure_builder = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar,
        actions=(base_action, dependent_action), reduction_config=config,
        protection=protection,
    )
    with pytest.raises(CandidateReconstructionError, match="INVALID_DEPENDENCY_CLOSURE"):
        closure_builder.build(("dependent-valid",))

    unsupported = ReductionAction.create(
        action_id="parallel",
        operation=ActionOperation.PARALLEL_CORRIDOR_AGGREGATION,
        source_ids=(sidecar.source_id("Line", "ab"),),
        target_ids=(sidecar.source_id("Line", "ab"),),
        mapping_delta=(MappingTransition(
            sidecar.source_id("Line", "ab"), sidecar.source_id("Line", "ab"),
            "preserved"),),
        voltage_layer="110", component_id="component-a",
        region_fingerprint="region-a", audit_reason="not Stage-2 supported",
    )
    with pytest.raises(CandidateReconstructionError, match="UNSUPPORTED_ACTION_TYPE"):
        PypsaAdaptiveCandidateBuilder(
            source_network=source, source_sidecar=sidecar,
            actions=(unsupported,), reduction_config=config, protection=protection,
        ).build((unsupported.action_id,))


def test_action_mapping_mismatch_and_ineffective_action_fail_closed(tmp_path: Path) -> None:
    """Declared action effects must match materialized MappingBundle rows."""
    source, sidecar = _source()
    action = _radial_action(sidecar)
    invalid = ReductionAction.create(
        action_id="wrong-map",
        operation=ActionOperation.RADIAL_ABSORPTION,
        source_ids=action.source_ids,
        target_ids=(sidecar.source_id("Bus", "a"), sidecar.source_id("Bus", "b")),
        mapping_delta=(
            MappingTransition(sidecar.source_id("Bus", "a"),
                              sidecar.source_id("Bus", "b"), "clustered"),
            MappingTransition(sidecar.source_id("Bus", "b"),
                              sidecar.source_id("Bus", "a"), "clustered"),
            MappingTransition(sidecar.source_id("Line", "ab"), None,
                              "absorbed_internal"),
        ),
        voltage_layer="110", component_id="component-a",
        region_fingerprint="region-a", audit_reason="wrong map target",
    )
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar, actions=(invalid,),
        reduction_config=_config(tmp_path),
        protection=ProtectionSet(frozenset(), frozenset(), frozenset(),
                                 frozenset(), frozenset()),
    )
    with pytest.raises(CandidateReconstructionError, match="ACTION_MAPPING_INCONSISTENT"):
        builder.build((invalid.action_id,))

    ineffective = ReductionAction.create(
        action_id="ineffective",
        operation=ActionOperation.RADIAL_ABSORPTION,
        source_ids=(sidecar.source_id("Bus", "a"),),
        target_ids=(sidecar.source_id("Bus", "a"),),
        mapping_delta=(MappingTransition(sidecar.source_id("Bus", "a"),
                                         sidecar.source_id("Bus", "a"),
                                         "preserved"),),
        voltage_layer="110", component_id="component-a",
        region_fingerprint="region-a", audit_reason="identity-only action",
    )
    with pytest.raises(CandidateReconstructionError, match="ACTION_MAPPING_INCONSISTENT"):
        PypsaAdaptiveCandidateBuilder(
            source_network=source, source_sidecar=sidecar, actions=(ineffective,),
            reduction_config=_config(tmp_path),
            protection=ProtectionSet(frozenset(), frozenset(), frozenset(),
                                     frozenset(), frozenset()),
        ).build((ineffective.action_id,))


def test_protected_action_cannot_be_omitted(tmp_path: Path) -> None:
    """Protected reduction actions remain active in every reconstruction."""
    source, sidecar = _source()
    action = _radial_action(sidecar)
    protected = ReductionAction.create(
        action_id=action.action_id,
        operation=action.operation,
        source_ids=action.source_ids,
        target_ids=action.target_ids,
        mapping_delta=action.mapping_delta,
        voltage_layer=action.voltage_layer,
        component_id=action.component_id,
        region_fingerprint=action.region_fingerprint,
        protected=True,
        reversible=False,
        inverse_semantics="",
        audit_reason=action.audit_reason,
    )
    builder = PypsaAdaptiveCandidateBuilder(
        source_network=source, source_sidecar=sidecar, actions=(protected,),
        reduction_config=_config(tmp_path),
        protection=ProtectionSet(frozenset(), frozenset(), frozenset(),
                                 frozenset(), frozenset()),
    )
    with pytest.raises(CandidateReconstructionError, match="REQUIRED_ACTION_OMITTED"):
        builder.build(())
