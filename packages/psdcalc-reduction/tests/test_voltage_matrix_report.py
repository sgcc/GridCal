"""Cross-scenario voltage policy reporting from canonical single-run reports."""

import inspect
import json

import pyarrow.parquet as pq
import pytest

from psdcalc.reduction.metrics.stage_inventory import CompressionStage, StageInventory
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.reporting.comparison import (
    CompressionComparisonReport,
    DeviceTransitionSummary,
    VoltageLayerComparison,
    VoltagePolicyReportSummary,
)
from psdcalc.reduction.reporting.voltage_matrix import CrossScenarioCompressionReport
from psdcalc.reduction.reporting.comparison_markdown import render_comparison_markdown


_VALID = "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"
_INVALID = "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID"
_PHYSICAL = "PHYSICAL_FIDELITY_NOT_YET_EVALUATED"


def _report(
    policy_id: str,
    *,
    fingerprint: str = "a" * 64,
    status: str = _VALID,
    candidate_buses: int = 6,
    accepted_buses: int | None = None,
    l1_buses: int = 8,
    candidate_lines: int = 8,
    accepted_lines: int | None = None,
    mapping_digest: str = "d" * 64,
    cutoff_buses: int = 0,
    cutoff_lines: int = 0,
    runtime: float = 3.0,
    cycle_gate: bool = True,
) -> CompressionComparisonReport:
    source_buses = 10
    source_lines = 12
    candidate_stage = StageInventory(
        CompressionStage.QUOTIENT_CANDIDATE,
        bus_count=candidate_buses, line_count=candidate_lines,
        bridge_count=2, articulation_count=1, two_core_bus_count=5,
        cycle_edge_count=6, simple_cycle_rank=2, multigraph_cycle_rank=3,
    )
    final_stage = StageInventory(
        CompressionStage.FINAL_RELOADED,
        bus_count=accepted_buses if accepted_buses is not None else candidate_buses,
        line_count=accepted_lines if accepted_lines is not None else candidate_lines,
        bridge_count=2, articulation_count=1, two_core_bus_count=5,
        cycle_edge_count=6, simple_cycle_rank=2, multigraph_cycle_rank=3,
    )
    stages = {
        CompressionStage.PSD_BPA_SOURCE.value: StageInventory(
            CompressionStage.PSD_BPA_SOURCE, bus_count=source_buses,
            line_count=source_lines, simple_cycle_rank=2, multigraph_cycle_rank=3),
        CompressionStage.PYPSA_FULL_PROJECTION.value: StageInventory(
            CompressionStage.PYPSA_FULL_PROJECTION, bus_count=source_buses,
            line_count=source_lines, simple_cycle_rank=2, multigraph_cycle_rank=3),
        CompressionStage.TOPOLOGY_SIMPLIFIED.value: StageInventory(
        CompressionStage.TOPOLOGY_SIMPLIFIED, bus_count=l1_buses, line_count=10,
            bridge_count=2, articulation_count=1, two_core_bus_count=5,
            cycle_edge_count=6, simple_cycle_rank=2, multigraph_cycle_rank=3),
        CompressionStage.QUOTIENT_CANDIDATE.value: candidate_stage,
        CompressionStage.FINAL_RELOADED.value: final_stage,
    }
    valid = status == _VALID
    if valid and accepted_buses is None:
        accepted_buses = candidate_buses
    if valid and accepted_lines is None:
        accepted_lines = candidate_lines
    layer = VoltageLayerComparison(
        voltage_kv=110.0, source_bus_count=10, projected_bus_count=10,
        simplified_bus_count=l1_buses, candidate_bus_count=candidate_buses,
        final_bus_count=(accepted_buses if accepted_buses is not None
                         else candidate_buses),
        mandatory_singleton_count=3, ring_endpoint_count=2,
        transformer_endpoint_count=1, shunt_endpoint_count=1,
        control_endpoint_count=1, parallel_singleton_endpoint_count=0,
        requested_cluster_count=6, planned_cluster_count=6,
        actual_cluster_count=candidate_buses, minimum_required_bus_count=3,
        candidate_compression_fraction=(source_buses-candidate_buses)/source_buses,
        accepted_compression_fraction=(
            (source_buses-accepted_buses)/source_buses
            if valid and accepted_buses is not None else None),
        cutoff_retained_bus_count=cutoff_buses,
        cutoff_retained_line_count=cutoff_lines,
        cutoff_mandatory_singleton_count=cutoff_buses,
        cutoff_identity_preserved_line_count=cutoff_lines,
    )
    policy = VoltagePolicyReportSummary(
        policy_id=policy_id, enabled=policy_id != "BASE",
        mode="freeze_at_or_above", requested_cutoff_kv=(
            None if policy_id == "BASE" else 110.0),
        source_voltage_layers=(35.0, 110.0),
        retained_voltage_layers=(() if policy_id == "BASE" else (110.0,)),
        compressible_voltage_layers=((35.0, 110.0) if policy_id == "BASE"
                                    else (35.0,)),
        retained_bus_count=cutoff_buses,
        retained_line_count=cutoff_lines,
        resolved_policy_fingerprint=("0" * 64 if policy_id == "BASE" else fingerprint),
        cutoff_mandatory_singleton_count=cutoff_buses,
        cutoff_identity_preserved_line_count=cutoff_lines,
        round_trip_validated=True,
    )
    gates = {
        "voltage_policy_resolved": True,
        "voltage_policy_singleton_buses": True,
        "voltage_policy_line_identity": True,
        "voltage_policy_voltage_membership": True,
        "voltage_policy_round_trip": True,
        "cycle_corridors": cycle_gate,
    }
    return CompressionComparisonReport.create(
        model_id="SYNTHETIC", run_id=policy_id.lower(),
        config_sha256="b" * 64, code_sha="c" * 40, tree_sha="e" * 40,
        source_sha256="f" * 64,
        parallel_corridor_policy="aggregate_corridor",
        final_structural_status=status,
        physical_fidelity_status=_PHYSICAL,
        stages=stages, failed_checks=tuple(
            key for key, passed in gates.items() if not passed),
        voltage_layers=(layer,),
        device_transitions=(DeviceTransitionSummary(
            component="Generator", source_count=4, target_count=3,
            preserved_count=2, aggregated_count=2, absorbed_internal_count=0,
            moved_count=0, mapping_coverage_fraction=1.0),),
        topology={
            "S1_PYPSA_FULL_PROJECTION_bridge_count": 3,
            "S1_PYPSA_FULL_PROJECTION_articulation_count": 1,
            "S1_PYPSA_FULL_PROJECTION_two_core_bus_count": 6,
            "S1_PYPSA_FULL_PROJECTION_cycle_edge_count": 7,
            "S1_PYPSA_FULL_PROJECTION_simple_cycle_rank": 2,
            "S1_PYPSA_FULL_PROJECTION_multigraph_cycle_rank": 3,
            "S2_TOPOLOGY_SIMPLIFIED_bridge_count": 2,
            "S2_TOPOLOGY_SIMPLIFIED_articulation_count": 1,
            "S2_TOPOLOGY_SIMPLIFIED_two_core_bus_count": 5,
            "S2_TOPOLOGY_SIMPLIFIED_cycle_edge_count": 6,
            "S2_TOPOLOGY_SIMPLIFIED_simple_cycle_rank": 2,
            "S2_TOPOLOGY_SIMPLIFIED_multigraph_cycle_rank": 3,
            "S3_QUOTIENT_CANDIDATE_simple_cycle_rank": 2,
            "S3_QUOTIENT_CANDIDATE_multigraph_cycle_rank": 3,
            "S4_FINAL_RELOADED_simple_cycle_rank": 2,
            "S4_FINAL_RELOADED_multigraph_cycle_rank": 3,
        },
        parallel_corridors={
            "parallel_corridor_count": 2,
            "parallel_only_cycle_count": 1,
            "parallel_multiplicity_on_simple_cycle_count": 1,
            "protected_parallel_corridor_count": 0,
        },
        mappings={"semantic_sha256": mapping_digest},
        named_gates=gates,
        protection_costs={
            "unique_protected_bus_count": cutoff_buses + 3,
            "unique_protected_line_count": cutoff_lines + 2,
        },
        runtime={"t_total": runtime, "t_topology": 1.0, "t_builder": 2.0},
        voltage_policy=policy,
    )


