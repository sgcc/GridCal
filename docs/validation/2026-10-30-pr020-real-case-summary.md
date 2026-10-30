# PR-020 real-case structural readiness

Status: **Beijing BASE passed the final multi-voltage structural Gate**. LOW_115, LT110 and Jilin remain unrun under the current follow-up scope.

The programmatic offline suite passed before the private source was read. The Beijing `BPA10056814` DAT/REF/MAP scan, PSDCase AC parse, and PSDCase-to-PyPSA projection completed. The source audit reproduced 194 genuine parallel corridors, 191 double-circuit corridors, three corridors with at least three members, and 91 parallel-only cycles. L+ false parallels, adapter duplicates, unresolved corridors, known DC cards, and unsupported active cards were all zero. The DAT SHA-256 was `09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e`.

The fresh identity audit found **183 L cards with a blank Circuit ID**: 182 in single-Line corridors and one in a parallel corridor. All 639 Lines had one unique DAT L-card line binding through PSDCase to PyPSA; duplicate card binding and endpoint mismatch counts were zero. None of the 183 blank-ID Lines had an L+ association. The opt-in audited BPA contract now preserves the blank value when its source card is unique and no corridor has more than one blank member. Programmatic tests cover both acceptance and rejection cases.

The byte-level L+ audit used the PSDCase parser's exact terminal slices, `raw[33:38]` and `raw[43:48]`. Its 18 terminal fields classify as **11 finite source values and seven explicit blank raw fields**; numeric-raw/`None` parse mismatches and malformed fields are zero. The anonymous classification digest is `0d74c089f6d11650a4c4fd2bfe08e9948e81038c4215089afcad2672491a25a0`. The source ledger now stores `finite` with its value or `blank` with `None`, validates raw against parsed values, and round-trips both states. It does not infer values from PyPSA Shunt.b.

An explicit L0→L1 projection now keeps the complete 639-row source ledger separate from the 504-row L1 preserved-source view. Authoritative topology maps account for **504 one-to-one preserved Lines and 135 absorbed internal Lines**; no generated L1 Line appeared in BASE. All 189 initially protected Lines and all 18 L+ terminal associations kept their required L1 identities. The lineage and projected ledger were persisted separately and read back. The fresh BASE run reached NetCDF export/reload and the final Gate, which returned `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID` with no failed checks. Its accepted Bus count is **1392**, an 8.84086444% structural Bus reduction from S1. This is a structural result only.

| Case | Scenario | S0 Bus | S0 Line | S2 Bus | S3 Bus | Accepted Bus | Final state |
|---|---|---:|---:|---:|---:|---:|---|
| BPA10056814 | BASE | 1527 | 639 | 1392 | 1392 | 1392 | MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID |
| BPA10056814 | LOW_115 | 1527 | 639 | — | — | — | NOT_RUN after source gate failure |
| BPA10056814 | LT110 | 1527 | 639 | — | — | — | NOT_RUN after source gate failure |
| BPA10056819 | BASE / low-voltage | — | — | — | — | — | NOT_RUN; Beijing must finish first |

The source view contains 96 two-winding Transformers, 416 three-winding groups, and 1248 original three-winding T legs; these are separate counts. It also contains 56 Generators, 219 Loads, 117 fixed Shunts, 17 BE objects, nine L+ cards, and 35 inactive unmatched REF X records. None of these counts is a compression acceptance result.

No PR-019 protected-parallel algorithm, `cycle_corridors` Gate, source model, or physical calculation was modified. LOW_115, LT110 and Jilin were deliberately not resumed. The original DAT cards and device identities are excluded from this public summary.

`PHYSICAL_FIDELITY_NOT_YET_EVALUATED`
