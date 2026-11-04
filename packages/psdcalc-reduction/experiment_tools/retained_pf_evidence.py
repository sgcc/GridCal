"""Strict JSON evidence adapter for retained-fidelity experiment drivers.

This module is execution tooling, not part of the installed package. It
serializes the existing public PyPSA PF result contract without invoking PF or
changing solver, fidelity, or controller semantics.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Mapping

from psdcalc.reduction.core.ids import StableId
from psdcalc.reduction.fidelity.pypsa_executor import (
    PowerFlowExecutionError,
    PowerFlowExecutionResult,
    PowerFlowExecutionStatus,
    PowerFlowSubnetworkStatus,
)


POWER_FLOW_RESULT_SCHEMA = "retained-pf-execution-result/v1"
_RESULT_FIELDS = {
    "schema",
    "status",
    "converged",
    "snapshot",
    "subnetwork_status",
    "solved_values",
    "solver_metadata",
    "failure_reason",
}
_VALUE_FIELDS = (
    "bus_vm_pu",
    "bus_va_deg",
    "bus_p_mw",
    "bus_q_mvar",
    "branch_p0",
    "branch_q0",
    "branch_p1",
    "branch_q1",
)


class RetainedPfEvidenceError(ValueError):
    """Raised when driver evidence is incomplete, corrupt, or non-canonical."""


def serialize_power_flow_result(result: PowerFlowExecutionResult) -> dict[str, object]:
    """Return a digest-bound JSON payload for one authoritative PF result."""
    if not isinstance(result, PowerFlowExecutionResult):
        raise RetainedPfEvidenceError("PF result must be PowerFlowExecutionResult")
    rows = []
    for item in result.subnetwork_status:
        if not isinstance(item, PowerFlowSubnetworkStatus):
            raise RetainedPfEvidenceError("subnetwork status row has an unsupported type")
        _validate_subnetwork_values(
            item.subnetwork_ordinal, item.converged, item.iterations, item.error,
        )
        rows.append({
            "subnetwork_ordinal": item.subnetwork_ordinal,
            "converged": item.converged,
            "iterations": item.iterations,
            "error": item.error,
        })
    solved_values = {
        name: _encode_values(getattr(result, name), name)
        for name in _VALUE_FIELDS
    }
    if result.failure_reason is not None and not isinstance(result.failure_reason, str):
        raise RetainedPfEvidenceError("failure_reason must be a string or None")
    body: dict[str, object] = {
        "schema": POWER_FLOW_RESULT_SCHEMA,
        "status": result.status.value,
        "converged": result.converged,
        "snapshot": None if result.snapshot is None else str(result.snapshot),
        "subnetwork_status": rows,
        "solved_values": solved_values,
        "solver_metadata": _validated_json_mapping(result.solver_metadata),
        "failure_reason": result.failure_reason,
    }
    digest = _digest(body)
    return {**body, "result_sha256": digest}


def deserialize_power_flow_result(payload: object) -> PowerFlowExecutionResult:
    """Validate a complete payload and restore the typed PF result fail-closed."""
    if not isinstance(payload, Mapping):
        raise RetainedPfEvidenceError("PF result evidence must be a mapping")
    expected = _RESULT_FIELDS | {"result_sha256"}
    if set(payload) != expected or payload.get("schema") != POWER_FLOW_RESULT_SCHEMA:
        raise RetainedPfEvidenceError("PF result evidence schema or fields are invalid")

    _require_digest(payload["result_sha256"])
    body = {key: payload[key] for key in _RESULT_FIELDS}
    status_value = payload["status"]
    try:
        status = PowerFlowExecutionStatus(status_value)
    except (TypeError, ValueError) as exc:
        raise RetainedPfEvidenceError("PF result status is invalid") from exc
    converged = payload["converged"]
    if not isinstance(converged, bool):
        raise RetainedPfEvidenceError("PF result converged flag must be bool")
    snapshot = payload["snapshot"]
    if snapshot is not None and not isinstance(snapshot, str):
        raise RetainedPfEvidenceError("PF result snapshot must be a string or None")

    raw_rows = payload["subnetwork_status"]
    if not isinstance(raw_rows, list):
        raise RetainedPfEvidenceError("subnetwork_status must be a list")
    subnetwork_rows = tuple(_decode_subnetwork_status(row) for row in raw_rows)
    ordinals = tuple(row.subnetwork_ordinal for row in subnetwork_rows)
    if ordinals != tuple(sorted(set(ordinals))):
        raise RetainedPfEvidenceError("subnetwork ordinals must be unique and sorted")

    raw_values = payload["solved_values"]
    if not isinstance(raw_values, Mapping) or set(raw_values) != set(_VALUE_FIELDS):
        raise RetainedPfEvidenceError("solved_values fields are incomplete or unsupported")
    solved_values = {
        name: _decode_values(raw_values[name], name)
        for name in _VALUE_FIELDS
    }
    metadata = _validated_json_mapping(payload["solver_metadata"])
    failure_reason = payload["failure_reason"]
    if failure_reason is not None and not isinstance(failure_reason, str):
        raise RetainedPfEvidenceError("failure_reason must be a string or None")

    if _digest(body) != payload["result_sha256"]:
        raise RetainedPfEvidenceError("PF result evidence digest differs")
    try:
        return PowerFlowExecutionResult(
            status=status,
            converged=converged,
            snapshot=snapshot,
            subnetwork_status=subnetwork_rows,
            **solved_values,
            solver_metadata=metadata,
            failure_reason=failure_reason,
        )
    except (PowerFlowExecutionError, TypeError, ValueError) as exc:
        raise RetainedPfEvidenceError("PF result evidence violates the result contract") from exc


def write_power_flow_result(path: Path, result: PowerFlowExecutionResult) -> str:
    """Atomically persist one result payload and return its semantic digest."""
    payload = serialize_power_flow_result(result)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return str(payload["result_sha256"])


def read_power_flow_result(path: Path) -> PowerFlowExecutionResult:
    """Reload JSON evidence, rejecting non-standard JSON constants and bad digests."""
    try:
        payload = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RetainedPfEvidenceError("PF result JSON could not be read safely") from exc
    return deserialize_power_flow_result(payload)


def write_power_flow_result(path: Path, result: PowerFlowExecutionResult) -> str:
    """Atomically persist one result payload and return its semantic digest."""
    payload = serialize_power_flow_result(result)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return str(payload["result_sha256"])


def read_power_flow_result(path: Path) -> PowerFlowExecutionResult:
    """Reload JSON evidence, rejecting non-standard JSON constants and bad digests."""
    try:
        payload = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)),
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RetainedPfEvidenceError("PF result JSON could not be read safely") from exc
    return deserialize_power_flow_result(payload)


def _encode_values(values: Mapping[StableId, float], field_name: str) -> dict[str, float]:
    if not isinstance(values, Mapping):
        raise RetainedPfEvidenceError(f"{field_name} must be a StableId mapping")
    encoded: dict[str, float] = {}
    for stable_id, value in values.items():
        if not isinstance(stable_id, StableId):
            raise RetainedPfEvidenceError(f"{field_name} keys must be StableId values")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise RetainedPfEvidenceError(f"{field_name} values must be finite numbers")
        encoded[stable_id.encode()] = float(value)
    return dict(sorted(encoded.items()))


def _decode_values(values: object, field_name: str) -> dict[StableId, float]:
    if not isinstance(values, Mapping):
        raise RetainedPfEvidenceError(f"{field_name} must be a mapping")
    decoded: dict[StableId, float] = {}
    for key, value in values.items():
        if not isinstance(key, str):
            raise RetainedPfEvidenceError(f"{field_name} StableId keys must be strings")
        try:
            stable_id = StableId.decode(key)
        except (TypeError, ValueError) as exc:
            raise RetainedPfEvidenceError(f"{field_name} contains an invalid StableId") from exc
        if stable_id.encode() != key or stable_id in decoded:
            raise RetainedPfEvidenceError(f"{field_name} StableId keys are not canonical")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise RetainedPfEvidenceError(f"{field_name} values must be finite numbers")
        decoded[stable_id] = float(value)
    if tuple(values) != tuple(sorted(values)):
        raise RetainedPfEvidenceError(f"{field_name} StableId keys must be sorted")
    return decoded


def _decode_subnetwork_status(payload: object) -> PowerFlowSubnetworkStatus:
    required = {"subnetwork_ordinal", "converged", "iterations", "error"}
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise RetainedPfEvidenceError("subnetwork status fields do not match the result contract")
    ordinal = payload["subnetwork_ordinal"]
    converged = payload["converged"]
    iterations = payload["iterations"]
    error = payload["error"]
    _validate_subnetwork_values(ordinal, converged, iterations, error)
    try:
        return PowerFlowSubnetworkStatus(ordinal, converged, iterations, error)
    except (PowerFlowExecutionError, TypeError, ValueError) as exc:
        raise RetainedPfEvidenceError("subnetwork status violates the result contract") from exc


def _validate_subnetwork_values(
    ordinal: object, converged: object, iterations: object, error: object,
) -> None:
    """Validate the authoritative subnetwork field names and scalar types."""
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise RetainedPfEvidenceError("subnetwork_ordinal must be a nonnegative integer")
    if not isinstance(converged, bool):
        raise RetainedPfEvidenceError("subnetwork converged must be bool")
    if iterations is not None and (
        isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 0
    ):
        raise RetainedPfEvidenceError("subnetwork iterations must be a nonnegative integer or None")
    if error is not None and (
        isinstance(error, bool) or not isinstance(error, (int, float))
        or not math.isfinite(float(error)) or error < 0
    ):
        raise RetainedPfEvidenceError("subnetwork error must be finite, nonnegative, or None")


def _validated_json_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise RetainedPfEvidenceError("solver_metadata must be a string-keyed mapping")
    result = dict(value)
    try:
        json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RetainedPfEvidenceError("solver_metadata is not finite JSON data") from exc
    return result


def _digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_digest(value: object) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise RetainedPfEvidenceError("result_sha256 must be a lowercase SHA-256 digest")
