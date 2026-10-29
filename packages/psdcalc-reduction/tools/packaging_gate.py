"""Build and check the distribution in a fresh wheel-installed environment.

The JSON summary is generated evidence and is never used as a source fixture.
Run with the package development Python, for example:

    .venv/bin/python tools/packaging_gate.py --output-dir /tmp/psdcalc-pr013
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile


PACKAGE = Path(__file__).resolve().parents[1]


class PackagingGateError(RuntimeError):
    """A distribution or installed package failed the packaging Gate."""


def _run(name: str, argv: list[str], summary: dict[str, object], *,
         cwd: Path, env: dict[str, str]) -> str:
    """Run a checked command and retain bounded evidence for the summary."""
    result = subprocess.run(argv, cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            check=False)
    steps = summary["steps"]
    assert isinstance(steps, list)
    steps.append({"name": name, "command": argv, "exit_code": result.returncode,
                  "output_tail": result.stdout[-8000:]})
    if result.returncode:
        raise PackagingGateError(f"{name} failed ({result.returncode}): "
                                 f"{result.stdout[-1200:]}")
    return result.stdout


def _digest(path: Path) -> str:
    """Return a SHA-256 digest for a built distribution."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_archives(sdist: Path, wheel: Path) -> dict[str, object]:
    """Require all Python modules and resources in both build formats."""
    project = tomllib.loads((PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))
    distribution = project["project"]["name"].replace("-", "_")
    dist_info = f"{distribution}-{project['project']['version']}.dist-info"
    with tarfile.open(sdist, "r:gz") as archive:
        source_members = {name.split("/", 1)[1] for name in archive.getnames()
                          if "/" in name}
    with zipfile.ZipFile(wheel) as archive:
        wheel_members = set(archive.namelist())
    modules = {str(path.relative_to(PACKAGE / "src"))
               for path in (PACKAGE / "src/psdcalc").rglob("*.py")}
    resources = {
        "psdcalc/reduction/resources/reduction.schema.json",
        "psdcalc/reduction/resources/reduction.first-batch.yaml",
        "psdcalc/reduction/resources/reduction.three-voltage.yaml",
    }
    expected_source = {f"src/{name}" for name in modules | resources} | {
        "pyproject.toml", "README.md", "LICENSE.md", "MANIFEST.in",
        "schema/reduction.schema.json", "examples/reduction.first-batch.yaml",
        "examples/reduction.three-voltage.yaml",
        "tests/test_package_resources.py", "tools/packaging_gate.py",
    }
    expected_wheel = modules | resources | {
        f"{dist_info}/entry_points.txt",
        f"{dist_info}/licenses/LICENSE.md",
    }
    missing_source = sorted(expected_source - source_members)
    missing_wheel = sorted(expected_wheel - wheel_members)
    if missing_source or missing_wheel:
        raise PackagingGateError(
            f"Missing archive members: sdist={missing_source}, wheel={missing_wheel}"
        )
    with zipfile.ZipFile(wheel) as archive:
        entry_points = archive.read(
            f"{dist_info}/entry_points.txt"
        ).decode("utf-8")
        if "psdcalc-reduction = psdcalc.reduction.cli:main" not in entry_points:
            raise PackagingGateError("CLI entry point is missing from wheel")
        for name, source in (
            ("reduction.schema.json", PACKAGE / "schema/reduction.schema.json"),
            ("reduction.first-batch.yaml",
             PACKAGE / "examples/reduction.first-batch.yaml"),
            ("reduction.three-voltage.yaml",
             PACKAGE / "examples/reduction.three-voltage.yaml"),
        ):
            if archive.read(f"psdcalc/reduction/resources/{name}") != source.read_bytes():
                raise PackagingGateError(f"Wheel resource differs from {source}")
    return {"sdist_member_count": len(source_members),
            "wheel_member_count": len(wheel_members),
            "module_count": len(modules), "resources": sorted(resources)}


