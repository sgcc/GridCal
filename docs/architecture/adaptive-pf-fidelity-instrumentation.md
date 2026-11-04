# Adaptive PF and Fidelity Observation Contract

## Status

Accepted for the local 0.8.0 development line, 2026-11-03.

## Scope and invariants

Instrumentation is observational only. It records the existing PyPSA executor result, the existing PR-022 metric set and frozen threshold resolution, adaptive stage outcomes, and monotonic durations. It does not call `Network.pf` a second time, recompute fidelity decisions, or feed observations back into scoring, admission, action selection, stopping, candidate identity, mapping identity, or derived-closure identity.

The report returned by `AdaptiveReductionController` and the candidate identity contract remain unchanged. `PerformanceTimingObservation` is runtime diagnostic evidence; duration values never enter a semantic digest or replay equality comparison.

## Public observation types

`NumericFidelityObservation` represents one authoritative PF execution or one authoritative metric/threshold comparison. It binds the source/candidate role, snapshot identity, PF statuses, per-subnetwork iteration/error values, metric rows, metric-set digest, resolved-threshold digest, source/candidate model digests, candidate identity, provenance, and field availability.

`PerformanceTimingObservation` records operation, role, candidate identity when applicable, monotonic duration in seconds, timing scope, run/process identity, backend/version, and availability. It separates executor end-to-end time from the measured interval around the single public `Network.pf` call. The latter is a nested solver-only duration, not an additional solve.

Every missing scalar uses an explicit `NOT_RECORDED` availability state. A value that does not apply uses `NOT_APPLICABLE`. Neither state is represented as zero. Numeric values must be finite and units are explicit.

Metric rows are projections of the existing `PowerFlowMetricSet`, `PowerFlowThresholdEvaluation`, and `ResolvedPowerFlowThresholds`. The row builder does not evaluate thresholds. Its status is copied from the authoritative evaluator's HARD/DIAGNOSTIC failure names; diagnostic metrics without a resolved scalar threshold are `NOT_APPLICABLE`, never PASS.

## Companion evidence and compatibility

The primary `adaptive-reduction-evidence/v2` artifact index is strict and remains byte/schema compatible. New runs add a separate `adaptive/numeric_observations.json` companion with schema `adaptive-numeric-observations/v1`. The companion is registered separately in `run_manifest.json`, with its exact relative path and SHA-256 in the manifest's auxiliary artifact digest map. The v2 `adaptive/evidence.json` artifact set and semantic digest are not changed.

Historical v2 runs with no companion continue through the existing replay path. The replay validates a present companion by checking its manifest path and file SHA-256, the companion payload digests, and freshly reconstructed numeric observations. Numeric observed values use the already-frozen `ReplayNumericTolerance`; metric-set and resolved-threshold digests and identity fields remain exact. Timing fields are type/range/scope checked and integrity-bound by the manifest, but are not compared with replay-time durations.

The companion contains canonical numeric observations, timing observations, controller-stage links, and separate numeric/timing payload digests. Its outer file SHA is stored in the run manifest. Runtime timing is excluded from the numeric observation digest.

## Timing boundaries

The recorded scopes are:

- source and candidate `PypsaAcPowerFlowExecutor.solve` end-to-end call, using the same boundary;
- the single `Network.pf` invocation inside the existing executor call, when that invocation occurs;
- candidate reconstruction total;
- candidate structural and AC-readiness validation;
- fidelity comparison plus frozen-threshold evaluation;
- attribution;
- adaptive controller total;
- evidence persistence through primary artifact registration, excluding the final self-hash write;
- fresh-process replay total.

All durations use `time.perf_counter()` and seconds. Solver-only duration excludes validation, clone, extraction, and surrounding executor work. If the executor rejects a request before invoking `Network.pf`, solver timing is explicitly `NOT_APPLICABLE`; if a custom executor cannot expose the boundary, it is `NOT_RECORDED`. One execution's timings are diagnostics only and do not establish speedup.

## Adaptive links and replay

The companion links numeric and timing observations to controller iteration/candidate identities and copies the controller's existing reconstruction, structural, readiness, PF, and Fidelity statuses. It does not revise the controller report. A candidate that does not enter PF has no PF metric observation; its existing controller stage status and its reconstruction/gate timing remain explicit.

Fresh replay rebuilds S1 candidates through the existing runtime, reuses the existing executor/evaluator/resolver, and compares companion numeric payloads. It validates timing observations but does not require elapsed durations to match. Metric or threshold tampering fails through the manifest-bound companion SHA and payload digest checks.

## Preservation requirements

The Stage2A, Stage2B, and Stage2C evidence files and private Stage2C execution artifacts are immutable. Stage2C remains `PASS / ACCEPTED`; fields that were originally `NOT_RECORDED` remain so. This implementation does not run Beijing, Stage2D, PF outside synthetic tests, or any new real-grid experiment. It does not change thresholds or third-party dependencies.