def test_base_and_cutoff_policy_are_explicit_and_preserve_protection_costs() -> None:
    base = _report("BASE")
    cutoff = _report("CUT_110", fingerprint="1" * 64,
                     cutoff_buses=4, cutoff_lines=3)

    assert base.voltage_policy.policy_id == "BASE"
    assert base.voltage_policy.enabled is False
    assert base.voltage_policy.requested_cutoff_kv is None
    assert base.voltage_policy.retained_bus_count == 0
    assert cutoff.voltage_layers[0].cutoff_mandatory_singleton_count == 4
    assert cutoff.voltage_layers[0].cutoff_identity_preserved_line_count == 3
    assert cutoff.parallel_corridors["parallel_only_cycle_count"] == 1
    assert cutoff.parallel_corridors["protected_parallel_corridor_count"] == 0
    single_markdown = render_comparison_markdown(cutoff)
    assert "## C1. Voltage policy" in single_markdown
    assert "## C2. Protection cost" in single_markdown
    assert "## H1. Named structural Gates" in single_markdown


def test_invalid_candidate_is_not_promoted_to_accepted_result() -> None:
    invalid = _report("CUT_110", status=_INVALID,
                      candidate_buses=7, accepted_buses=None,
                      candidate_lines=9, accepted_lines=None,
                      cycle_gate=False)
    report = CrossScenarioCompressionReport.create((_report("BASE"), invalid))
    row = next(item for item in report.scenarios if item.policy_id == "CUT_110")

    assert row.candidate_bus_count == 7
    assert row.accepted_bus_count is None
    assert row.candidate_bus_compression_fraction == pytest.approx(0.3)
    assert row.accepted_bus_compression_fraction is None
    assert {gate.gate_name for gate in report.gate_matrix
            if gate.policy_id == "CUT_110" and not gate.passed} == {"cycle_corridors"}


