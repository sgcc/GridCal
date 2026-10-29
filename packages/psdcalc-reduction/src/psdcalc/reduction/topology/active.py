"""Shared static branch-activity interpretation for topology algorithms."""

import pandas as pd

from psdcalc.reduction.exceptions import BackendCapabilityError


def branch_is_active(row: pd.Series) -> bool:
    """Return a branch's static active flag, rejecting an unknown state."""
    value = row.get("active", True)
    if pd.isna(value):
        raise BackendCapabilityError("Branch static active state is missing")
    return bool(value)
