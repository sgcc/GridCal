# PSDCalc Reduction 0.5.0 Post-Merge Baseline

## Purpose and frozen baseline

This document records the accepted post-merge capability baseline for the adaptive-reduction design. It does not change historical PR evidence.

| Item | Frozen value |
|---|---|
| Base branch | `codex/psdcalc-phase1-base` |
| Baseline commit | `4a147f0fa426bdd29ccf8b1787833e7a52c2e72d` |
| Baseline tree | `7557b2c6427f1bda03c30d2baed1a85e20e5b359` |
| Package | `psdcalc-reduction 0.5.0` |
| Static PF reduction milestone | `CLOSED` |
| Current validated fidelity layer | `REDUCTION_FIDELITY` |
| Projection fidelity | `NOT YET VALIDATED` |
| Physical equivalence | `NOT CLAIMED` |

The merge tree is the same as the final PR-022 tree. PR-022 was merged as a two-parent merge commit. This baseline records no semantic tree drift.

## Terminology and evidence boundary

- **STRUCTURAL_VALIDITY** reports whether declared network, mapping, topology, and structural invariants pass.
- **AC_MODEL_READINESS** reports whether the model is supported by the current AC PF executor.
- **PF_EXECUTION** reports solver execution outcome, including convergence or non-convergence.
- **REDUCTION_FIDELITY** compares a PyPSA S1 model with its reduced PyPSA S4 model at the same frozen operating point.
- **PROJECTION_FIDELITY** compares a source-model/reference result with its PyPSA S1 projection. It has not been validated.
- **PHYSICAL_EQUIVALENCE** is a broader physical-behavior claim. It is not established.

The PR-022 public label is `STATIC_SNAPSHOT_AC_PF_FIDELITY_WITHIN_PREREGISTERED_TOLERANCE`. It describes only static-snapshot PyPSA S1→S4 reduction fidelity under a frozen project experiment threshold set. It is not a synonym for “equivalent,” and it does not establish source-model projection fidelity, time-series behavior, contingency performance, OPF feasibility, or general physical equivalence.

## Validated capability inventory

“Real case” means the capability participated in an anonymous real-case workflow. It does not expand the claim beyond the stated evidence boundary.