def test_equal_resolved_fingerprint_requires_equal_semantic_results() -> None:
    first = _report("CUT_110", fingerprint="2" * 64)
    same = _report("CUT_115", fingerprint="2" * 64)
    report = CrossScenarioCompressionReport.create((_report("BASE"), first, same))
    assert len(report.scenarios) == 3

    mismatch = _report("CUT_115", fingerprint="2" * 64, candidate_buses=5)
    with pytest.raises(ValueError, match="RESOLVED_POLICY_NONDETERMINISM"):
        CrossScenarioCompressionReport.create((_report("BASE"), first, mismatch))

    mismatched_map = _report("CUT_115", fingerprint="2" * 64,
                             mapping_digest="9" * 64)
    with pytest.raises(ValueError, match="RESOLVED_POLICY_NONDETERMINISM"):
        CrossScenarioCompressionReport.create((_report("BASE"), first, mismatched_map))

    # Different requested thresholds can resolve to the same actual layers.
    cut_35 = _report("CUT_35", fingerprint="5" * 64)
    cut_37 = _report("CUT_37", fingerprint="5" * 64)
    assert len(CrossScenarioCompressionReport.create(
        (_report("BASE"), cut_35, cut_37)).scenarios) == 3


def test_cross_scenario_deltas_and_all_long_matrices() -> None:
    base = _report("BASE", candidate_buses=7, accepted_buses=7,
                   candidate_lines=9, accepted_lines=9, cutoff_buses=0,
                   cutoff_lines=0, runtime=5.0)
    cutoff = _report("CUT_110", fingerprint="3" * 64,
                     candidate_buses=6, accepted_buses=6,
                     l1_buses=7,
                     candidate_lines=8, accepted_lines=8,
                     cutoff_buses=4, cutoff_lines=3, runtime=8.0,
                     cycle_gate=True)
    report = CrossScenarioCompressionReport.create((cutoff, base))
    row = next(item for item in report.scenarios if item.policy_id == "CUT_110")

    assert row.delta_l1_bus_vs_base == -1
    assert row.delta_candidate_bus_vs_base == -1
    assert row.delta_accepted_bus_vs_base == -1
    assert row.delta_bus_compression_fraction_vs_base == pytest.approx(0.1)
    assert row.delta_candidate_bus_compression_fraction_vs_base == pytest.approx(0.1)
    assert row.delta_cutoff_retained_bus_count_vs_base == 4
    assert row.delta_runtime_vs_base == pytest.approx(3.0)
    assert report.voltage_layer_matrix[0].voltage_kv == 110.0
    assert {row.stage for row in report.topology_matrix} == {
        "S1_PYPSA_FULL_PROJECTION", "S2_TOPOLOGY_SIMPLIFIED",
        "S3_QUOTIENT_CANDIDATE", "S4_FINAL_RELOADED",
    }
    assert len(report.device_transition_matrix) == 2
    assert {row.gate_name for row in report.gate_matrix} >= {
        "voltage_policy_resolved", "cycle_corridors",
    }
    assert {row.phase for row in report.runtime_matrix} == {
        "t_topology", "t_planning", "t_builder", "t_persist_reload_gate",
        "t_report", "t_total", "peak_rss",
    }

    invalid = _report("CUT_230", fingerprint="6" * 64,
                      status=_INVALID, accepted_buses=None, accepted_lines=None,
                      candidate_buses=5, candidate_lines=7, cycle_gate=False)
    invalid_report = CrossScenarioCompressionReport.create((base, invalid))
    invalid_row = next(item for item in invalid_report.scenarios
                       if item.policy_id == "CUT_230")
    assert invalid_row.delta_accepted_bus_vs_base is None
    assert invalid_row.delta_accepted_line_vs_base is None
    assert invalid_row.delta_bus_compression_fraction_vs_base is None
    assert invalid_row.delta_line_compression_fraction_vs_base is None
    assert invalid_row.delta_candidate_bus_compression_fraction_vs_base is not None


