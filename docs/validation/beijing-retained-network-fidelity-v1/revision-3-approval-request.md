# Revision 3 Freeze Approval Request

**Request status:** `PREPARED_NOT_SUBMITTED`

**Freeze status:** `DRAFT`

**Independent approval:** `PENDING`

**Approval record:** `NONE`

## Requested action

Please have an independent reviewer assess and either approve Revision 3 or return specific changes. The reviewer should independently verify the freeze identity, production/test evidence separation, unchanged inherited experiment contract, locked-runtime evidence, and retained claim boundaries.

## Frozen identity

- Revision 3 draft: `docs/validation/beijing-retained-network-fidelity-v1/freeze-revision-3-draft.json`
- Revision 3 draft SHA-256: `8a10f480f72457ecf96708781ba114277a62801b540cc32284a3e95393ae24ee`
- Preparation commit/tree: `d3039b53c3e63d978fa0bfcf31de9249168102a2` / `ebcfec4c1f077445f6408374d963e952a58ba16b`
- Parent Revision 2 SHA-256: `25394674ba28e7de013bd3cc57da080155443109b77ec7f9eba7c8d57a8d2b04`

## Materials for independent review

1. `freeze-revision-3-draft.md` and `freeze-revision-3-draft.json` — the proposed freeze and its machine-readable contract.
2. `freeze-revision-2.md` and `freeze-revision-2.json` — immutable parent contract and the basis for the Revision 3 delta.
3. `attempt-004-canonical-metric-replay-fix-review.md` — StableId canonical summation implementation review.
4. `attempt-005-source-drift-test-repair-review.md` and `.json` — test binding repair, positive/negative source identity behavior, and locked-runtime results.
5. `revision-3-independent-approval-review.md` and `.json` — identity reconciliation, static checks, warning comparison, evidence links, and claim boundaries.
6. `attempt-004-stop-summary.md` and `.json` — original Attempt 004 failure and STOP evidence; status remains failed.
7. `attempt-005-execution-lease-application.md` and `.json` — separate lease application draft; it is not an issued lease.

## Evidence summary

- Production implementation: `d46f18dafdd5af3e83458e642b62d8e6a2325a12`, tree `66c897e6357f09b3dff690166cfdfff96843cf75`.
- Test repair: `32006039913aecb4ba9c46fc1b24e5e0a07b8014`, tree `740b93010b6d8cd53fe97ba87f4115269e531e05`.
- Locked Python 3.11.15 package suite: 695 passed / 0 failed; focused suite: 192 passed / 0 failed.
- Strict digest and fresh-process replay: 81 passed / 0 failed; HARD admission: 22 passed / 0 failed.
- Revision 2 and Revision 3 common warning comparison: each 670 passed / 0 failed and 16,703 warnings, with matching categories and sources; regression assessment `NO`.
- Test-file SHA-256 binding was reconciled in the Revision 3 draft. Production implementation digest remains unchanged.

## Approval boundaries

This request does not authorize an Attempt 005 run. Attempt 004 remains `FAIL_STOPPED_FIRST_POLICY`; no historical evidence is promoted. No Beijing real-grid PF was run for this submission. Projection fidelity remains `NOT VALIDATED`, physical equivalence remains `NOT CLAIMED`, and the lease remains unissued.

## Submission status

The repository contains no defined freeze-approval portal, approver identity, or separate approval record format. No existing open PR is associated with the current local development branch, and no authorized external approval destination was supplied. Therefore this request is prepared for handoff but **has not been submitted**. No reviewer, signature, or approval is represented here.
