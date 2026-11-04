"""Synthetic AC evidence tests for identity-preserved network comparisons."""

from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import replace

import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.fidelity.pypsa_executor import (
    PowerFlowExecutionStatus,
    PowerFlowOperatingPointContract,
    PypsaAcPowerFlowExecutor,
)
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.reporting.comprehensive import EvidenceStatus, MetricGroup
from psdcalc.reduction.reporting.comprehensive import (
    ComprehensiveReductionReport,
    ReportProvenance,
)
from psdcalc.reduction.reporting.comprehensive_builder import build_comprehensive_run
from psdcalc.reduction.reporting.comprehensive_export import (
    export_report_bundle,
    render_markdown,
    render_metrics_csv,
    render_retained_fidelity_csv,
    render_voltage_layers_csv,
)
from psdcalc.reduction.reporting.retained_fidelity import (
    RetainedFidelityError,
    compare_retained_network_fidelity,
)


def _network(*, reduced: bool) -> pypsa.Network:
    network = pypsa.Network()
    network.add("Bus", "H0", v_nom=500.0)
    network.add("Bus", "H1", v_nom=500.0)
    network.add("Bus", "L0", v_nom=220.0)
    network.add("Bus", "M" if reduced else "L1", v_nom=220.0)
    if not reduced:
        network.add("Bus", "L2", v_nom=220.0)
    network.add("Line", "high", bus0="H0", bus1="H1", r=0.1, x=1.0, b=0.0, s_nom=200.0)
    network.add("Transformer", "T", bus0="H1", bus1="L0", r=0.01, x=0.1, s_nom=200.0)
    if reduced:
        network.add("Line", "eq", bus0="M", bus1="L0", r=0.05, x=0.5, b=0.0, s_nom=200.0)
        network.add("Load", "load_cluster", bus="M", p_set=50.0, q_set=15.0)
    else:
        network.add("Line", "P1", bus0="L0", bus1="L1", r=0.1, x=1.0, b=0.0, s_nom=100.0)
        network.add("Line", "P2", bus0="L0", bus1="L1", r=0.1, x=1.0, b=0.0, s_nom=100.0)
        network.add("Line", "internal", bus0="L1", bus1="L2", r=0.1, x=0.3, b=0.0, s_nom=100.0)
        network.add("Load", "load_1", bus="L1", p_set=30.0, q_set=8.0)
        network.add("Load", "load_2", bus="L2", p_set=20.0, q_set=7.0)
    network.add("Generator", "slack", bus="H0", control="Slack", p_set=0.0,
                q_set=0.0, p_nom=200.0, vm_pu=1.0)
    return network


def _contract(network: pypsa.Network) -> PowerFlowOperatingPointContract:
    return PowerFlowOperatingPointContract(
        snapshots=(network.snapshots[0],),
        solver="pypsa.newton_raphson",
        tolerance=1e-8,
        max_iterations=None,
        distribute_slack=False,
        slack_weights=None,
        use_seed=False,
        control_policy="preserve_source_controls",
        tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest="a" * 64,
        device_schedule_digest="b" * 64,
    )


