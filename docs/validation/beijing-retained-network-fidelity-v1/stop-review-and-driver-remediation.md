# Retained Fidelity V1 STOP review and driver remediation

Status: `REVIEWED; DRIVER FIXED; ATTEMPT_002 NOT EXECUTED`

## STOP review

The committed `stop-summary.md` and `stop-summary.json` remain the authoritative
record for Attempt 001. Their SHA-256 values before and after this remediation
are respectively `57d753473d677f69b8ba5b6af851c195f7df876d96ce555241d6b8ac3619b607`
and `fd6d4f4301d086e084f963d79d14eeac92aee2876b20f26dd34c036f6c55ef3e`.

The source, parser, and locked-environment identity gates passed. The
`BACKBONE_500` candidate passed the final structural Gate and AC readiness.
The source PF executor invoked the solver once and returned a
`PowerFlowExecutionResult`. The temporary real-case driver then raised
`AttributeError` while reading `PowerFlowSubnetworkStatus.ordinal`, before it
persisted a PF result. The authoritative status field is
`subnetwork_ordinal`.

This is an `EVIDENCE_SERIALIZATION_INTERFACE_MISMATCH` in the temporary
execution driver. It is not evidence of PF convergence or nonconvergence. The
source PF numerical status remains `NOT_RECORDED`; it must not be labeled
`PF_NOT_CONVERGED`. Candidate PF, fidelity evaluation, replay, and
`BACKBONE_220` / `BACKBONE_100` were not run. The private run area had no saved
PF result and no separate persisted traceback file; the committed STOP record
preserves the failure classification and point of termination.

## Remediation

The read-side driver now uses the authoritative `subnetwork_ordinal` contract
through `experiment_tools.retained_pf_evidence`. That strict adapter preserves
status, convergence, snapshot identity, every subnetwork row, StableId-keyed
solved values, solver metadata, and failure reason in
`retained-pf-execution-result/v1` JSON with a recomputed SHA-256. Missing or
unknown fields, non-finite values, invalid StableIds, and digest mismatches
fail closed. No compatibility fallback maps `ordinal` to a default.

The private runner now places Attempt 002 outputs in a separate output
namespace and treats Attempt 001 candidate artifacts as read-only inputs. This
change only affects evidence capture and output placement. The package source
under `src/`, PyPSA PF executor, PF call parameters, metric definitions,
thresholds, structural Gates, mappings, and controller were not changed.

## Verification

All new tests use synthetic PyPSA networks. They cover actual one- and
two-subnetwork PF execution, source and candidate observation roles, status and
iteration/error round trips, missing values, nonconvergence, malformed payload
rejection, tamper detection, fresh-process replay, numeric-observation
consistency, and timing separation. Each synthetic `solve` produces one solver
timing callback; no diagnostic PF call was added.

Under the locked Python 3.11.15 / PyPSA 1.2.4 environment, the focused driver
tests passed `5/5`; retained-network and comprehensive-report tests passed
`40/40`; PF/Fidelity tests passed `107/107`; adaptive/replay tests passed
`192/192`; and the full package suite passed `681/681` with zero failures.
The full suite emitted 16,703 dependency/test warnings, primarily PyPSA and
Pandas deprecations; a small number concerned test-host CPU discovery and
Pandas dtype assignment. `compileall`, offline lock validation, and
`git diff --check` passed. Package version remains `0.8.0`; `uv.lock` and all
earlier experiment evidence are unchanged. No Beijing PF, adaptive controller,
or real Stage2D experiment ran during this remediation.

## Claim boundary

`ATTEMPT_001 = FAILED` remains permanent. This remediation does not recover its
missing source PF result and does not establish retained-network fidelity.

`Projection fidelity = NOT VALIDATED`

`Physical equivalence = NOT CLAIMED`

Attempt 002 has a separate execution lease in
`attempt-002-execution-lease.json` and remains unexecuted.
