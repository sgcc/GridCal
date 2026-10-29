"""Structural gate checks on detached, programmatic PyPSA networks."""

import pypsa

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.protection.engine import ProtectionEngine
from psdcalc.reduction.validation.structural import StructuralValidator


def _two_component_network() -> pypsa.Network:
    network = pypsa.Network()
    for name in ("a", "b", "c", "d"):
        network.add("Bus", name, v_nom=110.0)
    network.add("Line", "ab", bus0="a", bus1="b", r=0.01, x=0.1)
    network.add("Line", "cd", bus0="c", bus1="d", r=0.01, x=0.1)
    network.add("Load", "demand", bus="b", p_set=10.0)
    return network


def test_valid_disconnected_network_is_not_rejected() -> None:
    original = _two_component_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(original, "source")
    bundle = MappingBundle(
        bus_map=tuple(MapEntry(sidecar.source_id("Bus", name),
                               sidecar.source_id("Bus", name), "preserved", "step")
                      for name in original.buses.index),
        branch_map=tuple(MapEntry(sidecar.source_id("Line", name),
                                  sidecar.source_id("Line", name), "preserved", "step")
                         for name in original.lines.index),
        device_map=(MapEntry(sidecar.source_id("Load", "demand"),
                             sidecar.source_id("Load", "demand"), "preserved", "step"),),
    )
    protected = ProtectionEngine().compile(adapter.inventory(original, sidecar), [])
    result = StructuralValidator().validate(original, original.copy(), sidecar,
                                            bundle, protected)
    assert result.status == "STRUCTURALLY_VALID"
    assert result.checks["component_boundaries"] is True


def test_missing_branch_mapping_is_invalid() -> None:
    original = _two_component_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(original, "source")
    bundle = MappingBundle(
        bus_map=tuple(MapEntry(sidecar.source_id("Bus", name),
                               StableId("source", "Bus", name), "preserved", "step")
                      for name in original.buses.index),
        branch_map=(), device_map=(),
    )
    protected = ProtectionEngine().compile(adapter.inventory(original, sidecar), [])
    result = StructuralValidator().validate(original, original.copy(), sidecar,
                                            bundle, protected)
    assert result.status == "STRUCTURALLY_INVALID"
    assert result.checks["mapping_coverage"] is False


def test_wrong_device_terminal_is_structurally_invalid() -> None:
    original = _two_component_network()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(original, "source")
    bundle = MappingBundle(
        bus_map=tuple(MapEntry(sidecar.source_id("Bus", name),
                               sidecar.source_id("Bus", name), "preserved", "step")
                      for name in original.buses.index),
        branch_map=tuple(MapEntry(sidecar.source_id("Line", name),
                                  sidecar.source_id("Line", name), "preserved", "step")
                         for name in original.lines.index),
        device_map=(MapEntry(sidecar.source_id("Load", "demand"),
                             sidecar.source_id("Load", "demand"), "preserved", "step"),),
    )
    reduced = original.copy()
    reduced.loads.at["demand", "bus"] = "a"
    protected = ProtectionEngine().compile(adapter.inventory(original, sidecar), [])

    result = StructuralValidator().validate(original, reduced, sidecar,
                                            bundle, protected)

    assert result.status == "STRUCTURALLY_INVALID"
    assert result.checks["device_ownership"] is False
