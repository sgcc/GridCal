# ADR-003: Sidecar stable identity authority

Status: Accepted for the first batch, 2026-10-29.

## Decision

`StableId(namespace, component, source_key)` is the persistent identity. An external sidecar maps each StableId to the PyPSA component name and is authoritative across NetCDF/CSV export and import. A custom `source_id` column can mirror this data but cannot be required for a round trip. Duplicate or missing sidecar bindings are errors.

## Consequences

Maps use StableIds, never array positions. A line absorbed inside a quotient cluster has mapping type `absorbed_internal` and no target line ID. The sidecar and its digest are versioned artifacts.
