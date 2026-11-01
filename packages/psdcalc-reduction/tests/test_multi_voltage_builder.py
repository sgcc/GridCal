"""Pure in-memory multi-voltage quotient construction on small PyPSA grids."""

from dataclasses import replace
from enum import Enum

import pandas as pd
import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import AggregationConfig
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.exceptions import (
    BackendCapabilityError,
    ConfigSemanticError,
    MaterializationError,
)
from psdcalc.reduction.materializers.multi_voltage import MultiVoltageQuotientBuilder
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.protection.engine import ProtectionEngine
from psdcalc.reduction.validation.multi_voltage import (
    AggregationInvariantSpec, MultiVoltageStructuralValidator,
)


def three_voltage_network() -> pypsa.Network:
    """Create a Transformer-linked 110/35/10-kV network with time-series load."""
    network = pypsa.Network()
    snapshots = pd.date_range("2030-01-01", periods=2, freq="h")
    network.set_snapshots(snapshots)
    network.snapshot_weightings.loc[:, "objective"] = [1, 2]
    for name, voltage in (("ha", 110), ("hb", 110), ("hc", 110),
                          ("ma", 35), ("mb", 35), ("la", 10), ("lb", 10)):
        network.add("Bus", name, v_nom=voltage)
    for name, a, b in (("hab", "ha", "hb"), ("hbc", "hb", "hc"),
                       ("mab", "ma", "mb"), ("lab", "la", "lb")):
        network.add("Line", name, bus0=a, bus1=b, r=0.01, x=0.1, s_nom=100)
    network.add("Transformer", "tx_hm", bus0="ha", bus1="ma",
                r=0.01, x=0.1, s_nom=100)
    network.add("Transformer", "tx_ml", bus0="mb", bus1="la",
                r=0.02, x=0.2, s_nom=50)
    network.add("Generator", "slack", bus="ha", control="Slack", p_nom=100)
    network.add("Load", "demand", bus="lb", p_set=pd.Series([5.0, 7.0], index=snapshots))
    return network


def partition(network: pypsa.Network, sidecar: object) -> PartitionPlan:
    """Cluster only two free 110-kV buses; keep every interface singleton."""
    assignment = {"ha": "ha", "hb": "hv-free", "hc": "hv-free",
                  "ma": "ma", "mb": "mb", "la": "la", "lb": "lb"}
    stable = {sidecar.source_id("Bus", name):
              StableId("n", "BusCluster", cluster)
              for name, cluster in assignment.items()}
    backend = {name: stable[sidecar.source_id("Bus", name)].encode()
               for name in assignment}
    return PartitionPlan(stable, backend, 6, 6, "reject")


def identity_partition(network: pypsa.Network, sidecar: object) -> PartitionPlan:
    """Give every Bus a separate stable target for topology Gate tests."""
    stable = {sidecar.source_id("Bus", str(name)): StableId("n", "Bus", str(name))
              for name in network.buses.index}
    backend = {str(name): stable[sidecar.source_id("Bus", str(name))].encode()
               for name in network.buses.index}
    return PartitionPlan(stable, backend, len(stable), len(stable), "reject")


def test_builder_keeps_transformers_maps_and_preliminary_only() -> None:
    """Construct a three-layer quotient without a persisted success status."""
    network = three_voltage_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    before = adapter.topology_signature(network, sidecar)

    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protection,
    )

    assert len(result.network.buses) == 6
    assert set(result.network.transformers.index) == {"tx_hm", "tx_ml"}
    assert len(result.mappings.bus_map) == 7
    assert len(result.mappings.branch_map) == 6
    assert any(row.source_id == sidecar.source_id("Line", "hbc") and
               row.mapping_type == "absorbed_internal"
               for row in result.mappings.branch_map)
    assert len(result.mappings.transformer_map) == 2
    assert result.preliminary.stage == "builder"
    assert result.preliminary.finalized is False
    assert not hasattr(result.preliminary, "status")
    assert all(result.preliminary.checks.values())
    assert result.network.snapshots.equals(network.snapshots)
    assert result.network.snapshot_weightings.equals(network.snapshot_weightings)
    assert adapter.topology_signature(network, sidecar) == before


def test_precheck_detects_changed_transformer_parameter_and_direction() -> None:
    """Hash all Transformer attributes with ordered mapped StableId terminals."""
    network = three_voltage_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protection,
    )
    validator = MultiVoltageStructuralValidator()
    changed = result.network.copy()
    changed.transformers.loc["tx_hm", "x"] += 0.01
    evidence = validator.precheck(network, changed, sidecar,
                                  result.target_sidecar, result.mappings, (),
                                  protection,
                                  voltage_layer_map=result.voltage_layer_map)
    assert evidence.checks["transformer_semantics"] is False

    swapped = result.network.copy()
    a = swapped.transformers.loc["tx_hm", "bus0"]
    b = swapped.transformers.loc["tx_hm", "bus1"]
    swapped.transformers.loc["tx_hm", ["bus0", "bus1"]] = [b, a]
    evidence = validator.precheck(network, swapped, sidecar,
                                  result.target_sidecar, result.mappings, (),
                                  protection,
                                  voltage_layer_map=result.voltage_layer_map)
    assert evidence.checks["transformer_semantics"] is False


def test_nonempty_shape_is_explicit_capability_error() -> None:
    """The first Builder cannot silently discard a Shape table."""
    network = three_voltage_network()
    network.add("Shape", "area")
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])

    with pytest.raises(BackendCapabilityError, match="Shape"):
        MultiVoltageQuotientBuilder().build(
            network, sidecar, partition(network, sidecar),
            AggregationConfig(), protection,
        )


def test_unknown_dynamic_attribute_fails_explicitly() -> None:
    """A populated unknown series cannot disappear during the layer quotient."""
    network = three_voltage_network()
    network.components["Load"].dynamic["mystery"] = pd.DataFrame(
        {"demand": [1.0, 2.0]}, index=network.snapshots
    )
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])

    with pytest.raises(BackendCapabilityError, match="mystery"):
        MultiVoltageQuotientBuilder().build(
            network, sidecar, partition(network, sidecar),
            AggregationConfig(aggregate_one_ports=["Load"]), protection,
        )


