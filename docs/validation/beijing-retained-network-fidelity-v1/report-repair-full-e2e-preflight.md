# Retained Fidelity report repair: synthetic full E2E preflight

Status: **PASS for synthetic execution only**. This record does not contain a Beijing PF result. The source freeze and Attempts 001–003 remain immutable.

## Type contract checked before export

| Field | Model/runtime type | Export contract |
| --- | --- | --- |
| `FidelityObservation.severity` | `str` literal (`HARD` or `DIAGNOSTIC`) | Format directly; no `.value` access. |
| `sample_count` | `int \| None` | Preserve integer in JSON and CSV; display missing as `NOT_RECORDED`. |
| `mapping_coverage` | `float \| None` | Preserve numeric fraction in JSON and CSV. |
| `observed`, `threshold` | `EvidenceValue` | Read typed `value`, `state`, `unit`; never replace missing with zero. |
| `outcome` | `MetricOutcome` enum | Read `.value` only for the enum. |
| `margin` | Numeric observation value | Kept in the independent `adaptive-numeric-observations/v1` companion; it is not a comprehensive-report field. |

The repaired exporter keeps the report JSON schema and numerical meaning unchanged. A nonempty HARD/DIAGNOSTIC report now exports JSON, Markdown, metrics CSV, voltage-layer CSV, retained-fidelity CSV, and policy-comparison CSV.

## Contract coverage matrix

| Requirement | Authoritative API | Driver call | Durable artifact | Test and result |
| --- | --- | --- | --- | --- |
| Frozen S1, sidecar, source ledger, parser and lease | `execute_from_frozen_inputs`, `ParallelSourceLedger` | Validate digests, exact revision and synthetic preflight bytes before building | Input bindings, `resolved_config.yaml`, run manifest | Synthetic Attempt 004 entry, tampered lease and tampered preflight: PASS/fail closed. |
| One source PF and three independent candidates | `PypsaAcPowerFlowExecutor.solve`, `MultiVoltageQuotientPipeline.run` | One source solve, then fresh pipeline per policy | `source-pf.json`, `pf/source.json`, `pf/candidate.json`, L1/L2 and maps | Spy counted 1 + 3 solves; three-policy synthetic: PASS. |
| Mapping and structural/AC Gate | `MappingBundle`, pipeline structural and readiness results | Persist, reload, and verify each candidate | Parquet maps, sidecar, structural/readiness JSON, candidate provenance | Three policies structurally valid and AC ready; map tamper fails closed. |
| Whole-network fidelity | Existing comparison, frozen threshold resolver and evaluator | Compare solved S1/S4 without changing admission | `metrics/fidelity.json`, `pf/binding.json` | Two synthetic policies outside tolerance, one within; none relabeled as globally valid. |
| Retained devices and interfaces | `compare_retained_network_fidelity` | Read-only grouped diagnostics | `metrics/retained_fidelity.json` | Preserved Bus/Line/Transformer samples and two matched interfaces in the compressing synthetic policy: PASS. |
| Numeric observations | `NumericObservationBundle`, PF and fidelity projectors | Project saved PF and metric inputs; no adaptive journal fabricated | `metrics/numeric_observations.json` | Nonempty rows, metric/threshold digest binding, forged metric plus manifest hash rejected. |
| Timing | `PerformanceTimingObservation`, `perf_counter` | Measure solver-only, executor total, reconstruction, fidelity, report, replay; leave separately unavailable Gate timing unrecorded | `metrics/numeric_observations.json`, `metrics/stage_timings.json` | Finite nonnegative checks and scope/readback tests: PASS. Included durations are never summed into an alleged total. |
| Report exports and cross-policy comparison | `build_comprehensive_run`, `export_report_bundle` | Build from authoritative PF, mapping and Gate results | Six per-policy exports and six cross-policy exports with manifests | Nonempty actual-PF export and cross-policy tamper tests: PASS. |
| Independent replay | `verify_policy_artifacts`, `verify_numeric_projection`, `verify_cross_policy_report` | New process reloads NetCDF, Parquet, PF and reports without solving PF | Candidate provenance, bound manifest and replay timing | Fresh-process replay of each policy: PASS; numeric, retained and cross-policy tamper fail closed. |
| Stop semantics | `_write_stop`, frozen policy order | Stop at first failure; later policies `NOT_RUN` | `attempt-stop.json` | Source PF nonconvergence and second-policy failure: PASS. Candidate nonconvergence has no fidelity admission. |
| Historical evidence | Byte SHA-256 audit | Read-only comparison | Original freeze and Attempts 001–003 | Four original digests unchanged; no historical data rewritten. |

## Synthetic acceptance and limits

The full package suite passed on Python 3.11.15: **693 passed, 0 failed**. Python 3.13.9 focused reporting/driver regression passed: **15 passed, 0 failed**. `compileall`, `git diff --check`, and `uv lock --check --offline` passed.

The local test sdist and wheel were built from the repaired 0.8.0 package. A separate virtual environment installed only the wheel; import resolved from its `site-packages`, nonempty report export passed, and the installed schema resource matched the source resource. The offline cache lacked `annotated-types==0.8.0`, so dependency installation in that separate environment did not complete. The report smoke used the already verified locked Python 3.11 dependency path. This is a local package check, **not a new 0.8.0 release claim**.

No Beijing PF, adaptive controller, or Stage2D run was performed. `FULL_E2E_PREFLIGHT=PASS` applies to the synthetic experiment flow and evidence contracts. It does not assert that any Beijing policy will converge or meet fidelity thresholds.
