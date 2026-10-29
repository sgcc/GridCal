"""Configuration contracts for multi-voltage construction."""

import pytest

from psdcalc.reduction.config.loader import load_config
from psdcalc.reduction.exceptions import (
    ConfigSchemaError, ConfigSemanticError, UnsupportedFeatureError,
)
from test_config import minimal_config


def valid():
    data = minimal_config()
    data['pipeline'] = {'materialization_family': 'multi_voltage_quotient'}
    data['voltage_layers'] = {'aliases': {110.0: 110.0}}
    data['partition']['target']['per_layer'] = {'110': 2}
    data['aggregation']['invariants'] = [{'component': 'Load', 'attribute': 'p_set', 'strategy': 'sum', 'dynamic': True}]
    return data


def test_multi_voltage_configuration():
    config = load_config(valid())
    assert config.voltage_layers.aliases == {110.0: 110.0}
    assert config.partition.target.per_layer == {'110': 2}
    assert config.aggregation.invariants[0].dynamic


@pytest.mark.parametrize('section,value', [
    ('voltage_layers', {'aliases': {0: 110}}),
    ('voltage_layers', {'aliases': {110: float('nan')}}),
    ('voltage_layers', {'unexpected': 1}),
    ('aggregation', {'invariants': [{'component': 'Load', 'attribute': 'p_set', 'strategy': 'unknown'}]}),
])
def test_invalid_config(section, value):
    data = valid()
    data[section] = value
    with pytest.raises((ConfigSchemaError, ConfigSemanticError)):
        load_config(data)


def test_multi_voltage_requires_count_and_topology():
    data = valid()
    data['partition']['target'] = {'type': 'ratio', 'ratio': 0.5}
    with pytest.raises(ConfigSemanticError):
        load_config(data)


def test_save_all_levels_only_controls_optional_hierarchy() -> None:
    """L1/L2 audit levels are mandatory even when the optional flag is false."""
    data = valid()
    data['output'] = {'save_all_levels': True}
    with pytest.raises(UnsupportedFeatureError, match='optional hierarchy'):
        load_config(data)
