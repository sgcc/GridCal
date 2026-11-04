# Attempt 005 Source-Drift Test Repair Review

## Result

The prior full-suite failure was a test-fixture error, not a production Gate defect. The test named `test_frozen_attempt004_entry_runs_three_synthetic_policies` bound its expected source identity to the dynamically observed current `HEAD`, then expected that same source to be rejected as drift. In a committed checkout, the implementation matched that binding, so the expected `FROZEN_IMPLEMENTATION_CHANGE_REQUIRED` exception was not raised.

The test is now split into two distinct cases:

- `test_frozen_attempt004_entry_runs_three_synthetic_policies` binds the actual current source and confirms the three synthetic policies complete with converged candidate PF results.
- `test_frozen_attempt004_rejects_source_drift_before_pf` binds the frozen implementation to the Revision 2 commit, confirms the checked-out implementation differs from that commit, and invokes the real frozen-input entry point. It receives exactly `FROZEN_IMPLEMENTATION_CHANGE_REQUIRED`, makes zero PF calls, and creates no output result directory.

The source-identity check is in `execute_from_frozen_inputs()` and runs before source inputs are loaded or candidate PF can start. The negative case passed against that production path; no production bypass or defect was found.

## Frozen runtime and verification

- Interpreter: isolated locked Python 3.11.15 environment
- Platform: macOS 26.2, arm64
- PyPSA 1.2.4; NumPy 2.4.6; pandas 3.1.0; NetworkX 3.6.1; SciPy 1.17.1; highspy 1.15.1; Polars 2.1.0; Pydantic 2.14.1; Hypothesis 6.170.13
- `uv.lock` SHA-256: `779d901329988f1f76e549a146a3c00a65c570b0cc0335b80ee281b717b55712`
- Focused PF/Fidelity/evidence/attempt suite: 192 passed, 0 failed; 4,479 warnings.
- Strict metric and fresh-process replay suite: 81 passed, 0 failed; 127 warnings.
- HARD admission suite: 22 passed, 0 failed.
- Full package suite: 695 passed, 0 failed; 20,671 warnings in 375.55 seconds.
- `compileall`, `uv lock --check --offline`, and `git diff --check`: PASS.

The full run used the locked interpreter and current checkout source (`PYTHONPATH=src`). The suite exercised only synthetic fixtures; no Beijing input or real-grid PF was run.

## Strict replay and admission

The 81-test strict replay set passed, including persistence/reload, independent-process replay, digest equality, and tamper rejection. The 22-test HARD admission set passed. The StableId canonical-order implementation remains unchanged. The source-drift rejection test independently asserts zero PF calls; the positive three-policy test retains its intended synthetic PF execution.

## Warning review

The complete suite reported 20,671 warnings: 20,670 `Pandas4Warning` instances from the locked PyPSA/pandas compatibility paths and 1 joblib `UserWarning` about physical-core discovery. No new warning category was observed. The prior same-environment common-test comparison remains 670 passed / 0 failed and 16,703 warnings for both Revision 2 and Revision 3; categories and source counts matched, with no numeric-calculation or evidence-integrity warning identified. The full-run warning total equals the earlier full-run total despite replacing the broken expectation with the explicit positive/negative pair. `WARNING_REGRESSION=NO` for the comparable common set and this repair; warning cleanup is outside scope.

## Revision 3 binding impact

Production source is unchanged from the frozen implementation commit `d46f18dafdd5af3e83458e642b62d8e6a2325a12` / tree `66c897e6357f09b3dff690166cfdfff96843cf75`. `metrics.py` still has the frozen SHA-256 `2f0bc8ad6ea9f6ac350bf4c6541327fbb6e11fdddfe501917e27a5d6b08dba26`.

The Revision 3 draft also explicitly binds `implementation_revision.test_attempt_driver_sha256`. Its recorded value (`867b446dbcba648f8cd805acce842d933dabcf81f474f1396e9ef7061f459588`) no longer matches the repaired test file (`0d50c6549439c65fcb3b76c710811c70aaea13fea2029691f3cd25ee2c3dab53`). The implementation commit/tree and metric-source binding remain valid, but the test-file binding and the draft's prior test evidence are stale. The draft freeze was not edited. Before independent Revision 3 approval, reviewers must either approve an auditable draft amendment binding the repaired test SHA and this validation result, or explicitly accept a separate validation addendum with those identities. Therefore `REVISION3_FREEZE_APPROVAL_READY=NO` until that binding is resolved; Revision 3 remains DRAFT.

## Change scope and historical evidence

Changed files are limited to the attempt-driver test and this non-sensitive review evidence (Markdown and JSON). No production code, dependency lock, threshold, metric formula, digest rule, execution Gate, or freeze file changed. Revision 2 and Attempts 001–004 evidence remain untouched.

- `LEASE_ISSUED=NO`
- `REAL_GRID_PF_RUNS=0`
- `ATTEMPT005=NOT_STARTED`
- Projection fidelity remains `NOT VALIDATED`; physical equivalence remains `NOT CLAIMED`.

Next step: resolve the Revision 3 test-file evidence binding through independent review, then seek independent freeze approval. Do not treat this test repair or its synthetic tests as authorization to run Attempt005.
