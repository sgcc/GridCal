"""Custom busmap planning and deterministic connectivity tests."""

import csv
from pathlib import Path

import pypsa
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import MatchConfig, ProtectionRule
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.planners.custom_busmap import CustomBusmapPlanner
from psdcalc.reduction.protection.engine import ProtectionEngine
from psdcalc.reduction.exceptions import PartitionError


def two_islands() -> pypsa.Network:
    """Create two separate two-bus source components."""
    network = pypsa.Network()
    for name in ("a", "b", "c", "d"):
        network.add("Bus", name, v_nom=110.0)
    network.add("Line", "ab", bus0="a", bus1="b", r=0.01, x=0.1)
    network.add("Line", "cd", bus0="c", bus1="d", r=0.01, x=0.1)
    return network


def write_map(path: Path, sidecar: object, groups: dict[str, str]) -> None:
    """Write an authoritative stable-ID custom map."""
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["source_id", "target_id"])
        writer.writeheader()
        for source, target in groups.items():
            writer.writerow({
                "source_id": sidecar.source_id("Bus", source).encode(),
                "target_id": StableId("n", "BusCluster", target).encode(),
            })


def test_disconnected_cluster_rejects_or_splits_deterministically(tmp_path: Path) -> None:
    network = two_islands()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    path = tmp_path / "map.csv"
    write_map(path, sidecar, dict.fromkeys(("a", "b", "c", "d"), "all"))
    planner = CustomBusmapPlanner()

    with pytest.raises(PartitionError, match="disconnected"):
        planner.plan(network, sidecar, path, protected, connectivity_policy="reject")

    first = planner.plan(network, sidecar, path, protected,
                         connectivity_policy="split_disconnected")
    second = planner.plan(network, sidecar, path, protected,
                          connectivity_policy="split_disconnected")
    assert first.requested_cluster_count == 1
    assert first.actual_cluster_count == 2
    assert first.stable_busmap == second.stable_busmap


def test_cross_voltage_cluster_is_rejected(tmp_path: Path) -> None:
    network = two_islands()
    network.buses.loc["b", "v_nom"] = 220.0
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    path = tmp_path / "map.csv"
    write_map(path, sidecar, {"a": "one", "b": "one", "c": "two", "d": "two"})

    with pytest.raises(PartitionError, match="voltage"):
        CustomBusmapPlanner().plan(network, sidecar, path, protected)


def test_split_keeps_protected_anchor_in_its_original_component(tmp_path: Path) -> None:
    network = two_islands()
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    anchor = sidecar.source_id("Bus", "c")
    path = tmp_path / "map.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["source_id", "target_id"])
        writer.writeheader()
        for name in network.buses.index:
            writer.writerow({"source_id": sidecar.source_id("Bus", str(name)).encode(),
                             "target_id": anchor.encode()})
    protected = ProtectionEngine().compile(
        adapter.inventory(network, sidecar), [
            ProtectionRule(
                id="anchor", component="Bus", action="preserve_terminal_bus",
                match=MatchConfig(
                    field="source_id", op="eq", value=anchor.encode()),
            )
        ]
    )

    plan = CustomBusmapPlanner().plan(network, sidecar, path, protected,
                                      connectivity_policy="split_disconnected")
    assert plan.stable_busmap[anchor] == anchor
