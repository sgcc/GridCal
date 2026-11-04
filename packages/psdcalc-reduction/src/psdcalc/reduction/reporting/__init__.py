"""Structural reporting for reduction pipelines."""

from .opportunity import CompressionOpportunityReport, build_compression_opportunity_report
from .comparison import CompressionComparisonReport, VoltagePolicyReportSummary
from .voltage_matrix import CrossScenarioCompressionReport
from .comprehensive import (
    ComprehensiveReductionReport,
    ComprehensiveRunReport,
    EvidenceReference,
    EvidenceStatus,
    EvidenceValue,
    FidelityObservation,
    MetricGroup,
    MetricOutcome,
    ReportProvenance,
    ErrorStatistics,
    RetainedGroupComparison,
    CrossVoltageInterfaceReport,
    RetainedVoltageLayerComparison,
    RetainedNetworkFidelityReport,
    ValueStatistics,
)
from .retained_fidelity import RetainedFidelityError, compare_retained_network_fidelity
from .comprehensive_builder import (
    ComprehensiveReportError,
    FidelityGroupInput,
    build_comprehensive_run,
)
from .comprehensive_export import (
    ComprehensiveExportError,
    export_report_bundle,
    render_markdown,
    render_metrics_csv,
    render_policy_comparison_csv,
    render_retained_fidelity_csv,
    render_voltage_layers_csv,
    validate_public_export_text,
)
from .comprehensive_history import (
    HistoricalEvidenceBundle,
    HistoricalEvidenceError,
    HistoricalObservation,
    HistoricalScenarioEvidence,
    export_historical_evidence_bundle,
    historical_evidence_csv,
    historical_evidence_json,
    historical_evidence_markdown,
    historical_policy_comparison_csv,
    load_historical_evidence,
)

__all__ = [
    "CompressionOpportunityReport", "build_compression_opportunity_report",
    "CompressionComparisonReport", "VoltagePolicyReportSummary",
    "CrossScenarioCompressionReport",
    "ComprehensiveReductionReport", "ComprehensiveRunReport",
    "EvidenceReference", "EvidenceStatus", "EvidenceValue",
    "FidelityObservation", "MetricGroup", "MetricOutcome",
    "ReportProvenance", "ErrorStatistics", "RetainedGroupComparison",
    "CrossVoltageInterfaceReport", "RetainedVoltageLayerComparison",
    "RetainedNetworkFidelityReport", "RetainedFidelityError",
    "ValueStatistics",
    "compare_retained_network_fidelity",
    "ComprehensiveReportError", "FidelityGroupInput", "build_comprehensive_run",
    "ComprehensiveExportError", "export_report_bundle", "render_markdown",
    "render_metrics_csv", "render_policy_comparison_csv", "render_voltage_layers_csv",
    "render_retained_fidelity_csv",
    "validate_public_export_text", "HistoricalEvidenceError",
    "HistoricalEvidenceBundle", "HistoricalObservation", "HistoricalScenarioEvidence",
    "export_historical_evidence_bundle", "historical_evidence_csv",
    "historical_evidence_json", "historical_evidence_markdown",
    "historical_policy_comparison_csv", "load_historical_evidence",
]
