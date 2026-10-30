# PR-020 real-case structural readiness

Status: **FAILED before the multi-voltage structural pipeline**. No candidate or accepted compression result exists.

The programmatic offline suite passed before the private source was read. The Beijing `BPA10056814` DAT/REF/MAP scan, PSDCase AC parse, and PSDCase-to-PyPSA projection completed. The source audit reproduced 194 genuine parallel corridors, 191 double-circuit corridors, three corridors with at least three members, and 91 parallel-only cycles. L+ false parallels, adapter duplicates, unresolved corridors, known DC cards, and unsupported active cards were all zero. The DAT SHA-256 was `09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e`.

The strict audited BPA source profile requires a nonempty Circuit ID on every L-card Line. The fresh source scan found **183 L cards with a blank Circuit ID**; no nonblank DAT/PSDCase Circuit ID mismatch was found. `build_bpa_source_ledger` therefore raised `BackendCapabilityError` before topology simplification, planning, quotient materialization, persistence, or the final Gate. A programmatic regression test records this fail-closed behavior. This is a source-profile contract conflict, not evidence of Line loss by the quotient.

| Case | Scenario | S0 Bus | S0 Line | S2 Bus | S3 Bus | Accepted Bus | Final state |
|---|---|---:|---:|---:|---:|---:|---|
| BPA10056814 | BASE | 1527 | 639 | — | — | — | FAILED: required Circuit ID blank |
| BPA10056814 | LOW_115 | 1527 | 639 | — | — | — | NOT_RUN after source gate failure |
| BPA10056814 | LT110 | 1527 | 639 | — | — | — | NOT_RUN after source gate failure |
| BPA10056819 | BASE / low-voltage | — | — | — | — | — | NOT_RUN; Beijing must finish first |

The source view contains 96 two-winding Transformers, 416 three-winding groups, and 1248 original three-winding T legs; these are separate counts. It also contains 56 Generators, 219 Loads, 117 fixed Shunts, 17 BE objects, nine L+ cards, and 35 inactive unmatched REF X records. None of these counts is a compression acceptance result.

No PR-019 algorithm, `cycle_corridors` Gate, source model, or physical calculation was modified. Next review must decide how the strict source-profile Circuit ID contract applies to blank L cards before rerunning the real pipeline. The original DAT cards and device identities are excluded from this public summary.

`PHYSICAL_FIDELITY_NOT_YET_EVALUATED`
