"""Run-manifest data contract without physical acceptance states."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> str:
    """Return a timezone-qualified UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


class RunManifest(BaseModel):
    """Persistent state of one reduction run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    status: Literal[
        "RUNNING", "FAILED", "STRUCTURALLY_VALID", "STRUCTURALLY_INVALID",
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID",
        "MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID",
    ] = "RUNNING"
    operation_status: Literal["STRUCTURALLY_SIMPLIFIED", "PLANNING_APPROXIMATION"] | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    input_hash: str | None = None
    topology_signature: str | None = None
    output_hash: str | None = None
    seed: int | None = None
    software_versions: dict[str, str] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    failure: dict[str, str] | None = None
