# ADR-006: Public PyPSA compatibility boundary

Status: Accepted for the first batch, 2026-10-29.

## Decision

All PyPSA spatial clustering calls go through `psdcalc.reduction.backends.pypsa.compat`. It invokes only `network.cluster.spatial.busmap_by_kmeans`, `busmap_by_hac`, `busmap_by_greedy_modularity`, and `get_clustering_from_busmap`. If a public accessor or method is absent, it raises `BackendCapabilityError`; it never imports PyPSA private modules. K-means receives an explicit seed.

## Consequences

The tested resolution is captured in `packages/psdcalc-reduction/uv.lock` (initial PyPSA version 1.2.4). Upgrade work must rerun contract and integration tests. If a private API becomes unavoidable, a separate ADR and one compatibility module change are required before use.
