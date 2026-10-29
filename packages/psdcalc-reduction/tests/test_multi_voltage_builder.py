"""Pure in-memory multi-voltage quotient construction on small PyPSA grids."""

import pandas as pd
import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import AggregationConfig
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.exceptions import BackendCapabilityError
from psdcalc.reduction.materializers.multi_voltage import MultiVoltageQuotientBuilder
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
                                  protection)
    assert evidence.checks["transformer_semantics"] is False

    swapped = result.network.copy()
    a = swapped.transformers.loc["tx_hm", "bus0"]
    b = swapped.transformers.loc["tx_hm", "bus1"]
    swapped.transformers.loc["tx_hm", ["bus0", "bus1"]] = [b, a]
    evidence = validator.precheck(network, swapped, sidecar,
                                  result.target_sidecar, result.mappings, (),
                                  protection)
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
    assert not result.preliminary.checks["ring_topology"]


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
                                  protected)
    assert evidence.checks["aggregation_invariants"] is False

    changed = result.network.copy()
    changed.carriers.loc["electricity", "co2_emissions"] = 0.5
    evidence = validator.precheck(network, changed, sidecar,
                                  result.target_sidecar, result.mappings, (),
                                  protected)
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
    result = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=["Load"]), protected,
    )

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

    assert result.preliminary.checks["ring_topology"]
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
                                  (spec,), protected)
    assert evidence.checks["aggregation_invariants"]
    changed.loads_t.p_set.loc[network.snapshots[0], target_name] += 1
    evidence = validator.precheck(network, changed, sidecar,
                                  result.target_sidecar, result.mappings,
                                  (spec,), protected)
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
