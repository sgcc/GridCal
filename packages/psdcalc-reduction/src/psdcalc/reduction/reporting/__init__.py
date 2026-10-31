"""Structural reporting for reduction pipelines."""

from .opportunity import CompressionOpportunityReport, build_compression_opportunity_report
from .comparison import CompressionComparisonReport, VoltagePolicyReportSummary
from .voltage_matrix import CrossScenarioCompressionReport

__all__ = [
    "CompressionOpportunityReport", "build_compression_opportunity_report",
    "CompressionComparisonReport", "VoltagePolicyReportSummary",
    "CrossScenarioCompressionReport",
]
