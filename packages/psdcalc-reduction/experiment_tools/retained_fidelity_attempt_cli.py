"""Private-input launcher for a fresh retained-fidelity experiment attempt.

The CLI accepts frozen S1, sidecar, ledger and a configuration template. No
historical candidate or earlier run directory is accepted as an input.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import sys
from typing import Mapping, Sequence

import pypsa
import yaml

from psdcalc.reduction import __version__
from experiment_tools.retained_fidelity_driver import (
    FrozenAttemptBindings, FrozenPolicyRun, RetainedAttemptError,
    run_frozen_policies,
)
from psdcalc.reduction.adaptive.candidates import _semantic_config_digest, _sidecar_digest
from psdcalc.reduction.backends.pypsa.adapter import PypsaAdapter
from psdcalc.reduction.config.models import ReductionConfig
from psdcalc.reduction.core.ids import IdentitySidecar
from psdcalc.reduction.fidelity.contracts import (
    PowerFlowMetricPolicy, PowerFlowOperatingPointContract,
)
from psdcalc.reduction.fidelity.metrics import metric_policy_digest, operating_point_digest
from psdcalc.reduction.fidelity.persistence import FrozenThresholdSet, canonical_model_digest
from psdcalc.reduction.protection.effective import source_ledger_digest
from psdcalc.reduction.topology.parallel_provenance import ParallelSourceLedger


POLICY_ORDER = ("BACKBONE_500", "BACKBONE_220", "BACKBONE_100")


def _sha256(path: Path) -> str:
    """Read one input's exact bytes for a frozen file identity check."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require_equal(actual: object, expected: object, label: str) -> None:
    """Reject any frozen input or contract mismatch before a new run directory."""
    if actual != expected:
        raise RetainedAttemptError(f"CANDIDATE_PROVENANCE_INVALID: {label}")


def prepare_policy_runs(
    freeze: Mapping[str, object], template: ReductionConfig, *,
    source_path: Path, output_root: Path,
) -> tuple[FrozenPolicyRun, ...]:
    """Resolve the frozen ordered cutoffs from one config, without materializing."""
    rows = freeze.get("policies")
    if not isinstance(rows, list) or not rows:
        raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: policy freeze")
    runs: list[FrozenPolicyRun] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: policy row")
        cutoff = float(row["requested_cutoff_kv"])
        config = template.model_copy(update={
            "input": template.input.model_copy(update={"path": str(Path(source_path).resolve())}),
            "run": template.run.model_copy(update={"output_dir": str(Path(output_root).resolve())}),
            "voltage_policy": template.voltage_policy.model_copy(update={
                "enabled": True, "mode": "freeze_at_or_above", "cutoff_kv": cutoff,
            }),
        })
        _require_equal(_semantic_config_digest(config), row["semantic_config_digest"],
                       "semantic config")
        runs.append(FrozenPolicyRun(
            str(row["policy_id"]), config,
            str(row["resolved_policy_fingerprint"]),
            str(row["semantic_config_digest"]),
            str(row["effective_protection_digest"]),
        ))
    return tuple(runs)


def _verify_parser_manifest(freeze: Mapping[str, object], parser_src: Path) -> None:
    """Check all 22 frozen parser files before using the frozen S1 projection."""
    provenance = freeze["parser_provenance"]
    rows = provenance["manifest"]
    if len(rows) != 22:
        raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: parser manifest count")
    for row in rows:
        path = (Path(parser_src) / row["path"]).resolve()
        if not path.is_relative_to(Path(parser_src).resolve()):
            raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: parser path")
        _require_equal(_sha256(path), row["sha256"], "parser manifest")


def _contract_from_freeze(
    freeze: Mapping[str, object], source: pypsa.Network,
) -> PowerFlowOperatingPointContract:
    """Rebuild the unchanged PR-022 one-snapshot PF call contract."""
    pf = freeze["pf_executor"]
    op = freeze["operating_point"]
    contract = PowerFlowOperatingPointContract(
        snapshots=tuple(source.snapshots), solver=pf["solver"],
        tolerance=pf["tolerance"], max_iterations=None,
        distribute_slack=pf["distribute_slack"], slack_weights=None,
        use_seed=pf["use_seed"], control_policy="preserve_source_controls",
        tap_policy="fixed_at_source_schedule",
        phase_shift_policy="fixed_at_source_schedule",
        topology_digest=op["source_topology_digest"],
        device_schedule_digest=op["source_device_schedule_digest"],
    )
    _require_equal(operating_point_digest(contract), op["digest"], "operating point")
    return contract


