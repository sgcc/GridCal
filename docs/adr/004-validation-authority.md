# ADR-004: Validation authority

Status: Accepted for the first batch, 2026-10-29.

## Decision

PR-001 through PR-010 can emit only `STRUCTURALLY_VALID` or `STRUCTURALLY_INVALID`. Structural checks cover maps, protected identities, terminals, and connectivity per original connected component/subnetwork. No first-batch report claims physical feasibility, convergence, accuracy, or full-grid acceptance.

## Consequences

Full-grid independent validation is a later capability and has its own evidence contract. The presence of a PyPSA quotient network does not imply it passes power flow.
