"""End-to-end custom build on an offline programmatic PyPSA network."""

import json
from pathlib import Path

import pandas as pd
import pypsa
import yaml

from psdcalc.reduction.cli import main
from psdcalc.reduction.core.ids import StableId


def test_build_persists_structural_evidence(tmp_path: Path, capsys) -> None:
    network = pypsa.Network()
    for name in ("a", "b", "c"):
        network.add("Bus", name, v_nom=110.0)
    network.add("Line", "ab", bus0="a", bus1="b", r=0.01, x=0.1)
    network.add("Line", "bc", bus0="b", bus1="c", r=0.01, x=0.1)
    network.add("Load", "load", bus="c", p_set=5.0)
    network.export_to_netcdf(tmp_path / "input.nc")
    pd.DataFrame([
        {"source_id": StableId("n", "Bus", name).encode(),
         "target_id": StableId("n", "BusCluster", target).encode()}
        for name, target in (("a", "left"), ("b", "left"), ("c", "right"))
    ]).to_csv(tmp_path / "map.csv", index=False)
    config = {
        "schema_version": "0.1.0",
        "run": {"id": "case", "seed": 7, "output_dir": "runs"},
        "input": {"backend": "pypsa", "path": "input.nc", "format": "netcdf",
                  "source_namespace": "n"},
        "task": {"type": "planning", "profile": "planning_aggregation",
                 "reduction_role": "candidate", "final_authority": "original"},
        "partition": {"planner": "custom_busmap", "target": {"type": "count",
                                                         "n_clusters": 2},
                      "custom_busmap": {"path": "map.csv"}},
        "aggregation": {"backend": "pypsa", "aggregate_one_ports": ["Load"]},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    assert main(["build", str(path)]) == 0
    manifest = json.loads((tmp_path / "runs/case/run_manifest.json").read_text())
    assert manifest["status"] == "STRUCTURALLY_VALID"
    assert (tmp_path / "runs/case/maps/branch_map.parquet").exists()
    assert main(["report", str(tmp_path / "runs/case")]) == 0
    assert "STRUCTURALLY_VALID" in capsys.readouterr().out


def test_topology_build_preserves_transformer_interface(tmp_path: Path) -> None:
    network = pypsa.Network()
    for name, voltage in (("a", 110), ("r", 110), ("lv", 10)):
        network.add("Bus", name, v_nom=voltage)
    network.add("Line", "ar", bus0="a", bus1="r", r=0.01, x=0.1)
    network.add("Transformer", "t", bus0="a", bus1="lv", r=0.01, x=0.1)
    network.add("Load", "demand", bus="r", p_set=3.0)
    network.export_to_netcdf(tmp_path / "input.nc")
    config = {
        "schema_version": "0.1.0",
        "run": {"id": "topology", "seed": 7, "output_dir": "runs"},
        "input": {"backend": "pypsa", "path": "input.nc", "format": "netcdf",
                  "source_namespace": "n"},
        "task": {"type": "planning", "profile": "planning_aggregation",
                 "reduction_role": "candidate", "final_authority": "original"},
        "pipeline": {"materialization_family": "simplification"},
        "simplification": {"operations": [{"id": "t1", "type": "voltage_aware_topology",
                                           "enabled": True,
                                           "mode": "keep_transformer_interface"}]},
        "aggregation": {"backend": "pypsa"},
    }
    path = tmp_path / "topology.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")

    assert main(["build", str(path)]) == 0
    run = tmp_path / "runs/topology"
    manifest = json.loads((run / "run_manifest.json").read_text())
    assert manifest["status"] == "STRUCTURALLY_VALID"
    assert manifest["operation_status"] == "STRUCTURALLY_SIMPLIFIED"
    assert (run / "validation/voltage_layer_map.json").exists()
    restored = pypsa.Network(run / "levels/L1.nc")
    assert set(restored.transformers.index) == {"t"}
    assert set(restored.buses.index) == {"a", "lv"}
