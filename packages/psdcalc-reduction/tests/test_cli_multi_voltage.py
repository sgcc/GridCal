"""CLI acceptance for the persisted multi-voltage structural path."""

import json
from pathlib import Path

import yaml

from psdcalc.reduction.cli import main
from test_three_voltage_pipeline import pipeline_config, three_voltage_source


def test_cli_plan_build_report(tmp_path: Path, capsys) -> None:
    """The CLI exposes planning, final status, and opportunity evidence."""
    source = tmp_path / "source.nc"
    three_voltage_source().export_to_netcdf(source)
    config = pipeline_config(source, run_id="cli-multi-voltage")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json")),
                           encoding="utf-8")
    assert main(["validate-config", str(config_path)]) == 0
    capsys.readouterr()
    assert main(["plan", str(config_path)]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["requested_cluster_count"] == 7
    assert main(["build", str(config_path)]) == 0
    run_dir = Path(capsys.readouterr().out.strip())
    assert main(["report", str(run_dir)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["manifest"]["status"] == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID")
    assert report["multi_voltage_structural"]["status"] == (
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID")
    assert report["preliminary"]["finalized"] is False
    assert report["opportunity"]["realized_bus_compression_fraction"] > 0
    comparison_dir = run_dir / "reports" / "compression-comparison"
    comparison_dir.mkdir(parents=True)
    (comparison_dir / "compression_comparison.json").write_text(
        json.dumps({"model_id": "SYNTHETIC", "parallel_corridor_policy":
                    "preserve_protected_members", "final_structural_status":
                    "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
                    "physical_fidelity_status": "PHYSICAL_FIDELITY_NOT_YET_EVALUATED",
                    "candidate_bus_compression_fraction": 0.2,
                    "accepted_bus_compression_fraction": 0.2,
                    "failed_checks": [], "stages": {}, "parallel_corridors": {}}))
    assert main(["report", str(run_dir)]) == 0
    appended = json.loads(capsys.readouterr().out)
    assert appended["compression_comparison"]["model_id"] == "SYNTHETIC"