def _mapping(source: pypsa.Network, target: pypsa.Network) -> MappingBundle:
    source_sidecar = PypsaAdapter().identity_sidecar(source, "retained-synthetic")
    target_sidecar = PypsaAdapter().identity_sidecar(target, "retained-synthetic")

    def sid(sidecar, component: str, name: str) -> StableId:
        return sidecar.source_id(component, name)

    buses = (
        *(MapEntry(sid(source_sidecar, "Bus", name), sid(target_sidecar, "Bus", name),
                   "preserved", "test") for name in ("H0", "H1", "L0")),
        MapEntry(sid(source_sidecar, "Bus", "L1"), sid(target_sidecar, "Bus", "M"),
                 "clustered", "test"),
        MapEntry(sid(source_sidecar, "Bus", "L2"), sid(target_sidecar, "Bus", "M"),
                 "clustered", "test"),
    )
    branches = (
        MapEntry(sid(source_sidecar, "Line", "high"), sid(target_sidecar, "Line", "high"),
                 "preserved", "test"),
        MapEntry(sid(source_sidecar, "Line", "P1"), sid(target_sidecar, "Line", "eq"),
                 "aggregated", "test"),
        MapEntry(sid(source_sidecar, "Line", "P2"), sid(target_sidecar, "Line", "eq"),
                 "aggregated", "test"),
        MapEntry(sid(source_sidecar, "Line", "internal"), None,
                 "absorbed_internal", "test"),
        MapEntry(sid(source_sidecar, "Transformer", "T"),
                 sid(target_sidecar, "Transformer", "T"), "preserved", "test"),
    )
    devices = (
        MapEntry(sid(source_sidecar, "Generator", "slack"),
                 sid(target_sidecar, "Generator", "slack"), "preserved", "test"),
        MapEntry(sid(source_sidecar, "Load", "load_1"),
                 sid(target_sidecar, "Load", "load_cluster"), "aggregated", "test"),
        MapEntry(sid(source_sidecar, "Load", "load_2"),
                 sid(target_sidecar, "Load", "load_cluster"), "aggregated", "test"),
    )
    return MappingBundle(tuple(buses), tuple(branches), tuple(devices))


def _solved_pair():
    source, target = _network(reduced=False), _network(reduced=True)
    source_sidecar = PypsaAdapter().identity_sidecar(source, "retained-synthetic")
    target_sidecar = PypsaAdapter().identity_sidecar(target, "retained-synthetic")
    executor = PypsaAcPowerFlowExecutor()
    source_pf = executor.solve(source, source_sidecar, _contract(source))
    target_pf = executor.solve(target, target_sidecar, _contract(target))
    assert source_pf.status is PowerFlowExecutionStatus.PF_CONVERGED
    assert target_pf.status is PowerFlowExecutionStatus.PF_CONVERGED
    return source, target, source_sidecar, target_sidecar, _mapping(source, target), source_pf, target_pf


def test_synthetic_pf_compares_preserved_objects_and_parallel_boundary():
    source, target, source_sidecar, target_sidecar, mapping, source_pf, target_pf = _solved_pair()
    report = compare_retained_network_fidelity(
        source_network=source,
        target_network=target,
        source_sidecar=source_sidecar,
        target_sidecar=target_sidecar,
        mappings=mapping,
        source_pf=source_pf,
        target_pf=target_pf,
        cutoff_kv=(500.0, 220.0),
    )
    groups = {row.group: row for row in report.groups}
    retained = groups[MetricGroup.RETAINED_BACKBONE]
    assert retained.retained_bus_count.value == 3
    assert retained.bus_vm.sample_count.value == 3
    assert retained.line_p.sample_count.value == 2
    assert retained.transformer_p.sample_count.value == 2
    assert retained.mapping_coverage.value == 1.0
    assert groups[MetricGroup.AGGREGATED_REGION].bus_vm.sample_count.state is EvidenceStatus.NOT_RECORDED
    assert groups[MetricGroup.WHOLE_NETWORK].bus_vm.sample_count.value == 3

    interface = report.interfaces
    assert interface.interface_count.value == 1
    assert interface.source_branch_member_count.value == 2
    assert interface.target_branch_count.value == 1
    assert interface.p_error_mw.sample_count.value == 1
    assert interface.q_error_mvar.sample_count.value == 1
    assert math.isfinite(interface.p_error_mw.rmse.value)
    assert interface.source_p_mw.maximum.value == pytest.approx(
        source_pf.branch_p0[source_sidecar.source_id("Line", "P1")]
        + source_pf.branch_p0[source_sidecar.source_id("Line", "P2")])
    assert interface.target_p_mw.maximum.value == pytest.approx(
        target_pf.branch_p1[target_sidecar.source_id("Line", "eq")])
    assert abs(interface.p_error_mw.maximum.value) < 0.01
    assert any(layer.cutoff_kv == 500.0 and layer.voltage_kv == 500.0
               for layer in report.layers)
    assert any(layer.cutoff_kv == 220.0 and layer.voltage_kv == 220.0
               for layer in report.layers)