| Capability | Implemented | Validated | Real case | Evidence | Boundary / next validation |
|---|---:|---:|---:|---|---|
| Stable source identity and sidecar | Yes | Yes | Yes | PR-020 anonymous summary; PR-021/022 evidence | Sidecar is authoritative; do not rely on PyPSA backend names for identity. |
| MappingBundle and composed mappings | Yes | Yes | Yes | PR-020 final named gates; PR-022 replay artifacts | Mapping correctness does not imply physical equivalence. Adaptive iterations need per-candidate map digests. |
| L0 source ledger, L1 projection, topology lineage | Yes | Yes | Yes | PR-020 summary: Beijing 639 L0 Lines, 504 L1 Lines, 135 absorbed-internal lineage rows | Keep source truth, projected view, and absorbed/derived lineage separate. |
| Multi-voltage structural quotient | Yes | Yes | Yes | PR-020, PR-021 and PR-022 structural reports | Voltage layers remain separate; Transformer terminals are singleton. |
| Voltage-aware topology simplification | Yes | Yes | Yes | PR-021 and PR-022 voltage-policy evidence | Does not support arbitrary topology transformations. |
| Protected parallel corridor identity | Yes | Yes | Yes | PR-020 summary: 189 protected Beijing source Lines preserved | Protection identity and structural checks are not contingency certification. |
| L+ association and terminal Shunt identity | Yes | Yes | Yes | PR-020: 18 Beijing associations and 10 Jilin associations passed | Source value is provenance; Shunt electrical semantics have a separate hash. |
| Transformer structural projection and identity | Yes | Yes | Yes | PR-017 readiness and PR-020 Transformer Gates | Includes projected three-winding T-leg rows; not a Transformer physical-equivalence result. |
| Voltage cutoff policy and resolved fingerprint | Yes | Yes | Yes | PR-021 seven-policy matrix | Cutoff is a protection policy; it does not remap or mutate nominal voltage. |
| Retained Bus/Line/device identity | Yes | Yes | Yes | PR-021 tests and PR-020 named Gates | Only declared protected objects receive this identity guarantee. |
| Structural Gate and source immutability | Yes | Yes | Yes | PR-020 named Gate results; PR-021/022 final status | Structural status remains independent from PF execution and fidelity. |
| AC electrical Line materialization | Yes | Yes | Yes | PR-022 line-equivalence implementation and seven-policy run | Unsupported or invalid equivalents fail closed. |
| One-to-one Line effective semantics | Yes | Yes | Yes | PR-022 corrected materialization evidence | Type-backed values are resolved through PyPSA before copying effective electrical values. |
| Many-to-one parallel-admittance equivalent | Yes | Yes | Yes | PR-022 post-fix Beijing matrix | Supports implemented parallel-Line cases only; it is not a general network equivalent. |
| AC model readiness validation | Yes | Yes | Yes | PR-022 seven-policy report | Readiness is not solver convergence. |
| PyPSA AC PF execution | Yes | Yes | Yes | PR-022: all seven S4 cases converged | Static snapshot and frozen operating point only. |
| Bus voltage magnitude and angle comparison | Yes | Yes | Yes | PR-022 fidelity report | Within the registered comparison mapping and tolerance contract. |
| Branch P/Q and cutset P/Q comparison | Yes | Yes | Yes | PR-022 fidelity report | Applies to mapped branches/cutsets and declared directions. |
| Injection and passive-loss comparison | Yes | Yes | Yes | PR-022 fidelity report | Does not validate other operating points or dynamic behavior. |
| PF evidence persistence and replay | Yes | Yes | Yes | PR-022 evidence store and replay | Replay reloads models, sidecars, mappings, structural reports, metrics and thresholds. |
| Frozen threshold-set provenance | Yes | Yes | Yes | `PR022_AC_PF_FIDELITY_V1` artifact | Project experiment thresholds, not IEEE or national-standard limits. |
| StorageUnit/Store input digest coverage | Yes | Synthetic tamper/replay validated | Real cases contained zero such devices | PR-022 replay evidence | Beijing S1/S4 StorageUnit and Store counts were all zero; nonzero impact was not tested in those cases. |
| Structural-report digest binding | Yes | Yes | Yes | PR-022 persistence/replay evidence | Replay reads actual report payloads and independently checks binding and digest. |
| Time-series PF | No | No | No | Not implemented | Requires separate snapshot, weighting, state and control contracts. |
| N-1 validation | No | No | No | Not implemented | Requires an explicit contingency set and security criteria. |
| OPF / UC | No | No | No | Not implemented | Out of scope. |
| VAR optimization / voltage-control equivalence | No | No | No | Not implemented | Control behavior is not certified by current PF fidelity. |
| VeraGrid PF fidelity | No | No | No | Not implemented | No VeraGrid PF comparison was run. |
| PSD/BPA→PyPSA projection fidelity | No | No | No | PR-017/020 are readiness and structural projection evidence only | Requires a validated source reference result and a source-to-PyPSA comparison contract. |
| Physical equivalence | No | No | No | Explicitly not claimed | No result in this baseline supports this claim. |

## PR-022 static reduction-fidelity evidence

PR-022 used anonymous Beijing S1 input with 1,527 Bus and 639 Line. After the electrical materialization correction, all seven policies passed the structural Gate and AC readiness, converged in PyPSA AC PF, and passed the preregistered static fidelity thresholds. CUT_115 and CUT_110 produced identical resolved policies and target digests; CUT_37 and CUT_35 did likewise.

The experiment threshold set was `PR022_AC_PF_FIDELITY_V1`, SHA-256 `4688f0f021f5aaabbfcb75bd1dcd8a1228753669dd415761d2d1b5471dac3f24`. It was frozen before real-case evaluation and was not changed after observing results. These are project experiment thresholds, not standards limits.

The historical pre-fix CUT_115 failure was traced to S4 materialization producing two active Lines with zero series impedance. The corrected implementation preserves effective one-to-one Line semantics, computes parallel-admittance equivalents for aggregated corridors, preserves total shunt admittance, and rejects unsupported equivalents. The final seven-policy evidence was regenerated/replayed after the correction; reported numerical metric drift was none. Historical non-convergent runtime is not evidence of speedup.

Package evidence at the frozen implementation records 449 passed and 0 failed offline tests. This review did not rerun tests or real cases.

## Next research boundary

PR-023A is scoped to heuristic adaptive refinement within **REDUCTION_FIDELITY**: starting from a PyPSA S1 network, reuse the frozen PF and metric contracts to attribute hard violations and selectively expand the reduced candidate. It must not be described as optimal reduction.

PSD/BPA→PyPSA **PROJECTION_FIDELITY** remains a separate design and evidence stream. Until it is validated, S1→S4 results must not be presented as a source-model fidelity result.
