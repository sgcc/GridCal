# Revision 3 post-merge implementation rebind (draft)

Status: **DRAFT — independent approval pending**

This revises the unapproved Revision 3 draft to bind the implementation merged by PR #15. It preserves the experiment contract. The exact prior draft is retained in `freeze-revision-3-draft-pre-merge.json` (SHA-256 `8a10f480f72457ecf96708781ba114277a62801b540cc32284a3e95393ae24ee`); Revision 2 remains the parent (`25394674ba28e7de013bd3cc57da080155443109b77ec7f9eba7c8d57a8d2b04`).

## Implementation identity

| Binding | Value |
|---|---|
| Base branch | `codex/psdcalc-phase1-base` |
| Merge commit | `79016cfc82e7b05ffbe978187e435c2132789645` |
| Merge tree | `541a3fd05914db30fe63482e81fe58273ed82ef9` |
| Parent 1 | `e16de2fb2d6a5f96246b1e47f361bb0d897607e5` |
| Parent 2 / PR #15 head | `b38ce7d89caebab1efc70cdd39a2302f5e70e42f` |
| PR #15 | [Retained Fidelity implementation](https://github.com/sgcc/GridCal/pull/15) |
| Package | `psdcalc-reduction 0.8.0` |
| Lock SHA-256 | `779d901329988f1f76e549a146a3c00a65c570b0cc0335b80ee281b717b55712` |
| Threshold set SHA-256 | `4688f0f021f5aaabbfcb75bd1dcd8a1228753669dd415761d2d1b5471dac3f24` |
| Frozen metrics source SHA-256 | `2f0bc8ad6ea9f6ac350bf4c6541327fbb6e11fdddfe501917e27a5d6b08dba26` (unchanged) |

The merge tree is exactly the final PR head tree. This makes the existing full-suite result applicable to the same source snapshot, but the suite was not rerun under the merge commit identity.

## Validation evidence

| Gate | Result | Bound identity |
|---|---|---|
| Full package | **687 passed / 0 failed / 0 skipped** | PR head `b38ce7d89caebab1efc70cdd39a2302f5e70e42f`, tree `541a3fd05914db30fe63482e81fe58273ed82ef9`, locked Python 3.11.15; reused by exact tree equality |
| Focused post-merge | **140 passed / 0 failed** | Merge `79016cfc82e7b05ffbe978187e435c2132789645`, tree `541a3fd05914db30fe63482e81fe58273ed82ef9`, Python 3.11.15, PyPSA 1.2.4; 4,352 warnings |
| Current source import | PASS | Explicit source path resolved to current merge worktree |
| Strict new-process replay/digest | PASS | Focused suite |
| Tamper rejection | PASS | Focused suite |
| Source-drift rejection | PASS | Before PF; zero PF calls |
| StableId canonical ordering | PASS | Focused suite |
| Offline lock check | PASS | `uv lock --check --offline` with isolated cache |
| Real-grid PF this task | **0** | No Beijing PF executed |

The earlier 695/192 evidence belongs to the pre-PR source tree. The previous PR focused count of 235 was superseded; 140 is the final focused count on PR #15 Head.

## Contract and scope review

Comparison against Revision 2 and the prior Revision 3 draft found the Fidelity contract, retained metric contract, PF executor, operating point, policies, execution order, stop rule, and Attempt005 request unchanged. HARD/DIAGNOSTIC classification, formulas, thresholds, metric digest fields, strict replay, policy order, first-hard-failure stop, and PF call budget remain unchanged. Threshold set is `PR022_AC_PF_FIDELITY_V1`, SHA-256 `4688f0f021f5aaabbfcb75bd1dcd8a1228753669dd415761d2d1b5471dac3f24`.

**Disclosed API difference:** `packages/psdcalc-reduction/src/psdcalc/reduction/reporting/__init__.py` no longer exports the separate historical `comprehensive_history` archive module. This public export-surface difference is outside the frozen PF calculation/replay scope and requires independent interface review; whole-package API equivalence is not claimed. The duplicate evidence writer/reader definitions were removed while canonical definitions remain.

PR #15's Pylint workflow did not execute analysis: Python 3.8 setup failed on Ubuntu 26.04; Python 3.9/3.10 jobs were cancelled. No formal GitHub Review record existed. These are historical facts, not passing checks.

## Historical and authorization boundaries

- Attempt004 remains `FAIL_STOPPED_FIRST_POLICY`; original STOP evidence is unchanged.
- Attempts 001–003 and Revision 2 are unchanged.
- Attempt005 is `NOT_STARTED`; no lease is issued.
- Projection fidelity is **NOT VALIDATED**; physical equivalence is **NOT CLAIMED**.
- Independent freeze approval is **PENDING**.

Companion files: `freeze-revision-3-draft.json`, `revision-3-approval-request-post-merge.json`, and `attempt-005-execution-lease-application-post-merge.json`. Historical evidence files were copied byte-for-byte; no raw grid model or private execution data is included.
