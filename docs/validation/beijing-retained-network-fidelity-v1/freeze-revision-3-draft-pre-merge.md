# Retained Fidelity V1: freeze revision 3 draft

_Draft for independent review on 2026-11-04. This document is not an execution authorization._

---

## Status and lineage

Revision 3 is a draft derived from the immutable revision 2 contract. Revision 2 remains authoritative until this draft is independently reviewed and accepted. This draft does not issue an Attempt 005 lease.

| Item | Binding |
| --- | --- |
| Parent revision | `freeze-revision-2.json` |
| Parent SHA-256 | `25394674ba28e7de013bd3cc57da080155443109b77ec7f9eba7c8d57a8d2b04` |
| Original freeze SHA-256 | `b1a3bc72150063b7560d3bb2616e546c5f8020aa5db529645891a1931f5d9476` |
| Implementation commit | `d46f18dafdd5af3e83458e642b62d8e6a2325a12` |
| Implementation tree | `66c897e6357f09b3dff690166cfdfff96843cf75` |
| Test repair commit | `32006039913aecb4ba9c46fc1b24e5e0a07b8014` |
| Test repair tree | `740b93010b6d8cd53fe97ba87f4115269e531e05` |
| Attempt driver test SHA-256 | `0d50c6549439c65fcb3b76c710811c70aaea13fea2029691f3cd25ee2c3dab53` |
| Test repair review | `attempt-005-source-drift-test-repair-review.md` and `.json` |
| Package | `psdcalc-reduction 0.8.0` |
| Revision status | `DRAFT_PENDING_INDEPENDENT_REVIEW` |
| Attempt 005 authorization | `NOT_AUTHORIZED` |

The machine-readable companion is `freeze-revision-3-draft.json`. It retains the complete revision 2 contract and adds separate production implementation and test-evidence bindings. This draft amendment supersedes only the prior attempt-driver test SHA and stale test results; it does not replace or rewrite immutable Revision 2.

## Revision delta

The sole implementation change is deterministic ordering for three existing floating-point sums in `fidelity/metrics.py`: bus active-balance sums and paired Line/Transformer terminal active-power loss sums now traverse StableIds in `StableId.encode()` order. Terminal P values remain paired by the same StableId.

The change retains the Python `float` values, built-in `sum`, metric formulas, units, loss denominator floor, serialized metric fields, digest contract, HARD thresholds, DIAGNOSTIC_ONLY classification, PF executor, mappings, and reduction behavior. It is a numerical replay determinism correction; it does not change the experiment's scientific question or acceptance policy.

The production binding remains the implementation commit/tree and metric-source SHA shown in the JSON. The test file is bound separately under `test_evidence`: its prior SHA `867b446dbcba648f8cd805acce842d933dabcf81f474f1396e9ef7061f459588` is superseded by `0d50c6549439c65fcb3b76c710811c70aaea13fea2029691f3cd25ee2c3dab53`, as committed in `32006039913aecb4ba9c46fc1b24e5e0a07b8014`. The repaired tests cover both matching source acceptance and source-drift rejection before PF.

The prior Attempt 004 lease remains stale for this implementation. Attempt 004 remains `FAIL_STOPPED_FIRST_POLICY` after an exact metric replay digest mismatch; it is not retroactively repaired or promoted. A new lease must be reviewed and issued against this implementation and the accepted revision 3.

## Preserved experiment contract

The following revision 2 bindings are carried forward unchanged:

| Contract | Value |
| --- | --- |
| Source alias | `BEIJING_AC_ONLY` |
| Source DAT SHA-256 | `09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e` |
| Source model digest | `6339c7e08e09381a21fe39268362f718b9912aa2322456d033eaa5b912868150` |
| Source sidecar digest | `7fc151484448d06fe430b43a8618503a0caed123b2c9530d8b9de19afb3c6165` |
| Source ledger digest | `5293db89fee4cc9e773880afe8b5a17d00b75b78aa8cb5f15697d92170976bd7` |
| Base resolved-protection digest | `45a7be7cfa3070507c8c8615a4676a6c7ed4b0b04ee1c571f27fcb4d14186f38` |
| Operating-point digest | `334a7275a2e837722016db2b732d23e2c30ce392e3b1f592ade5d5f995706c1e` |
| Metric-policy digest | `745cdb4b5cdb6d8547e1ac9139be988b6cd393b9e1f7838bfb245aa32f6db5ad` |
| Threshold set | `PR022_AC_PF_FIDELITY_V1`, version 1 |
| Threshold artifact SHA-256 | `4688f0f021f5aaabbfcb75bd1dcd8a1228753669dd415761d2d1b5471dac3f24` |
| Policy order | `BACKBONE_500` → `BACKBONE_220` → `BACKBONE_100` |
| Locked execution runtime | Python `3.11.15`, PyPSA `1.2.4`, revision 2 lock and package manifest |

The existing hard thresholds and retained-network diagnostic definitions are inherited byte-for-byte as JSON values from revision 2. Diagnostic metrics remain diagnostic and cannot alter PR-022 admission.

## Attempt 005 request boundaries

The proposed order is fixed to the three policies above. Each policy starts from the same immutable frozen S1, sidecar, and ledger. A first hard failure stops the attempt and marks later policies `NOT_RUN`. The cross-policy report is permitted only after all required per-policy gates and fresh-process replays pass. Replay performs no PF solve. Stage2D and adaptive control remain out of scope.

The proposed maximum PF budget is four calls: one source PF for the attempt and at most one candidate PF per policy. Earlier gate failures or a first failed policy reduce the actual count. No diagnostic PF calls or retry of a failed policy is allowed. Execution must use a newly created private Attempt 005 output root; it must not reuse prior candidates or outputs.

## Validation evidence and limits

The locked Python 3.11.15 / PyPSA 1.2.4 environment has now passed 695 full-package tests and 192 focused PF/Fidelity/evidence/attempt tests, with zero failures. The evidence is bound to test repair commit/tree `32006039913aecb4ba9c46fc1b24e5e0a07b8014` / `740b93010b6d8cd53fe97ba87f4115269e531e05` and the repaired test-file SHA above. Strict metric and fresh-process replay tests passed 81/81; HARD admission tests passed 22/22. Tamper rejection, compileall, `git diff --check`, and offline lock checks passed.

The full suite reported 20,671 warnings: 20,670 `Pandas4Warning` instances from the locked PyPSA/pandas compatibility paths and one Joblib physical-core detection `UserWarning`. On the comparable common test set, Revision 2 and Revision 3 each recorded 670 passed tests and 16,703 warnings with matching categories/source counts; no numerical-calculation or evidence-integrity warning was identified. Warning regression is `NO` for the comparable test set. No warning filters were added.

The original Attempt 004 STOP evidence remains unchanged and invalid with recorded status `FAIL_STOPPED_FIRST_POLICY`. Projection fidelity remains `NOT VALIDATED`; physical equivalence remains `NOT CLAIMED`. No real-grid PF or candidate materialization was performed for this draft.

## Approval state

This amended revision remains a review artifact only. Independent acceptance of the freeze and separate approval/issuance of the Attempt 005 execution lease are required before any candidate construction, Beijing PF, fidelity evaluation, or report generation. Attempt 004 remains failed; these synthetic validation results do not promote it.
