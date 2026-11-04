# Revision 3 post-merge implementation rebind review

**Status: DRAFT — independent freeze approval pending**

This review rebinds the unapproved Revision 3 draft to the implementation merged by PR #15. It does not authorize Beijing execution and does not change the experiment contract.

## Verified implementation identity

| Item | Verified value |
|---|---|
| Target branch | `codex/psdcalc-phase1-base` |
| Merge commit | `79016cfc82e7b05ffbe978187e435c2132789645` |
| Merge tree | `541a3fd05914db30fe63482e81fe58273ed82ef9` |
| Parent 1 | `e16de2fb2d6a5f96246b1e47f361bb0d897607e5` |
| Parent 2 / PR #15 head | `b38ce7d89caebab1efc70cdd39a2302f5e70e42f` |
| PR #15 | [Retained Fidelity implementation](https://github.com/sgcc/GridCal/pull/15) |
| Package | `psdcalc-reduction 0.8.0` |
| Dependency lock SHA-256 | `779d901329988f1f76e549a146a3c00a65c570b0cc0335b80ee281b717b55712` |
| Frozen threshold artifact SHA-256 | `4688f0f021f5aaabbfcb75bd1dcd8a1228753669dd415761d2d1b5471dac3f24` |
| Frozen metrics source SHA-256 | `2f0bc8ad6ea9f6ac350bf4c6541327fbb6e11fdddfe501917e27a5d6b08dba26` |

The merge commit has exactly the expected two parents. Its tree equals PR #15's final head tree. The full-suite result below was run on the PR head, not rerun under the merge commit; it applies to the same source snapshot by exact tree equality. The focused suite was rerun on the merge commit itself.

## Implementation and binding comparison

The old Revision 3 draft remains preserved byte-for-byte as `freeze-revision-3-draft-pre-merge.json` with SHA-256 `8a10f480f72457ecf96708781ba114277a62801b540cc32284a3e95393ae24ee`. The revised draft SHA-256 is `0bf40ce99fd3d577583c1fd01f43abc098afa81cd2091992f46dc133af75e930`. Revision 2 remains unchanged and is the parent freeze.

The revised JSON binds SHA-256 values for all 21 PR #15 production/tooling files, all 9 changed test files, the dependency lock, and the threshold artifact. The `metrics.py` content hash is unchanged from the old draft. Commit identity changed from the old development commit to the merge commit; this is recorded as an identity rebind, not represented as a code-equivalence inference.

The production files within the frozen calculation/replay scope match the prior bound content. PR #15 removed duplicate PF evidence writer/reader definitions while retaining the canonical definitions. One public API difference is disclosed: `reporting/__init__.py` no longer exports the separate historical `comprehensive_history` archive module because that module was outside PR #15 scope. This does not change frozen PF calculation or replay semantics, but remains an independent interface-review item. Whole-package public API equivalence is not claimed.

## Test and environment evidence

| Validation | Result | Evidence boundary |
|---|---|---|
| Full package suite | **687 passed / 0 failed / 0 skipped** | Python 3.11.15 locked environment; run at PR head `b38ce7d...`, tree `541a3fd...`; same tree as merge, not rerun under merge SHA |
| Focused post-merge suite | **140 passed / 0 failed** | Python 3.11.15, PyPSA 1.2.4; run on merge commit; 4,352 warnings |
| Locked dependency check | **PASS** | 67 locked distributions; `uv lock --check --offline` |
| Current source import | **PASS** | Explicit import paths resolved to the merge worktree source |
| Strict fresh-process replay and digest validation | **PASS** | Included in the focused regression suite |
| Evidence tamper rejection | **PASS** | Included in the focused regression suite |
| Source-drift rejection | **PASS** | Rejected before PF; zero PF calls |
| StableId canonical-order regression | **PASS** | Included in the focused regression suite |

The earlier 695/192 results refer to the pre-PR source tree and remain historical. The previous PR description's 235 focused count was superseded by the final PR-head result; 140 is the focused count freshly rerun on the merge commit. The warning counts from the historical common-set comparison are not directly compared with the current focused suite.

## Contract and claim boundary

Revision 2 and the original Revision 3 draft were compared for the common frozen Fidelity contract, retained metric contract, PF executor, operating point, policies, execution order, and stop rule; these are unchanged. The Attempt005 request exists only in Revision 3 and is unchanged from its pre-merge draft. HARD/DIAGNOSTIC classification, metric formulas and units, thresholds, digest fields, strict replay, strategy order, first-hard-failure stop, and PF call budget remain unchanged.

Attempt004 remains `FAIL_STOPPED_FIRST_POLICY`; its STOP evidence is preserved. Attempts 001–003 and Revision 2 are unmodified. The original Revision 3 draft and old implementation identity are preserved for traceability.

PR #15's historical Pylint workflow did not run Pylint: Python 3.8 setup failed because Ubuntu 26.04 lacked that interpreter, and Python 3.9/3.10 jobs were cancelled. PR #15 had no formal GitHub Review record. Neither fact is relabeled as a successful check or approval.

- Beijing PF runs in this rebind task: **0**.
- Attempt005: **NOT_STARTED**; lease: **NOT_ISSUED**.
- Projection fidelity: **NOT VALIDATED**.
- Physical equivalence: **NOT CLAIMED**.
- Independent freeze approval: **PENDING**.

## Review package and PR scope

The canonical revised draft is `freeze-revision-3-draft.json` and its readable companion is `freeze-revision-3-draft.md`. New post-merge approval and lease-request documents bind the revised freeze SHA; the lease remains an unissued request. Existing historical freeze, STOP, replay, warning and test evidence was copied without alteration. No model, raw run output, third-party wheel, credential, or local checkout path is included.

The proposed review branch is based on the PR #15 merge commit and contains only validation/freeze/approval evidence. It does not repeat the 35 PR #15 implementation files. Independent reviewers should specifically inspect the disclosed `reporting/__init__.py` export difference and the distinction between the reused full-suite tree evidence and the fresh merge-commit focused run.


Draft PR title/body material is prepared in `revision-3-review-pr-description.md`. GitHub branch creation returned HTTP 403 (`Resource not accessible by integration`), so no remote branch or PR was created; no further write attempt was made.
