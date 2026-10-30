# PR-020 Real-Case Structural Acceptance Implementation Plan

> **For agentic workers:** Execute each task in order with failing tests before implementation.

**Goal:** Produce one anonymous five-stage structural comparison report and run the audited Beijing and Jilin AC-only cases without making physical-fidelity claims.

**Architecture:** Reuse PR-017 source inventory and provenance, PR-019 protected-member policy, the existing Pipeline and final Gate. A read-only stage observer captures S2/S3/S4 inventories; a report object owns every JSON, Markdown and Parquet value. Private source rows and paths remain in local run artifacts. Public validation documents contain aggregates and hashes only.

**Tech Stack:** Python 3.13, PyPSA 1.2, Pydantic 2, pandas, PyArrow, NetworkX, pytest, uv.

## Global constraints

- Base commit `03e5a2fbac05e591c456ee088e5e4b2d6bcac590`, tree `79f2ac8c5cf11392fc284e6220a65860840ba598`.
- Keep `PHYSICAL_FIDELITY_NOT_YET_EVALUATED`; do not run PF, NR/FDPF, Ward/Kron, N-1, AFDPF-R, OPF, VAR optimization, UC or GNN.
- Do not alter PR-019 protected-member behavior or weaken the cycle, ring, mapping or final structural Gates.
- Run only programmatic tests until the complete report, privacy and corruption tests pass.
- If any real-case stop condition in the task brief occurs, retain failure evidence and stop the remaining real-case matrix.

### Task 1: Report contracts and stage metrics

**Create:** `packages/psdcalc-reduction/src/psdcalc/reduction/reporting/comparison.py`, `packages/psdcalc-reduction/src/psdcalc/reduction/metrics/stage_inventory.py`.
**Test:** `packages/psdcalc-reduction/tests/test_compression_comparison.py`.

- [ ] Write failing tests for S0–S4 order, source-only transformer fields, active Line topology, distinct simple/multigraph ranks, VALID/INVALID accepted fields, layer sums, and deterministic semantic content.
- [ ] Add frozen typed stage, voltage, mapping, energy, topology, parallel and runtime models; reject inconsistent counts.
- [ ] Collect S0 from `BpaAcInventory` and PSDCase, S1–S4 from public PyPSA tables and active Line graph.

### Task 2: Single-source report rendering and privacy

**Create:** `reporting/comparison_markdown.py`, `reporting/redaction.py`.
**Modify:** `cli.py` to show existing comparison if present.
**Test:** `tests/test_compression_comparison.py`, `tests/test_cli_multi_voltage.py`.

- [ ] Write failing JSON/Markdown/Parquet consistency and forbidden-token tests.
- [ ] Persist the report and derived tables from one validated report object; register every artifact in the manifest.
- [ ] Keep raw names, StableIds, DAT bytes, absolute paths, usernames and hostnames out of public reports.

### Task 3: Stage observation and provenance

**Modify:** `pipelines/multi_voltage.py` only to emit read-only stage snapshots and timed phase boundaries; keep Builder behavior unchanged.
**Create:** a source-ledger constructor under `readiness/` that consumes audited PSDCase/DAT evidence and the existing generic ledger types.
**Test:** programmatic `tests/test_compression_comparison_pipeline.py` and source-ledger fixtures.

- [ ] Write failing tests for S3 vs S4, provenance uniqueness, L+ source value distinct from projected `Shunt.b`, corruption, and report status gating.
- [ ] Capture parse/projection/topology/planning/builder/persist-reload-gate/report timings; mark uninstrumented subphases `NOT_AVAILABLE`.

### Task 4: Programmatic gates

- [ ] Run complete offline package tests, compileall, `git diff --check`, schema/resource equality, build and clean wheel-only CLI smoke.
- [ ] Resolve report and privacy failures before opening any real fixture.

### Task 5: Private Beijing then Jilin acceptance

- [ ] Recheck byte-exact AC-only and unsupported-card Gates, source counts, source SHA and audit expectations.
- [ ] Run Beijing BASE, LOW_115 and LT110 with `preserve_protected_members`; calculate targets from current structural floors.
- [ ] Stop on a listed stop condition. Otherwise run Jilin BASE and one applicable low-voltage policy.
- [ ] Retain private raw artifacts locally; commit only anonymous aggregates, hashes, named Gate outcomes and measured runtimes.

### Task 6: Review submission

- [ ] Re-run all required gates and archive checks, record source commit/tree and artifact hashes.
- [ ] Create Draft PR #8 from the independent branch with ten requested body sections and explicit physical-fidelity boundary.
