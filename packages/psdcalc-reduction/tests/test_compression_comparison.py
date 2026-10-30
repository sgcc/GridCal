"""Candidate and accepted structural-comparison semantics."""

import pytest
import pyarrow.parquet as pq
import json

from psdcalc.reduction.metrics.stage_inventory import CompressionStage, StageInventory
from psdcalc.reduction.reporting.comparison import (
    CompressionComparisonReport, VoltageLayerComparison,
    EnergyAggregateSummary, summarize_device_transitions,
)
from psdcalc.reduction.reporting.comparison_markdown import (
    write_comparison_report, persist_pipeline_comparison,
)
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry


def _stages() -> dict[str, StageInventory]:
    return {
        stage.value: StageInventory(stage, bus_count=count, line_count=lines)
        for stage, count, lines in zip(CompressionStage,
                                        (10, 10, 8, 6, 6),
                                        (9, 9, 8, 7, 7))
    }


def test_only_valid_final_gate_has_accepted_compression() -> None:
    """A candidate count never becomes an accepted result on invalid Gate."""
    common = dict(model_id="SYNTHETIC", run_id="run", config_sha256="a" * 64,
                  code_sha="b" * 40, tree_sha="c" * 40,
                  parallel_corridor_policy="preserve_protected_members",
                  stages=_stages(), physical_fidelity_status=(
                      "PHYSICAL_FIDELITY_NOT_YET_EVALUATED"))
    valid = CompressionComparisonReport.create(
        **common, final_structural_status=(
            "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"), failed_checks=())
    assert valid.candidate_bus_compression_fraction == pytest.approx(0.4)
    assert valid.accepted_bus_compression_fraction == pytest.approx(0.4)
    assert valid.accepted_final_bus_count == 6
    assert valid.accepted_final_line_count == 7
    invalid = CompressionComparisonReport.create(
        **common, final_structural_status=(
            "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID"),
        failed_checks=("protected_parallel_line_semantics",))
    assert invalid.candidate_bus_count == 6
    assert invalid.candidate_bus_compression_fraction == pytest.approx(0.4)
    assert invalid.accepted_final_bus_count is None
    assert invalid.accepted_final_line_count is None
    assert invalid.accepted_bus_compression_fraction is None
    assert invalid.accepted_line_compression_fraction is None


def test_stage_order_and_private_names_are_rejected() -> None:
    """Reports fail closed on missing stages and private identifiers."""
    kwargs = dict(model_id="SYNTHETIC", run_id="run", config_sha256="a" * 64,
                  code_sha="b" * 40, tree_sha="c" * 40,
                  parallel_corridor_policy="aggregate_corridor",
                  final_structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
                  physical_fidelity_status="PHYSICAL_FIDELITY_NOT_YET_EVALUATED",
                  failed_checks=())
    with pytest.raises(ValueError, match="five ordered stages"):
        CompressionComparisonReport.create(**kwargs, stages={})
    with pytest.raises(ValueError, match="public identifier"):
        CompressionComparisonReport.create(**{**kwargs, "run_id": "/Users/private"},
                                           stages=_stages())


def test_json_markdown_parquet_share_one_public_report(tmp_path) -> None:
    """Renderers agree on counts and do not leak private identifiers."""
    report = CompressionComparisonReport.create(
        model_id="SYNTHETIC", run_id="run", config_sha256="a" * 64,
        code_sha="b" * 40, tree_sha="c" * 40,
        parallel_corridor_policy="preserve_protected_members",
        final_structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID",
        physical_fidelity_status="PHYSICAL_FIDELITY_NOT_YET_EVALUATED",
        stages=_stages(), failed_checks=("cycle_corridors",))
    paths = write_comparison_report(report, tmp_path)
    data = json.loads(paths["compression_comparison.json"].read_text())
    table = pq.read_table(paths["stage_inventory.parquet"]).to_pylist()
    markdown = paths["compression_comparison.md"].read_text()
    assert data["candidate_bus_count"] == 6
    assert data["accepted_final_bus_count"] is None
    assert table[4]["bus_count"] == data["stages"][
        CompressionStage.FINAL_RELOADED.value]["bus_count"]
    assert "| S4_FINAL_RELOADED | 6 | 7 |" in markdown
    assert "PHYSICAL_FIDELITY_NOT_YET_EVALUATED" in markdown
    for path in paths.values():
        if path.suffix in {".json", ".md"}:
            text = path.read_text()
            assert "/Users/" not in text
            assert "sid:[" not in text


def test_mapping_transition_counts_use_source_rows() -> None:
    """Many-to-one Line mapping counts sources, distinct targets and absorption."""
    a, b, c = (StableId("n", "Line", key) for key in ("a", "b", "c"))
    target = StableId("n", "Line", "target")
    bundle = MappingBundle((), (
        MapEntry(a, target, "aggregated", "q"),
        MapEntry(b, target, "aggregated", "q"),
        MapEntry(c, None, "absorbed_internal", "q"),
    ), ())
    rows = summarize_device_transitions(bundle, {"Line": 3}, {"Line": 1})
    line, = [row for row in rows if row.component == "Line"]
    assert line.source_count == 3
    assert line.target_count == 1
    assert line.aggregated_count == 2
    assert line.absorbed_internal_count == 1
    assert line.mapping_coverage_fraction == 1.0


