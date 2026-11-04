# Attempt 005 freeze preparation review

_Read-only implementation review and offline preparation on 2026-11-04. No Beijing PF was run._

---

## Result

| Gate | Result | Basis |
| --- | --- | --- |
| Patch review | PASS | Commit `d46f18dafdd5af3e83458e642b62d8e6a2325a12`, tree `66c897e6357f09b3dff690166cfdfff96843cf75` |
| Freeze revision | `3 DRAFT` | Parent revision 2 retained; awaiting independent review |
| Contract semantics | Unchanged | Only deterministic summation order changes |
| Warning regression | UNRESOLVED | 19,766 reported; categories summarized below, per-category counts unavailable |
| Offline preflight | PASS for review/application preparation | No source model load, candidate build, or PF; runtime must be rechecked before execution |
| Lease materials | Review draft only | Application exists; unresolved warning comparison and locked-runtime recheck remain before formal execution request |

## Patch review

The implementation commit has four changed files: `fidelity/metrics.py`, its PF-metric tests, the retained Fidelity attempt-driver test, and the canonical replay fix review. The production diff changes only ordering for three existing sums:

1. Source and target bus active-power balances are summed by `StableId.encode()` order.
2. Passive Line/Transformer terminal active powers are paired by the same StableId and summed in encoded-ID order.

The formula, Python float representation, built-in `sum`, units, loss denominator floor, serializer format, metric digest fields, thresholds, HARD/DIAGNOSTIC classification, PF executor, mapping, and reduction behavior are unchanged. The attempt-driver regression now confirms the old Attempt 004 lease fails closed after implementation drift. No unrelated production change was found.

## Test and warning evidence

The committed fix-review report records:

- Focused PF metrics/evidence/driver tests: `29 passed / 0 failed`.
- Full package suite: `694 passed / 0 failed`, with `19,766` warnings, under Python `3.13.9` and PyPSA `1.2.4`.
- Separate canonical-order test: `1 passed / 0 failed`.
- Synthetic fresh-process replay: PASS; tampered evidence rejection: PASS.
- `compileall`, `git diff --check`, and `uv lock --check --offline`: PASS.
- No extra real-grid PF calls.

The tests were not rerun in this freeze-preparation task. The warning categories reported for the run are PyPSA/Pandas deprecation or future-behavior warnings, Joblib's physical-core detection environment warning, and a Pandas dtype warning in a synthetic pipeline test. Per-category warning counts were not saved in the committed test summary. The changed production file contains no warning-producing compatibility calls, and no warning affecting numeric order, evidence integrity, or replay was identified. Because no comparable prior-run warning inventory and no per-category counts were retained, `WARNING_REGRESSION=UNRESOLVED`.

The full-suite runtime differs from the frozen Attempt 005 runtime. The full suite used Python `3.13.9`; the revision 2 execution contract binds Python `3.11.15` and a specific 67-distribution manifest. The lock file SHA remains `779d901329988f1f76e549a146a3c00a65c570b0cc0335b80ee281b717b55712`. Any actual execution must verify that exact environment before source loading or PF.

## Freeze revision and lineage

`freeze-revision-3-draft.json` copies the full revision 2 contract and changes only its revision/status and implementation binding, with explicit review and validation evidence. It binds the fix commit/tree and byte hashes of the metric implementation and regression tests. The parent revision 2 file SHA is `25394674ba28e7de013bd3cc57da080155443109b77ec7f9eba7c8d57a8d2b04`; the original freeze SHA remains `b1a3bc72150063b7560d3bb2616e546c5f8020aa5db529645891a1931f5d9476`.

The DAT, source model, sidecar, source ledger, protection, operating-point, threshold, parser, and dependency identities are inherited from revision 2 without recalculation against private inputs in this task. The source DAT SHA remains `09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e`. Policy order and stop-first-failure rules remain unchanged.

## Attempt 004 remains historical failure

The Attempt 004 stop summary remains byte-identical to the recorded SHA-256 `866221a956a2100a58b279e2d0386c4766d741295530f94244222a5e8e06c6f5`. Its recorded status remains `FAIL_STOPPED_FIRST_POLICY`, with the first policy rejected by an exact metric replay digest mismatch. Revision 3 does not rewrite, re-sign, reinterpret, or promote any result from Attempts 001–004.

## Offline preflight and lease application

`attempt-005-offline-preflight.json` binds the revision parent, fix commit/tree, current lock digest, source and threshold identities inherited from the freeze, policy order, replay requirement, and privacy checks. It confirms no private model, device identity, raw PF arrays, or untracked `.serena/` directory is staged for this evidence set. It does not rehash the private model or run an execution gate.

`attempt-005-execution-lease-application.json` is an application draft, not a valid lease. It requests only the three frozen policies in order, specifies a maximum of four PF calls, preserves fail-stop handling, and requires a fresh private output directory. It is not issued or activated. A formal execution request should wait until the warning comparison is resolved and the exact locked Python 3.11.15 environment is reverified.

## Decision and boundaries

The freeze draft and lease application are ready for independent review, but the formal execution request is not yet ready: warning regression is unresolved and the exact locked runtime was not revalidated here. They are not an execution authorization. Attempt 005 remains `NOT_STARTED`; real-grid PF runs in this task: `0`. Projection fidelity remains `NOT VALIDATED`; physical equivalence remains `NOT CLAIMED`; Stage2D is out of scope.

## Files added by this preparation

- `freeze-revision-3-draft.json`
- `freeze-revision-3-draft.md`
- `attempt-005-offline-preflight.json`
- `attempt-005-execution-lease-application.json`
- `attempt-005-execution-lease-application.md`
- `attempt-005-freeze-preparation-review.md`
