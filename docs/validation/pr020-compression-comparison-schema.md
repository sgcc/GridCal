# CompressionComparisonReport contract

The report is one immutable object rendered to JSON, Markdown, and seven Parquet tables. `S0_PSD_BPA_SOURCE` is the source-device view; `S1`–`S4` are PyPSA materializations. S3 is a memory candidate. S4 is the exported and reloaded network checked by the final structural Gate.

| Section | Source | Interpretation |
|---|---|---|
| Stage inventory | BPA inventory/PSDCase and PyPSA public tables | Source groups and projected Transformer legs remain separate |
| Voltage layers | Pipeline opportunity evidence | Counts and structural floors by canonical voltage |
| Device transition | Composed `MappingBundle` | One row per source object and mapping type |
| Energy scale | Static and snapshot-weighted input attributes | Aggregation/inventory statistic only |
| Topology | Active same-layer Line graph | Simple and multigraph cycle ranks are distinct |
| Parallel corridors | Existing protected-parallel evidence | Exact member and semantic Gate counts |
| Runtime | Measured phases, with `NOT_AVAILABLE` for unavailable splits | No estimated cost is presented as measured |
| Final Gate | Named final structural checks | The sole source of structural acceptance |

`candidate_*` values may be present when the final Gate is invalid. `accepted_*` values exist only for `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID`. A preflight or source-provenance failure cannot produce a final structural status or accepted compression. Reports reject absolute paths, encoded StableIds, and caller-supplied private tokens before writing any public file.

`PHYSICAL_FIDELITY_NOT_YET_EVALUATED`