def test_transformer_dynamic_series_is_preserved_in_memory() -> None:
    """The Transformer hash includes its supported time series."""
    network = three_voltage_network()
    network.components["Transformer"].dynamic["s_max_pu"]["tx_hm"] = [1.0, 0.8]
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])

    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protection,
    )

    assert result.preliminary.checks["transformer_semantics"] is True
    assert result.network.components["Transformer"].dynamic["s_max_pu"][
        "tx_hm"
    ].tolist() == [1.0, 0.8]


def test_parallel_line_aggregation_keeps_many_to_one_branch_map() -> None:
    """Two source Lines may point to one target Line without losing traceability."""
    network = three_voltage_network()
    network.add("Line", "hab_parallel", bus0="ha", bus1="hb",
                r=0.01, x=0.1, s_nom=100)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    stable = {sidecar.source_id("Bus", str(name)): StableId("n", "Bus", str(name))
              for name in network.buses.index}
    backend = {str(name): stable[sidecar.source_id("Bus", str(name))].encode()
               for name in network.buses.index}
    plan = PartitionPlan(stable, backend, len(stable), len(stable), "reject")

    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, plan,
        AggregationConfig(aggregate_one_ports=["Load"]), protection,
    )
    targets = [row.target_id for row in result.mappings.line_map
               if row.source_id.source_key in {"hab", "hab_parallel"}]

    assert len(targets) == 2
    assert targets[0] == targets[1]
    assert targets[0] is not None
    assert result.preliminary.checks["simple_ring_topology"]
    assert not result.preliminary.checks["cycle_corridors"]
    assert result.preliminary.details["multigraph_cycle_rank_delta"] == "-1"


def test_one_to_one_line_materialization_preserves_zero_length_electrical_values() -> None:
    """A surviving line with zero geometry keeps its source electrical model."""
    network = three_voltage_network()
    network.lines.loc["hab", ["r", "x", "g", "b", "length", "num_parallel",
                               "terrain_factor"]] = (
        0.012, 0.125, 0.0002, 0.0003, 0.0, 2.0, 0.8
    )
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])

    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, identity_partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protection,
    )
    line_map = next(row for row in result.mappings.branch_map
                    if row.source_id == sidecar.source_id("Line", "hab"))
    assert line_map.target_id is not None
    target_name = result.target_sidecar.backend_name(line_map.target_id)
    target = result.network.lines.loc[target_name]

    assert target.r == pytest.approx(0.012)
    assert target.x == pytest.approx(0.125)
    assert target.g == pytest.approx(0.0002)
    assert target.b == pytest.approx(0.0003)
    assert target.num_parallel == pytest.approx(2.0)
    assert target.type == ""
    assert bool(target.active)
    assert target.s_nom == pytest.approx(100.0)
    assert target.terrain_factor == pytest.approx(0.8)


def _identity_build(network: pypsa.Network):
    """Build an identity-busmap quotient for electrical line regressions."""
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, identity_partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protection,
    )
    return result, sidecar


def _target_line_for_source(result, source_sidecar, source_name: str) -> pd.Series:
    """Resolve a source Line through the result map to its target row."""
    source_id = source_sidecar.source_id("Line", source_name)
    row = next(item for item in result.mappings.branch_map
               if item.source_id == source_id)
    assert row.target_id is not None
    target_name = result.target_sidecar.backend_name(row.target_id)
    return result.network.lines.loc[target_name]


def test_parallel_line_materialization_preserves_total_series_and_shunt_admittance() -> None:
    """Equal source members become one explicit electrical equivalent."""
    network = three_voltage_network()
    network.lines.loc["hab", ["r", "x", "g", "b", "length", "num_parallel"]] = (
        0.01, 0.02, 0.0001, 0.0002, 0.0, 2.0
    )
    network.add("Line", "hab_parallel", bus0="ha", bus1="hb", r=0.01, x=0.02,
                g=0.0001, b=0.0002, length=0.0, num_parallel=3.0, s_nom=100)

    result, sidecar = _identity_build(network)
    target = _target_line_for_source(result, sidecar, "hab")

    assert target.r == pytest.approx(0.005)
    assert target.x == pytest.approx(0.01)
    assert target.g == pytest.approx(0.0002)
    assert target.b == pytest.approx(0.0004)
    assert target.num_parallel == pytest.approx(1.0)
    assert target.type == ""


def test_unequal_parallel_lines_use_effective_pypsa_member_admittances() -> None:
    """Unequal members combine as admittances without reapplying num_parallel."""
    network = three_voltage_network()
    network.lines.loc["hab", ["r", "x", "g", "b", "length", "num_parallel"]] = (
        0.01, 0.02, 0.0001, 0.0002, 0.0, 2.0
    )
    network.add("Line", "hab_parallel", bus0="ha", bus1="hb", r=0.03, x=0.04,
                g=0.0003, b=0.0004, length=0.0, num_parallel=3.0, s_nom=100)

    result, sidecar = _identity_build(network)
    target = _target_line_for_source(result, sidecar, "hab")
    expected_impedance = 1.0 / (1.0 / complex(0.01, 0.02)
                                + 1.0 / complex(0.03, 0.04))

    assert complex(target.r, target.x) == pytest.approx(expected_impedance)
    assert target.g == pytest.approx(0.0004)
    assert target.b == pytest.approx(0.0006)
    assert target.num_parallel == pytest.approx(1.0)


def test_inactive_parallel_member_does_not_change_active_equivalent() -> None:
    """Inactive source members remain in lineage but do not alter active Y."""
    network = three_voltage_network()
    network.lines.loc["hab", ["r", "x", "g", "b", "length", "num_parallel"]] = (
        0.01, 0.02, 0.0001, 0.0002, 0.0, 1.0
    )
    network.add("Line", "hab_inactive", bus0="ha", bus1="hb", r=0.03, x=0.04,
                g=0.0003, b=0.0004, length=0.0, num_parallel=4.0,
                active=False, s_nom=100)

    result, sidecar = _identity_build(network)
    target = _target_line_for_source(result, sidecar, "hab")
    rows = [row for row in result.mappings.branch_map
            if row.source_id in {sidecar.source_id("Line", "hab"),
                                 sidecar.source_id("Line", "hab_inactive")}]

    assert len(rows) == 2
    assert rows[0].target_id == rows[1].target_id
    assert target.r == pytest.approx(0.01)
    assert target.x == pytest.approx(0.02)
    assert target.g == pytest.approx(0.0001)
    assert target.b == pytest.approx(0.0002)
    assert bool(target.active)


