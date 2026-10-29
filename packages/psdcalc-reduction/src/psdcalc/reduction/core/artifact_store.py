"""Atomic, run-scoped artifact storage and failure evidence."""

from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from psdcalc.reduction.core.manifests import RunManifest, utc_now
from psdcalc.reduction.exceptions import ArtifactError


_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SUBDIRS = ("input", "levels", "maps", "records", "validation", "metrics", "logs")


class ArtifactStore:
    """Own one new run directory and write artifacts atomically."""

    def __init__(self, root: str | Path, run_id: str) -> None:
        if not _RUN_ID.fullmatch(run_id):
            raise ArtifactError(f"Invalid run ID: {run_id!r}")
        self.root = Path(root).resolve()
        self.run_dir = self.root / run_id
        try:
            self.run_dir.mkdir(parents=True, exist_ok=False)
            for name in _SUBDIRS:
                (self.run_dir / name).mkdir()
        except OSError as exc:
            raise ArtifactError(f"Cannot create run directory {self.run_dir}: {exc}") from exc
        self._manifest = RunManifest(run_id=run_id)
        self._save_manifest()

    def path(self, relative: str) -> Path:
        """Resolve a run-relative path while rejecting escape attempts."""
        target = (self.run_dir / relative).resolve()
        if not target.is_relative_to(self.run_dir):
            raise ArtifactError(f"Artifact path escapes run directory: {relative}")
        return target

    @property
    def manifest(self) -> RunManifest:
        """Return the latest persisted manifest value."""
        return self._manifest

    def write_json(self, relative: str, value: Any) -> Path:
        """Atomically write a JSON artifact under this run directory."""
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        return self.write_text(relative, payload)

    def write_text(self, relative: str, value: str) -> Path:
        """Atomically write a UTF-8 text artifact under this run directory."""
        target = self.path(relative)
        if not target.parent.is_dir():
            raise ArtifactError(f"Artifact parent does not exist: {target.parent}")
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                             prefix=f".{target.name}.", delete=False) as stream:
                temporary = stream.name
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        except OSError as exc:
            raise ArtifactError(f"Cannot write {target}: {exc}") from exc
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
        return target

    def _save_manifest(self) -> None:
        """Persist the current manifest atomically."""
        self.write_json("run_manifest.json", self._manifest.model_dump(mode="json"))

    def record_artifact(self, name: str, relative: str) -> None:
        """Attach an existing artifact path to the manifest."""
        if not self.path(relative).exists():
            raise ArtifactError(f"Artifact does not exist: {relative}")
        artifacts = {**self._manifest.artifacts, name: relative}
        self._manifest = self._manifest.model_copy(update={"artifacts": artifacts,
                                                    "updated_at": utc_now()})
        self._save_manifest()

    def set_metadata(self, *, input_hash: str | None = None,
                     config_hash: str | None = None,
                     topology_signature: str | None = None,
                     output_hash: str | None = None,
                     seed: int | None = None,
                     software_versions: dict[str, str] | None = None) -> None:
        """Persist reproducibility fields once their evidence is available."""
        changes: dict[str, object] = {"updated_at": utc_now()}
        for key, value in (("input_hash", input_hash),
                           ("config_hash", config_hash),
                           ("topology_signature", topology_signature),
                           ("output_hash", output_hash), ("seed", seed),
                           ("software_versions", software_versions)):
            if value is not None:
                changes[key] = value
        self._manifest = self._manifest.model_copy(update=changes)
        self._save_manifest()

    def fail(self, stage: str, error: BaseException) -> None:
        """Persist failure evidence and mark the run failed."""
        evidence = {"stage": stage, "error_type": type(error).__name__,
                    "message": str(error), "timestamp": utc_now()}
        self.write_json("validation/failure.json", evidence)
        self._manifest = self._manifest.model_copy(update={
            "status": "FAILED", "failure": evidence, "updated_at": utc_now(),
        })
        self._save_manifest()

    def set_structural_result(self, valid: bool,
                              operation_status: str | None = None) -> None:
        """Persist the structural gate outcome without physical acceptance."""
        if operation_status not in {None, "STRUCTURALLY_SIMPLIFIED",
                                    "PLANNING_APPROXIMATION"}:
            raise ArtifactError(f"Invalid operation status: {operation_status}")
        self._manifest = self._manifest.model_copy(update={
            "status": "STRUCTURALLY_VALID" if valid else "STRUCTURALLY_INVALID",
            "operation_status": operation_status,
            "updated_at": utc_now(),
        })
        self._save_manifest()

    def set_multi_voltage_result(self, valid: bool) -> None:
        """Persist only the final post-reload multi-voltage structural state."""
        self._manifest = self._manifest.model_copy(update={
            "status": ("MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID" if valid else
                       "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID"),
            "operation_status": None,
            "updated_at": utc_now(),
        })
        self._save_manifest()

    @contextmanager
    def capture(self, stage: str) -> Iterator[None]:
        """Record any stage failure and re-raise the original exception."""
        try:
            yield
        except BaseException as exc:
            self.fail(stage, exc)
            raise
