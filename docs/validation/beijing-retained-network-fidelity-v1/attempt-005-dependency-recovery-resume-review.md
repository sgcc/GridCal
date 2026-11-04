# Attempt 005 dependency recovery resume review

_Date: 2026-11-04. Offline dependency recovery only; no tests or power-flow runs were started._

## Result

The user-supplied `polars-runtime-32==2.1.0` wheel was independently found and its SHA-256 matched the stated official digest and `uv.lock`. It was installed only into the isolated Python 3.11.15 environment at the isolated temporary Python 3.11.15 environment using uv's offline wheelhouse support. A subsequent required locked offline sync progressed past the earlier Polars and Pillow blockers and stopped at the next unavailable artifact, `netcdf4==1.7.5.1.post1`. No matching wheel was found in the approved Attempt 005 wheelhouse or the dedicated temporary uv cache.

Because the complete locked environment is unavailable, no package-version equivalence claim, test, replay, or warning comparison is made. No network access was used.

## Artifact and lock evidence

| Item | Result |
| --- | --- |
| Supplied Polars wheel | Present |
| Observed SHA-256 | `317314d65949f1d58c4939adf83aa0d2cfaf8da50fd4a725a2de27bde2c10018` |
| User-provided expected SHA-256 | Exact match |
| `uv.lock` Polars wheel SHA-256 | Exact match |
| `uv.lock` SHA-256 | `779d901329988f1f76e549a146a3c00a65c570b0cc0335b80ee281b717b55712` |
| Isolated Python | 3.11.15 |
| Polars runtime in isolated environment | 2.1.0 |
| Required locked sync | Failed closed at missing netCDF4 wheel |

The currently reported missing file is:

- Distribution/version: `netcdf4==1.7.5.1.post1`
- Locked artifact: `netcdf4-1.7.5.1.post1-cp311-abi3-macosx_15_0_arm64.whl`
- Locked SHA-256: `e241c3e8c3af6a2e04a989bf80db31c580e4692ea6fc8f41d998ef1b2010fbcc`
- Locked URL: `https://files.pythonhosted.org/packages/f7/85/4bf2ff1dc7ab58e777e44f6a25d196f3104b916b8836e4f749fe9e77b158/netcdf4-1.7.5.1.post1-cp311-abi3-macosx_15_0_arm64.whl`

The wheel was not found in the approved Attempt 005 wheelhouse or the dedicated temporary uv cache. Its hash is recorded from the lock only; there is no local file to verify. The earlier Pillow blocker report remains preserved as historical recovery evidence. This sync's progress beyond Pillow does not establish that Pillow or every other dependency is installed in the environment: the sync aborted before completing installation.

## Validation status

| Gate | Status |
| --- | --- |
| Polars artifact integrity | PASS |
| Python 3.11.15 interpreter | PASS |
| Exact locked dependency sync | FAIL — missing netCDF4 artifact |
| Full package suite | NOT RUN |
| Fidelity/PF evidence focused tests | NOT RUN |
| Synthetic fresh-process replay | NOT RUN |
| Strict digest validation | NOT RUN |
| Revision 2 / Revision 3 warning comparison | UNRESOLVED |
| Revision 3 freeze approval readiness | NO |
| Attempt 005 lease | NOT ISSUED |
| Real-grid PF calls | 0 |
| Attempt 005 | NOT STARTED |

No source code, dependency declaration, lockfile, thresholds, freeze contract, or historical Attempt evidence was changed. No wheel, environment, private model, or `.serena/` content is committed.

## Next required input

Provide the exact locked netCDF4 wheel through an approved local channel, then independently verify its SHA-256 against the lock before retrying `uv sync --offline --locked --group dev`. If synchronization exposes another missing artifact, record and resolve it through the same approved process. Do not run tests until the complete locked sync succeeds.
