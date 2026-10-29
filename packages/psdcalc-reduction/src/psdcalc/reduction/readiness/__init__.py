"""Readiness gates for external source-model adapters."""

from .parallel_corridor import (
    ParallelCorridorAudit,
    ParallelCorridorAuditPaths,
    ParallelCorridorAuditResult,
)

__all__ = ["ParallelCorridorAudit", "ParallelCorridorAuditPaths",
           "ParallelCorridorAuditResult"]
