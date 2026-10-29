"""Immutable operation records for replayable reduction steps."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from psdcalc.reduction.core.manifests import utc_now


class ReductionRecord(BaseModel):
    """One auditable, immutable reduction operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    operation_type: Literal["simplification", "aggregation", "equivalencing"]
    parent_grid_hash: str
    child_grid_hash: str
    input_signature: str
    output_signature: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    removed_objects: tuple[str, ...] = ()
    created_objects: tuple[str, ...] = ()
    mapping_files: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    software_versions: dict[str, str] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=utc_now)
