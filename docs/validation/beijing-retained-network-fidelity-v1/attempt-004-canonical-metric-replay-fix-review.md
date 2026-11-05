# Attempt 004 canonical metric replay fix review

## Outcome

`CANONICAL_ORDER_FIX = PASS`. The metric implementation now traverses StableIds in the same order as the PF evidence serializer: `StableId.encode()` string order. It retains Python `float`, built-in `sum`, the existing formulas, and the `1e-6 MW` loss denominator floor. For passive-branch losses, each `branch_p0` value is paired with `branch_p1` by that same StableId.

The old Attempt 004 lease must continue to reject this implementation. The synthetic lease test now asserts `FROZEN_IMPLEMENTATION_CHANGE_REQUIRED` and verifies that execution stops before creating an output directory. This preserves the requirement for a newly reviewed freeze revision and execution lease before any future real-grid run.

## Change summary

In `metrics.py`, `_passive_branch_loss` now sums Line and Transformer terminal P values in sorted encoded-ID order. `compare_power_flow_results` now sums source and target `bus_p_mw` in the same order. No PF executor, serializer, map, reducer, threshold artifact, digest field, or metric definition changed.

The synthetic order regression builds Line and Transformer StableIds, computes metrics and their full metric digest, then reverses the source dictionaries while keeping key/value pairs fixed. It verifies canonical ordering and exact metrics/digest equality. The PF objects are constructed directly, so this regression invokes no solver. Existing retained evidence tests exercise PF-result serialization, strict digest rejection, and child-process replay; the retained Fidelity driver tests also run their established small synthetic PF fixtures and clean-process replay.

The suite initially had one failure in the Attempt 004 lease preflight test. It tried to execute a lease after the frozen implementation source had changed. The code correctly refused with `FROZEN_IMPLEMENTATION_CHANGE_REQUIRED`; the test now checks that fail-closed result instead of expecting the stale lease to proceed.

## Verification

| Check | Result |
| --- | --- |
| PF metrics + retained PF evidence + retained Fidelity driver tests | `29 passed / 0 failed` |
| Canonical-order regression on final synthetic values | `1 passed / 0 failed` |
| Full package suite | `694 passed / 0 failed` (Python 3.13.9 development environment; 19,766 existing dependency/test warnings) |
| Full-suite first run | `693 passed / 1 expected stale-lease assertion failure`; after updating that test to require fail-closed behavior, the full suite passed |
| `compileall` (`src` and `experiment_tools`) | PASS |
| `git diff --check` | PASS |
| `uv lock --check --offline` with writable temporary cache | PASS |
| Beijing PF / Attempt 005 | `0 / NOT_STARTED` |

The 29-test focused run included existing independent-process replay and tamper rejection coverage. The final exact synthetic insertion-order test was rerun separately after its values were tightened. No real-grid model was loaded for PF and no PF solver was called by the canonical-order test or implementation.

## Contracts and historical evidence

All formulas, inputs, threshold limits, HARD/DIAGNOSTIC classifications, metric fields, and digest field sets remain unchanged. `CONTRACT_CHANGE_REQUIRED = NO`. The code change only makes floating-point addition deterministic under the already-required serialized key order.

Attempt 004's PF, fidelity, and STOP artifacts were not edited or re-signed. The prior failed evidence remains failed. Projection fidelity remains `NOT VALIDATED`; physical equivalence remains `NOT CLAIMED`.

Before a future real-case attempt, its new freeze revision must bind the post-fix implementation commit/tree and the `metrics.py` source digest, describe canonical encoded-StableId summation as an observability/replay determinism correction, and keep the existing operating point, thresholds, and metric formulas. The attempt must use a new lease and a new output directory. This report does not authorize that attempt.

## Files changed

- `packages/psdcalc-reduction/src/psdcalc/reduction/fidelity/metrics.py`
- `packages/psdcalc-reduction/tests/test_pf_metrics.py`
- `packages/psdcalc-reduction/tests/test_retained_fidelity_attempt_driver.py`
- `docs/validation/beijing-retained-network-fidelity-v1/attempt-004-canonical-metric-replay-fix-review.md`

## Remaining limits

The validation covers synthetic PF evidence only. Attempt 004 remains invalid and cannot be retroactively made valid by this implementation. No new execution freeze or real-grid run was performed.
