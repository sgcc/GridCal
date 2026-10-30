"""Offline end-to-end persistence of opt-in protected parallel members."""

import json
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from psdcalc.reduction.core.ids import StableId

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.loader import load_config
from psdcalc.reduction.core.artifact_store import ArtifactStore
from psdcalc.reduction.pipelines.multi_voltage import MultiVoltageQuotientPipeline
from psdcalc.reduction.topology.parallel_provenance import (
    LPlusAssociation, ParallelSourceLedger, SourceProfileContract,
)
from psdcalc.reduction.validation.multi_voltage_final import network_semantic_signature

from test_three_voltage_pipeline import pipeline_config, three_voltage_source


def _config(path, run_id):
    base = pipeline_config(path, run_id=run_id, n_clusters=8)
    raw = base.model_dump(mode="json")
    raw["aggregation"]["parallel_corridors"] = {
        "policy": "preserve_protected_members"}
    return load_config(raw)


def test_parallel_only_cycle_survives_full_pipeline(tmp_path) -> None:
    """The final reloaded network keeps two independent active Lines."""
    source = three_voltage_source()
    source.add("Line", "uv2", bus0="v", bus1="u", r=0.02, x=0.13,
               s_nom=90)
    input_path = tmp_path / "source.nc"
    source.export_to_netcdf(input_path)
    sidecar = PypsaAdapter().identity_sidecar(source, "n")
    before = network_semantic_signature(source)
    config = _config(input_path, "protected")
    store = ArtifactStore(tmp_path / "runs", config.run.id)
    result = MultiVoltageQuotientPipeline().run(
        original_network=source, source_sidecar=sidecar,
        config=config, store=store)
    assert result.structural.status == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID")
    assert result.structural.checks["protected_parallel_member_coverage"]
    assert result.structural.checks["protected_parallel_line_semantics"]
    assert result.structural.checks["cycle_corridors"]
    assert network_semantic_signature(source) == before
    assert all(row.mapping_type == "preserved"
               for row in result.composed_mappings.line_map
               if row.source_id.source_key in {"uv", "uv2"})
    assert json.loads((store.run_dir / "validation/multi_voltage_structural.json")
                      .read_text())["status"] == result.structural.status
    assert result.manifest.parallel_corridor_policy == "preserve_protected_members"
    assert "preserve_protected_members" in (
        store.run_dir / "resolved_config.yaml").read_text()
    assert json.loads((store.run_dir / "validation/preliminary.json").read_text())[
        "parallel_corridor_policy"] == "preserve_protected_members"
    assert json.loads((store.run_dir / "validation/multi_voltage_structural.json")
                      .read_text())["parallel_corridor_policy"] == (
        "preserve_protected_members")
    assert any(record.parameters.get("parallel_corridor_policy") ==
               "preserve_protected_members" for record in result.records)
    report = result.opportunity_report
    assert report.parallel_cycle_type_counts["parallel_only"] == 1
    assert report.parallel_corridor_policy == "preserve_protected_members"
    assert report.parallel_corridor_count == 1
    assert report.double_circuit_corridor_count == 1
    assert report.parallel_only_cycle_count == 1
    assert report.parallel_multiplicity_on_simple_cycle_count == 0
    assert report.protected_parallel_corridor_count == 1
    assert report.protected_active_member_count == 2
    assert report.protected_total_member_count == 2
    assert report.detached_member_count == 2
    assert report.reconnected_member_count == 2
    assert report.preserved_mapping_count == 2
    assert report.line_semantic_hash_pass_count == 2
    assert report.parallel_singleton_endpoint_count == 2
    assert report.protected_member_retention_line_cost == 1
    assert report.accepted_structural_compression_fraction is not None


