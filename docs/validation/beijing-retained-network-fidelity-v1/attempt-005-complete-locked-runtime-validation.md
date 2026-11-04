# Attempt 005 complete locked-runtime validation

_Date: 2026-11-04. All checks used synthetic/offline test data; no Beijing model or real-grid PF was run._

## Result

The two approved Python 3.11 arm64 wheels were independently rehashed and matched their exact `uv.lock` entries. With the existing uv cache and the supplied wheelhouse, the isolated Python 3.11.15 environment completed `uv sync --offline --locked --group dev`; all 67 selected locked dependencies matched. No dependency or lock changes were made.

The full package suite is **not clean**: 693 passed and one test failed. The failing test is `test_frozen_attempt004_entry_runs_three_synthetic_policies`. Revision 2 passes this test. Commit `d46f18d` changed the test from exercising the synthetic three-policy entry to expecting `FROZEN_IMPLEMENTATION_CHANGE_REQUIRED`, but the test creates its lease using the current `HEAD` as the frozen implementation commit/tree. In a clean committed tree the lease and source match, so the guard correctly does not raise. The failure is therefore a test fixture/assertion that depended on the patch being uncommitted; it does not establish a PF or digest defect. No test or implementation code was changed in this validation task.

## Locked runtime and artifact checks

| Check | Result |
| --- | --- |
| Runtime | Python 3.11.15, macOS 26.2 arm64 |
| uv | 0.11.17; `sync` supports `--offline`, `--locked`, `--group`, `--python`, and `--find-links` |
| Package | `psdcalc-reduction` 0.8.0, imported from the current checkout |
| Selected lock closure | 67 distributions; all installed versions match the Python 3.11/macOS arm64 lock selection |
| PyPSA / NumPy / SciPy / NetworkX | 1.2.4 / 2.4.6 / 1.17.1 / 3.6.1 |
| pandas / Polars / Pydantic / highspy | 3.1.0 / 2.1.0 / 2.14.1 / 1.15.1 |
| pytest / Hypothesis | 9.2.0 / 6.170.13 |
| PyArrow / netCDF4 / Pillow / PyProj | 23.0.1 / 1.7.5.1.post1 / 13.0.0 / 3.7.2 |
| `uv lock --check --offline` | PASS |
| Lock SHA-256 | `779d901329988f1f76e549a146a3c00a65c570b0cc0335b80ee281b717b55712` |
| Polars runtime wheel SHA-256 | `317314d65949f1d58c4939adf83aa0d2cfaf8da50fd4a725a2de27bde2c10018` — expected, lock, and file match |
| netCDF4 wheel SHA-256 | `e241c3e8c3af6a2e04a989bf80db31c580e4692ea6fc8f41d998ef1b2010fbcc` — expected, lock, and file match |

The uv sync installed 67 platform-selected locked distributions plus the local project distribution. No selected dependency remained missing.

## Test results

| Suite | Result | Runtime |
| --- | --- | --- |
| Full package suite, Python 3.11.15 | **693 passed, 1 failed, 20,671 warnings** | 192.23 s |
| Focused PF/Fidelity/evidence/Attempt/adaptive set | **190 passed, 1 failed, 4,479 warnings** | 276.67 s |
| Metrics + PF persistence/replay + numeric observations | **81 passed, 0 failed, 127 warnings** | 26.33 s |
| HARD-contract and retained-fidelity tests | **22 passed, 0 failed** | 11.11 s |
| Revision 2 common test set | **670 passed, 0 failed, 16,703 warnings** | 154.72 s |
| Revision 3 common test set | **670 passed, 0 failed, 16,703 warnings** | 156.52 s |
| `compileall` | PASS | — |
| `git diff --check` | PASS | — |

The common warning comparison used the same Python 3.11.15 environment, pytest configuration, and test files. The two changed test files (`test_pf_metrics.py` and `test_retained_fidelity_attempt_driver.py`) were excluded from that common-set comparison and exercised separately in the focused runs. Warning counts and categories were identical across the common set.

## Warning classification

The common set produced 16,702 `Pandas4Warning` and one `UserWarning` in both revisions:

| Source | Count | Message class |
| --- | ---: | --- |
| PyPSA `networks.py` | 266 | pandas `DataFrame.drop(inplace=...)` deprecation |
| PyPSA `network/io.py` | 301 | `future.infer_string` option deprecation |
| PyPSA `network/io.py` | 375 | pandas `DataFrame.set_index(inplace=...)` deprecation |
| PyPSA `network/transform.py` | 1,867 | pandas `DataFrame.drop(inplace=...)` deprecation |
| PyPSA `network/transform.py` | 13,890 | pandas `DataFrame.drop(inplace=...)` deprecation |
| Synthetic test fixture dtype assignment | 3 | assigning numeric value to boolean dtype |
| Joblib loky CPU discovery | 1 | physical-core count environment parsing; logical-core fallback |

The full Revision 3 run reported 20,670 `Pandas4Warning` plus one Joblib `UserWarning`. No warning was identified as involving metric arithmetic, evidence serialization, digest checks, or replay integrity. `WARNING_REGRESSION=NO` applies to the identical common test set; the changed test files have separate results and are not folded into that comparison.

## Replay and admission checks

- Canonical StableId-order metric tests passed.
- Synthetic save/load and independent-process replay tests passed.
- Strict metric/content digest validation and tampered-evidence rejection passed.
- Frozen HARD threshold and admission contract tests passed; no threshold or evaluator changes were made.
- PF calls in these tests were synthetic. No Beijing input was loaded and no real-grid PF was executed.

## Freeze decision and scope

Revision 3 remains `DRAFT`. Freeze approval is **not ready** until the deterministic test expectation is corrected in a separately reviewed change and the full locked suite passes. This task did not change source code, tests, dependency declarations, `uv.lock`, Revision 2/3 freeze files, thresholds, or historical Attempt evidence.

`LEASE_ISSUED=NO`; `REAL_GRID_PF_RUNS=0`; `ATTEMPT005=NOT_STARTED`; Stage2D was not run. The temporary pytest logs were retained outside the repository for this review. No third-party wheel or private model was committed.
