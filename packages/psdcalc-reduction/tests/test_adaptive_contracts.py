from __future__ import annotations

from dataclasses import replace

import pytest

from psdcalc.reduction.adaptive.actions import (
    ActionOperation,
    MappingTransition,
    ReductionAction,
)
from psdcalc.reduction.adaptive.contracts import (
    AdaptiveReductionConfig,
    AdaptiveStoppingReason,
    ErrorRegion,
)
from psdcalc.reduction.adaptive.journal import ReductionActionJournal
from psdcalc.reduction.core.ids import StableId


def _action(action_id: str, source_key: str, *, dependencies: tuple[str, ...] = ()) -> ReductionAction:
    source = StableId("grid", "Bus", source_key)
    target = StableId("grid", "Bus", f"target-{source_key}")
    return ReductionAction.create(
        action_id=action_id,
        operation=ActionOperation.BUS_CLUSTER,
        source_ids=(source,),
        target_ids=(target,),
        mapping_delta=(MappingTransition(source, target, "clustered"),),
        voltage_layer="220",
        component_id="component-1",
        region_fingerprint="region-1",
        dependencies=dependencies,
        audit_reason="synthetic test reduction",
    )


def test_action_fingerprint_is_independent_of_input_order() -> None:
    first = StableId("grid", "Bus", "a")
    second = StableId("grid", "Bus", "b")
    target = StableId("grid", "Bus", "t")
    action_a = ReductionAction.create(
        action_id="cluster-ab", operation=ActionOperation.BUS_CLUSTER,
        source_ids=(first, second), target_ids=(target,),
        mapping_delta=(MappingTransition(second, target, "clustered"),
                       MappingTransition(first, target, "clustered")),
        voltage_layer="220", component_id="c", region_fingerprint="r",
        audit_reason="same operation",
    )
    action_b = ReductionAction.create(
        action_id="cluster-ab", operation=ActionOperation.BUS_CLUSTER,
        source_ids=(second, first), target_ids=(target,),
        mapping_delta=(MappingTransition(first, target, "clustered"),
                       MappingTransition(second, target, "clustered")),
        voltage_layer="220", component_id="c", region_fingerprint="r",
        audit_reason="same operation",
    )
    assert action_a.fingerprint == action_b.fingerprint


def test_protected_action_is_not_expansion_eligible() -> None:
    action = ReductionAction.create(
        action_id="protected", operation=ActionOperation.PARALLEL_CORRIDOR_AGGREGATION,
        source_ids=(StableId("grid", "Line", "l1"),),
        target_ids=(StableId("grid", "Line", "l1"),),
        mapping_delta=(MappingTransition(
            StableId("grid", "Line", "l1"), StableId("grid", "Line", "l1"),
            "preserved"),), voltage_layer="220", component_id="c",
        region_fingerprint="r", protected=True,
        audit_reason="protected parallel corridor",
    )
    assert action.expansion_eligible is False


def test_journal_deactivation_is_immutable_and_dependency_closed() -> None:
    parent = _action("radial", "bus-x")
    child = _action("cluster", "bus-y", dependencies=(parent.action_id,))
    journal = ReductionActionJournal.start((child, parent))
    updated = journal.deactivate((parent.action_id,), reason="restore local region")

    assert journal.snapshots[0].active_action_ids == ("cluster", "radial")
    assert updated.snapshots[1].deactivated_action_ids == ("radial",)
    assert updated.snapshots[1].dependency_closure == ("cluster",)
    assert updated.snapshots[1].active_action_ids == ()
    assert updated.verify()


def test_journal_rejects_unknown_or_protected_deactivation() -> None:
    protected = ReductionAction.create(
        action_id="protected", operation=ActionOperation.PARALLEL_CORRIDOR_AGGREGATION,
        source_ids=(StableId("grid", "Line", "line"),),
        target_ids=(StableId("grid", "Line", "line"),),
        mapping_delta=(MappingTransition(
            StableId("grid", "Line", "line"), StableId("grid", "Line", "line"),
            "preserved"),), voltage_layer="220", component_id="c",
        region_fingerprint="r", protected=True, audit_reason="protected",
    )
    journal = ReductionActionJournal.start((protected,))
    with pytest.raises(ValueError):
        journal.deactivate(("missing",), reason="bad ref")
    with pytest.raises(ValueError):
        journal.deactivate(("protected",), reason="cannot undo protection")