def test_one_to_one_typed_line_retains_type_length_and_num_parallel_semantics() -> None:
    """A one-to-one typed line resolves to the same PyPSA electrical values."""
    network = three_voltage_network()
    network.add("LineType", "typed", r_per_length=0.2, x_per_length=0.4,
                c_per_length=15.0, f_nom=50.0)
    network.lines.loc["hab", ["type", "length", "num_parallel"]] = (
        "typed", 12.0, 3.0
    )
    source_effective = network.copy()
    source_effective.calculate_dependent_values()

    result, sidecar = _identity_build(network)
    target = _target_line_for_source(result, sidecar, "hab")
    target_effective = result.network.copy()
    target_effective.calculate_dependent_values()
    target_name = result.target_sidecar.backend_name(
        next(row.target_id for row in result.mappings.branch_map
             if row.source_id == sidecar.source_id("Line", "hab"))
    )

    assert target.type == "typed"
    assert target.length == pytest.approx(12.0)
    assert target.num_parallel == pytest.approx(3.0)
    assert target_effective.lines.at[target_name, "r"] == pytest.approx(
        source_effective.lines.at["hab", "r"]
    )
    assert target_effective.lines.at[target_name, "x"] == pytest.approx(
        source_effective.lines.at["hab", "x"]
    )
    assert target_effective.lines.at[target_name, "b"] == pytest.approx(
        source_effective.lines.at["hab", "b"]
    )


def test_typed_parallel_lines_combine_pypsa_effective_admittances() -> None:
    """Type length and multiplicity are resolved once before parallel reduction."""
    network = three_voltage_network()
    network.add("LineType", "typed", r_per_length=0.2, x_per_length=0.4,
                c_per_length=15.0, f_nom=50.0)
    network.lines.loc["hab", ["type", "length", "num_parallel", "g"]] = (
        "typed", 12.0, 3.0, 0.0001
    )
    network.add("Line", "hab_parallel", bus0="ha", bus1="hb", type="typed",
                length=8.0, num_parallel=4.0, g=0.0002, s_nom=100)
    source_effective = network.copy()
    source_effective.calculate_dependent_values()
    first = source_effective.lines.loc["hab"]
    second = source_effective.lines.loc["hab_parallel"]
    expected_z = 1.0 / (
        1.0 / complex(first.r, first.x) + 1.0 / complex(second.r, second.x)
    )

    result, sidecar = _identity_build(network)
    target = _target_line_for_source(result, sidecar, "hab")

    assert complex(target.r, target.x) == pytest.approx(expected_z)
    assert target.g == pytest.approx(first.g + second.g)
    assert target.b == pytest.approx(first.b + second.b)
    assert target.num_parallel == pytest.approx(1.0)
    assert target.type == ""


def test_aggregated_active_zero_impedance_line_fails_closed() -> None:
    """Unsupported active zero-Z members fail instead of creating a bad target."""
    network = three_voltage_network()
    network.lines.loc["hab", ["r", "x", "length"]] = (0.0, 0.0, 0.0)
    network.add("Line", "hab_zero", bus0="ha", bus1="hb", r=0.0, x=0.0,
                length=0.0, s_nom=100)

    with pytest.raises(MaterializationError, match="AC_LINE_EQUIVALENCE_UNSUPPORTED"):
        _identity_build(network)


def test_all_inactive_parallel_corridor_fails_closed() -> None:
    """A multi-member corridor with no active member has no AC equivalent."""
    network = three_voltage_network()
    network.lines.loc["hab", ["r", "x", "length", "active"]] = (
        0.01, 0.02, 0.0, False
    )
    network.add("Line", "hab_inactive", bus0="ha", bus1="hb", r=0.03, x=0.04,
                length=0.0, active=False, s_nom=100)

    with pytest.raises(MaterializationError, match="all-inactive corridor"):
        _identity_build(network)


