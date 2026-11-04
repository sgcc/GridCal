# Retained Fidelity V1: Attempt 005 lease application draft

_Application material for independent review; no execution authority is granted._

---

## Request status

| Item | Value |
| --- | --- |
| Attempt | `BEIJING_RETAINED_NETWORK_FIDELITY_V1_ATTEMPT_005` |
| Freeze | Revision 3 draft, pending independent review |
| Implementation | `d46f18dafdd5af3e83458e642b62d8e6a2325a12` / `66c897e6357f09b3dff690166cfdfff96843cf75` |
| Request status | `DRAFT_PENDING_FREEZE_REVIEW_AND_APPROVAL` |
| Lease issued | `NO` |
| Attempt authorized | `NO` |

The machine-readable application follows the existing `retained-fidelity-execution-lease/v1` field vocabulary but is explicitly labeled `LEASE_APPLICATION_NOT_ISSUED_LEASE`. It binds the amended Revision 3 draft SHA-256 `8a10f480f72457ecf96708781ba114277a62801b540cc32284a3e95393ae24ee`; it must not be used by the experiment launcher as an active lease.

## Bound inputs and software

The application binds the revision 3 draft and its revision 2 parent, original freeze, source DAT/model, S1 sidecar, source ledger, base protection, operating point, metric policy, PR-022 threshold artifact, package version, implementation commit/tree, and package lock SHA-256. Source and model digests are inherited from the reviewed freeze; this offline preparation did not load or rehash private source files.

The proposed execution runtime is the Revision 2 locked environment: Python `3.11.15`, PyPSA `1.2.4`, and the exact recorded distribution manifest and lock digest. In this environment the repaired test commit passed 695 full-package tests and 192 focused tests with zero failures; strict digest/replay passed 81 tests, and HARD admission passed 22 tests. This evidence validates the amended test binding but does not issue execution authority.

## Requested execution bounds

- Policy order: `BACKBONE_500` → `BACKBONE_220` → `BACKBONE_100`.
- Start each policy from the same immutable frozen S1, sidecar, and ledger.
- Stop at the first hard gate failure; later policies are `NOT_RUN`.
- Generate cross-policy comparison only after all required policy gates and clean-process replays pass.
- Maximum PF calls: four total (one source, up to three candidate calls); no diagnostic, replay, retry, or recovery PF calls.
- Create a new private Attempt 005 output directory at execution time; do not reuse earlier candidate models or outputs.
- Preserve all failure evidence. Digest mismatch, metric/threshold tampering, evidence serialization/replay failure, or nonconvergence follows the frozen stop behavior.
- Do not run Stage2D or an adaptive controller.

## Claim boundary

This application does not authorize a claim of projection fidelity or physical equivalence. Those remain `NOT VALIDATED` and `NOT CLAIMED`, respectively. A passing offline preflight is not a lease and does not authorize real-grid execution.

The JSON companion `attempt-005-execution-lease-application.json` is a draft only. Revision 3 still requires independent freeze approval, and the lease application requires a separate review and issuance. A new independently reviewed and issued lease must be produced before execution.
