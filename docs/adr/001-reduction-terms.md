# ADR-001: Reduction terminology

Status: Accepted for the first batch, 2026-10-29.

## Decision

Use `simplification` for local structural edits, `aggregation` for busmap quotient construction, and `equivalencing` for boundary-response preserving elimination. First-batch PyPSA output is `aggregation`. API, record, CLI and report must not call it a Ward or electrical equivalent.

## Consequences

The operation type is explicit in records. Simplification and equivalencing remain unsupported until separate increments implement and validate them.
