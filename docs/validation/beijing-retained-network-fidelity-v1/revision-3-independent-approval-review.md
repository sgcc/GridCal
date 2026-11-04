# Revision 3 Independent Approval Review Package

**Review-package status:** READY FOR INDEPENDENT REVIEW

**Freeze status:** `DRAFT_PENDING_INDEPENDENT_REVIEW`

**Independent approval:** `NOT_GRANTED`

**Attempt 005 lease:** `NOT_ISSUED`

This package reconciles Revision 3's test evidence binding and records the locked-runtime validation. It is a preparation artifact, not approval or authorization to execute the Beijing case.

## Identity and reconciliation

The Revision 3 draft changed from SHA-256 `6a728e66ff3803ec17d51fa7c1e0c063e7a6b2813ff248e467ce1f755bbda4d7` to `8a10f480f72457ecf96708781ba114277a62801b540cc32284a3e95393ae24ee`.

The repaired test file is bound to the exact content in commit `32006039913aecb4ba9c46fc1b24e5e0a07b8014`, tree `740b93010b6d8cd53fe97ba87f4115269e531e05`, SHA-256 `0d50c6549439c65fcb3b76c710811c70aaea13fea2029691f3cd25ee2c3dab53`. This supersedes the draft's former test-file SHA-256 `867b446dbcba648f8cd805acce842d933dabcf81f474f1396e9ef7061f459588`.

The production implementation binding remains `d46f18dafdd5af3e83458e642b62d8e6a2325a12` / tree `66c897e6357f09b3dff690166cfdfff96843cf75`. The bound `metrics.py` SHA-256 remains `2f0bc8ad6ea9f6ac350bf4c6541327fbb6e11fdddfe501917e27a5d6b08dba26`. No production source or execution Gate changed during this reconciliation.

## Revision 2 to Revision 3 technical delta

Revision 3 carries the Revision 2 experiment contract forward. Its production change is limited to deterministic ordering for existing active-power reductions: bus active-balance terms and paired passive-branch terminal active-power terms are accumulated in `StableId.encode()` order. Each branch's two terminal values remain paired by the same StableId.

The Python `float` type, built-in `sum`, formulas, units, loss denominator floor, serialized metric fields and digest contract are unchanged. The PR-022 HARD thresholds, `DIAGNOSTIC_ONLY` classification, strict digest/replay Gate, PF executor and arguments, operating point, mapping/reduction behavior, three-policy order, and first-hard-failure stopping rule are unchanged.

## Test-binding repair

The former positive test derived its expected frozen identity from the current `HEAD` and then expected that same identity to fail. The repaired tests now make the two intended states explicit:

1. Matching source is accepted by the source identity Gate and the synthetic `BACKBONE_500`, `BACKBONE_220`, and `BACKBONE_100` paths complete with converged candidate PF results.
2. Drifted source is compared with the exact Revision 2 implementation commit/tree (`e1f075d9923b6ed530603eb2676eb73d1c35131a` / `eb9cb40f8d79bc95daccca1f4a893bcda869b6ef`) and rejected as `FROZEN_IMPLEMENTATION_CHANGE_REQUIRED` before PF; the test verifies zero PF calls and no result output directory.

These tests exercise the real frozen-source check. No production-code defect or bypass was found.

## Locked-runtime evidence

The tests ran with Python 3.11.15 and the lock-bound environment: PyPSA 1.2.4, NumPy 2.4.6, pandas 3.1.0, NetworkX 3.6.1, SciPy 1.17.1, highspy 1.15.1, Polars 2.1.0, Pydantic 2.14.1, Hypothesis 6.170.13, and `uv.lock` SHA-256 `779d901329988f1f76e549a146a3c00a65c570b0cc0335b80ee281b717b55712`.

| Verification | Result |
| --- | ---: |
| Full package suite | 695 passed / 0 failed |
| Focused PF/Fidelity/evidence/attempt suite | 192 passed / 0 failed |
| Strict metric and fresh-process replay suite | 81 passed / 0 failed |
| HARD admission suite | 22 passed / 0 failed |
| StableId canonical-order regression | PASS |
| Evidence tamper rejection | PASS |
| `compileall` | PASS |
| `uv lock --check --offline` | PASS |
| `git diff --check` | PASS |

The full run reported 20,671 warnings. On the identical common test set, Revision 2 and Revision 3 each produced 670 passed tests and 16,703 warnings, with the same categories and source counts. The warnings were PyPSA/pandas deprecations plus one Joblib physical-core discovery warning; none was identified as affecting numeric results or evidence integrity. `WARNING_REGRESSION=NO` for that comparable set.

## Historical and claim boundaries

Attempt 004 remains `FAIL_STOPPED_FIRST_POLICY` due to its recorded strict metric replay mismatch. Its STOP evidence, the Revision 2 freeze, and Attempts 001–003 remain unchanged. Revision 3's test repair does not promote Attempt 004.

No Beijing model was loaded and no real-grid PF was run in this task. Attempt 005 was not started; no lease was issued. Projection fidelity remains `NOT VALIDATED`, and physical equivalence remains `NOT CLAIMED`.

## Independent review decision required

The package is ready for a separate reviewer to assess the reconciled draft and evidence. This preparation is not the independent approval. Revision 3 remains DRAFT until that reviewer records acceptance; a separate lease approval and issuance remain required before any real-case execution.
