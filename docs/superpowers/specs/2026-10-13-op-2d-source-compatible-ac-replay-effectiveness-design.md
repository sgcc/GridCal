# OP-2D Source-Compatible AC Replay Effectiveness Design

## Goal

Measure whether the already-frozen source operating-point overlays improve a
single, source-compatible VeraGrid AC replay for `BPA10056818` and
`BPA10056832`. This phase records evidence only; it does not claim exact
APFUltra operating-point reproduction.

## Scope and Boundaries

The implementation creates a standalone OP-2D runner and independent
validation artifacts. It uses one AC solve per variant with the same profile:
`tolerance=1e-6`, `max_iter=100`, stored MAP/source voltage initialization,
and no damping or case-specific tuning.

The runner must not call `run_source_replay_coupled_ac()`, run a mixed AC/DC
outer loop, alter frozen OP-2A/2B/2C behavior, modify the frozen mixed AC/DC
core, or write to Original/APFUltra sources. It must not allocate bus-level
generation across individual generators or infer switched-shunt, transformer
phase, or unresolved DC-terminal semantics.

## Variants

Each case is copied into three independent private grids.

| Variant | Private-grid state |
| --- | --- |
| A: `BASELINE` | Stored MAP `Vm`/`Va` initial guess only. |
| B: `OP_2A` | Variant A plus the existing aggregate bus-generation overlay and proven source-skipped cutset boundary. |
| C: `OP_2B` | Variant B plus the existing bus-control and transformer-tap initial-state overlay. |

Only existing OP-2A/2B builders may be used for their respective overlays.
All variants invoke the same AC-solver entry point with identical profile
values. A fresh private grid prevents one variant's loads, slack flags, or tap
settings from leaking into another.

## Comparison Contract

After each single AC solve, compare the final solved state with source/MAP
records in explicit domains: `Vm`/`Va`, bus-level `Pgen`/`Qgen`, branch
`P`/`Q`, proven cutset `P`/`Q`, bus/control state, and transformer tap state.
For each domain, emit `compared`, `skipped`, and `unresolved` coverage with
provenance. Unresolved source semantics remain unmodeled and are never filled
by a name-based assumption.

Improvement ratios compare the same source/target metric domain across A, B,
and C. Control state and tap state use state-specific equality/coverage
metrics; they are not inferred from fixed-voltage power residuals.

## Statuses

`SOURCE_OPERATING_POINT_AC_REPLAY_EXECUTION = PASS` only means both cases
completed A/B/C single AC solves with the uniform profile and reproducible,
complete artifacts. It does not establish physical reproduction.

`SOURCE_OPERATING_POINT_REPRODUCTION_EFFECTIVENESS` is classified only from
the recorded comparable metrics as `MATERIAL_IMPROVEMENT`,
`PARTIAL_IMPROVEMENT`, `NO_MATERIAL_IMPROVEMENT`, or `NOT_ESTABLISHED`.
Convergence alone cannot select any improvement classification.

## Verification

Focused tests must prove private-grid isolation, shared solver profile, no
outer-loop call, no individual-generator allocation, unresolved-field
exclusion, explicit domain coverage, same-domain improvement calculation,
state-specific control/tap metrics, immutable OP-2A/2B/2C artifacts, and an
unchanged frozen mixed AC/DC core hash.

