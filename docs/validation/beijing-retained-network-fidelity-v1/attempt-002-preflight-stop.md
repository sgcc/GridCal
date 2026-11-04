# Retained Fidelity Attempt 002: preflight stop

Attempt 002 stopped before its synthetic preflight and before any Beijing PF.
The exact Git head/tree, freeze ancestry, driver remediation ancestry, original
STOP hashes, and driver source hashes matched their leases. Its separate
private output directory did not exist.

Two contracts prevent the requested three-policy execution:

1. The original freeze orders `BACKBONE_500`, `BACKBONE_220`, and
   `BACKBONE_100`, while the committed Attempt 002 lease authorizes
   `BACKBONE_500` first and states that other policies are
   `NOT_AUTHORIZED_BY_THIS_ATTEMPT_LEASE`. The current request requires all
   three within this attempt and instructs stopping on an unexplained contract
   conflict.
2. The leased local execution driver reads the previous attempt's persisted
   candidate as an input. The current execution contract requires each
   candidate to be built independently from frozen S1. Driver hashes match
   the lease, so this is a contract mismatch, not an unreviewed file change.

No synthetic preflight, source or candidate PF, strategy materialization,
Fidelity evaluation, report generation, manifest creation, or replay ran in
Attempt 002. All corresponding statuses remain `NOT_RUN`. Attempt 001 remains
`FAILED`; its STOP evidence and the original freeze were not changed. The
latest 681-pass package suite belongs to the earlier driver remediation and
is not an Attempt 002 execution result.

Resolution requires a separate reviewed driver and execution-lease revision
that explicitly authorizes the intended policy scope and binds independent
candidate reconstruction. This STOP record contains no model, device
identity, raw PF value, or private filesystem path.

`Projection fidelity = NOT VALIDATED`

`Physical equivalence = NOT CLAIMED`
