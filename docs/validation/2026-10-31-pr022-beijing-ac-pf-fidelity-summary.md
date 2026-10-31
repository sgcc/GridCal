# PR-022 Beijing AC PF Fidelity Summary

## 🧾 Scope and evidence boundary

This report records a static-snapshot AC power-flow comparison for an anonymous Beijing AC-only case. It separates structural validity, AC model readiness, and PF fidelity. The result is not a claim of physical equivalence.

The public fidelity label is **`STATIC_SNAPSHOT_AC_PF_FIDELITY_WITHIN_PREREGISTERED_TOLERANCE`**. The physical-equivalence claim is **NO**. The result does not establish time-series behavior, contingency performance, OPF feasibility, or equivalence under other operating points.

## 🧭 Run history and root cause

The historical pre-fix run had a converged S1 case but non-convergent reduced targets. The CUT_115 incremental diagnostic localized the first failure to S4 electrical materialization: S1 converged, L1 converged, and S3 made no additional Bus contraction; S4 materialization produced two active Lines with zero series impedance. Restoring both reactances restored convergence.

`ROOT_CAUSE=REDUCTION_PARAMETER_EQUIVALENCE_ERROR`

The corrected implementation now:

- retains effective source electrical semantics for one-to-one Lines;
- uses parallel-admittance equivalents for aggregated corridors;
- preserves total shunt admittance;
- fails closed for unsupported equivalents; and
- independently rejects invalid materialized Lines in AC readiness validation.

The final seven-policy matrix was rerun after the implementation fix. All seven S4 networks passed the structural Gate, passed AC readiness, converged in AC PF, and met the frozen fidelity thresholds. Historical non-convergent runtimes are excluded and are not used to claim speedup.

## 📏 Preregistered thresholds

| Field | Value |
|---|---|
| Threshold set | `PR022_AC_PF_FIDELITY_V1` |
| Threshold digest | `4688f0f021f5aaabbfcb75bd1dcd8a1228753669dd415761d2d1b5471dac3f24` |
| Frozen before real-case evaluation | `true` |
| Classification | Project experiment thresholds; not IEEE or national-standard limits |
| Changed after viewing case results | `false` |

The registered threshold definitions and provenance are in the [threshold-set record](2026-10-31-pr022-beijing-threshold-set-v1.json).

## 📊 Final seven-policy results

The source reference had 1,527 buses and 639 Lines. Compression percentages are computed against that S1 inventory. PF runtime is one additional post-fix S4 solve per policy and is diagnostic only.

| Policy | S4 Bus / Line | Bus compression | Line compression | S4 PF converged | Vm RMSE (p.u.) | Vm max (p.u.) | Cutset P max (MW) | Cutset Q max (MVAr) | Loss relative error | S4 PF runtime (s, diagnostic) | Fidelity status |
|---|---:|---:|---:|:---:|---:|---:|---:|---:|---:|---:|---|
| BASE | 1,392 / 399 | 8.8409% | 37.5587% | Yes | 5.3780e-05 | 1.9638e-04 | 7.3478 | 1.1963 | 1.1097e-04 | 0.3633 | `PF_FIDELITY_WITHIN_TOLERANCE` |
| CUT_330 | 1,392 / 399 | 8.8409% | 37.5587% | Yes | 5.3780e-05 | 1.9638e-04 | 7.3478 | 1.1963 | 1.1097e-04 | 0.3479 | `PF_FIDELITY_WITHIN_TOLERANCE` |
| CUT_230 | 1,395 / 507 | 8.6444% | 20.6573% | Yes | 5.5819e-05 | 2.2709e-04 | 7.3478 | 1.1963 | 1.1103e-04 | 0.3555 | `PF_FIDELITY_WITHIN_TOLERANCE` |
| CUT_115 | 1,525 / 637 | 0.1310% | 0.3130% | Yes | 4.8879e-06 | 3.4535e-05 | 7.3478 | 0.8391 | 8.1052e-05 | 0.4019 | `PF_FIDELITY_WITHIN_TOLERANCE` |
| CUT_110 | 1,525 / 637 | 0.1310% | 0.3130% | Yes | 4.8879e-06 | 3.4535e-05 | 7.3478 | 0.8391 | 8.1052e-05 | 0.3990 | `PF_FIDELITY_WITHIN_TOLERANCE` |
| CUT_37 | 1,525 / 637 | 0.1310% | 0.3130% | Yes | 4.8879e-06 | 3.4535e-05 | 7.3478 | 0.8391 | 8.1052e-05 | 0.3872 | `PF_FIDELITY_WITHIN_TOLERANCE` |
| CUT_35 | 1,525 / 637 | 0.1310% | 0.3130% | Yes | 4.8879e-06 | 3.4535e-05 | 7.3478 | 0.8391 | 8.1052e-05 | 0.3845 | `PF_FIDELITY_WITHIN_TOLERANCE` |

## ✅ Independent gate states

| Gate | Result |
|---|---|
| Structural status | `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID` for all seven policies |
| AC readiness status | `AC_MODEL_READY` for all seven policies |
| PF fidelity status | `PF_FIDELITY_WITHIN_TOLERANCE` for all seven policies |
| Public interpretation | Structural validity + AC model readiness + static-snapshot AC PF fidelity within preregistered tolerance |

Equivalent-cutoff determinism also held. `CUT_115`/`CUT_110` and `CUT_37`/`CUT_35` had identical S4 model and mapping digests, convergence, metrics, and fidelity status within each pair.

## 📦 Implementation and package evidence

| Evidence | Value |
|---|---|
| Tested implementation commit | `e32661df07fbb5e00f1009d4f46daa3fb921e0e6` |
| Tested implementation tree | `75c4e37bdd6dcc40e66b85986cd2cfe487250357` |
| Package version | `0.5.0` |
| Package metadata commit | `944adb982a7447d3f718f37ba1547215cea666e5` |
| Full offline package tests | 417 passed, 0 failed; 15,992 warnings; 63.71 s |
| Offline wheel PF/replay smoke tests | 34 passed, 0 failed; 48 warnings; 24.13 s |
| Offline lock check | Pass |
| Compileall | Pass |
| `git diff --check` | Pass |
| Fresh wheel-only install | Pass in a new Python 3.13.9 environment with locked dependencies installed offline |
| Installed package version | `0.5.0` |
| Installed PyPSA version | `1.2.4` |
| sdist SHA-256 | `67fc522c062867f2989c438635b5aaf6e313ec1c413d7376364baa3fe1e48c7b` |
| wheel SHA-256 | `055d9ef31dc4fdfdee292dc39181a0010a54f3dd28627ee05cca1afc0fbd9404` |
| sdist / wheel archive members | 130 / 93 |

Warnings were dominated by upstream PyPSA/Pandas deprecations, plus test-cache and existing dtype notices. No dependency versions were changed for the 0.5.0 package metadata update.

## 🚧 Fidelity boundary

The recorded statuses remain distinct: structural status, AC readiness status, and PF fidelity status. The result supports only a converged static-snapshot PyPSA AC PF comparison within the preregistered project thresholds. It does not establish **physical equivalence**.
