# Retained Fidelity V1: Attempt 004 STOP

**ATTEMPT_004_EXECUTION = FAIL_STOPPED_FIRST_POLICY.** The authoritative execution reached `BACKBONE_500` and stopped at `FRESH_PROCESS_REPLAY_INVALID`. `BACKBONE_220` and `BACKBONE_100` are `NOT_RUN`; the cross-policy report is `NOT_RUN`. This is an anonymous summary of the private durable artifacts. The companion JSON contains aggregate voltage-layer counts, numeric diagnostics and artifact SHA-256 bindings; it contains no source device identities or private paths.

The exact source commit/tree, original freeze, revision 2, Attempt 004 lease, synthetic full E2E preflight, driver bytes, 22 parser files, locked Python 3.11.15 / PyPSA 1.2.4 environment, DAT/REF, frozen S1, sidecar and source ledger passed their entry checks. An initial command used a relative freeze-file path from the package directory and failed before reading the freeze, creating an output root or invoking PF. The corrected command started the formal run in a new private directory. Both invocation logs are retained privately and bound by SHA-256 in the JSON.

## First-policy stage results

| Stage | Observed result |
| --- | --- |
| Source PF | `PF_CONVERGED`; one subnetwork, 5 iterations, final error `1.419087716618256e-9` |
| Fresh candidate, MappingBundle and structural Gate | Completed; `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID`; structural mapping coverage `1.0` |
| AC readiness | `AC_MODEL_READY` |
| Candidate PF | `PF_CONVERGED`; one subnetwork, 6 iterations, final error `6.467274374231142e-10` |
| Whole-network Fidelity | Stored evaluation `PF_FIDELITY_WITHIN_TOLERANCE`, **diagnostic and not accepted** because durable replay failed |
| Retained-network diagnostics | Persisted; preserved-backbone Bus/branch samples `1392 / 1558`, mapping coverage `1.0` |
| Numeric observations and report exports | 3 numeric observations, 6 timing observations, and all six comprehensive export files persisted; report content reload passed |
| Independent fresh-process replay | **FAIL** at exact metric equality/digest check; no replay PF call |

The constructed candidate, **not an accepted compression result**, had Bus `1527 → 1392` (8.8409%), Line `639 → 399` (37.5587%), Transformer backend rows `1344 → 1344` (0%), and total branches `1983 → 1743` (12.1029%). Transformer rows include backend projection legs; physical three-winding unit count is `NOT_RECORDED`. No accepted Bus or branch compression is reported.

The stored PR-022 HARD diagnostics were Vm max `0.0001963802709357143 pu` versus `0.01 pu`, Vm RMSE `5.3780459403704873e-5 pu` versus `0.005 pu`, cutset P max `7.347785465051743 MW` versus `37.7 MW`, cutset Q max `1.1963330126860683 MVAr` versus `10.806389513775056 MVAr`, and relative passive-branch loss difference `0.000110974407528934` versus `0.02`. Their stored status remains unaccepted because of the replay Gate.

For the retained backbone, Vm RMSE was `4.957787940019917e-5 pu`; Va RMSE `0.027602084887974887 degree`; preserved Line P/Q RMSE `0.7000204348019538 MW / 0.12131382763633891 MVAr` across 428 terminal samples each; preserved Transformer P/Q RMSE `0.3573745259714882 MW / 0.058211299664516346 MVAr` across 2688 terminal samples each. The interface count was zero, so interface P/Q error and mapping coverage are `NOT_RECORDED`, not zero error. These grouped metrics are diagnostic only.

Recorded diagnostic timings were source PF solver/end-to-end `0.236559 / 0.410209 s`, candidate reconstruction `57.822069 s`, candidate PF solver/end-to-end `0.221728 / 0.377601 s`, whole-network fidelity `0.148159 s`, retained diagnostics `1.780521 s`, and report generation `2.565422 s`. Fresh replay failed, so its duration is `NOT_RECORDED`. These scopes overlap and are not summed or presented as speedup.

## Stop cause and evidence boundary

Read-only replay of persisted PF results reproduced the failure. Three passive-loss/balance metric scalars differed from their saved values by `5.684341886080802e-14 MW`, `1.2307049910426082e-16` in relative loss, and `3.637978807091713e-12 MW`. The recomputed metric digest therefore differed from the saved digest. The frozen threshold digest and the within-tolerance decision matched; the exact-equality replay contract still **failed closed**. The private `attempt-stop.json` records `BACKBONE_500=FAILED` and both later policies `NOT_RUN`; its generic `RetainedAttemptError` code is supplemented by the specific `FRESH_PROCESS_REPLAY_INVALID` traceback in the bound private log.

No code or threshold was changed, no policy was retried, and no Attempt 005 or Stage2D run began. Attempts 001–003 and their original freeze evidence remain unchanged. Projection fidelity is **NOT VALIDATED**; physical equivalence is **NOT CLAIMED**. The next step is a separate review of numeric replay canonicalization and its experiment-freeze consequences before any new real-case execution.