def test_declared_alias_materializes_one_layer_without_raw_voltage_guessing() -> None:
    """An explicit alias changes the layer key used by the local quotient."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110.0)
    network.add("Bus", "b", v_nom=109.9)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    stable = {sidecar.source_id("Bus", name): StableId("n", "Bus", name)
              for name in ("a", "b")}
    plan = PartitionPlan(stable,
                         {name: stable[sidecar.source_id("Bus", name)].encode()
                          for name in ("a", "b")}, 2, 2, "reject")

    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, plan, AggregationConfig(), protected,
        voltage_aliases={109.9: 110.0},
    )

    assert result.network.buses.v_nom.to_list() == [110.0, 110.0]
    assert result.voltage_layer_map[sidecar.source_id("Bus", "b")] == (
        109.9, 110.0
    )
    assert result.preliminary.stage == "builder"


def test_precheck_catches_changed_dynamic_injection_and_metadata() -> None:
    """A child with altered load profile or Carrier metadata fails precheck."""
    network = three_voltage_network()
    network.add("Carrier", "electricity", co2_emissions=0.25)
    network.generators.loc["slack", "carrier"] = "electricity"
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )
    validator = MultiVoltageStructuralValidator()
    changed = result.network.copy()
    load_target = next(row.target_id for row in result.mappings.device_map
                       if row.source_id == sidecar.source_id("Load", "demand"))
    assert load_target is not None
    load_name = result.target_sidecar.backend_name(load_target)
    changed.loads_t.p_set.loc[changed.snapshots[0], load_name] += 1.0
    evidence = validator.precheck(network, changed, sidecar,
                                  result.target_sidecar, result.mappings, (),
                                  protected,
                                  voltage_layer_map=result.voltage_layer_map)
    assert evidence.checks["aggregation_invariants"] is False

    changed = result.network.copy()
    changed.carriers.loc["electricity", "co2_emissions"] = 0.5
    evidence = validator.precheck(network, changed, sidecar,
                                  result.target_sidecar, result.mappings, (),
                                  protected,
                                  voltage_layer_map=result.voltage_layer_map)
    assert evidence.checks["metadata_tables"] is False


def test_controlled_and_shunt_buses_remain_singleton() -> None:
    """PV and shunt terminals survive a valid in-memory quotient."""
    network = three_voltage_network()
    network.add("Generator", "pv", bus="mb", control="PV", p_nom=10)
    network.add("ShuntImpedance", "reactor", bus="la", g=0, b=-0.1)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])

    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )

    assert all(result.preliminary.checks.values())
    assert len(result.network.generators) == 2
    assert len(result.network.shunt_impedances) == 1


def test_builder_is_deterministic_and_preserves_separate_components() -> None:
    """Two identical builds retain disconnected islands and stable mappings."""
    network = three_voltage_network()
    network.add("Bus", "island", v_nom=10)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    base = partition(network, sidecar)
    island_id = sidecar.source_id("Bus", "island")
    stable = dict(base.stable_busmap)
    stable[island_id] = StableId("n", "Bus", "island")
    backend = dict(base.backend_busmap)
    backend["island"] = stable[island_id].encode()
    plan = PartitionPlan(stable, backend, 7, 7, "reject")
    builder = MultiVoltageQuotientBuilder()
    first = builder.build(network, sidecar, plan,
                          AggregationConfig(aggregate_one_ports=["Load"]), protected)
    second = builder.build(network, sidecar, plan,
                           AggregationConfig(aggregate_one_ports=["Load"]), protected)

    assert first.mappings == second.mappings
    assert [(row.input_signature, row.output_signature, row.parameters)
            for row in first.records] == [
                (row.input_signature, row.output_signature, row.parameters)
                for row in second.records
            ]
    assert first.preliminary.checks["connected_components"]


def test_unsupported_component_table_fails_before_build() -> None:
    """A populated GlobalConstraint cannot disappear silently."""
    network = three_voltage_network()
    network.add("GlobalConstraint", "limit", type="primary_energy", sense="<=",
                constant=100)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    with pytest.raises(BackendCapabilityError, match="GlobalConstraint"):
        MultiVoltageQuotientBuilder().build(
            network, sidecar, partition(network, sidecar),
            AggregationConfig(aggregate_one_ports=["Load"]), protected,
        )


def test_investment_period_metadata_survives_in_memory() -> None:
    """Multi-period snapshots and weightings are part of the Builder precheck."""
    network = three_voltage_network()
    network.set_investment_periods([2030, 2040])
    network.investment_period_weightings.loc[2040, "objective"] = 2.0
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    original_snapshots = repr(network.snapshots)
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )
    assert repr(network.snapshots) == original_snapshots

    assert result.preliminary.checks["snapshots"]
    assert result.preliminary.checks["investment_periods"]
    assert result.network.investment_period_weightings.equals(
        network.investment_period_weightings
    )


def test_cycle_endpoints_are_mandatory_and_corridor_survives() -> None:
    """A three-Line ring keeps its endpoint singletons and cycle corridor."""
    network = three_voltage_network()
    network.add("Line", "hca", bus0="hc", bus1="ha", r=0.01, x=0.1,
                s_nom=100)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    stable = {sidecar.source_id("Bus", str(name)): StableId("n", "Bus", str(name))
              for name in network.buses.index}
    backend = {str(name): stable[sidecar.source_id("Bus", str(name))].encode()
               for name in network.buses.index}
    plan = PartitionPlan(stable, backend, len(stable), len(stable), "reject")
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, plan,
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )

    assert result.preliminary.checks["simple_ring_topology"]
    assert result.preliminary.checks["cycle_corridors"]


def test_invariant_snapshot_selector_is_explicit() -> None:
    """Only selected snapshots contribute to an explicit invariant."""
    network = three_voltage_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    spec = AggregationInvariantSpec(
        "Load", "p_set", "sum", dynamic=True,
        scope="source_connected_component", snapshots=(network.snapshots[0],),
    )
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
        invariants=(spec,),
    )
    target_id = next(row.target_id for row in result.mappings.device_map
                     if row.source_id == sidecar.source_id("Load", "demand"))
    assert target_id is not None
    target_name = result.target_sidecar.backend_name(target_id)
    changed = result.network.copy()
    changed.loads_t.p_set.loc[network.snapshots[1], target_name] += 1
    validator = MultiVoltageStructuralValidator()
    evidence = validator.precheck(network, changed, sidecar,
                                  result.target_sidecar, result.mappings,
                                  (spec,), protected,
                                  voltage_layer_map=result.voltage_layer_map)
    assert evidence.checks["aggregation_invariants"]
    changed.loads_t.p_set.loc[network.snapshots[0], target_name] += 1
    evidence = validator.precheck(network, changed, sidecar,
                                  result.target_sidecar, result.mappings,
                                  (spec,), protected,
                                  voltage_layer_map=result.voltage_layer_map)
    assert not evidence.checks["aggregation_invariants"]


def test_declared_generator_dynamic_strategy_is_checked() -> None:
    """A supported declared Generator series survives the quotient."""
    network = three_voltage_network()
    network.generators_t.p_max_pu["slack"] = [1.0, 0.5]
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    specs = (
        AggregationInvariantSpec("Load", "p_set", "sum", dynamic=True),
        AggregationInvariantSpec("Generator", "p_max_pu", "preserve_exact",
                                 dynamic=True),
    )
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
        invariants=specs,
    )

    assert result.preliminary.checks["aggregation_invariants"]


def test_line_free_load_island_uses_identity_quotient() -> None:
    """A singleton island with a Load need not call PyPSA's empty-Line path."""
    network = three_voltage_network()
    network.add("Bus", "island", v_nom=5)
    network.add("Load", "island_load", bus="island", p_set=1.0)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    base = partition(network, sidecar)
    stable = dict(base.stable_busmap)
    stable[sidecar.source_id("Bus", "island")] = StableId("n", "Bus", "island")
    backend = dict(base.backend_busmap)
    backend["island"] = stable[sidecar.source_id("Bus", "island")].encode()
    plan = PartitionPlan(stable, backend, 7, 7, "reject")
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, plan, AggregationConfig(aggregate_one_ports=["Load"]),
        protected,
    )

    assert len(result.network.loads) == 2
    assert all(result.preliminary.checks.values())


