# Comprehensive compression report implementation plan

> **For agentic workers:** execute this plan inline in the current worktree. Preserve the two commit boundary requested by the user: report contracts first, aggregation/exporters second.

**Goal:** Add a reusable, provenance-bound comprehensive reduction report with structural and optional AC fidelity evidence, deterministic JSON/Markdown/CSV exports, and historical evidence validation.

**Architecture:** Use frozen Pydantic v2 report models as the canonical versioned schema. A pure builder adapts PyPSA inventories, `MappingBundle`, existing opportunity/comparison reports, optional PF/Fidelity evidence, and policy results into that schema. Historical adapters normalize only explicitly supported committed summaries; JSON, Markdown, and CSV all render from the same validated report and never run PF or modify admission.

**Tech Stack:** Python 3.13, Pydantic v2, PyPSA public network tables, pandas/NetworkX already in the lock, pytest, JSON Schema, CSV, Markdown.

## Global Constraints

- Keep package version `0.8.0` and do not change dependency declarations or `uv.lock`.
- Do not run Beijing PF, adaptive controller, or Stage2D.
- Do not modify Stage2A/B/C evidence or PR-022 HARD/DIAGNOSTIC/admission semantics.
- Hash StableIds for public report identities; never export backend names, raw StableIds, private paths, raw cards, or device names.
- Missing evidence is `NOT_RECORDED`, `NOT_AVAILABLE`, or `NOT_APPLICABLE`, never inferred zero.
- Structural ceiling is a bound, not a global optimum; candidate and accepted compression remain separate.
- Commit A adds only schema/contracts and contract tests; Commit B adds aggregation, exporters, historical validation, and integration tests.

---

## File plan

| File | Responsibility |
| --- | --- |
| `packages/psdcalc-reduction/src/psdcalc/reduction/reporting/comprehensive.py` | Frozen report models, enums, strict validation, schema export, canonical JSON, digest binding |
| `packages/psdcalc-reduction/src/psdcalc/reduction/reporting/comprehensive_builder.py` | Pure adapters from source/target networks, mappings, opportunity/comparison, optional PF and runtime evidence |
| `packages/psdcalc-reduction/src/psdcalc/reduction/reporting/comprehensive_history.py` | Allowlisted, digest-bound adapters for PR-020/021/022 and Stage2C public summaries |
| `packages/psdcalc-reduction/src/psdcalc/reduction/reporting/comprehensive_export.py` | Deterministic Markdown and metrics/layers/policy CSV writers |
| `packages/psdcalc-reduction/src/psdcalc/reduction/resources/comprehensive-report.schema.json` | Generated package JSON Schema for `psdcalc-comprehensive-reduction-report/v1` |
| `packages/psdcalc-reduction/src/psdcalc/reduction/reporting/__init__.py` | Public exports |
| `packages/psdcalc-reduction/tests/test_comprehensive_report_contracts.py` | Required/status/value/digest/schema/privacy contract tests |
| `packages/psdcalc-reduction/tests/test_comprehensive_report_builder.py` | Synthetic voltage, topology, mappings, Transformer projection and PF group tests |
| `packages/psdcalc-reduction/tests/test_comprehensive_report_export.py` | JSON/Markdown/CSV equality, missing-PF output, and privacy tests |
| `packages/psdcalc-reduction/tests/test_comprehensive_report_history.py` | Historical schema/version/digest separation and no Stage2C backfill tests |
| `docs/validation/local-comprehensive-reduction-report-v1.json` | Anonymous regenerated report from committed public historical summaries |
| `docs/validation/local-comprehensive-reduction-report-v1.md` | Human-readable regenerated historical report |
| `docs/validation/local-comprehensive-reduction-report-v1-*.csv` | Metrics, voltage-layer, and policy comparison tables |

## Public interfaces

```python
def build_comprehensive_run(
    *,
    run_id: str,
    source_network: pypsa.Network,
    target_network: pypsa.Network | None = None,
    mappings: MappingBundle | None = None,
    opportunity: CompressionOpportunityReport | None = None,
    comparison: CompressionComparisonReport | None = None,
    structural: MultiVoltageStructuralResult | None = None,
    fidelity_groups: Sequence[FidelityGroupInput] = (),
    voltage_policies: Sequence[VoltageCutoffPolicy] = (),
    runtime: Sequence[RuntimeEvidence] = (),
    provenance: ReportProvenance,
) -> ComprehensiveRunReport: ...

def load_historical_reports(repository_root: Path) -> ComprehensiveReductionReport: ...

def export_comprehensive_report(
    report: ComprehensiveReductionReport, directory: Path
) -> Mapping[str, Path]: ...
```

