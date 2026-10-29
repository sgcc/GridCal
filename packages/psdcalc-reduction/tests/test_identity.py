"""Stable identity and deterministic signature tests."""

from pathlib import Path

import pypsa
import pytest
from hypothesis import given, strategies as st

from psdcalc.reduction.core.ids import IdentityBinding, IdentitySidecar, StableId
from psdcalc.reduction.core.inventory import GridInventory, InventoryItem
from psdcalc.reduction.core.signatures import (
    TopologyBranch, TopologyBus, hash_file, hash_path, network_content_hash,
    topology_signature,
)
from psdcalc.reduction.exceptions import IdentityError


def test_stable_id_encoding_is_round_trip_and_separator_safe() -> None:
    first = StableId("a:b", "Line", "x/y")
    second = StableId("a", "b:Line", "x/y")

    assert StableId.decode(first.encode()) == first
    assert first.encode() != second.encode()


@given(st.text(min_size=1, max_size=30), st.text(min_size=1, max_size=30),
       st.text(min_size=1, max_size=30))
def test_stable_id_round_trip_for_arbitrary_source_keys(
    namespace: str, component: str, source_key: str
) -> None:
    source_id = StableId(namespace, component, source_key)
    assert StableId.decode(source_id.encode()) == source_id


def test_sidecar_rejects_duplicate_backend_binding() -> None:
    first = IdentityBinding("Bus", "b1", StableId("a", "Bus", "1"))
    second = IdentityBinding("Bus", "b1", StableId("a", "Bus", "2"))

    with pytest.raises(IdentityError, match="duplicate"):
        IdentitySidecar([first, second])


def test_topology_signature_ignores_input_order_but_not_voltage() -> None:
    a = TopologyBus(StableId("n", "Bus", "a"), 110.0)
    b = TopologyBus(StableId("n", "Bus", "b"), 110.0)
    line = TopologyBranch(StableId("n", "Line", "l"), a.source_id, b.source_id)

    assert topology_signature([a, b], [line]) == topology_signature([b, a], [line])
    assert topology_signature([a, b], [line]) != topology_signature(
        [a, TopologyBus(b.source_id, 220.0)], [line]
    )


def test_file_hash_changes_with_bytes(tmp_path: Path) -> None:
    path = tmp_path / "grid.nc"
    path.write_bytes(b"first")
    before = hash_file(path)
    path.write_bytes(b"second")
    assert hash_file(path) != before


def test_csv_folder_hash_covers_names_and_bytes(tmp_path: Path) -> None:
    folder = tmp_path / "csv"
    folder.mkdir()
    (folder / "b.csv").write_text("x\n2\n")
    (folder / "a.csv").write_text("x\n1\n")
    before = hash_path(folder)
    (folder / "b.csv").write_text("x\n3\n")
    assert hash_path(folder) != before


def test_network_content_hash_covers_device_values() -> None:
    network = pypsa.Network()
    network.add("Bus", "a", v_nom=110)
    network.add("Load", "demand", bus="a", p_set=1.0)
    before = network_content_hash(network)
    network.loads.at["demand", "p_set"] = 2.0
    assert network_content_hash(network) != before


def test_inventory_counts_by_component() -> None:
    bus = StableId("n", "Bus", "1")
    inventory = GridInventory([
        InventoryItem(bus, "Bus"),
        InventoryItem(StableId("n", "Generator", "g"), "Generator", (bus,), True),
    ])

    assert inventory.counts() == {"Bus": 1, "Generator": 1}