def test_l_plus_both_terminals_type_and_dynamic_line_survive_reload(tmp_path) -> None:
    """Preserved LineType, time series, and terminal Shunts survive NetCDF."""
    source = three_voltage_source()
    source.add("Line", "uv2", bus0="v", bus1="u", r=0.02, x=0.13,
               s_nom=90)
    source.add("LineType", "U", r_per_length=0.1, x_per_length=0.2,
               c_per_length=0.01, i_nom=1.0)
    source.lines.loc["uv", "type"] = "U"
    source.lines.loc["uv2", "type"] = "U"
    source.components["Line"].dynamic["s_max_pu"] = pd.DataFrame(
        {"uv": [0.9, 0.8], "uv2": [0.7, 0.6]}, index=source.snapshots)
    source.add("ShuntImpedance", "lplus_from", bus="u", b=-0.4)
    source.add("ShuntImpedance", "lplus_to", bus="v", b=-0.6)
    ids = PypsaAdapter().identity_sidecar(source, "n")
    base = ids.source_id("Line", "uv")
    ledger = ParallelSourceLedger(
        SourceProfileContract("programmatic", "unavailable"), (),
        tuple(LPlusAssociation(base, "lplus-card", side,
                               ids.source_id("Bus", bus),
                               ids.source_id("ShuntImpedance", shunt), value)
              for side, bus, shunt, value in (
                  ("FROM", "u", "lplus_from", -0.4),
                  ("TO", "v", "lplus_to", -0.6))))
    input_path = tmp_path / "source.nc"
    source.export_to_netcdf(input_path)
    config = _config(input_path, "lplus")
    store = ArtifactStore(tmp_path / "runs", config.run.id)
    result = MultiVoltageQuotientPipeline(source_ledger=ledger).run(
        original_network=source, source_sidecar=ids,
        config=config, store=store)
    assert result.structural.status == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID")
    assert all(result.structural.checks[key] for key in (
        "protected_parallel_line_semantics", "protected_parallel_type_references",
        "l_plus_association_coverage", "l_plus_terminal_owner_consistency",
        "l_plus_semantic_round_trip", "parallel_evidence_round_trip"))
    assert result.opportunity_report.l_plus_association_count == 2
    assert result.opportunity_report.l_plus_shunt_preserved_count == 2
    assert (store.run_dir / "input/parallel_provenance/l_plus_associations.parquet").exists()


def test_protected_dynamic_line_multiindex_snapshots(tmp_path) -> None:
    """Complete Line semantics include period-indexed time series."""
    source = three_voltage_source()
    source.add("Line", "uv2", bus0="u", bus1="v", r=0.02, x=0.13)
    source.set_investment_periods([2030, 2040])
    source.components["Line"].dynamic["s_max_pu"] = pd.DataFrame(
        {"uv": [0.9, 0.8, 0.85, 0.75],
         "uv2": [0.7, 0.6, 0.65, 0.55]}, index=source.snapshots)
    source.components["Load"].dynamic["p_set"] = pd.DataFrame(
        {"demand": [5.0, 7.0, 6.0, 8.0]}, index=source.snapshots)
    source.components["Transformer"].dynamic["s_max_pu"] = pd.DataFrame(
        {"tx1": [0.9, 0.8, 0.85, 0.75]}, index=source.snapshots)
    source.snapshot_weightings["objective"] = [1.0, 2.0, 3.0, 4.0]
    source.investment_period_weightings["objective"] = [1.5, 2.5]
    signature = network_semantic_signature(source)
    input_path = tmp_path / "source.nc"
    source.export_to_netcdf(input_path)
    config = _config(input_path, "multiindex-protected")
    result = MultiVoltageQuotientPipeline().run(
        original_network=source,
        source_sidecar=PypsaAdapter().identity_sidecar(source, "n"),
        config=config,
        store=ArtifactStore(tmp_path / "runs", config.run.id))
    assert result.structural.status == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID")
    assert result.structural.checks["protected_parallel_line_semantics"]
    assert network_semantic_signature(source) == signature


