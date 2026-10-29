# ADR-002: Read-only source network

Status: Accepted for the first batch, 2026-10-29.

## Decision

The caller's original `pypsa.Network` and input artifact are read-only. Load and inventory may inspect them; planning and materialization operate on a clone. Record source and output hashes. A changed original hash fails validation.

## Consequences

No in-place user network edits or monkey-patching. A failed run still writes manifest and evidence describing the stage and artifacts already produced.
