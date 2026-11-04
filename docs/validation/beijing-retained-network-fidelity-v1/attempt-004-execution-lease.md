# Retained Fidelity V1: Attempt 004 execution lease

Status: **READY_NOT_EXECUTED**. This lease permits a future, separate execution of `BACKBONE_500 → BACKBONE_220 → BACKBONE_100` only after all frozen input and environment checks pass. It is not an AC power-flow result.

## Binding and scope

- The original experiment freeze remains `docs/validation/beijing-retained-fidelity-v1-freeze.json`, byte SHA-256 `b1a3bc72150063b7560d3bb2616e546c5f8020aa5db529645891a1931f5d9476`.
- Freeze revision 2 is `freeze-revision-2.json`; its byte SHA-256 is recorded in the companion lease JSON. It retains the source inputs, operating point, three policies, PF executor, PR-022 threshold set and retained-network diagnostic definitions. The original freeze's implementation SHA describes historical attempts only.
- The revised implementation is commit `e1f075d9923b6ed530603eb2676eb73d1c35131a`, tree `e3939bf275393244f2ce98396c30132c0b041b32`. Commit `c9021d1fbbd5926e78c7ef3810e52f01d1279a8d` repaired the report exporter's typed-field access. The later commit connected reporting, numeric observations, timing and replay to the experiment driver. Neither commit changed PF parameters, scientific metric definitions, thresholds, admission, model mapping or adaptive selection.
- The three driver-file byte digests, all 22 parser-file digests, locked Python 3.11.15 / PyPSA 1.2.4 environment, other locked package versions, configuration template and frozen S1/sidecar/ledger identities are bound in `attempt-004-execution-lease.json` and the revision 2 freeze. A mismatch stops before creating a run directory.
- The synthetic full end-to-end preflight is `report-repair-full-e2e-preflight.json`, with its SHA-256 bound in the lease. Three distinct voltage policies were built from the same frozen synthetic S1, each reached structural and AC-readiness Gates, underwent actual PyPSA source/candidate PF, produced whole-network and retained-network diagnostics, numeric/timing observations, all report exports, and fresh-process replay. This validates the workflow, not Beijing convergence or fidelity acceptance.

## Future execution rules

Attempt 004 must use a newly created private output root reserved for Attempt 004. No previous candidate or run directory may be used as input. The source PF may be reused within this attempt; each policy reconstructs a fresh candidate. On the first failed policy, retain its evidence, stop, and label later policies `NOT_RUN`. Replay loads durable PF and report artifacts and verifies their contents and digests without another PF solve. Timing values are checked for validity but excluded from semantic equality, fingerprints and admission.

Per-policy artifacts include NetCDF, sidecar, Parquet maps, structural/readiness evidence, PF source/candidate records, fidelity and retained diagnostics, `adaptive-numeric-observations/v1`, `retained-fidelity-stage-timings/v1`, and the comprehensive JSON, Markdown and four CSV files. The attempt root also contains a digest-bound cross-policy comparison. The numeric observation schema is used as an experiment projection with no fabricated adaptive action journal.

Attempts 001, 002 and 003, including their STOP evidence, remain byte-for-byte unchanged. This lease does not start Beijing PF, the adaptive controller or Stage2D. Projection fidelity remains **NOT VALIDATED** and physical equivalence remains **NOT CLAIMED**.