def test_missing_pf_data_and_empty_groups_are_not_recorded():
    source, target = _network(reduced=False), _network(reduced=True)
    source_sidecar = PypsaAdapter().identity_sidecar(source, "retained-synthetic")
    target_sidecar = PypsaAdapter().identity_sidecar(target, "retained-synthetic")
    report = compare_retained_network_fidelity(
        source_network=source,
        target_network=target,
        source_sidecar=source_sidecar,
        target_sidecar=target_sidecar,
        mappings=_mapping(source, target),
        source_pf=None,
        target_pf=None,
        cutoff_kv=(600.0, 100.0),
    )
    assert all(group.bus_vm.maximum.state is EvidenceStatus.NOT_RECORDED for group in report.groups)
    assert all(layer.bus_vm.maximum.state is EvidenceStatus.NOT_RECORDED for layer in report.layers)
    low_layer = next(row for row in report.layers
                     if row.cutoff_kv == 100.0 and row.voltage_kv == 220.0)
    assert low_layer.policy_protected_bus_count.value == 3
    retained = next(row for row in report.groups if row.group is MetricGroup.RETAINED_BACKBONE)
    assert retained.mapping_coverage.state is EvidenceStatus.AVAILABLE
    assert retained.mapping_coverage.value > 0.0
    empty = compare_retained_network_fidelity(
        source_network=source, target_network=target,
        source_sidecar=source_sidecar, target_sidecar=target_sidecar,
        mappings=None, source_pf=None, target_pf=None, cutoff_kv=(600.0,),
    )
    empty_retained = next(row for row in empty.groups if row.group is MetricGroup.RETAINED_BACKBONE)
    assert empty_retained.bus_vm.maximum.state is EvidenceStatus.NOT_RECORDED


def test_bus_angle_comparison_is_invariant_to_reference_offset():
    source, target, source_sidecar, target_sidecar, mapping, source_pf, target_pf = _solved_pair()
    baseline = compare_retained_network_fidelity(
        source_network=source, target_network=target,
        source_sidecar=source_sidecar, target_sidecar=target_sidecar,
        mappings=mapping, source_pf=source_pf, target_pf=target_pf,
        cutoff_kv=(500.0,))
    shifted = replace(target_pf, bus_va_deg={key: value + 37.0
                                             for key, value in target_pf.bus_va_deg.items()})
    offset = compare_retained_network_fidelity(
        source_network=source, target_network=target,
        source_sidecar=source_sidecar, target_sidecar=target_sidecar,
        mappings=mapping, source_pf=source_pf, target_pf=shifted,
        cutoff_kv=(500.0,))
    base_row = next(row for row in baseline.groups
                    if row.group is MetricGroup.RETAINED_BACKBONE)
    offset_row = next(row for row in offset.groups
                      if row.group is MetricGroup.RETAINED_BACKBONE)
    assert offset_row.bus_va.rmse.value == pytest.approx(base_row.bus_va.rmse.value, abs=1e-10)


def test_partial_region_injection_is_not_compared_as_complete_cluster():
    source, target, source_sidecar, target_sidecar, mapping, source_pf, target_pf = _solved_pair()
    omitted = source_sidecar.source_id("Bus", "L2")
    partial_source_pf = replace(
        source_pf,
        bus_p_mw={key: value for key, value in source_pf.bus_p_mw.items() if key != omitted},
    )
    report = compare_retained_network_fidelity(
        source_network=source, target_network=target,
        source_sidecar=source_sidecar, target_sidecar=target_sidecar,
        mappings=mapping, source_pf=partial_source_pf, target_pf=target_pf,
        cutoff_kv=(500.0,))
    assert report.interfaces.region_net_injection_p_error_mw.sample_count.state is EvidenceStatus.NOT_RECORDED