def test_parallel_corridor_in_triangle_remains_valid() -> None:
    """Collapsing redundant parallel Lines keeps a simple triangle ring."""
    network = three_voltage_network()
    network.add("Line", "hca", bus0="hc", bus1="ha", r=0.01, x=0.1,
                s_nom=100)
    network.add("Line", "hab_parallel", bus0="ha", bus1="hb",
                r=0.01, x=0.1, s_nom=100)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, identity_partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )

    assert result.preliminary.checks["simple_ring_topology"]
    assert result.preliminary.checks["cycle_corridors"]
    assert result.preliminary.details["multigraph_cycle_rank_delta"] == "-1"
    assert all(result.preliminary.checks.values())


def test_voltage_layer_gate_detects_target_corruption() -> None:
    """A target Bus with wrong canonical v_nom fails in-memory evidence."""
    network = three_voltage_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )
    changed = result.network.copy()
    target = next(row.target_id for row in result.mappings.bus_map
                  if row.source_id == sidecar.source_id("Bus", "hb"))
    assert target is not None
    changed.buses.loc[result.target_sidecar.backend_name(target), "v_nom"] = 99
    evidence = MultiVoltageStructuralValidator().precheck(
        network, changed, sidecar, result.target_sidecar,
        result.mappings, (), protected,
        voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["voltage_layers"]
    changed.buses["v_nom"] = changed.buses.v_nom.astype(object)
    changed.buses.loc[result.target_sidecar.backend_name(target), "v_nom"] = "bad"
    evidence = MultiVoltageStructuralValidator().precheck(
        network, changed, sidecar, result.target_sidecar,
        result.mappings, (), protected,
        voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["voltage_layers"]


def test_transformer_and_control_mapping_gates() -> None:
    """Swapped Transformer targets and missing or wrong controls are visible."""
    network = three_voltage_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )
    validator = MultiVoltageStructuralValidator()
    tx = list(result.mappings.transformer_map)
    assert len(tx) == 2
    # Build the swapped rows by Transformer identity, independent of Line order.
    swapped = tuple(
        replace(row, target_id=next(other.target_id for other in tx
                                    if other.source_id != row.source_id))
        if row.source_id.component == "Transformer" else row
        for row in result.mappings.branch_map
    )
    wrong_tx = replace(result.mappings, branch_map=swapped)
    evidence = validator.precheck(
        network, result.network, sidecar, result.target_sidecar,
        wrong_tx, (), protected, voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["transformer_mapping_identity"]

    wrong_type = replace(result.mappings, branch_map=tuple(
        replace(row, mapping_type="aggregated")
        if row.source_id == tx[0].source_id else row
        for row in result.mappings.branch_map
    ))
    evidence = validator.precheck(
        network, result.network, sidecar, result.target_sidecar,
        wrong_type, (), protected, voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["transformer_mapping_identity"]

    missing = replace(result.mappings, control_map=())
    evidence = validator.precheck(
        network, result.network, sidecar, result.target_sidecar,
        missing, (), protected, voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["control_mapping_coverage"]

    wrong_control = replace(
        result.mappings.control_map[0],
        target_id=next(row.target_id for row in result.mappings.device_map
                       if row.source_id.component == "Load"),
    )
    wrong = replace(result.mappings, control_map=(wrong_control,))
    evidence = validator.precheck(
        network, result.network, sidecar, result.target_sidecar,
        wrong, (), protected, voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["control_owner_consistency"]
    assert not evidence.checks["control_target_existence"]


def test_preserve_exact_accepts_string_and_bool_values() -> None:
    """Exact invariants compare nonnumeric Generator attributes directly."""
    network = three_voltage_network()
    network.add("Carrier", "wind")
    network.generators.loc["slack", "carrier"] = "wind"
    network.generators.loc["slack", "committable"] = True
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    specs = (
        AggregationInvariantSpec("Load", "p_set", "sum", dynamic=True),
        AggregationInvariantSpec("Generator", "carrier", "preserve_exact"),
        AggregationInvariantSpec("Generator", "committable", "preserve_exact"),
    )
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
        invariants=specs,
    )
    assert result.preliminary.checks["aggregation_invariants"]
    changed = result.network.copy()
    generator_id = next(row.target_id for row in result.mappings.device_map
                        if row.source_id == sidecar.source_id("Generator", "slack"))
    assert generator_id is not None
    changed.generators.loc[result.target_sidecar.backend_name(generator_id),
                           "carrier"] = "other"
    evidence = MultiVoltageStructuralValidator().precheck(
        network, changed, sidecar, result.target_sidecar, result.mappings,
        specs, protected, voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["aggregation_invariants"]


def test_sum_rejects_nonnumeric_attribute() -> None:
    """A string source attribute must not coerce to an additive zero."""
    network = three_voltage_network()
    network.add("Carrier", "wind")
    network.generators.loc["slack", "carrier"] = "wind"
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )
    specs = (
        AggregationInvariantSpec("Load", "p_set", "sum", dynamic=True),
        AggregationInvariantSpec("Generator", "carrier", "sum"),
    )
    with pytest.raises(BackendCapabilityError, match="numeric"):
        MultiVoltageStructuralValidator().precheck(
            network, result.network, sidecar, result.target_sidecar,
            result.mappings, specs, protected,
            voltage_layer_map=result.voltage_layer_map,
        )


def test_preserve_exact_accepts_enum_attribute() -> None:
    """Enum-valued metadata is compared through its declared value."""
    class DispatchMode(Enum):
        FIRM = "firm"

    network = three_voltage_network()
    network.generators.loc["slack", "dispatch_mode"] = DispatchMode.FIRM
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    specs = (
        AggregationInvariantSpec("Load", "p_set", "sum", dynamic=True),
        AggregationInvariantSpec("Generator", "dispatch_mode", "preserve_exact"),
    )
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
        invariants=specs,
    )
    assert result.preliminary.checks["aggregation_invariants"]


@pytest.mark.parametrize("field,value", [
    ("strategy", "average"), ("strategy", []),
    ("scope", "region"), ("scope", []),
    ("atol", float("nan")), ("atol", -1), ("atol", "0.1"),
])
def test_invalid_invariant_spec_fails_at_creation(field: str, value: object) -> None:
    """Strategy, scope, and tolerance are checked at runtime."""
    arguments = {"component": "Load", "attribute": "p_set", "strategy": "sum"}
    arguments[field] = value
    with pytest.raises(ConfigSemanticError):
        AggregationInvariantSpec(**arguments)


def test_builder_records_requested_planned_and_repaired_counts() -> None:
    """A deterministic split never relabels the pre-repair count as requested."""
    network = pypsa.Network()
    for name in ("a", "b", "c"):
        network.add("Bus", name, v_nom=110)
    network.add("Line", "ab", bus0="a", bus1="b", x=0.1)
    network.add("Line", "bc", bus0="b", bus1="c", x=0.1)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    stable = {sidecar.source_id("Bus", name): StableId(
        "n", "BusCluster", "same" if name != "b" else "middle"
    ) for name in ("a", "b", "c")}
    backend = {name: stable[sidecar.source_id("Bus", name)].encode()
               for name in ("a", "b", "c")}
    plan = PartitionPlan(stable, backend, 2, 2, "split_disconnected")
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, plan, AggregationConfig(), protected,
    )

    assert result.requested_cluster_count == 2
    assert result.planned_cluster_count == 2
    assert result.actual_cluster_count == 3
    assert result.layer_cluster_counts == {"110": (2, 3)}
    assert result.records[0].parameters["requested_cluster_count"] is None
    assert result.records[0].parameters["planned_cluster_count"] == 2
    assert result.records[0].parameters["actual_cluster_count"] == 3


def test_line_free_colocated_generators_fail_explicitly() -> None:
    """Identity fallback cannot silently skip requested Generator aggregation."""
    network = three_voltage_network()
    network.add("Bus", "island", v_nom=5)
    network.add("Generator", "g1", bus="island", p_nom=1)
    network.add("Generator", "g2", bus="island", p_nom=2)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    base = partition(network, sidecar)
    stable = dict(base.stable_busmap)
    stable[sidecar.source_id("Bus", "island")] = StableId("n", "Bus", "island")
    backend = dict(base.backend_busmap)
    backend["island"] = stable[sidecar.source_id("Bus", "island")].encode()
    plan = PartitionPlan(stable, backend, 7, 7, "reject")
    with pytest.raises(BackendCapabilityError, match="Generator"):
        MultiVoltageQuotientBuilder().build(
            network, sidecar, plan,
            AggregationConfig(aggregate_one_ports=["Generator"]), protected,
        )


def test_line_free_default_generator_grouping_is_checked() -> None:
    """An unconfigured Generator collision must not bypass quotient grouping."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110)
    network.add("Generator", "g1", bus="a", carrier="wind", p_nom=1)
    network.add("Generator", "g2", bus="a", carrier="wind", p_nom=2)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    with pytest.raises(BackendCapabilityError, match="Generator"):
        MultiVoltageQuotientBuilder().build(
            network, sidecar, identity_partition(network, sidecar),
            AggregationConfig(aggregate_one_ports=["Load"]), protected,
        )


def test_line_free_distinct_generator_carriers_are_preserved() -> None:
    """Different carriers are different PyPSA aggregation groups."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110)
    network.add("Generator", "g1", bus="a", carrier="wind", p_nom=1)
    network.add("Generator", "g2", bus="a", carrier="solar", p_nom=2)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, identity_partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )
    assert len(result.network.generators) == 2
    assert result.preliminary.checks["aggregation_invariants"]


def test_line_free_configured_loads_use_bus_and_carrier_groups() -> None:
    """Different Load carriers do not collide in the identity fallback."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110)
    network.add("Load", "l1", bus="a", carrier="electricity", p_set=1)
    network.add("Load", "l2", bus="a", carrier="heat", p_set=2)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, identity_partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )
    assert len(result.network.loads) == 2


def test_dynamic_invariant_uses_static_fallback_for_nan() -> None:
    """A missing dynamic sample uses its static component value."""
    network = three_voltage_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )
    source = network.copy()
    target = result.network.copy()
    target_id = next(row.target_id for row in result.mappings.device_map
                     if row.source_id == sidecar.source_id("Load", "demand"))
    assert target_id is not None
    target_name = result.target_sidecar.backend_name(target_id)
    source.loads.loc["demand", "p_set"] = 2.0
    target.loads.loc[target_name, "p_set"] = 2.0
    source.loads_t.p_set.loc[source.snapshots[0], "demand"] = float("nan")
    target.loads_t.p_set.loc[target.snapshots[0], target_name] = 2.0
    evidence = MultiVoltageStructuralValidator().precheck(
        source, target, sidecar, result.target_sidecar, result.mappings,
        (), protected, voltage_layer_map=result.voltage_layer_map,
    )
    assert evidence.checks["aggregation_invariants"]


@pytest.mark.parametrize("component", ["Line", "Transformer", "Link"])
def test_inactive_branch_does_not_join_connected_components(component: str) -> None:
    """Inactive static branches cannot merge source connected components."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110)
    network.add("Bus", "b", v_nom=10 if component == "Transformer" else 110)
    kwargs = {"bus0": "a", "bus1": "b", "active": False}
    if component == "Line":
        kwargs["x"] = 0.1
    network.add(component, "ab", **kwargs)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    child = network.copy()
    child.remove(component, "ab")
    child_sidecar = adapter.identity_sidecar(child, "n")
    busmap = {sidecar.source_id("Bus", name): child_sidecar.source_id("Bus", name)
              for name in ("a", "b")}
    assert MultiVoltageStructuralValidator._components_preserved(
        network, child, sidecar, child_sidecar, busmap,
    )


def test_precheck_independently_checks_all_mandatory_singletons() -> None:
    """A forged map cannot merge a protected Bus after partition validation."""
    network = three_voltage_network()
    network.add("Line", "hca", bus0="hc", bus1="ha", r=0.01, x=0.1)
    network.add("ShuntImpedance", "sh", bus="hb", g=0.01)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, identity_partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )
    validator = MultiVoltageStructuralValidator()
    for name in ("ha", "hb", "hc", "ma"):
        source = sidecar.source_id("Bus", name)
        other = next(row.target_id for row in result.mappings.bus_map
                     if row.source_id == sidecar.source_id("Bus", "mb"))
        forged = replace(result.mappings, bus_map=tuple(
            replace(row, target_id=other) if row.source_id == source else row
            for row in result.mappings.bus_map
        ))
        evidence = validator.precheck(
            network, result.network, sidecar, result.target_sidecar,
            forged, (), protected, voltage_layer_map=result.voltage_layer_map,
        )
        assert not evidence.checks["protected_singletons"], name


