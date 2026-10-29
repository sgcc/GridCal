"""Quotient mapping and composition tests."""

import pypsa
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import AggregationConfig
from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.exceptions import MappingError
from psdcalc.reduction.mapping.bundle import MappingBundle
from psdcalc.reduction.mapping.compose import compose_maps
from psdcalc.reduction.mapping.rows import MapEntry
from psdcalc.reduction.materializers.quotient import QuotientMaterializer
from psdcalc.reduction.planners.base import PartitionPlan
from psdcalc.reduction.protection.engine import ProtectionEngine


def test_internal_line_has_explicit_absorbed_mapping() -> None:
    network = pypsa.Network()
    for index in range(3):
        network.add("Bus", f"b{index}", v_nom=110.0, x=float(index), y=0.0)
    network.add("Line", "internal", bus0="b0", bus1="b1", r=0.01, x=0.1)
    network.add("Line", "external", bus0="b1", bus1="b2", r=0.01, x=0.1)
    network.add("Load", "load", bus="b2", p_set=10.0)
    network.add("Generator", "g", bus="b0", p_nom=20.0, control="Slack")
    adapter = PypsaAdapter()
    sidecar = adapter.identity_sidecar(network, "n")
    c0 = StableId("n", "BusCluster", "c0")
    c1 = StableId("n", "BusCluster", "c1")
    stable = {sidecar.source_id("Bus", "b0"): c0,
              sidecar.source_id("Bus", "b1"): c0,
              sidecar.source_id("Bus", "b2"): c1}
    partition = PartitionPlan(stable, {f"b{i}": stable[sidecar.source_id("Bus", f"b{i}")].encode()
                                           for i in range(3)}, 2, 2, "reject")
    protected = ProtectionEngine().compile(adapter.inventory(network, sidecar), [])
    quotient = QuotientMaterializer().materialize(
        network, sidecar, partition, AggregationConfig(), protected
    )

    bundle = MappingBundle.from_quotient(network, sidecar, partition, quotient,
                                         operation_id="cluster-1")

    internal = next(row for row in bundle.branch_map
                    if row.source_id == sidecar.source_id("Line", "internal"))
    assert internal.mapping_type == "absorbed_internal"
    assert internal.target_id is None
    assert len(bundle.bus_map) == 3
    assert len(bundle.device_map) == 2
    assert len(bundle.control_map) == 1
    assert bundle.control_map[0].source_id == sidecar.source_id("Generator", "g")


def test_map_composition_preserves_absorbed_branch() -> None:
    source = StableId("n", "Line", "l0")
    middle = StableId("n", "Line", "l1")
    final = StableId("n", "Line", "l2")
    first = [MapEntry(source, middle, "aggregated", operation_id="a")]
    second = [MapEntry(middle, final, "aggregated", operation_id="b")]

    assert compose_maps(first, second)[0].target_id == final

    absorbed = [MapEntry(source, None, "absorbed_internal", operation_id="a")]
    assert compose_maps(absorbed, second)[0].target_id is None


def test_mapping_bundle_parquet_readback_and_corruption(tmp_path) -> None:
    """Persisted maps decode exactly and reject damaged StableId cells."""
    bus = StableId("n", "Bus", "b")
    line = StableId("n", "Line", "l")
    bundle = MappingBundle(
        (MapEntry(bus, bus, "preserved", "op"),),
        (MapEntry(line, line, "preserved", "op"),), (), (),
    )
    files = bundle.save_parquet(tmp_path)
    expected = tuple(files)
    assert MappingBundle.load_parquet(tmp_path, expected_files=expected) == bundle
    table = pq.read_table(files["bus_map"])
    rows = table.to_pylist()
    rows[0]["target_id"] = "corrupt"
    pq.write_table(pa.Table.from_pylist(rows), files["bus_map"])
    with pytest.raises(MappingError):
        MappingBundle.load_parquet(tmp_path, expected_files=expected)


@pytest.mark.parametrize("component", ["Line", "Transformer"])
def test_branch_projection_must_match_authoritative_map(tmp_path, component) -> None:
    """Damaged read-only branch projections cannot pass readback."""
    bus = StableId("n", "Bus", "b")
    line = StableId("n", component, "l")
    bundle = MappingBundle((MapEntry(bus, bus, "preserved", "op"),),
                           (MapEntry(line, line, "preserved", "op"),), ())
    bundle.save_parquet(tmp_path)
    wrong = MapEntry(line, StableId("n", component, "wrong"), "aggregated", "op")
    name = "line_map" if component == "Line" else "transformer_map"
    pq.write_table(pa.Table.from_pylist([wrong.to_row()]),
                   tmp_path / f"{name}.parquet")
    with pytest.raises(MappingError, match="differs from branch_map"):
        MappingBundle.load_parquet(
            tmp_path, expected_files=("bus_map", "branch_map", name))