All three APIs are read-only with respect to networks and source evidence. The builder computes exact nominal-voltage groups, mapping coverage and identity digests, but never derives per-layer PF metrics from whole-network aggregates.

## Task 1: Contract models and schema

**Files:** create `comprehensive.py`, `comprehensive-report.schema.json`, and `test_comprehensive_report_contracts.py`; modify `reporting/__init__.py`.

- [ ] Write tests for strict extra-field rejection, state/value consistency, finite numbers, bounded coverage, SHA-256 format, and deterministic schema/report digests.
- [ ] Run only the new contract tests and confirm they fail because the report models do not yet exist.
- [ ] Implement frozen `EvidenceStatus`, `MetricStatus`, `MetricGroup`, `EvidenceValue`, `ReportProvenance`, `NetworkInventory`, `OpportunitySection`, `StructuralComparison`, `VoltageLayerSummary`, `FidelityObservation`, `RuntimeEvidence`, `ComprehensiveRunReport`, and `ComprehensiveReductionReport` models.
- [ ] Generate the JSON Schema from the model; compare it byte-for-byte with the package resource in a test.
- [ ] Run contract tests and existing package-resource tests; commit as `feat(reduction): add comprehensive compression report contracts`.

## Task 2: Synthetic live-run aggregation

**Files:** create `comprehensive_builder.py` and `test_comprehensive_report_builder.py`.

- [ ] Write failing tests for exact 525/230/115 kV grouping, `>= cutoff` retention, equivalent resolved policies, Bus/Line/Transformer/Link/device inventory, 3-winding physical-vs-T-leg separation, connected components, and map coverage.
- [ ] Add fixtures with a radial spur, a protected cycle, a parallel corridor, two- and three-winding transformer evidence, mapped devices, and independently supplied fidelity-group metrics.
- [ ] Implement inventory extraction from public PyPSA tables and active topology; require complete unique MappingBundle source rows when mappings are supplied.
- [ ] Map `CompressionOpportunityReport` values by explicit attributes only; emit missing fields as `NOT_RECORDED`; do not substitute zero.
- [ ] Convert supplied PR-022 metric observations and threshold evaluations without recalculating thresholds, changing severity, or changing admission.
- [ ] Run the focused builder tests and the existing opportunity, voltage-layer, mapping, and PF metric tests.

## Task 3: Historical adapters

**Files:** create `comprehensive_history.py` and `test_comprehensive_report_history.py`.

- [ ] Test reading the committed PR-020 structural summaries, PR-021 policy matrix, PR-022 fidelity summary, and Stage2C execution summary using their own schemas and source SHA bindings.
- [ ] Assert every output run retains its own experiment/schema/package/implementation identity; assert Stage2C is not populated with PR-022 Fidelity metrics.
- [ ] Normalize unavailable Transformer, eligibility, retained-network, and PF subgroup fields as `NOT_RECORDED`.
- [ ] Reject unknown schema versions, malformed digests, conflicting identities, or changed bytes after a supplied digest was bound.
- [ ] Verify no raw StableIds, backend names, absolute paths, source cards, or device labels occur in public output.

## Task 4: Deterministic exporters

**Files:** create `comprehensive_export.py` and `test_comprehensive_report_export.py`.

- [ ] Test that JSON, Markdown tables, metrics CSV, voltage-layer CSV, and policy comparison CSV expose identical canonical metric values and preserve unavailable statuses.
- [ ] Test no-PF input exports all structural sections and marks PF metrics `NOT_RECORDED` without raising.
- [ ] Implement deterministic serialization from one validated report object; write atomically and return named output paths.
- [ ] Run focused exporter and historical report tests; regenerate the new anonymous validation outputs only from committed historical summaries.

## Task 5: Full regression and Commit B

- [ ] Run all package tests offline, `compileall`, `git diff --check`, and `uv lock --check --offline`.
- [ ] Verify package resources are present in source and wheel manifests without changing package version or dependencies.
- [ ] Audit all changed outputs for absolute paths, private identities, unsupported status claims, and stale/mixed evidence.
- [ ] Commit only aggregation/exporters/history/tests/generated report outputs as `feat(reduction): implement structural and fidelity report exporters`.
- [ ] Verify both commit messages, parent order, final tree, clean tracked worktree, and unchanged Stage2A/B/C files.
