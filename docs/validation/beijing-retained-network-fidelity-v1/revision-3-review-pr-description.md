# Draft PR creation material

**Title:** Revision 3: rebind retained-fidelity freeze to merged implementation

## Purpose

Submit the unapproved Revision 3 experiment-freeze draft for independent review after PR #15 was merged. This PR contains validation and approval records only; it does not repeat PR #15 implementation files and does not authorize an experiment.

## Rebinding

- PR #15 merge commit: `79016cfc82e7b05ffbe978187e435c2132789645`
- Merge tree: `541a3fd05914db30fe63482e81fe58273ed82ef9`
- PR #15 final head: `b38ce7d89caebab1efc70cdd39a2302f5e70e42f`
- Merge tree equals final PR head tree.
- Revised Revision 3 freeze SHA-256: `8beeea47347c8f8ef820a67d156c97ef808ba874ef5aca72054fe649f8f6ead4`
- Prior unapproved draft SHA-256 is retained in the review package and was not overwritten.

The PR should contain only the Revision 3 freeze, identity manifests, historical non-sensitive experiment/stop records, and independent review/lease-request documents. It must not contain any PR #15 production code, real-grid model, raw PF data, third-party wheel, credential, local path, or Stage2D changes.

## Validation evidence

- Full package suite: **687 passed / 0 failed / 0 skipped**, run on PR #15 head in the locked Python 3.11.15 environment. Because the merge tree is identical, this is applicable to the same source snapshot; it was not rerun under the merge commit SHA.
- Focused post-merge suite: **140 passed / 0 failed**, run on the merge commit in Python 3.11.15 / PyPSA 1.2.4.
- `uv lock --check --offline`: PASS.
- Base-to-PR-016-head `git diff --check 79016cfc82e7b05ffbe978187e435c2132789645 HEAD`: PASS (exit code 0); whitespace/error check only, not a test rerun, freeze approval, or real-grid acceptance.
- Strict fresh-process replay, digest validation, evidence tamper rejection, source-drift rejection before PF, StableId canonical-order tests, and HARD admission regression: PASS in the focused suite.
- Beijing PF calls in this task: **0**.

## Review disclosures

- The Pylint workflow associated with PR #15 did not execute Pylint: Python 3.8 setup failed on Ubuntu 26.04; Python 3.9 and 3.10 jobs were cancelled. No formal GitHub Review record was present.
- `reporting/__init__.py` does not export the separate historical `comprehensive_history` archive module because that module was excluded from PR #15. This public export-surface difference is outside the frozen PF calculation/replay scope and is explicitly submitted for interface review.
- Attempt004 remains `FAIL_STOPPED_FIRST_POLICY`; its evidence is unchanged.
- Attempt005 is `NOT_STARTED`; no execution lease has been issued.
- Projection fidelity remains `NOT VALIDATED`; physical equivalence is `NOT CLAIMED`.
- Independent freeze approval remains `PENDING`.