def test_report_artifacts_are_registered_in_manifest(tmp_path) -> None:
    """All public formats belong to the same run manifest."""
    report = CompressionComparisonReport.create(
        model_id="SYNTHETIC", run_id="run", config_sha256="a" * 64,
        code_sha="b" * 40, tree_sha="c" * 40,
        parallel_corridor_policy="aggregate_corridor",
        final_structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
        physical_fidelity_status="PHYSICAL_FIDELITY_NOT_YET_EVALUATED",
        stages=_stages(), failed_checks=())
    store = ArtifactStore(tmp_path, "run")
    paths = persist_pipeline_comparison(report, store)
    assert len(paths) == 11
    assert all(name.startswith("comparison_") for name in store.manifest.artifacts)
    assert len(store.manifest.artifacts) == len(paths)


def test_voltage_layer_sums_must_match_global_stage_counts() -> None:
    """An incomplete layer table cannot support a public compression claim."""
    layer = VoltageLayerComparison(
        voltage_kv=110, source_bus_count=9, projected_bus_count=9,
        simplified_bus_count=8, candidate_bus_count=6, final_bus_count=6,
        mandatory_singleton_count=2, ring_endpoint_count=2,
        transformer_endpoint_count=0, shunt_endpoint_count=0,
        control_endpoint_count=0, parallel_singleton_endpoint_count=2,
        requested_cluster_count=6, planned_cluster_count=6,
        actual_cluster_count=6, minimum_required_bus_count=6,
        candidate_compression_fraction=1 / 3,
        accepted_compression_fraction=1 / 3)
    with pytest.raises(ValueError, match="Voltage layer sums"):
        CompressionComparisonReport.create(
            model_id="SYNTHETIC", run_id="run", config_sha256="a" * 64,
            code_sha="b" * 40, tree_sha="c" * 40,
            parallel_corridor_policy="aggregate_corridor",
            final_structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
            physical_fidelity_status="PHYSICAL_FIDELITY_NOT_YET_EVALUATED",
            stages=_stages(), failed_checks=(), voltage_layers=(layer,))


def test_public_writer_rejects_raw_stable_id_and_private_path(tmp_path) -> None:
    """Raw identities never become public JSON, Markdown or Parquet."""
    common = dict(model_id="SYNTHETIC", run_id="run",
                  config_sha256="a" * 64, code_sha="b" * 40,
                  tree_sha="c" * 40, parallel_corridor_policy="aggregate_corridor",
                  final_structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID",
                  physical_fidelity_status="PHYSICAL_FIDELITY_NOT_YET_EVALUATED",
                  stages=_stages(), failed_checks=("cycle_corridors",))
    for private in ('sid:["n","Bus","private"]', "/Users/private/case.dat"):
        report = CompressionComparisonReport.create(**common,
                                                    topology={"source": private})
        with pytest.raises(ValueError, match="private source identity"):
            write_comparison_report(report, tmp_path)
    assert not (tmp_path / "compression_comparison.json").exists()


def test_semantic_report_content_is_independent_of_runtime() -> None:
    """A rerun may change timings without changing structural evidence."""
    common = dict(model_id="SYNTHETIC", run_id="run",
                  config_sha256="a" * 64, code_sha="b" * 40,
                  tree_sha="c" * 40, parallel_corridor_policy="aggregate_corridor",
                  final_structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
                  physical_fidelity_status="PHYSICAL_FIDELITY_NOT_YET_EVALUATED",
                  stages=_stages(), failed_checks=())
    first = CompressionComparisonReport.create(**common, runtime={"t_total": 1.0})
    second = CompressionComparisonReport.create(**common, runtime={"t_total": 2.0})
    a, b = first.to_dict(), second.to_dict()
    a.pop("runtime")
    b.pop("runtime")
    assert a == b


def test_energy_inventory_is_present_in_json_markdown_and_parquet(tmp_path) -> None:
    """The energy table is rendered from the canonical report object."""
    energy = EnergyAggregateSummary(
        stage=CompressionStage.PYPSA_FULL_PROJECTION, scope="global",
        voltage_kv=None, source_component=None, region=None,
        load_count=2, load_p_set=12.5, load_q_set=3.0,
        generator_count=1, generator_p_nom=20.0, generator_p_set=13.0,
        shunt_count=1, shunt_g=0.0, shunt_b=-0.2,
        weighted_load_p=None, weighted_load_q=None,
        weighted_generation_p=None)
    report = CompressionComparisonReport.create(
        model_id="SYNTHETIC", run_id="run", config_sha256="a" * 64,
        code_sha="b" * 40, tree_sha="c" * 40,
        parallel_corridor_policy="aggregate_corridor",
        final_structural_status="MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
        physical_fidelity_status="PHYSICAL_FIDELITY_NOT_YET_EVALUATED",
        stages=_stages(), failed_checks=(), energy_aggregates=(energy,))
    paths = write_comparison_report(report, tmp_path)
    data = json.loads(paths["compression_comparison.json"].read_text())
    parquet = pq.read_table(paths["energy_aggregate.parquet"]).to_pylist()
    markdown = paths["compression_comparison.md"].read_text()
    assert data["energy_aggregates"][0]["load_p_set"] == 12.5
    assert parquet[0]["load_p_set"] == 12.5
    assert "## F. Energy scale" in markdown
    assert "12.5" in markdown
