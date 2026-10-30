"""Five-stage inventory and active-Line graph contracts."""

import pypsa

from psdcalc.reduction.metrics.stage_inventory import (
    CompressionStage, pypsa_stage_inventory, source_stage_inventory,
)
from psdcalc.reduction.readiness.bpa_ac import BpaAcInventory
from psdcalc.reduction.readiness.psdcase_ac import PsdCaseAcAdapter
from test_bpa_ac_readiness import _case_with_three_winding_and_lplus
from pathlib import Path


def test_active_parallel_lines_have_distinct_cycle_ranks() -> None:
    """A parallel-only cycle has multi rank one and simple rank zero."""
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110)
    network.add("Bus", "b", v_nom=110)
    network.add("Line", "first", bus0="a", bus1="b", r=0.1, x=0.2)
    network.add("Line", "second", bus0="b", bus1="a", r=0.2, x=0.3)
    network.add("Line", "inactive", bus0="a", bus1="b", active=False)
    inventory = pypsa_stage_inventory(
        network, CompressionStage.PYPSA_FULL_PROJECTION)
    assert inventory.bus_count == 2
    assert inventory.line_count == 3
    assert inventory.active_line_count == 2
    assert inventory.inactive_line_count == 1
    assert inventory.active_connected_component_count == 1
    assert inventory.simple_cycle_rank == 0
    assert inventory.multigraph_cycle_rank == 1
    assert inventory.cycle_edge_count == 2
    assert inventory.bridge_count == 0
    assert inventory.articulation_count == 0
    assert inventory.two_core_bus_count == 0


def test_disconnected_active_line_topology() -> None:
    """Isolated Buses remain connected components in each voltage layer."""
    network = pypsa.Network()
    for name in ("a", "b", "c"):
        network.add("Bus", name, v_nom=110)
    network.add("Line", "ab", bus0="a", bus1="b", r=0.1, x=0.2)
    inventory = pypsa_stage_inventory(
        network, CompressionStage.FINAL_RELOADED)
    assert inventory.active_connected_component_count == 2
    assert inventory.largest_component_bus_count == 2
    assert inventory.bridge_count == 1
    assert inventory.simple_cycle_rank == inventory.multigraph_cycle_rank == 0


def test_source_three_winding_group_and_legs_are_distinct() -> None:
    """S0 counts source devices and DAT legs independently of S1 materialization."""
    case = _case_with_three_winding_and_lplus()
    cards = BpaAcInventory(Path("private.dat"), 10,
                           {"BE": 1, "B ": 4, "L ": 1, "L+": 1, "T ": 3},
                           {}, {}, {})
    projected = PsdCaseAcAdapter().adapt(case, cards, namespace="fixture")
    source = source_stage_inventory(case, cards, projected)
    materialized = pypsa_stage_inventory(
        projected.network, CompressionStage.PYPSA_FULL_PROJECTION)
    assert source.source_transformer2w_count == 0
    assert source.source_transformer3w_group_count == 1
    assert source.source_transformer3w_leg_count == 3
    assert source.transformer_count == 1
    assert materialized.transformer_count == 3
    assert source.source_be_count == 1
    assert source.source_l_plus_count == 1
    assert source.known_dc_card_count == 0
