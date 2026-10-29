# PR-016 MultiVoltageQuotientPipeline Implementation Plan

> **For agentic workers:** Use subagent-driven-development for independent units and inline execution for the integration path. Every behavior change starts with a failing offline test.

**Goal:** Close the three-voltage structural path from source PyPSA artifact to reloaded quotient, composed maps, final Gate, manifest, and report.

**Architecture:** `VoltageLayerEnergyPlanner` produces a stable-ID partition after topology simplification. `MultiVoltageQuotientPipeline` owns persistence, map composition, export/reload, final validation, and failure evidence. `MultiVoltageFinalValidator` wraps the Builder's preliminary structural checks only after clean reload and adds original-to-final mapping, metadata, and immutability checks.

**Tech Stack:** Python 3.13, PyPSA 1.2.4 public APIs, Pydantic v2, pandas, NetworkX, pytest; all tests offline.

## Global Constraints

- Use accepted `docs/adr/008-multi-voltage-quotient.md` and keep Builder pure in memory.
- Final statuses are only `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID`, `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID`, and `FAILED`.
- Never claim AC PF, reactive-power, OPF, or control equivalence.
- Do not modify PyPSA, PyPSA-Earth, or VeraGrid sources; do not monkey-patch or use PyPSA private APIs.
- Stable-ID sidecars are authoritative; source network remains semantically unchanged; random planners use `run.seed`.

### Task 1: Strict configuration and resources

**Files:** `config/models.py`, `config/loader.py`, `config/schema_export.py`, `schema/reduction.schema.json`, package resource schema and YAML, `examples/reduction.three-voltage.yaml`, config tests.

**Interfaces:** `config.voltage_layers.aliases`, `config.partition.target.per_layer`, `config.aggregation.invariants`; pipeline family `multi_voltage_quotient`.

- [ ] Write tests that parse valid aliases, per-layer targets, and invariants and reject unknown or invalid values.
- [ ] Run the focused test and confirm it fails on missing fields.
- [ ] Add strict Pydantic fields and semantic gates, regenerate both schema copies, and add example resources.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Per-layer energy planner

**Files:** `planners/layer_energy.py`, `tests/test_layer_energy.py`.

**Interface:** `VoltageLayerEnergyPlanner.plan(network, sidecar, protection, *, planner, n_clusters, seed, builders, options, connectivity_policy, aliases, per_layer) -> PartitionPlan`.

- [ ] Write a three-voltage test for mandatory singleton terminals and deterministic per-layer allocation.
- [ ] Confirm the test fails because the planner does not exist.
- [ ] Classify layers, allocate targets, plan on detached layer copies with explicit seed, qualify target IDs, and validate the global partition.
- [ ] Run the focused tests and confirm they pass.

### Task 3: Final validation and opportunity report

**Files:** `validation/multi_voltage_final.py`, `reporting/opportunity.py`, `tests/test_multi_voltage_persistence.py`, `tests/test_opportunity_report.py`.

**Interfaces:** `MultiVoltageStructuralResult(status, checks, details)` and `CompressionOpportunityReport` with counts, rejection reasons, theoretical and realized compression fractions.

- [ ] Write tests for clean reload, corrupted child voltage/Transformer/map, disconnected source components, and report arithmetic.
- [ ] Confirm the tests fail because final result/report APIs do not exist.
- [ ] Reuse `MultiVoltageStructuralValidator.precheck` on the reloaded network; add original-to-final coverage, sidecar, snapshot/weighting, and input-signature checks; compute opportunity statistics from topology roles and maps.
- [ ] Run focused tests and confirm they pass.

### Task 4: Pipeline, manifest, CLI, and report

**Files:** `pipelines/multi_voltage.py`, `core/manifests.py`, `core/artifact_store.py`, `mapping/compose.py` if needed, `cli.py`, package `__init__.py`, `README.md`, three-voltage pipeline/CLI/failure tests.

**Interface:** `MultiVoltageQuotientPipeline.run(*, original_network, source_sidecar, config, store) -> MultiVoltagePipelineResult`.

- [ ] Write end-to-end and failure-evidence tests, then confirm red.
- [ ] Compose topology and quotient maps for bus/branch/device/control, save both sidecars, maps, records, network levels, preliminary and final evidence, opportunity report, and manifest.
- [ ] Export NetCDF, reload through the public adapter, run final Gate, and set only allowed multi-voltage statuses; `ArtifactStore.capture` writes `FAILED` for exceptions.
- [ ] Route CLI build/plan/report to the new path when `pipeline.materialization_family=multi_voltage_quotient` and retain the existing single-voltage path.
- [ ] Run focused tests and confirm green.

### Task 5: Acceptance and PR

- [ ] Run the complete offline package suite, `compileall`, `git diff --check`, schema resource consistency, and a wheel install/CLI smoke check if packaging changed.
- [ ] Review file diff against ADR-008 and verify the source network semantic signature is unchanged.
- [ ] Commit cohesive PR-016 changes, publish a Draft PR from `codex/psdcalc-pr016`, attach it, and report exact tests and structural limits.