def test_incomplete_mapping_reports_coverage_and_missing_preserved_objects():
    source, target, source_sidecar, target_sidecar, mapping, source_pf, target_pf = _solved_pair()
    incomplete_bus_rows = tuple(row for row in mapping.bus_map
                                if row.source_id != source_sidecar.source_id("Bus", "H1"))
    incomplete = MappingBundle(incomplete_bus_rows, mapping.branch_map[:-1], mapping.device_map)
    report = compare_retained_network_fidelity(
        source_network=source,
        target_network=target,
        source_sidecar=source_sidecar,
        target_sidecar=target_sidecar,
        mappings=incomplete,
        source_pf=source_pf,
        target_pf=target_pf,
        cutoff_kv=(500.0,),
    )
    retained = next(row for row in report.groups if row.group is MetricGroup.RETAINED_BACKBONE)
    assert retained.missing_bus_count.value == 1
    assert retained.mapping_coverage.value < 1.0
    assert retained.bus_vm.sample_count.value == 2


def test_three_winding_t_leg_projection_counts_backend_rows_without_boundary_duplication():
    source = pypsa.Network()
    for name, voltage in (("HV", 500.0), ("MV", 220.0), ("LV", 110.0), ("STAR", 220.0)):
        source.add("Bus", name, v_nom=voltage)
    for name, bus, reactance in (("leg_h", "HV", 0.1),
                                 ("leg_m", "MV", 0.2),
                                 ("leg_l", "LV", 0.3)):
        source.add("Transformer", name, bus0=bus, bus1="STAR", r=0.01,
                   x=reactance, s_nom=100.0)
    target = source.copy()
    source_sidecar = PypsaAdapter().identity_sidecar(source, "three-winding")
    target_sidecar = PypsaAdapter().identity_sidecar(target, "three-winding")

    def rows(sidecar, component, names):
        return tuple(MapEntry(sidecar.source_id(component, name),
                              sidecar.source_id(component, name), "preserved", "test")
                     for name in names)

    mapping = MappingBundle(
        rows(source_sidecar, "Bus", ("HV", "MV", "LV", "STAR")),
        rows(source_sidecar, "Transformer", ("leg_h", "leg_m", "leg_l")),
        (),
    )
    report = compare_retained_network_fidelity(
        source_network=source, target_network=target,
        source_sidecar=source_sidecar, target_sidecar=target_sidecar,
        mappings=mapping, source_pf=None, target_pf=None,
        cutoff_kv=(500.0, 220.0, 110.0))
    assert report.interfaces.interface_count.value == 0
    assert all(row.transformer_count_basis == "BACKEND_ROWS" for row in report.layers)
    mv_row = next(row for row in report.layers if row.cutoff_kv == 220.0
                  and row.voltage_kv == 220.0)
    assert mv_row.source_transformer_count.value == 3
    assert mv_row.retained_transformer_count.value == 3


def test_mapping_reference_to_missing_target_fails_closed():
    source, target, source_sidecar, target_sidecar, mapping, source_pf, target_pf = _solved_pair()
    bad = MapEntry(mapping.bus_map[0].source_id, StableId("synthetic", "Bus", "absent"),
                   "preserved", "tampered")
    invalid = MappingBundle((bad, *mapping.bus_map[1:]), mapping.branch_map, mapping.device_map)
    with pytest.raises(RetainedFidelityError):
        compare_retained_network_fidelity(
            source_network=source, target_network=target,
            source_sidecar=source_sidecar, target_sidecar=target_sidecar,
            mappings=invalid, source_pf=source_pf, target_pf=target_pf,
            cutoff_kv=(500.0,),
        )


