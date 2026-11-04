# Retained Fidelity V1: Attempt 003 execution lease

**Status: `READY_NOT_EXECUTED`.** This lease supplements the original
`BEIJING_RETAINED_NETWORK_FIDELITY_V1` three-policy freeze. It authorizes a new
attempt after the Attempt 002 preflight conflict. It does not report a Beijing
power-flow result or an accepted reduction.

| Attempt | Historical state | Meaning |
| --- | --- | --- |
| 001 | `FAILED_EVIDENCE_SERIALIZATION` | First execution stopped during PF evidence serialization. Its STOP evidence is unchanged. |
| 002 | `FAILED_PREFLIGHT_CONTRACT_CONFLICT` | The one-policy lease and historical candidate input conflicted with the three-policy freeze. No Beijing PF was run. Its lease and STOP evidence are unchanged. |
| 003 | `READY_NOT_EXECUTED` | A separate ordered three-policy execution is authorized, subject to all runtime identity checks below. |

## Frozen identities

The original freeze is
`docs/validation/beijing-retained-fidelity-v1-freeze.json` at commit
`741a6e9f7411176b321e6becd51749bcbc6ffa16`, with file SHA-256
`b1a3bc72150063b7560d3bb2616e546c5f8020aa5db529645891a1931f5d9476`.
The package implementation remains commit
`30d8c3e2cb75732a8e2fe151d1fd50ea3319344c`, tree
`a40d8623dcee5e1004603b95cb93afc9790823de`, package version `0.8.0`.
The separate experiment-driver fix is commit
`3755f59d4b8741670d4a644a61684a75ae3ed4b5`, tree
`e0df5e817d7ea1043d90ba88a00b2b4597b64057`.
The driver and launcher byte hashes, frozen input byte hashes, and all remaining
contract digests are specified in the companion JSON lease. Runtime must check
them before creating a new output directory.

The frozen source DAT SHA-256 is
`09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e`.
The S1 model semantic digest is
`6339c7e08e09381a21fe39268362f718b9912aa2322456d033eaa5b912868150`;
the sidecar digest is
`7fc151484448d06fe430b43a8618503a0caed123b2c9530d8b9de19afb3c6165`;
the explicit source ledger digest is
`5293db89fee4cc9e773880afe8b5a17d00b75b78aa8cb5f15697d92170976bd7`.
The original 22-file parser manifest must be checked file by file, with
aggregate manifest SHA-256
`609458cff5e87e710261ba5dbad9ba82872e0dfe9b832de4d20a13a2f950a596`.
The runtime is Python `3.11.15`, PyPSA `1.2.4`, and the unchanged locked
`psdcalc-reduction 0.8.0` environment.

## Ordered policy authorization

| Order | Policy | Cutoff, kV | Resolved retained layers, kV | Policy fingerprint | Semantic config digest | Effective protection digest |
| --- | --- | ---: | --- | --- | --- | --- |
| 1 | `BACKBONE_500` | 500 | 525 | `9bc79f4ffc72574e402701b965009b4b53ef2868f3ffcfe3d48dce0d457ee11a` | `37e33807332de8db1c7091bad2dbc07a12be0011a219a765004d97c7d9bf9f22` | `90fa5fcaa497ae096f9d8aa13d1ecf5142bcf1a4b6ad5c0877f5e57ed579c1d3` |
| 2 | `BACKBONE_220` | 220 | 230, 525 | `d1e41e3787653b83050758f352f86d6847514b207818ad84a393c3886127840d` | `5c224eeb145b3b78cae1a7ab6b8fc9a4658ba16002bd56773cbf4b4552901a4e` | `7baecd611eb313e90255c8bbcda68ff90ee3d294ac07a6449a5b760e0749bb77` |
| 3 | `BACKBONE_100` | 100 | 115, 230, 525 | `620dc3dba2b46e65ea7dd6cccb5b64d9b70897c9f284fa2527977829e77ea0fd` | `f4711573087fce3688f0e22addf38f63ceb687a9f709633b4a23bc9556454990` | `c40fd93c94c8f6e059df74cdab9f9f71de709041c8cd33c78d24a2bc967aa119` |

Each policy starts from the same immutable frozen S1, sidecar, and source
ledger. It resolves the existing cutoff and protection contracts, then runs
the existing multi-voltage pipeline to create a **new** candidate and
MappingBundle. Neither an Attempt 001 candidate nor a previous policy's target
is accepted as input. Source PF may be reused only within Attempt 003 when its
source identity and operating point are identical; target PF is never reused.
Historical Bus counts are not candidate inputs or acceptance criteria.

## Gates, measurement, and replay

Each new candidate binds experiment and attempt IDs, source byte and semantic
identities, policy and effective protection, semantic config, planner and seed,
candidate model, mapping digest, plan bytes, and structural/readiness report
bytes. Existing final structural Gate must be
`MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID`; AC readiness must be
`AC_MODEL_READY` before candidate PF. The frozen PF executor contract digest is
`221369358669457b9ebe013fb6443fdff008585b93d718f302ad814df3e29ac6`,
with its original operating-point digest
`334a7275a2e837722016db2b732d23e2c30ce392e3b1f592ade5d5f995706c1e`.

The PR-022 threshold set remains `PR022_AC_PF_FIDELITY_V1`, SHA-256
`4688f0f021f5aaabbfcb75bd1dcd8a1228753669dd415761d2d1b5471dac3f24`.
The metric policy digest remains
`745cdb4b5cdb6d8547e1ac9139be988b6cd393b9e1f7838bfb245aa32f6db5ad`.
Retained metrics are **diagnostic only** and use
`retained-network-metrics/v1`. The comprehensive report contract is
`psdcalc-comprehensive-reduction-report/v1`; numeric observations use
`adaptive-numeric-observations/v1`. The JSON lease binds the exact contract
digests and the per-policy durable artifact layout. No new metric, threshold,
PF solver setting, planner, voltage mapping, or protection rule is authorized.

At formal execution, create a new private Attempt 003 output root and a
separate subdirectory per ordered policy. Persist the candidate NetCDF,
sidecar, MappingBundle, config, source ledger binding, plan, structural and AC
readiness reports, PF results, fidelity and retained metrics, and binding
records. Fresh-process replay reloads actual artifacts and recomputes model,
mapping, policy, report, PF, and threshold identities without calling PF.
Numeric comparison tolerances remain absolute `1e-12` and relative `1e-10`;
timing does not enter semantic identity or numeric equality.

Input drift, unsupported components, policy/protection/provenance mismatch,
structural or AC readiness failure, source PF nonconvergence, and evidence
integrity failure stop the ordered attempt. A failed policy is recorded as
`FAILED`; later policies are `NOT_RUN`. Candidate PF nonconvergence is recorded
as a distinct PF outcome and cannot produce fidelity admission. Structural,
AC readiness, PF, and fidelity states remain separate. No physical equivalence
is claimed.

This lease contains only anonymous counts, contract names, and hashes. Raw
model data, device identities, private paths, MappingBundle rows, and PF arrays
remain outside the repository. Attempt 003 has not materialized a Beijing
candidate or run Beijing PF.
