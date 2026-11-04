# Retained Fidelity clean implementation PR review

## Scope and ancestry

- `BASE_BRANCH`: `origin/codex/psdcalc-phase1-base`
- Base commit/tree: `e16de2fb2d6a5f96246b1e47f361bb0d897607e5` / `8adbfbd057181a82b502c758403e9d19fda8a07a`
- Implementation commit/tree: `35c9c473e52d2a55d1081a72ac3b400d4c9d23c1` / `a348ed39f0259161e0e166145317a6a89746528d`
- Latest verified implementation/test commit/tree before this report update: `3672684c2497443cbcb0cf069c53b166de455808` / `41c4d971086c18920e16450346fdbb3371c9debf`
- Base contains the merged PR-022 prerequisites; no prerequisite code is duplicated.

The implementation-only change set has 33 files: 16 package source files, 9 test files, 5 Retained Fidelity experiment-tool files, and 3 narrowly relevant design/freeze documents. The two required review artifacts bring the final PR diff to 35 files. The JSON digest catalog lists each implementation path and SHA-256. After removing independent historical-report archive code/data, the PR does not contain the comprehensive-history dataset or unrelated Stage2D-specific files.

## Source selection and identity

The Retained Fidelity and observation implementation was selected from development head `0b943129347d1f282a2da1758c09c7a7bdb04265`. The stable-order fix came from `d46f18dafdd5af3e83458e642b62d8e6a2325a12`; the source-drift test repair came from `32006039913aecb4ba9c46fc1b24e5e0a07b8014` and was adapted to this PR's reachable base commit.

All selected production files match the source development head byte-for-byte except `reporting/__init__.py`, where exports for the excluded historical archive module were removed. `fidelity/metrics.py` exactly matches the StableId-order fix commit; its SHA-256 is `2f0bc8ad6ea9f6ac350bf4c6541327fbb6e11fdddfe501917e27a5d6b08dba26`. The 9 test-file digests, 16 production-file digests, source freeze JSON digest, and unchanged `uv.lock` digest are in `retained-fidelity-clean-implementation-pr-review.json`.

The included original anonymized freeze contract JSON is required by the existing frozen Retained Fidelity runner and its synthetic source-identity test: the runner reads and verifies that exact repository-relative file. It contains contract metadata and digests, not the private network model, device identities, raw cards, or local paths. The Attempt 004 runner/output modules are included because they provide the reusable frozen-input execution, PF evidence, report persistence, and replay path under review; no historical Attempt output directory or lease is included.

## Verification

- Focused PF/Fidelity, Retained Fidelity, numeric observation, report, evidence, and replay tests: **235 passed, 0 failed**. The final privacy-fixture recheck additionally passed 20 focused report tests and 2 source-identity tests.
- Full package suite on Python 3.11.15: **687 passed, 0 failed, 0 skipped**.
- Synthetic PF → persistence → fresh-process replay and source identity matching/drift rejection: passed. Source drift is rejected before PF; PF call count is zero on the rejection path.
- Strict digest/tamper checks and existing HARD/DIAGNOSTIC admission regression tests: passed.
- `compileall`: PASS.
- `uv lock --check --offline`: PASS; the dev dependency export resolves 67 applicable locked distributions and all 67 match the verified Python 3.11.15 environment. `uv.lock` SHA-256 is `779d901329988f1f76e549a146a3c00a65c570b0cc0335b80ee281b717b55712`, identical to base.
- `git diff --check`: PASS.
- Tests imported `psdcalc` and `psdcalc.reduction` from this clean worktree.

The suite emitted 20,674 warnings, principally PyPSA/pandas deprecations from NetCDF and network transformation paths, plus a joblib core-count warning, Hypothesis in-memory database notices, and three pandas dtype warnings in an existing test. No warning filtering was added.

## Boundaries and publication

- No unrelated Stage2D files are included; shared controller/runtime edits add observational recording and elapsed-time capture only. Existing action-selection, admission, PF contracts, thresholds, mappings, and candidate identity behavior were regression-tested.
- No Revision 3 draft/approval/lease, private Beijing model, raw real-case run output, third-party wheel, credentials, local code-graph cache, or `uv.lock` change is included.
- Beijing PF runs: `0`.
- Revision 3 rebind required after PR-1 merge: `YES`.
- Revision 3 freeze status: `DRAFT`; lease issued: `NO`; Attempt005: `NOT_STARTED`.
- `CLEAN_PR_CREATED`: `NO`; `PR_URL`: `NONE`.
- Publication blocker: the GitHub integration returned `403 Resource not accessible by integration` while creating the remote branch. Git transport also could not resolve `github.com`; no remote ref or PR was created.