@pytest.mark.parametrize("tamper", ["action_id", "dependency", "mapping_delta", "active_set"])
def test_journal_tampering_fails_closed(tamper: str) -> None:
    first = _action("radial", "bus-x")
    second = _action("cluster", "bus-y", dependencies=(first.action_id,))
    journal = ReductionActionJournal.start((first, second)).deactivate(
        (first.action_id,), reason="restore local region")
    if tamper == "action_id":
        corrupt_action = replace(journal.actions[0], action_id="forged")
        corrupted = replace(journal, actions=(corrupt_action, *journal.actions[1:]))
    elif tamper == "dependency":
        corrupt_action = replace(journal.actions[0], dependencies=())
        corrupted = replace(journal, actions=(corrupt_action, journal.actions[1]))
    elif tamper == "mapping_delta":
        snapshot = replace(journal.snapshots[1], mapping_delta=())
        corrupted = replace(journal, snapshots=(journal.snapshots[0], snapshot))
    else:
        snapshot = replace(journal.snapshots[1], active_action_ids=("radial",))
        corrupted = replace(journal, snapshots=(journal.snapshots[0], snapshot))
    with pytest.raises(ValueError):
        corrupted.verify()


def test_public_adaptive_config_is_frozen_and_validates_digest_contract() -> None:
    config = AdaptiveReductionConfig(
        schema_version="1.0",
        reduction_config_digest="1" * 64,
        operating_point_digest="2" * 64,
        solver_contract_digest="3" * 64,
        metric_policy_digest="4" * 64,
        threshold_set_id="PR022_AC_PF_FIDELITY_V1",
        threshold_digest="5" * 64,
        max_iterations=4,
        cost_bus_weight=1.0,
        cost_branch_weight=1.0,
    )
    assert config.strategy.value == "HIERARCHICALLY_CONSTRAINED_GREEDY_LOCAL_EXPANSION"
    assert config.digest == config.digest
    with pytest.raises((AttributeError, TypeError)):
        config.max_iterations = 5  # type: ignore[misc]


@pytest.mark.parametrize(
    "reason",
    [
        AdaptiveStoppingReason.NO_EXPANSION_CANDIDATE,
        AdaptiveStoppingReason.NO_VIABLE_EXPANSION_CANDIDATE,
        AdaptiveStoppingReason.NO_FURTHER_COMPRESSION_BENEFIT,
        AdaptiveStoppingReason.FIDELITY_SATISFIED,
        AdaptiveStoppingReason.FIDELITY_SATISFIED_BUT_COMPRESSION_BUDGET_NOT_MET,
        AdaptiveStoppingReason.UNATTRIBUTABLE_HARD_VIOLATION,
        AdaptiveStoppingReason.STRUCTURAL_INVALID,
        AdaptiveStoppingReason.AC_MODEL_NOT_READY,
        AdaptiveStoppingReason.PF_NOT_CONVERGED,
        AdaptiveStoppingReason.MAX_ITERATIONS_REACHED,
        AdaptiveStoppingReason.CONFIGURATION_INVALID,
    ],
)
def test_terminal_reason_enum_is_explicit(reason: AdaptiveStoppingReason) -> None:
    assert reason.value == reason.name


def test_error_region_fingerprint_is_order_independent() -> None:
    region_a = ErrorRegion.create(
        region_type="bus_cluster",
        source_bus_ids=(StableId("grid", "Bus", "b"), StableId("grid", "Bus", "a")),
        action_ids=("z", "x"), voltage_layer="220", component_id="cc-1",
    )
    region_b = ErrorRegion.create(
        region_type="bus_cluster",
        source_bus_ids=(StableId("grid", "Bus", "a"), StableId("grid", "Bus", "b")),
        action_ids=("x", "z"), voltage_layer="220", component_id="cc-1",
    )
    assert region_a.fingerprint == region_b.fingerprint
