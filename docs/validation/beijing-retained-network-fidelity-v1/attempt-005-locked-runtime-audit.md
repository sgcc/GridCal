# Attempt 005 locked-runtime and warning audit

_Offline environment and freeze-contract audit on 2026-11-04. No PF or test suite was run._

---

## Result

`LOCKED_RUNTIME_UNAVAILABLE`. The required Python 3.11.15 interpreter exists, but the exact locked dependency environment could not be installed from the offline cache. The independent environment sync stopped before pytest started because the locked macOS arm64 wheel for `polars-runtime-32==2.1.0` was absent. The stop rule was followed: no Python 3.13 test substitution, network access, dependency change, or PF execution.

| Gate | Result |
| --- | --- |
| Python 3.11.15 interpreter | PASS, interpreter only |
| Exact locked dependency environment | UNAVAILABLE |
| Dependency lock identity | PASS; current lock SHA matches Revision 2 |
| Full suite and focused tests | NOT RUN |
| Synthetic fresh-process replay and tamper checks | NOT RUN |
| Warning regression comparison | UNRESOLVED |
| Revision 3 static contract review | PASS; this is not freeze approval |
| Freeze approval ready | NO |
| Attempt 005 lease issued | NO |

## Frozen environment contract

The exact versions below were read from Revision 2's locked environment fields and verified against its lock digest. PF uses `PypsaAcPowerFlowExecutor` through public `pypsa.Network.pf`, Newton–Raphson, tolerance `1e-8`, no slack distribution, source-P-set slack weights, and the PyPSA 1.2.4 public default iteration limit. Diagnostic execution is not allowed to add PF calls.

| Component | Frozen version |
| --- | --- |
| Python | `3.11.15` |
| PyPSA | `1.2.4` |
| NumPy | `2.4.6` |
| SciPy | `1.17.1` |
| NetworkX | `3.6.1` |
| pandas | `3.1.0` |
| Polars | `2.1.0` |
| Pydantic | `2.14.1` |
| highspy | `1.15.1` |
| pytest | `9.2.0` |
| Hypothesis | `6.170.13` |

The locked `packages/psdcalc-reduction/uv.lock` SHA-256 remains `779d901329988f1f76e549a146a3c00a65c570b0cc0335b80ee281b717b55712`, matching Revision 2. This confirms the lock identity; it does not prove that every locked wheel is available locally. Bound evidence components remain `adaptive-numeric-observations/v1`, `retained-fidelity-stage-timings/v1`, `retained-fidelity-cross-policy/v1`, `psdcalc-comprehensive-reduction-report/v1`, and `retained-fidelity-candidate-provenance/v1`; their schemas and digest checks are unchanged from Revision 2.

The host is macOS 26.2 on arm64. The standalone interpreter at `~/.local/bin/python3.11` reports Python 3.11.15 but has none of the locked project dependencies installed. Other discovered Python 3.11 environments do not match: one contains a development PyPSA 0.18 build and older NumPy/SciPy/highspy; another has no PyPSA and mismatched NumPy, pandas, Pydantic, highspy, and pytest. The package virtual environments are Python 3.13.9 and are not acceptable substitutes.

## Offline environment attempt

An isolated Python 3.11.15 environment was created under a temporary directory. Running `uv sync --offline --locked --group dev` resolved 74 packages, then exited with status 1 because the local cache lacked:

```text
polars_runtime_32-2.1.0-cp311-abi3-macosx_11_0_arm64.whl
```

The command was offline. The lock file was not modified, and the test suite did not start. The missing artifact is the environment blocker; this is not a test failure or implementation failure.

## Test, replay, and integrity evidence

Because the exact locked environment could not be assembled, this audit did not run the full suite, focused Fidelity/PF-evidence/Attempt tests, StableId order regression, fresh-process replay, digest/tamper checks, compileall, or a fresh lock command. No test result is inferred from Python 3.13.9. The prior fix-review evidence remains a historical report only and is not promoted to frozen-runtime validation.

No source model, candidate, Beijing network, or PF solver was invoked. Real-grid PF calls in this audit: `0`.

## Warning baseline

The previous Python 3.13.9 report recorded `19,766` warnings but did not retain category counts. It is not comparable to the required Python 3.11.15 run. Neither a current locked-runtime warning report nor a Revision 2 same-environment baseline could be produced because the required locked dependency environment is unavailable.

Therefore `WARNING_REGRESSION=UNRESOLVED`. No warning reduction or regression is inferred from the non-comparable total. The machine-readable warning report leaves current counts, baseline counts, and the difference table unset rather than fabricating values. No warning filters or test behavior were changed.

## Revision 3 static contract review

The static comparison found the following Revision 2 bindings unchanged in Revision 3:

- hard threshold payload, threshold set ID/version/artifact digest, and metric-policy digest;
- DIAGNOSTIC_ONLY metric set and retained metric contract;
- PF executor contract and operating-point digest;
- input identity bindings;
- metric/evidence schemas and strict fresh-process replay requirement;
- policy definitions and order: `BACKBONE_500`, `BACKBONE_220`, `BACKBONE_100`;
- stop-at-first-hard-failure behavior and Attempt 004's failed status.

Revision 3 binds the canonical-summation implementation commit/tree and source digest. It adds no projection-fidelity or physical-equivalence claim. Static contract review is `PASS`, but independent approval is not granted. Attempt 004 remains `FAIL_STOPPED_FIRST_POLICY`; it is not changed to PASS.

## Decision

Revision 3 remains `DRAFT`. It is **not ready for independent freeze approval** because the exact locked runtime tests and a same-environment warning comparison are incomplete. The next prerequisite is restoration of the locked `polars-runtime-32==2.1.0` artifact in an approved offline cache, followed by the specified tests and warning comparison in the exact Python 3.11.15 environment.

The Attempt 005 lease remains `NOT_ISSUED`; Attempt 005 remains `NOT_STARTED`. No real-grid input or evidence was modified. Projection fidelity remains `NOT VALIDATED`; physical equivalence remains `NOT CLAIMED`.

## Machine-readable evidence

- `attempt-005-locked-runtime-audit.json`
- `attempt-005-warning-baseline.json`
