# PF and Fidelity Instrumentation Implementation Plan

> **For agentic workers:** Execute the checked steps in order in the local `codex/psdcalc-next-development` worktree. Keep the two requested local commits separate.

**Goal:** Add replayable numeric PF/Fidelity observations and timing diagnostics without changing any numerical or adaptive decision semantics.

**Architecture:** The existing executor, metric evaluator, and frozen threshold resolver remain authoritative. A new adaptive observation companion captures their outputs and stage timing; the existing v2 evidence artifact index remains unchanged, while the companion is separately SHA-bound in the run manifest.

**Tech Stack:** Python 3.11+ package source, dataclasses, PyPSA 1.2.4, Pydantic v2, pytest, canonical JSON, `time.perf_counter()`.

## Global Constraints

- Keep package version `0.8.0` and third-party dependency declarations/lock entries unchanged.
- Do not alter PF call arguments, call count, operating point, source/candidate state, metric definitions, HARD/DIAGNOSTIC groups, frozen thresholds, admission, scoring, ranking, action sequence, stopping reason, candidate fingerprints, MappingBundle digests, or closure digests.
- Do not modify or overwrite Stage2A, Stage2B, or Stage2C original evidence; do not run Beijing PF or Stage2D.
- Do not push, create a PR, or merge.
- Use two local commits: Commit A for the design/contract tests, Commit B for implementation and regression tests.

## Files

- Create `docs/architecture/adaptive-pf-fidelity-instrumentation.md`: accepted schema/compatibility and semantic boundary.
- Create `docs/superpowers/plans/2026-11-03-pf-fidelity-instrumentation.md`: executable work breakdown.
- Create `packages/psdcalc-reduction/src/psdcalc/reduction/adaptive/observations.py`: typed observations, canonical serialization, metrics projection, comparison, and recorder.
- Modify `packages/psdcalc-reduction/src/psdcalc/reduction/core/manifests.py` and `core/artifact_store.py`: optional auxiliary artifact SHA binding without changing old manifest reads.
- Modify `adaptive/candidates.py`, `adaptive/pypsa_runtime.py`, and `adaptive/controller.py`: record timing and observation role links only.
- Modify `adaptive/evidence.py`: optional companion persistence and fresh replay verification, leaving the exact v2 main artifact index unchanged.
- Modify adaptive package exports.
- Create `packages/psdcalc-reduction/tests/test_adaptive_numeric_observations.py`; extend adaptive persistence/controller tests only where needed.

## Tasks

### Task 1 — Freeze observation contracts (Commit A)

- [x] Add this design and plan.
- [x] Add contract tests for finite numeric values, explicit availability states, canonical order-independent JSON, timing-unit/scope validation, and the distinction between NOT_RECORDED and NOT_APPLICABLE.
- [x] Run the new tests and verify the expected RED is solely the not-yet-implemented observation module/API.
- [ ] Commit design and contract tests as `docs(reduction): define PF fidelity numeric observation contracts`.

### Task 2 — Implement typed observations and authoritative projections

- [ ] Implement immutable types and strict deserializers in `adaptive/observations.py`.
- [ ] Project PF execution fields directly from `PowerFlowExecutionResult.subnetwork_status`; never rerun PF or infer absent values.
- [ ] Project every existing HARD and DIAGNOSTIC metric from `PowerFlowMetricSet`, thresholds from `ResolvedPowerFlowThresholds`, and status from `PowerFlowThresholdEvaluation`; calculate margin only as `threshold - observed`.
- [ ] Add exact digest and finite/unit validation tests.

### Task 3 — Instrument runtime/controller without semantic input

- [ ] Time source/candidate executor calls at the same outer boundary.
- [ ] Time candidate reconstruction, structural/AC readiness, assessment, attribution, and controller total with `perf_counter()`.
- [ ] Mark initial incumbent, trial candidate, and rebuilt incumbent observations through an optional telemetry sink; never place timing or observation IDs in report scoring/identity fields.
- [ ] Compare a fixed synthetic report and fingerprints against a run with telemetry disabled; require identical PF call count, report/action sequence, terminal reason, candidate identities, mapping digests, closure digests, and source signature.

### Task 4 — Persist companion and replay it

- [ ] Store `adaptive-numeric-observations/v1` separately from the strict v2 artifact index.
- [ ] Register the companion path and SHA-256 in the optional manifest auxiliary digest map.
- [ ] Replay validates companion bytes/payload and independently regenerates numeric observations from the fresh controller replay.
- [ ] Compare numeric values using the existing frozen `ReplayNumericTolerance`; compare identity/digest/threshold fields exactly.
- [ ] Validate timing fields and manifest integrity without comparing timing values to replay timing.
- [ ] Preserve legacy v2 replay when the companion is absent; add numeric/threshold tamper, timing-isolation, and fresh-process replay tests.

### Task 5 — Run gates and Commit B

- [ ] Run observation focused tests, PF/Fidelity regressions, controller regressions, persistence/replay regressions, then the full package suite.
- [ ] Run `compileall`, `git diff --check`, `uv lock --check --offline`, source import check, sdist/wheel build, and isolated wheel smoke when a locked offline environment is available.
- [ ] Recompute and compare tracked Stage2A/Stage2B/Stage2C evidence hashes against the frozen base; require no changes.
- [ ] Commit implementation as `feat(reduction): add replayable PF fidelity numeric instrumentation` only after the implementation and available verification gates pass.