def test_voltage_layer_gate_checks_link_terminals_and_extra_ports() -> None:
    """The child Link cannot cross a layer or acquire a third terminal."""
    network = pypsa.Network()
    for name, voltage in (("a", 110), ("b", 110), ("c", 10)):
        network.add("Bus", name, v_nom=voltage)
    network.add("Link", "ab", bus0="a", bus1="b", p_nom=10)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, identity_partition(network, sidecar),
        AggregationConfig(), protected,
    )
    validator = MultiVoltageStructuralValidator()
    link_name = str(result.network.links.index[0])
    low_target = next(row.target_id for row in result.mappings.bus_map
                      if row.source_id == sidecar.source_id("Bus", "c"))
    assert low_target is not None
    changed = result.network.copy()
    changed.links.loc[link_name, "bus1"] = result.target_sidecar.backend_name(low_target)
    evidence = validator.precheck(
        network, changed, sidecar, result.target_sidecar, result.mappings,
        (), protected, voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["voltage_layers"]
    changed = result.network.copy()
    changed.links.loc[link_name, "bus2"] = result.target_sidecar.backend_name(low_target)
    evidence = validator.precheck(
        network, changed, sidecar, result.target_sidecar, result.mappings,
        (), protected, voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["voltage_layers"]


@pytest.mark.parametrize("component,attribute", [
    ("FictionalComponent", "p_set"), ("Load", "nonexistent_attribute"),
])
def test_explicit_invariant_missing_reference_is_semantic_error(
    component: str, attribute: str,
) -> None:
    """Mistyped invariant references fail before materialization."""
    network = three_voltage_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    with pytest.raises(ConfigSemanticError, match="Invariant"):
        MultiVoltageQuotientBuilder().build(
            network, sidecar, partition(network, sidecar),
            AggregationConfig(aggregate_one_ports=["Load"]), protected,
            invariants=(AggregationInvariantSpec(component, attribute, "sum"),),
        )


def test_configured_non_one_port_aggregation_fails_explicitly() -> None:
    """A Line cannot be requested as a one-port aggregation component."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    with pytest.raises(BackendCapabilityError, match="one-port"):
        MultiVoltageQuotientBuilder().build(
            network, sidecar, identity_partition(network, sidecar),
            AggregationConfig(aggregate_one_ports=["Line"]), protected,
        )


def test_same_layer_link_is_preserved() -> None:
    """A two-terminal Link within one layer remains mapped and connected."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110)
    network.add("Bus", "b", v_nom=110)
    network.add("Link", "ab", bus0="a", bus1="b", p_nom=10)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, identity_partition(network, sidecar),
        AggregationConfig(), protected,
    )
    assert len(result.network.links) == 1
    assert len([row for row in result.mappings.branch_map
                if row.source_id.component == "Link"]) == 1
    assert all(result.preliminary.checks.values())


def test_cross_layer_link_fails_explicitly() -> None:
    """A Link across nominal layers needs a later reconnection handler."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110)
    network.add("Bus", "b", v_nom=10)
    network.add("Link", "ab", bus0="a", bus1="b", p_nom=10)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    with pytest.raises(BackendCapabilityError, match="Cross-layer Link"):
        MultiVoltageQuotientBuilder().build(
            network, sidecar, identity_partition(network, sidecar),
            AggregationConfig(), protected,
        )


def test_link_in_explicitly_aliased_layer_is_supported() -> None:
    """A declared alias gives a two-terminal Link one canonical layer."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110)
    network.add("Bus", "b", v_nom=109.9)
    network.add("Link", "ab", bus0="a", bus1="b", p_nom=10)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, identity_partition(network, sidecar),
        AggregationConfig(), protected, voltage_aliases={109.9: 110.0},
    )
    assert len(result.network.links) == 1
    assert result.preliminary.checks["voltage_layers"]