def test_corrupt_netcdf_protected_line_is_structurally_invalid(tmp_path) -> None:
    """A changed protected Line parameter fails the final semantic Gate."""
    class CorruptLineAdapter(PypsaAdapter):
        """Alter only the clean L2 reload."""

        def load(self, path, format):
            """Change one protected parameter after public NetCDF import."""
            n = super().load(path, format)
            if path.name == "L2.nc":
                name = next(name for name in n.lines.index
                            if ":protected:" in str(name))
                n.lines.loc[name, "r"] = 99.0
            return n

    source = three_voltage_source()
    source.add("Line", "uv2", bus0="v", bus1="u", r=0.02, x=0.13)
    path = tmp_path / "source.nc"
    source.export_to_netcdf(path)
    config = _config(path, "corrupt-line")
    store = ArtifactStore(tmp_path / "runs", config.run.id)
    result = MultiVoltageQuotientPipeline(CorruptLineAdapter()).run(
        original_network=source,
        source_sidecar=PypsaAdapter().identity_sidecar(source, "n"),
        config=config, store=store)
    assert result.structural.status == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID")
    assert result.structural.checks["protected_parallel_line_semantics"] is False
    assert store.manifest.status == result.structural.status


def test_dropped_netcdf_protected_line_fails_member_gate(tmp_path) -> None:
    """Equal topology elsewhere cannot excuse a missing Line member."""
    class DropLineAdapter(PypsaAdapter):
        """Remove a protected member only after L2 import."""

        def load(self, path, format):
            """Return a corrupted detached reload."""
            n = super().load(path, format)
            if path.name == "L2.nc":
                name = next(name for name in n.lines.index
                            if ":protected:" in str(name))
                n.remove("Line", name)
            return n

    source = three_voltage_source()
    source.add("Line", "uv2", bus0="v", bus1="u", r=0.02, x=0.13)
    path = tmp_path / "source.nc"
    source.export_to_netcdf(path)
    config = _config(path, "dropped-line")
    result = MultiVoltageQuotientPipeline(DropLineAdapter()).run(
        original_network=source,
        source_sidecar=PypsaAdapter().identity_sidecar(source, "n"),
        config=config,
        store=ArtifactStore(tmp_path / "runs", config.run.id))
    assert result.structural.status == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID")
    assert not result.structural.checks["protected_parallel_member_coverage"]
    assert not result.structural.checks["protected_parallel_multiplicity"]


def test_corrupt_parquet_protected_row_is_structurally_invalid(tmp_path) -> None:
    """A valid Parquet edit to map semantics fails readback and identity Gates."""
    class CorruptMapStore(ArtifactStore):
        """Change one saved quotient row before its required readback."""

        def record_artifact(self, name, relative):
            """Alter both branch and line projection consistently once."""
            super().record_artifact(name, relative)
            if name != "quotient_line_map":
                return
            for filename in ("branch_map", "line_map"):
                path = self.path(f"maps/quotient/{filename}.parquet")
                rows = pq.read_table(path).to_pylist()
                for row in rows:
                    if StableId.decode(row["source_id"]).source_key == "uv2":
                        row["mapping_type"] = "aggregated"
                pq.write_table(pa.Table.from_pylist(rows), path)

    source = three_voltage_source()
    source.add("Line", "uv2", bus0="v", bus1="u", r=0.02, x=0.13)
    path = tmp_path / "source.nc"
    source.export_to_netcdf(path)
    config = _config(path, "corrupt-parquet")
    store = CorruptMapStore(tmp_path / "runs", config.run.id)
    result = MultiVoltageQuotientPipeline().run(
        original_network=source,
        source_sidecar=PypsaAdapter().identity_sidecar(source, "n"),
        config=config, store=store)
    assert result.structural.status == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID")
    assert not result.structural.checks["protected_parallel_mapping_round_trip"]
    assert not result.structural.checks["protected_parallel_line_identity"]


