# PR-020 real-case structural readiness

Status: **FAILED before the multi-voltage structural pipeline**. No candidate or accepted compression result exists.

The programmatic offline suite passed before the private source was read. The Beijing `BPA10056814` DAT/REF/MAP scan, PSDCase AC parse, and PSDCase-to-PyPSA projection completed. The source audit reproduced 194 genuine parallel corridors, 191 double-circuit corridors, three corridors with at least three members, and 91 parallel-only cycles. L+ false parallels, adapter duplicates, unresolved corridors, known DC cards, and unsupported active cards were all zero. The DAT SHA-256 was `09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e`.

The fresh identity audit found **183 L cards with a blank Circuit ID**: 182 in single-Line corridors and one in a parallel corridor. All 639 Lines had one unique DAT L-card line binding through PSDCase to PyPSA; duplicate card binding and endpoint mismatch counts were zero. None of the 183 blank-ID Lines had an L+ association. The opt-in audited BPA contract now preserves the blank value when its source card is unique and no corridor has more than one blank member. Programmatic tests cover both acceptance and rejection cases.

The byte-level L+ audit used the PSDCase parser's exact terminal slices, `raw[33:38]` and `raw[43:48]`. Its 18 terminal fields classify as **11 finite source values and seven explicit blank raw fields**; numeric-raw/`None` parse mismatches and malformed fields are zero. The anonymous classification digest is `0d74c089f6d11650a4c4fd2bfe08e9948e81038c4215089afcad2672491a25a0`. The source ledger now stores `finite` with its value or `blank` with `None`, validates raw against parsed values, and round-trips both states. It does not infer values from PyPSA Shunt.b.

Beijing BASE subsequently passed the source ledger (639 Line entries and 18 L+ terminal associations), then failed at the existing L1 provenance check. Topology simplification produced **1392 Bus and 504 Line** from S1's **1527 Bus and 639 Line**. The ledger still covers all original Lines, while L1 has absorbed 135; its validation raised `IdentityError: Circuit provenance references an absent Line`. The run manifest is `FAILED`. No quotient candidate or final Gate exists. Resolving the source-ledger projection across topology absorption requires separate review; the check was not bypassed.

| Case | Scenario | S0 Bus | S0 Line | S2 Bus | S3 Bus | Accepted Bus | Final state |
|---|---|---:|---:|---:|---:|---:|---|
| BPA10056814 | BASE | 1527 | 639 | 1392 | — | — | FAILED: source ledger references absorbed L1 Line |
| BPA10056814 | LOW_115 | 1527 | 639 | — | — | — | NOT_RUN after source gate failure |
| BPA10056814 | LT110 | 1527 | 639 | — | — | — | NOT_RUN after source gate failure |
| BPA10056819 | BASE / low-voltage | — | — | — | — | — | NOT_RUN; Beijing must finish first |

The source view contains 96 two-winding Transformers, 416 three-winding groups, and 1248 original three-winding T legs; these are separate counts. It also contains 56 Generators, 219 Loads, 117 fixed Shunts, 17 BE objects, nine L+ cards, and 35 inactive unmatched REF X records. None of these counts is a compression acceptance result.

No PR-019 protected-parallel algorithm, `cycle_corridors` Gate, source model, or physical calculation was modified. Next review must define how authoritative source Line provenance is projected onto a topology-simplified L1 that has absorbed source Lines. The original DAT cards and device identities are excluded from this public summary.

`PHYSICAL_FIDELITY_NOT_YET_EVALUATED`