def test_comprehensive_report_exports_retained_fidelity_and_not_recorded(tmp_path):
    source, target, source_sidecar, target_sidecar, mapping, source_pf, target_pf = _solved_pair()
    run = build_comprehensive_run(
        provenance=ReportProvenance(
            study_id="retained-synthetic", run_id="retained-run",
            package_version="0.8.0", pypsa_version=pypsa.__version__),
        source_network=source,
        source_sidecar=source_sidecar,
        target_network=target,
        target_sidecar=target_sidecar,
        mappings=mapping,
        source_pf=source_pf,
        candidate_pf=target_pf,
        retained_cutoffs_kv=(500.0, 220.0),
    )
    report = ComprehensiveReductionReport(report_id="retained-report", runs=(run,))

    assert len(run.retained_network_fidelity.groups) == 4
    assert run.retained_network_fidelity.mapping_digest == mapping.semantic_digest()
    assert run.retained_network_fidelity.source_pf_digest is not None
    assert run.retained_network_fidelity.target_pf_digest is not None
    retained = next(row for row in run.retained_network_fidelity.groups
                    if row.group is MetricGroup.RETAINED_BACKBONE)
    assert retained.bus_vm.sample_count.value == 3
    assert len(run.retained_network_fidelity.layers) == 4
    markdown = render_markdown(report)
    metrics_csv = render_metrics_csv(report)
    layers_csv = render_voltage_layers_csv(report)
    assert "RETAINED_BACKBONE" in markdown
    assert "retained_network_fidelity" in metrics_csv
    rmse = retained.bus_vm.rmse.value
    assert format(rmse, ".17g") in markdown
    metric_rows = list(csv.DictReader(io.StringIO(metrics_csv)))
    metrics_csv_rows = list(csv.reader(io.StringIO(metrics_csv)))
    assert all(len(row) == len(metrics_csv_rows[0]) for row in metrics_csv_rows)
    bus_rmse_row = next(row for row in metric_rows
                        if row["section"] == "retained_network_fidelity.RETAINED_BACKBONE"
                        and row["metric"] == "bus_vm.rmse")
    assert float(bus_rmse_row["value"]) == pytest.approx(rmse)
    retained_csv = render_retained_fidelity_csv(report)
    assert "voltage_layer" in retained_csv
    assert ",500," in retained_csv
    retained_csv_rows = list(csv.reader(io.StringIO(retained_csv)))
    assert all(len(row) == len(retained_csv_rows[0]) for row in retained_csv_rows)
    assert "preserved_bus_identity_coverage" not in layers_csv
    paths = export_report_bundle(report, tmp_path)
    assert "retained_fidelity_csv" in paths
    assert paths["json"].read_text(encoding="utf-8") == report.to_json()
    json_data = json.loads(paths["json"].read_text(encoding="utf-8"))
    json_groups = json_data["runs"][0]["retained_network_fidelity"]["groups"]
    json_retained = next(row for row in json_groups if row["group"] == "RETAINED_BACKBONE")
    json_rmse = json_retained["bus_vm"]["rmse"]["value"]
    assert json_rmse == pytest.approx(rmse)
    assert paths["retained_fidelity_csv"].read_text(encoding="utf-8") == retained_csv
    public_json = report.to_json()
    for row in (*mapping.bus_map, *mapping.branch_map, *mapping.device_map):
        assert row.source_id.encode() not in public_json
        if row.target_id is not None:
            assert row.target_id.encode() not in public_json

    no_pf_run = build_comprehensive_run(
        provenance=ReportProvenance(
            study_id="retained-synthetic", run_id="no-pf-run",
            package_version="0.8.0", pypsa_version=pypsa.__version__),
        source_network=source,
        source_sidecar=source_sidecar,
        target_network=target,
        target_sidecar=target_sidecar,
        mappings=mapping,
        retained_cutoffs_kv=(500.0,),
    )
    no_pf_group = next(row for row in no_pf_run.retained_network_fidelity.groups
                       if row.group is MetricGroup.RETAINED_BACKBONE)
    assert no_pf_group.bus_vm.maximum.state is EvidenceStatus.NOT_RECORDED
