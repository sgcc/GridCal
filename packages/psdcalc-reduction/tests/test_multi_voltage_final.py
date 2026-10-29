"""Semantic baseline tests for final validation."""
import pypsa
from psdcalc.reduction.validation.multi_voltage_final import network_semantic_signature


def test_signature_detects_static_and_weighting_changes():
    network = pypsa.Network()
    network.add('Bus', 'a', v_nom=110)
    baseline = network_semantic_signature(network)
    assert network_semantic_signature(network.copy()) == baseline
    network.buses.loc['a', 'v_nom'] = 220
    assert network_semantic_signature(network) != baseline
    baseline = network_semantic_signature(network)
    network.snapshot_weightings.iloc[0, 0] = 2
    assert network_semantic_signature(network) != baseline


def test_final_clean_reload_and_corruption(tmp_path):
    from test_multi_voltage_builder import three_voltage_network, partition
    from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
    from psdcalc.reduction.config.models import AggregationConfig
    from psdcalc.reduction.materializers.multi_voltage import MultiVoltageQuotientBuilder
    from psdcalc.reduction.protection.engine import ProtectionEngine
    from psdcalc.reduction.validation.multi_voltage_final import MultiVoltageFinalValidator
    network = three_voltage_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, 'n')
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    baseline = network_semantic_signature(network)
    built = MultiVoltageQuotientBuilder().build(network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=['Load']), protection)
    path = tmp_path / 'child.nc'
    built.network.export_to_netcdf(path)
    child = pypsa.Network(path)
    def validate():
        return MultiVoltageFinalValidator().validate(network, child, sidecar,
            built.target_sidecar, built.mappings, (), protection,
            voltage_layer_map=built.voltage_layer_map, original_network=network,
            original_sidecar=sidecar, composed_mappings=built.mappings,
            original_input_signature=baseline)
    assert validate().status == 'MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID'
    child.transformers.loc['tx_hm', 'x'] = 99
    assert validate().status == 'MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID'


def test_final_rejects_target_without_source_provenance(tmp_path):
    """Every final non-metadata object must have a source mapping row."""
    from test_multi_voltage_builder import three_voltage_network, partition
    from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
    from psdcalc.reduction.config.models import AggregationConfig
    from psdcalc.reduction.core.ids import IdentitySidecar, StableId
    from psdcalc.reduction.materializers.multi_voltage import MultiVoltageQuotientBuilder
    from psdcalc.reduction.protection.engine import ProtectionEngine
    from psdcalc.reduction.validation.multi_voltage_final import MultiVoltageFinalValidator
    network = three_voltage_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, 'n')
    protection = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    built = MultiVoltageQuotientBuilder().build(
        network, sidecar, partition(network, sidecar),
        AggregationConfig(aggregate_one_ports=['Load']), protection)
    child = built.network.copy()
    child.add('Load', 'orphan', bus=str(child.buses.index[0]), p_set=0.0)
    rows = built.target_sidecar.to_rows() + [{
        'component': 'Load', 'backend_name': 'orphan',
        'source_id': StableId('n', 'Load', 'orphan').encode()}]
    target_sidecar = IdentitySidecar.from_rows(rows)
    result = MultiVoltageFinalValidator().validate(
        network, child, sidecar, target_sidecar, built.mappings, (), protection,
        voltage_layer_map=built.voltage_layer_map, original_network=network,
        original_sidecar=sidecar, composed_mappings=built.mappings,
        original_input_signature=network_semantic_signature(network))
    assert result.checks['target_provenance_coverage'] is False
    assert result.status == 'MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID'