def test_corrupt_target_sidecar_is_structurally_invalid(tmp_path) -> None:
    """A readable target StableId swap fails the final evidence Gate."""
    class CorruptSidecarStore(ArtifactStore):
        """Swap a protected Line binding after the target sidecar is saved."""

        def record_artifact(self, name, relative):
            """Corrupt one readback binding while preserving JSON shape."""
            super().record_artifact(name, relative)
            if name == "target_ids":
                path = self.path(relative)
                rows = json.loads(path.read_text())
                for row in rows:
                    if (row["component"] == "Line" and
                            StableId.decode(row["source_id"]).source_key == "uv2"):
                        row["source_id"] = StableId(
                            "n", "Line", "replacement").encode()
                path.write_text(json.dumps(rows))

    source = three_voltage_source()
    source.add("Line", "uv2", bus0="v", bus1="u", r=0.02, x=0.13)
    path = tmp_path / "source.nc"
    source.export_to_netcdf(path)
    config = _config(path, "corrupt-sidecar")
    store = CorruptSidecarStore(tmp_path / "runs", config.run.id)
    result = MultiVoltageQuotientPipeline().run(
        original_network=source,
        source_sidecar=PypsaAdapter().identity_sidecar(source, "n"),
        config=config, store=store)
    assert result.structural.status == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID")
    assert not result.structural.checks["parallel_evidence_round_trip"]
    assert not result.structural.checks["protected_parallel_target_provenance"]
    assert store.manifest.status == result.structural.status


def test_default_policy_keeps_ordinary_quotient_gate(tmp_path) -> None:
    """Omitting the selector retains 0.2.0 parallel aggregation behavior."""
    source = three_voltage_source()
    source.add("Line", "uv2", bus0="u", bus1="v", r=0.02, x=0.13)
    path = tmp_path / "source.nc"
    source.export_to_netcdf(path)
    config = pipeline_config(path, run_id="legacy-parallel", n_clusters=8)
    result = MultiVoltageQuotientPipeline().run(
        original_network=source,
        source_sidecar=PypsaAdapter().identity_sidecar(source, "n"),
        config=config,
        store=ArtifactStore(tmp_path / "runs", config.run.id))
    assert result.manifest.parallel_corridor_policy == "aggregate_corridor"
    assert result.structural.status == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID")
    assert result.structural.checks["cycle_corridors"] is False
    assert "protected_parallel_member_coverage" not in result.structural.checks
    assert result.opportunity_report.parallel_corridor_count == 1
    assert result.opportunity_report.parallel_only_cycle_count == 1
    assert result.opportunity_report.protected_total_member_count == 0
    assert result.opportunity_report.accepted_structural_compression_fraction is None


def test_mixed_active_inactive_corridor_survives_pipeline(tmp_path) -> None:
    """An inactive circuit is mapped but never counted as an active cycle."""
    source = three_voltage_source()
    source.add("Line", "uv2", bus0="v", bus1="u", r=0.02, x=0.13,
               active=False)
    path = tmp_path / "source.nc"
    source.export_to_netcdf(path)
    config = _config(path, "mixed-active")
    result = MultiVoltageQuotientPipeline().run(
        original_network=source,
        source_sidecar=PypsaAdapter().identity_sidecar(source, "n"),
        config=config,
        store=ArtifactStore(tmp_path / "runs", config.run.id))
    assert result.structural.status == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID")
    assert result.structural.checks["protected_parallel_active_state_sets"]
    assert result.opportunity_report.protected_active_member_count == 1
    assert result.opportunity_report.protected_inactive_member_count == 1


def test_protected_parallel_deterministic_rerun(tmp_path) -> None:
    """Same source and seed yield identical maps, Gates and report counts."""
    source = three_voltage_source()
    source.add("Line", "uv2", bus0="v", bus1="u", r=0.02, x=0.13)
    path = tmp_path / "source.nc"
    source.export_to_netcdf(path)
    ids = PypsaAdapter().identity_sidecar(source, "n")
    results = []
    for run_id in ("rerun-a", "rerun-b"):
        config = _config(path, run_id)
        results.append(MultiVoltageQuotientPipeline().run(
            original_network=source, source_sidecar=ids,
            config=config,
            store=ArtifactStore(tmp_path / "runs", run_id)))
    first, second = results
    assert first.structural.status == second.structural.status
    assert first.structural.checks == second.structural.checks
    assert (first.composed_mappings.canonical_rows() ==
            second.composed_mappings.canonical_rows())
    assert first.opportunity_report == second.opportunity_report