def test_json_markdown_and_parquet_are_deterministic_and_private(tmp_path) -> None:
    report = CrossScenarioCompressionReport.create((
        _report("BASE"),
        _report("CUT_110", fingerprint="4" * 64, cycle_gate=False,
                status=_INVALID, accepted_buses=None, accepted_lines=None),
    ))

    assert report.to_json() == report.to_json()
    markdown = report.to_markdown()
    assert markdown == report.to_markdown()
    assert "| CUT_110 | 110.0 | 110 | — | — | — | — |" in markdown
    assert "candidate" in markdown.lower()
    payload = report.to_json() + markdown
    for forbidden in ("sid:", "raw Bus", "raw Line", "/Users/", "/private/"):
        assert forbidden not in payload
    assert report.physical_fidelity_status == _PHYSICAL

    paths = report.save_parquet(tmp_path)
    assert set(paths) == {
        "scenarios", "voltage_layers", "topology", "device_transitions",
        "gates", "runtime", "deltas",
    }
    scenario_rows = pq.read_table(paths["scenarios"]).to_pylist()
    invalid = next(row for row in scenario_rows if row["policy_id"] == "CUT_110")
    assert invalid["accepted_bus_count"] is None
    assert invalid["candidate_bus_count"] == 6
    assert all("StableId" not in str(row) and "sid:" not in str(row)
               for table_path in paths.values()
               for row in pq.read_table(table_path).to_pylist())


def test_cross_report_constructor_accepts_reports_only() -> None:
    parameters = inspect.signature(CrossScenarioCompressionReport.create).parameters
    assert tuple(parameters) == ("reports",)


def test_mapping_semantic_digest_is_order_independent_and_semantic() -> None:
    source_a = StableId("n", "Bus", "a")
    source_b = StableId("n", "Bus", "b")
    target_a = StableId("n", "Bus", "cluster-a")
    target_b = StableId("n", "Bus", "cluster-b")
    rows = (
        MapEntry(source_a, target_a, "clustered", "run"),
        MapEntry(source_b, target_b, "clustered", "run"),
    )
    forward = MappingBundle(rows, (), ())
    reverse = MappingBundle(tuple(reversed(rows)), (), ())
    changed = MappingBundle((
        MapEntry(source_a, source_a, "preserved", "run"), rows[1]), (), ())

    assert forward.semantic_digest() == reverse.semantic_digest()
    assert forward.semantic_digest() != changed.semantic_digest()
