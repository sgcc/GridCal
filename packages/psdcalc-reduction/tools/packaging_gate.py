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
    }
    expected_source = {f"src/{name}" for name in modules | resources} | {
        "pyproject.toml", "README.md", "LICENSE.md", "MANIFEST.in",
        "schema/reduction.schema.json", "examples/reduction.first-batch.yaml",
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
        "gate": "PR-013 Packaging Gate", "status": "FAILED", "steps": [],
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
        pytest_output = _run("package_tests", [sys.executable, "-m", "pytest", "-q",
                                               "tests"], summary, cwd=PACKAGE, env=env)
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
assert json.loads(schema) == ReductionConfig.model_json_schema()
assert "schema_version" in example
(root / "installed-example.yaml").write_text(example, encoding="utf-8")
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
        exported = output_dir / "exported-schema.json"
        _run("cli_dump_schema", [cli, "dump-schema", str(exported)],
             summary, cwd=outside, env=env)
        if json.loads(exported.read_text(encoding="utf-8")) != json.loads(
            (PACKAGE / "schema/reduction.schema.json").read_text(encoding="utf-8")
        ):
            raise PackagingGateError("Installed CLI exported a different schema")
        manifest_dir = output_dir / "smoke-run"
        manifest_dir.mkdir(exist_ok=True)
        (manifest_dir / "run_manifest.json").write_text(
            json.dumps({"status": "STRUCTURALLY_VALID"}), encoding="utf-8"
        )
        _run("cli_report", [cli, "report", str(manifest_dir)],
             summary, cwd=outside, env=env)
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
