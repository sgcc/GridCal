"""JSON Schema export for the strict first-batch configuration."""

import json
from pathlib import Path

from psdcalc.reduction.config.models import ReductionConfig


def export_schema(path: str | Path) -> None:
    """Write the Pydantic v2 JSON Schema to an existing parent directory."""
    target = Path(path)
    target.write_text(
        json.dumps(ReductionConfig.model_json_schema(), ensure_ascii=False, indent=2,
                   sort_keys=True) + "\n",
        encoding="utf-8",
    )
