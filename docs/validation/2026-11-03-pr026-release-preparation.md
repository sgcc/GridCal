# PR-026 Stage 4: 0.8.0 release preparation

**Release preparation: PASS.** This report binds package validation to the PR-026 implementation while preserving the separate Beijing Stage 3 diagnostic. No Beijing power flow, fidelity evaluation, adaptive controller, or Stage2C execution occurred in this stage.

## Lineage and version boundary

| Milestone | Commit or tree |
| --- | --- |
| Phase base | `68bd37f1bc49e2bc31fa327fe4e66bf43e9398f7` |
| Stage2B RCA | `78693a4d1b8d9e58d2bda03f45806f5e5c773291` |
| PR-026 design | `ef9ee66065e94029a0f5c4b9102c6d568cdcbe48` |
| PR-026 implementation | `bdb513a2033d5163d959b37ca6a9d0a10f957fc5` |
| Tested implementation tree | `92aaf5a01b2cdb155eff64dd3f689e8636095840` |
| Stage 3 evidence | `9d48e56366e57ca166544d9e276bbf801991609c` |
| Stage 3 evidence tree | `4241e2f47fcf7dada169a85d49bc5f3974435bfa` |

Stage 3 changed only the two public compatibility evidence files. Its Beijing structural diagnostic ran under the 0.7.0 development version. Stage 4 changes the package's own version declarations to **0.8.0**; it does not change the derived-closure implementation, grouping adapter, candidate identity, replay, controller, or dependency versions. The Stage 3 result is not relabeled as a 0.8.0 Beijing execution. Historical Stage2A and Stage2B evidence remains unchanged; Stage2B remains a failed, unaccepted reconstruction attempt.

## Frozen Stage 3 structural compatibility

The frozen catalog had 135 actions: 125 READY and 10 REJECTED for `STRICT_SINGLETON_CONFLICT`. Source-member comparison matched 103/103 historical parallel groups and 208/208 source Lines: 102 groups of two and one group of four. There were zero additional parallel aggregation groups and 125 internal branch absorptions. The expected and actual derived-closure digest was `a3792678b1d7127cc03734a643c19029e91fd8ac05b7d9c86114365fa8c88066`. Direct-action mapping passed; protected identity and L+ identity violations were zero.

The candidate held 1,402 Buses, 409 Lines, 1,344 Transformers, no Links, and 1,753 total branches. Bus compression was 8.1860%; branch compression was 11.5986%. Its structural Gate passed 52/52 checks with `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID`; AC readiness passed 5/5 with `AC_MODEL_READY`. Fresh-process reconstruction and NetCDF, mapping, sidecar, and ledger readback passed. These are structural and readiness diagnostics, not PF or fidelity results.

## 0.8.0 package Gates

A fresh locked source environment used Python 3.13.9, PyPSA 1.2.4, and highspy 1.15.1. `uv lock --check --offline` and `uv sync --offline --group dev` passed. The complete clean source suite passed **623 tests, 0 failed, 0 skipped, 16,665 warnings** in 283.60 seconds. The existing locked development environment independently produced the same 623/0 result. Focused derived-closure, candidate, protection, controller, replay, source-ledger, and protected-parallel tests passed **143 tests, 0 failed** in 109.72 seconds. `compileall` and `git diff --check` passed.

| Distribution | SHA-256 | Archive members | Regular files |
| --- | --- | ---: | ---: |
| `psdcalc_reduction-0.8.0.tar.gz` | `a5c1332cab4d5f5e8d8b1295db6fc90878503fe0a335de30800e07bd279bac38` | 206 | 179 |
| `psdcalc_reduction-0.8.0-py3-none-any.whl` | `5807566ea4450bddceb3061d2e89e8c09c0cb6501ccd4597d8ed69d0f9261d8d` | 108 | 108 |

The isolated wheel environment installed the exact locked dependency versions and imported `psdcalc-reduction 0.8.0` from `site-packages`. Its copied, source-independent test suite passed **618 tests, 0 failed**, with five source-tree-only comparisons deselected. Those five are `test_schema_resource_matches_model_and_source`, the three parameterizations of `test_example_resource_matches_source`, and `test_packaged_threshold_resource_is_identical_to_frozen_source`. The wheel's schema, three YAML examples, and frozen threshold resource were separately byte-compared to the maintained source files; the schema also matched the Pydantic model. The separate clean sdist environment installed successfully and passed the version, public adaptive API, derived-mapping adapter, dependency-version, and packaged-resource smoke checks.

A subsequent rebuild after drafting this report had the same archive member names and identical contents for every member in both formats. Only archive-container bytes, such as timestamps, differed. The SHA-256 values above identify the original archives that were installed and tested; the report itself is excluded from the package source manifest.

Archive member names and contents were inspected. Neither archive contains private model files, raw ledger data, private run outputs, tracked `.serena`, private local paths, or unexpected large binaries. Existing source-profile labels and the public frozen threshold resource are code/resource names, not model contents. The PR ancestry deliberately includes anonymous Stage2B freeze/failure/RCA documents; they remain clearly marked as failed evidence and contain no private source identities. No unrelated commit or accidental user file was found.

## Claim boundary and next stage

Allowed claims: `DETERMINISTIC_DERIVED_MAPPING_CLOSURE_IMPLEMENTATION_VALIDATED` and `BEIJING_INITIAL_CANDIDATE_STRUCTURAL_COMPATIBILITY_VALIDATED`.

Beijing AC PF convergence, reduction fidelity acceptance, adaptive controller success, projection fidelity, physical equivalence, and global optimality are not claimed. **Projection fidelity = NOT VALIDATED. Physical equivalence = NOT CLAIMED. Stage2C = NOT_RUN.** Stage2C requires a new, separately frozen Beijing PF, fidelity, and controller experiment after this release preparation is reviewed.