def test_multi_terminal_link_fails_explicitly() -> None:
    """A third Link terminal cannot disappear in layer extraction."""
    network = pypsa.Network()
    for name in ("a", "b", "c"):
        network.add("Bus", name, v_nom=110)
    network.add("Link", "abc", bus0="a", bus1="b", bus2="c", p_nom=10)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    with pytest.raises(BackendCapabilityError, match="Multi-terminal Link"):
        MultiVoltageQuotientBuilder().build(
            network, sidecar, identity_partition(network, sidecar),
            AggregationConfig(), protected,
        )


def test_transformer_type_tap_and_phase_shift_are_semantically_checked() -> None:
    """Complete type metadata and tap fields survive and detect corruption."""
    network = three_voltage_network()
    network.add("TransformerType", "type_hm", s_nom=100, r=0.01, x=0.1,
                tap_side=0, tap_neutral=0, tap_min=-2, tap_max=2,
                tap_step=2.5, phase_shift=4.0)
    network.transformers.loc["tx_hm", "type"] = "type_hm"
    network.transformers.loc["tx_hm", "tap_position"] = 1
    network.transformers.loc["tx_hm", "phase_shift"] = 4.0
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )
    assert result.preliminary.checks["metadata_tables"]
    assert result.preliminary.checks["transformer_semantics"]
    validator = MultiVoltageStructuralValidator()
    for field in ("tap_position", "phase_shift"):
        changed = result.network.copy()
        changed.transformers.loc["tx_hm", field] += 1
        evidence = validator.precheck(
            network, changed, sidecar, result.target_sidecar, result.mappings,
            (), protected, voltage_layer_map=result.voltage_layer_map,
        )
        assert not evidence.checks["transformer_semantics"]
    changed = result.network.copy()
    changed.transformer_types.loc["type_hm", "x"] += 0.01
    evidence = validator.precheck(
        network, changed, sidecar, result.target_sidecar, result.mappings,
        (), protected, voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["transformer_semantics"]


def test_only_parallel_pair_loses_its_protected_cycle_corridor() -> None:
    """A two-Line multigraph cycle cannot collapse to a simple bridge."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110)
    network.add("Bus", "b", v_nom=110)
    for name in ("ab1", "ab2"):
        network.add("Line", name, bus0="a", bus1="b", r=0.01, x=0.1,
                    s_nom=100)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, identity_partition(network, sidecar),
        AggregationConfig(), protected,
    )
    assert result.preliminary.checks["simple_ring_topology"]
    assert not result.preliminary.checks["cycle_corridors"]
    assert result.preliminary.details["multigraph_cycle_rank_delta"] == "-1"


def test_unaggregated_parallel_pair_is_a_valid_cycle_corridor() -> None:
    """Two retained parallel Lines preserve a multigraph cycle."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110)
    network.add("Bus", "b", v_nom=110)
    for name in ("ab1", "ab2"):
        network.add("Line", name, bus0="a", bus1="b", x=0.1)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    rows = MappingBundle(
        tuple(MapEntry(sidecar.source_id("Bus", name),
                       sidecar.source_id("Bus", name), "preserved", "identity")
              for name in ("a", "b")),
        tuple(MapEntry(sidecar.source_id("Line", name),
                       sidecar.source_id("Line", name), "preserved", "identity")
              for name in ("ab1", "ab2")),
        (), (),
    )
    evidence = MultiVoltageStructuralValidator().precheck(
        network, network.copy(), sidecar, sidecar, rows, (), protected,
        voltage_layer_map={sidecar.source_id("Bus", name): (110.0, 110.0)
                           for name in ("a", "b")},
    )
    assert evidence.checks["cycle_corridors"]


def test_simple_ring_gate_rejects_equal_rank_rewired_corridor() -> None:
    """Equal cycle rank cannot hide a changed mapped corridor graph."""
    network = pypsa.Network()
    for name in ("a", "b", "c", "d"):
        network.add("Bus", name, v_nom=110)
    for name, a, b in (("ab", "a", "b"), ("bc", "b", "c"),
                       ("cd", "c", "d"), ("da", "d", "a"),
                       ("ac", "a", "c")):
        network.add("Line", name, bus0=a, bus1=b, x=0.1)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, identity_partition(network, sidecar),
        AggregationConfig(), protected,
    )
    changed = result.network.copy()
    chord = next(row.target_id for row in result.mappings.line_map
                 if row.source_id == sidecar.source_id("Line", "ac"))
    assert chord is not None
    changed.lines.loc[result.target_sidecar.backend_name(chord), "bus0"] = (
        result.target_sidecar.backend_name(sidecar.source_id("Bus", "b"))
    )
    changed.lines.loc[result.target_sidecar.backend_name(chord), "bus1"] = (
        result.target_sidecar.backend_name(sidecar.source_id("Bus", "d"))
    )
    evidence = MultiVoltageStructuralValidator().precheck(
        network, changed, sidecar, result.target_sidecar, result.mappings,
        (), protected, voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["simple_ring_topology"]


def test_voltage_layer_gate_checks_line_and_transformer_terminals() -> None:
    """A target Line crossing layers or Transformer within one layer fails."""
    network = three_voltage_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )
    validator = MultiVoltageStructuralValidator()
    changed = result.network.copy()
    high_line = next(row.target_id for row in result.mappings.line_map
                     if row.source_id == sidecar.source_id("Line", "hab"))
    assert high_line is not None
    changed.lines.loc[result.target_sidecar.backend_name(high_line), "bus1"] = (
        changed.transformers.loc["tx_ml", "bus1"]
    )
    evidence = validator.precheck(
        network, changed, sidecar, result.target_sidecar, result.mappings,
        (), protected, voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["voltage_layers"]

    changed = result.network.copy()
    changed.transformers.loc["tx_hm", "bus1"] = (
        changed.transformers.loc["tx_hm", "bus0"]
    )
    evidence = validator.precheck(
        network, changed, sidecar, result.target_sidecar, result.mappings,
        (), protected, voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["voltage_layers"]


def test_alias_canonical_voltage_is_checked_after_build() -> None:
    """The target Bus must keep the explicitly declared alias voltage."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110)
    network.add("Bus", "b", v_nom=109.9)
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, identity_partition(network, sidecar),
        AggregationConfig(), protected,
        voltage_aliases={109.9: 110},
    )
    assert result.preliminary.checks["voltage_layers"]
    changed = result.network.copy()
    target = next(row.target_id for row in result.mappings.bus_map
                  if row.source_id == sidecar.source_id("Bus", "b"))
    assert target is not None
    changed.buses.loc[result.target_sidecar.backend_name(target), "v_nom"] = 109.9
    evidence = MultiVoltageStructuralValidator().precheck(
        network, changed, sidecar, result.target_sidecar, result.mappings,
        (), protected, voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["voltage_layers"]


def test_voltage_layer_gate_rejects_mixed_target_ownership() -> None:
    """One target Bus cannot own source Buses from two canonical layers."""
    network = three_voltage_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )
    high_target = next(row.target_id for row in result.mappings.bus_map
                       if row.source_id == sidecar.source_id("Bus", "ha"))
    assert high_target is not None
    mixed = replace(result.mappings, bus_map=tuple(
        replace(row, target_id=high_target)
        if row.source_id == sidecar.source_id("Bus", "lb") else row
        for row in result.mappings.bus_map
    ))
    evidence = MultiVoltageStructuralValidator().precheck(
        network, result.network, sidecar, result.target_sidecar,
        mixed, (), protected, voltage_layer_map=result.voltage_layer_map,
    )
    assert not evidence.checks["voltage_layers"]
