# PR-026 Stage 3: Beijing derived-mapping compatibility diagnostic

**Diagnostic execution: COMPLETED. Candidate compatibility: PASS.** This is a new, non-PF reconstruction diagnostic. The historical Stage2B result remains `FAIL / NOT_EXECUTED` with `UNJOURNALED_MANY_TO_ONE_BRANCH_MAP_EFFECT`; its evidence was not changed.

## Frozen input binding

The S1 model, sidecar, explicit source ledger, resolved protection, 135-action catalog, reduction configuration, operating-point contract, and frozen threshold artifact matched the Stage2B freeze. The private historical per-member RCA inventory matched its published SHA-256 `8f14808793456f8c361f96e5137d05fa201a39c9d853b7a85392d18b08e8d670` before source-member comparison. The ledger retained 18 L+ associations. Action readiness was 125 READY and 10 REJECTED, all rejected for `STRICT_SINGLETON_CONFLICT`. The canonical READY and REJECTED fingerprint sets matched the frozen identity baseline; only the 125 READY actions were activated.

| Binding | SHA-256 |
| --- | --- |
| S1 semantic model | `6339c7e08e09381a21fe39268362f718b9912aa2322456d033eaa5b912868150` |
| Sidecar | `7fc151484448d06fe430b43a8618503a0caed123b2c9530d8b9de19afb3c6165` |
| Explicit source ledger | `5293db89fee4cc9e773880afe8b5a17d00b75b78aa8cb5f15697d92170976bd7` |
| Resolved protection | `45a7be7cfa3070507c8c8615a4676a6c7ed4b0b04ee1c571f27fcb4d14186f38` |
| Action catalog | `70f5932d4d27b572425cadfb63cc79fe505f844ec9e49e81b5bbc08cab6fb99e` |
| Frozen threshold set | `4688f0f021f5aaabbfcb75bd1dcd8a1228753669dd415761d2d1b5471dac3f24` |

## Candidate reconstruction and closure

The committed PR-026 candidate builder materialized the exact 125-action partition without planning again. Its expected and actual derived-closure digests both equal `a3792678b1d7127cc03734a643c19029e91fd8ac05b7d9c86114365fa8c88066`. Source-member comparison against the private historical RCA accounted for **103/103 groups and 208/208 Lines**: 102 two-member groups and one four-member group. There were no additional many-to-one groups. The closure also contained 125 `ABSORBED_INTERNAL` effects, for 228 derived effects total. No original group was converted into a synthetic ReductionAction.

Missing effects, unexpected effects, membership mismatches, protected identity violations, L+ identity violations, and direct-action mapping mismatches were all zero. The MappingBundle covered every supported source object. The build-attempt fingerprint, partition digest, mapping digest, and candidate fingerprint are recorded in the companion JSON; candidate identity uses `adaptive-candidate-identity/v2` within the `adaptive-reduction-evidence/v2` envelope.

| Inventory | S1 | Candidate |
| --- | ---: | ---: |
| Bus | 1527 | 1402 |
| Line | 639 | 409 |
| Transformer | 1344 | 1344 |
| Link | 0 | 0 |
| Total branch | 1983 | 1753 |

Bus compression was **8.1860%** and branch compression **11.5986%**, diagnostic counts only. The persisted NetCDF, MappingBundle, sidecar, and ledger passed readback. The final structural status was `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID` with 52/52 checks; AC model readiness was `AC_MODEL_READY` with 5/5 checks. The S1 semantic signature remained unchanged. A fresh process independently rebuilt the same partition, closure, mapping digest, candidate fingerprint, statuses, and inventory.

## Claim boundary

No Beijing PF, fidelity evaluation, or adaptive controller ran. This diagnostic does not establish PF convergence or fidelity. `Projection fidelity = NOT VALIDATED`; `Physical equivalence = NOT CLAIMED`. Full source identities, equipment names, and raw private evidence remain outside tracked files.