def execute_from_frozen_inputs(
    *, freeze_path: Path, lease_path: Path, source_dat: Path,
    source_ref: Path, source_s1: Path, source_sidecar: Path,
    source_ledger_dir: Path, parser_src: Path, config_template: Path,
    output_root: Path,
) -> None:
    """Validate all input leases, then build and solve three fresh policy runs."""
    freeze = json.loads(Path(freeze_path).read_text(encoding="utf-8"))
    lease = json.loads(Path(lease_path).read_text(encoding="utf-8"))
    driver_dir = Path(__file__).resolve().parent
    repo_root = driver_dir.parents[2]
    attempt_name = lease.get("attempt_id")
    if lease.get("status") != "READY_NOT_EXECUTED" or attempt_name not in {
            "BEIJING_RETAINED_NETWORK_FIDELITY_V1_ATTEMPT_003",
            "BEIJING_RETAINED_NETWORK_FIDELITY_V1_ATTEMPT_004"}:
        raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: attempt lease")
    attempt_number = str(attempt_name).rsplit("_", 1)[-1]
    if attempt_number == "003":
        _require_equal(_sha256(Path(freeze_path)), lease["original_freeze_sha256"],
                       "original freeze bytes")
    else:
        if freeze.get("freeze_revision") != 2:
            raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: freeze revision")
        _require_equal(lease.get("freeze_revision"), 2, "lease freeze revision")
        _require_equal(_sha256(Path(freeze_path)), lease["freeze_revision_sha256"],
                       "freeze revision bytes")
        _require_equal(freeze["original_freeze_sha256"],
                       lease["original_freeze_sha256"], "original freeze reference")
        _require_equal(_sha256(repo_root /
                       "docs/validation/beijing-retained-fidelity-v1-freeze.json"),
                       freeze["original_freeze_sha256"], "original freeze bytes")
        preflight = lease.get("synthetic_full_e2e_preflight")
        frozen_preflight = freeze.get("synthetic_full_e2e_preflight")
        if not isinstance(preflight, dict) or not isinstance(frozen_preflight, dict):
            raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: synthetic preflight binding")
        _require_equal(preflight.get("status"), "PASS", "synthetic preflight status")
        _require_equal(frozen_preflight.get("status"), "PASS", "frozen preflight status")
        _require_equal(preflight.get("sha256"), frozen_preflight.get("sha256"),
                       "synthetic preflight digest binding")
        preflight_path = repo_root / str(preflight.get("path", ""))
        if not preflight_path.is_file():
            raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: synthetic preflight file")
        _require_equal(_sha256(preflight_path), preflight["sha256"],
                       "synthetic preflight bytes")
        try:
            preflight_payload = json.loads(preflight_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RetainedAttemptError(
                "CANDIDATE_PROVENANCE_INVALID: synthetic preflight content") from exc
        if not isinstance(preflight_payload, dict):
            raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: synthetic preflight content")
        _require_equal(preflight_payload.get("schema"),
                       "retained-fidelity-full-e2e-preflight/v1",
                       "synthetic preflight schema")
        _require_equal(preflight_payload.get("status"), "PASS",
                       "synthetic preflight result")
    _require_equal(tuple(lease["authorized_policies"]), POLICY_ORDER, "lease policy order")
    driver_files = lease["driver_files_sha256"]
    required_driver_files = ["retained_fidelity_driver.py",
                             "retained_fidelity_attempt_cli.py"]
    if attempt_number == "004":
        required_driver_files.append("retained_fidelity_outputs.py")
    if set(driver_files) != set(required_driver_files):
        raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: driver file set")
    for name in required_driver_files:
        _require_equal(_sha256(driver_dir / name), driver_files[name], "driver bytes")
    _require_equal(_sha256(config_template), lease["config_template_sha256"],
                   "frozen config template bytes")
    _require_equal(_sha256(source_s1), lease["source_s1_sha256"], "frozen S1 bytes")
    _require_equal(_sha256(source_sidecar), lease["source_sidecar_file_sha256"],
                   "frozen sidecar bytes")
    runtime = lease["runtime_environment"]
    _require_equal(sys.version.split()[0], runtime["python"], "Python version")
    _require_equal(metadata.version("pypsa"), runtime["pypsa"], "PyPSA version")
    _require_equal(__version__, runtime["package_version"], "package version")
    if attempt_number == "004":
        locked_versions = runtime["locked_versions"]
        if (not isinstance(locked_versions, dict) or
                set(locked_versions) != {
                    "pydantic", "numpy", "pandas", "networkx", "scipy",
                    "highspy", "polars"}):
            raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: runtime version set")
        for name, version in locked_versions.items():
            _require_equal(metadata.version(name), version, f"{name} version")
    _require_equal(_sha256(repo_root / "packages/psdcalc-reduction/uv.lock"),
                   runtime["uv_lock_sha256"], "uv lock bytes")
    implementation_commit = lease["frozen_implementation_commit"]
    tree = subprocess.run(
        ["git", "rev-parse", f"{implementation_commit}^{{tree}}"], cwd=repo_root,
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    _require_equal(tree, lease["frozen_implementation_tree"],
                   "frozen implementation tree")
    source_diff = subprocess.run(
        ["git", "diff", "--quiet", implementation_commit, "--",
         "packages/psdcalc-reduction/src/psdcalc/reduction"], cwd=repo_root,
        check=False, capture_output=True, text=True,
    )
    if source_diff.returncode != 0:
        raise RetainedAttemptError("FROZEN_IMPLEMENTATION_CHANGE_REQUIRED")
    if Path(output_root).exists():
        raise RetainedAttemptError("CANDIDATE_PROVENANCE_INVALID: output root already exists")
    _verify_parser_manifest(freeze, parser_src)
    identity = freeze["input_identity"]
    _require_equal(_sha256(source_dat), identity["source_dat_sha256"], "source DAT")
    _require_equal(_sha256(source_ref), identity["reference_input_sha256"], "source REF")
    source = PypsaAdapter().load(source_s1, "netcdf")
    sidecar = IdentitySidecar.from_rows(json.loads(Path(source_sidecar).read_text(
        encoding="utf-8")))
    ledger = ParallelSourceLedger.load_parquet(source_ledger_dir)
    _require_equal(canonical_model_digest(source, sidecar), identity["source_model_digest"],
                   "frozen S1")
    _require_equal(_sidecar_digest(sidecar), identity["source_sidecar_digest"],
                   "frozen sidecar")
    _require_equal(source_ledger_digest(ledger), identity["source_ledger_digest"],
                   "frozen source ledger")
    template = ReductionConfig.model_validate(yaml.safe_load(
        Path(config_template).read_text(encoding="utf-8")))
    policies = prepare_policy_runs(
        freeze, template, source_path=source_s1, output_root=output_root)
    _require_equal(tuple(policy.policy_id for policy in policies), POLICY_ORDER,
                   "frozen policy order")
    contract = _contract_from_freeze(freeze, source)
    threshold_set = FrozenThresholdSet.load()
    _require_equal(threshold_set.sha256,
                   freeze["fidelity_contract"]["threshold_artifact_sha256"],
                   "frozen threshold SHA")
    metric_policy = PowerFlowMetricPolicy(**threshold_set.payload["metric_policy"])
    _require_equal(metric_policy_digest(metric_policy),
                   freeze["fidelity_contract"]["metric_policy_digest"],
                   "metric policy")
    bindings = FrozenAttemptBindings(
        experiment_id=freeze["experiment"]["experiment_id"],
        attempt_id=f"ATTEMPT_{attempt_number}",
        source_model_digest=identity["source_model_digest"],
        source_sidecar_digest=identity["source_sidecar_digest"],
        source_ledger_digest=identity["source_ledger_digest"],
        required_policy_order=POLICY_ORDER,
        operating_point_digest=freeze["operating_point"]["digest"],
        threshold_set_sha256=threshold_set.sha256,
        require_pf=True,
    )
    results = run_frozen_policies(
        source_network=source, source_path=source_s1, source_sidecar=sidecar,
        source_ledger=ledger, policies=policies, bindings=bindings,
        output_root=output_root, pf_contract=contract,
        metric_policy=metric_policy, threshold_set=threshold_set,
    )
    summary = {
        "schema": "retained-fidelity-attempt-summary/v1",
        "experiment_id": bindings.experiment_id,
        "attempt_id": bindings.attempt_id,
        "policy_order": list(POLICY_ORDER),
        "results": [{
            "policy_id": row.policy_id,
            "candidate_identity": row.candidate_identity,
            "source_pf_status": row.source_pf_status,
            "candidate_pf_status": row.candidate_pf_status,
            "fidelity_status": row.fidelity_status,
        } for row in results],
    }
    with (Path(output_root) / "attempt-summary.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, sort_keys=True, indent=2)
        stream.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Parse explicit frozen/private paths; this function never searches old runs."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("freeze", "lease", "source-dat", "source-ref", "source-s1",
                 "source-sidecar", "source-ledger-dir", "parser-src",
                 "config-template", "output-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    execute_from_frozen_inputs(
        freeze_path=args.freeze, lease_path=args.lease,
        source_dat=args.source_dat, source_ref=args.source_ref,
        source_s1=args.source_s1, source_sidecar=args.source_sidecar,
        source_ledger_dir=args.source_ledger_dir, parser_src=args.parser_src,
        config_template=args.config_template, output_root=args.output_root,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