def run_gate(output_dir: Path) -> dict[str, object]:
    """Execute tests, clean build, wheel-only installation, and CLI smoke."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {
        "gate": "PSDCalc Packaging Gate", "status": "FAILED", "steps": [],
        "python": sys.version.split()[0],
        "build_requirement": "setuptools>=70",
        "source_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PACKAGE, capture_output=True,
            text=True, check=True,
        ).stdout.strip(),
    }
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["MPLCONFIGDIR"] = str(output_dir / "matplotlib")
    Path(env["MPLCONFIGDIR"]).mkdir(exist_ok=True)
    outside = output_dir / "outside-checkout"
    outside.mkdir(exist_ok=True)
    try:
        summary["uv"] = _run("uv_version", ["uv", "--version"], summary,
                             cwd=outside, env=env).strip()
        test_env = {**env, "PYTHONPATH": str(PACKAGE / "src")}
        pytest_output = _run("package_tests", [sys.executable, "-m", "pytest", "-q",
                                               "tests"], summary, cwd=PACKAGE,
                             env=test_env)
        match = re.search(r"(\d+) passed", pytest_output)
        if match is None:
            raise PackagingGateError("pytest did not report a passed-test count")
        summary["passed_tests"] = int(match.group(1))
        _run("isolated_build", ["uv", "build", "--python", sys.executable,
                                "--out-dir", str(output_dir / "dist"), "--clear",
                                str(PACKAGE)], summary, cwd=outside, env=env)
        sdists = list((output_dir / "dist").glob("*.tar.gz"))
        wheels = list((output_dir / "dist").glob("*.whl"))
        if len(sdists) != 1 or len(wheels) != 1:
            raise PackagingGateError("Build must emit exactly one sdist and one wheel")
        sdist, wheel = sdists[0], wheels[0]
        summary["artifacts"] = {
            "sdist": {"path": str(sdist), "sha256": _digest(sdist)},
            "wheel": {"path": str(wheel), "sha256": _digest(wheel)},
        }
        summary["archive_check"] = _check_archives(sdist, wheel)
        clean_python = output_dir / "clean-venv/bin/python"
        _run("create_clean_venv", [sys.executable, "-m", "venv",
                                   str(output_dir / "clean-venv")],
             summary, cwd=outside, env=env)
        _run("wheel_only_install", ["uv", "pip", "install", "--python",
                                    str(clean_python), "--only-binary", ":all:",
                                    str(wheel)], summary, cwd=outside, env=env)
        smoke = r'''from importlib.resources import files
from pathlib import Path
import json
import psdcalc.reduction as package
from psdcalc.reduction.config.models import ReductionConfig
from importlib.metadata import version
root = Path.cwd().parent
assert "site-packages" in str(Path(package.__file__).resolve())
resources = files("psdcalc.reduction.resources")
schema = resources.joinpath("reduction.schema.json").read_text(encoding="utf-8")
example = resources.joinpath("reduction.first-batch.yaml").read_text(encoding="utf-8")
multi_example = resources.joinpath("reduction.three-voltage.yaml").read_text(encoding="utf-8")
assert json.loads(schema) == ReductionConfig.model_json_schema()
assert "schema_version" in example
assert "multi_voltage_quotient" in multi_example
(root / "installed-example.yaml").write_text(example, encoding="utf-8")
(root / "installed-three-voltage.yaml").write_text(multi_example, encoding="utf-8")
print(json.dumps({"module": package.__file__, "pypsa": version("pypsa"),
                  "pydantic": version("pydantic"),
                  "psdcalc_reduction": version("psdcalc-reduction")}))'''
        output = _run("installed_resource_import", [str(clean_python), "-I", "-c",
                                                     smoke], summary, cwd=outside,
                      env=env)
        summary["installed_versions"] = json.loads(output.strip().splitlines()[-1])
        cli = str(output_dir / "clean-venv/bin/psdcalc-reduction")
        _run("cli_help", [cli, "--help"], summary, cwd=outside, env=env)
        _run("cli_validate_config", [cli, "validate-config",
                                     str(output_dir / "installed-example.yaml")],
             summary, cwd=outside, env=env)
        _run("cli_validate_multi_voltage", [cli, "validate-config",
                                            str(output_dir / "installed-three-voltage.yaml")],
             summary, cwd=outside, env=env)
        exported = output_dir / "exported-schema.json"
        _run("cli_dump_schema", [cli, "dump-schema", str(exported)],
             summary, cwd=outside, env=env)
        if json.loads(exported.read_text(encoding="utf-8")) != json.loads(
            (PACKAGE / "schema/reduction.schema.json").read_text(encoding="utf-8")
        ):
            raise PackagingGateError("Installed CLI exported a different schema")
        multi_smoke = r'''from pathlib import Path
import pandas as pd
import pypsa
import yaml
root = Path.cwd().parent
n = pypsa.Network()
n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
for name, voltage in (("a", 110), ("b", 110), ("c", 110),
                      ("x", 35), ("z1", 35), ("z2", 35), ("y", 35),
                      ("u", 10), ("v", 10)):
    n.add("Bus", name, v_nom=voltage)
for name, left, right in (("ab", "a", "b"), ("bc", "b", "c"),
                          ("ca", "c", "a"), ("xz1", "x", "z1"),
                          ("z1z2", "z1", "z2"), ("z2y", "z2", "y"),
                          ("uv", "u", "v")):
    n.add("Line", name, bus0=left, bus1=right,
          r=0.01, x=0.1, s_nom=100)
n.add("Transformer", "tx1", bus0="a", bus1="x",
      r=0.01, x=0.1, s_nom=100)
n.add("Transformer", "tx2", bus0="y", bus1="u",
      r=0.01, x=0.1, s_nom=100)
n.add("Generator", "slack", bus="a", control="Slack", p_nom=100)
n.add("Generator", "pv", bus="c", control="PV", p_nom=20)
n.add("ShuntImpedance", "sh", bus="x", g=0.01)
n.add("Load", "demand", bus="v", p_set=5.0)
n.add("Load", "mid1", bus="z1", p_set=2.0)
n.add("Load", "mid2", bus="z2", p_set=3.0)
source = root / "smoke-three-voltage.nc"
n.export_to_netcdf(source)
config = {
    "schema_version": "0.1.0",
    "run": {"id": "smoke-three-voltage", "seed": 17,
            "output_dir": str(root / "smoke-runs")},
    "input": {"backend": "pypsa", "path": str(source),
              "format": "netcdf", "source_namespace": "smoke"},
    "task": {"type": "planning", "profile": "planning_aggregation",
             "reduction_role": "candidate", "final_authority": "original"},
    "pipeline": {"materialization_family": "multi_voltage_quotient"},
    "simplification": {"operations": [{"id": "topology",
                    "type": "voltage_aware_topology", "enabled": True,
                    "mode": "keep_transformer_interface"}]},
    "partition": {"planner": "pypsa_kmeans",
                  "target": {"type": "count", "n_clusters": 7,
                             "per_layer": {"10": 1, "35": 3, "110": 3}},
                  "connectivity_policy": "reject"},
    "aggregation": {"backend": "pypsa", "aggregate_one_ports": ["Load"]},
}
(root / "smoke-three-voltage.yaml").write_text(
    yaml.safe_dump(config), encoding="utf-8")'''
        _run("create_multi_voltage_fixture", [str(clean_python), "-I", "-c",
                                              multi_smoke], summary,
             cwd=outside, env=env)
        smoke_config = output_dir / "smoke-three-voltage.yaml"
        _run("cli_plan", [cli, "plan", str(smoke_config)],
             summary, cwd=outside, env=env)
        _run("cli_build", [cli, "build", str(smoke_config)],
             summary, cwd=outside, env=env)
        manifest_dir = output_dir / "smoke-runs/smoke-three-voltage"
        report_output = _run("cli_report", [cli, "report", str(manifest_dir)],
                             summary, cwd=outside, env=env)
        report_payload = json.loads(report_output)
        if report_payload["manifest"]["status"] != (
                "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID"):
            raise PackagingGateError("Installed CLI did not build a valid multi-voltage quotient")
        summary["status"] = "PASSED"
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        (output_dir / "test_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return summary


def main() -> int:
    """Run the Gate from the command line and print its generated summary path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = run_gate(args.output_dir)
    except Exception as exc:
        print(f"Packaging Gate failed: {exc}", file=sys.stderr)
        print(args.output_dir.resolve() / "test_summary.json")
        return 1
    print(f"{summary['status']}: {args.output_dir.resolve() / 'test_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
