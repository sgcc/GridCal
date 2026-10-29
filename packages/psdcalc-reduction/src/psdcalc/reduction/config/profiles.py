"""Built-in first-batch profile defaults."""

from copy import deepcopy
from typing import Any


_PROFILES: dict[str, dict[str, Any]] = {
    "planning_aggregation": {"pipeline": {"materialization_family": "aggregation"}},
    "ac_pf_strict": {"pipeline": {"materialization_family": "aggregation"}},
    "var_control_strict": {"pipeline": {"materialization_family": "aggregation"}},
    "ac_opf_warmstart": {"pipeline": {"materialization_family": "aggregation"}},
    "dc_security_screening": {"pipeline": {"materialization_family": "aggregation"}},
    "custom": {},
}


def profile_defaults(name: str) -> dict[str, Any]:
    """Return an independent copy of a named built-in profile."""
    return deepcopy(_PROFILES[name])
