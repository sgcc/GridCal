"""Immutable partition-plan result contract."""

from dataclasses import dataclass
from typing import Mapping

from psdcalc.reduction.core.ids import StableId


@dataclass(frozen=True)
class PartitionPlan:
    """Source-ID partition and transient PyPSA bus names."""

    stable_busmap: Mapping[StableId, StableId]
    backend_busmap: Mapping[str, str]
    requested_cluster_count: int
    actual_cluster_count: int
    connectivity_policy: str
