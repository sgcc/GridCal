# Attempt 005 offline dependency recovery review

_Local artifact lookup on 2026-11-04. No network access, tests, or PF were used._

---

## Result

`ARTIFACT_ACQUISITION_REQUIRED`. The user-provided Polars runtime wheel was independently rehashed and installed into the isolated Python 3.11.15 temporary environment using uv's offline wheelhouse support. The subsequent locked sync exposed another missing locked artifact, Pillow 13.0.0. No Pillow wheel was found in accessible local caches or wheelhouse locations, and this task does not authorize external acquisition. Recovery stops here. Revision 3 remains `DRAFT`, the Attempt 005 lease remains `NOT_ISSUED`, and Attempt 005 remains `NOT_STARTED`.

| Check | Result |
| --- | --- |
| Required Python 3.11.15 interpreter | PASS |
| Polars runtime wheel found | YES — supplied approved wheelhouse |
| Polars runtime SHA-256 verified | YES |
| Pillow 13.0.0 wheel found | NO |
| Exact frozen dependency environment | UNAVAILABLE — sync stops at Pillow |
| Tests and strict replay | NOT RUN |
| Warning regression | UNRESOLVED |
| Real-grid PF calls | 0 |

## Requested artifact

| Field | Value |
| --- | --- |
| Distribution | `polars-runtime-32` |
| Version | `2.1.0` |
| Wheel | `polars_runtime_32-2.1.0-cp311-abi3-macosx_11_0_arm64.whl` |
| Platform tag | `cp311-abi3-macosx_11_0_arm64` |
| Official source | `https://files.pythonhosted.org/packages/fe/38/07066430ac655a8cab2f9e96ea1fec6ca1f20ec47844afe117662d43ace0/polars_runtime_32-2.1.0-cp311-abi3-macosx_11_0_arm64.whl` |
| Required SHA-256 | `317314d65949f1d58c4939adf83aa0d2cfaf8da50fd4a725a2de27bde2c10018` |
| SHA-256 in `uv.lock` | `317314d65949f1d58c4939adf83aa0d2cfaf8da50fd4a725a2de27bde2c10018` |
| Lock entry agreement | PASS |

The Polars runtime locked tag matches the available Python 3.11.15 interpreter and macOS 26.2 arm64 host. Its independently calculated SHA-256 is `317314d65949f1d58c4939adf83aa0d2cfaf8da50fd4a725a2de27bde2c10018`, matching both the supplied official digest and the lock entry. Pillow is also locked for the current host as `pillow-13.0.0-cp311-cp311-macosx_11_0_arm64.whl`, SHA-256 `37290fbef0c73e2d51d594eb78fded7dc7170d1c3ad0a890269e6fd8c8c4e061`; no file was found to verify.

## Local search

The search covered the configured uv cache at `~/.cache/uv`, uv's local share directory, project package and wheelhouse locations, Downloads, Codex worktrees, temporary storage, Volumes, work/look/Documents locations, the Conda package cache, and Homebrew's local tree. No matching Pillow wheel filename was found. No original wheel was reconstructed from an installed package directory.

`pip cache list` could not inspect the user pip cache because pip reported that its cache directory is not owned or writable and disabled the cache. Permissions were not changed. No approved local offline mirror was identified in the accessible locations.

The first offline sync stopped because the Polars runtime wheel was not in uv's artifact cache. After independently verifying the provided wheel, `uv pip install --offline --no-deps --find-links ... polars-runtime-32==2.1.0` installed it into the isolated temporary Python 3.11.15 environment. The required `uv sync --offline --locked --group dev --find-links ... --python ...` then advanced to Pillow and stopped because its exact locked wheel was unavailable. The lock file remains unchanged at SHA-256 `779d901329988f1f76e549a146a3c00a65c570b0cc0335b80ee281b717b55712`.

## Required acquisition and validation

An authorized dependency custodian must provide `pillow-13.0.0-cp311-cp311-macosx_11_0_arm64.whl` through an approved channel. Its required SHA-256 is `37290fbef0c73e2d51d594eb78fded7dc7170d1c3ad0a890269e6fd8c8c4e061`. Verify it against the exact `uv.lock` wheel entry before use; if it differs, stop without modifying the lock.

After a verified wheel is supplied, resume with the isolated Python 3.11.15 environment and `uv sync --offline --locked --group dev`. Check all lock versions, not only Polars. Then run the frozen test/replay suite and same-environment Revision 2 warning baseline. Until those checks finish, `WARNING_REGRESSION` remains `UNRESOLVED` and Revision 3 is not ready for freeze approval.

## Boundaries

No dependency declaration, lock file, implementation code, metric, threshold, freeze contract, or historical Attempt evidence was changed. No wheel, installed package directory, temporary environment, private grid data, or `.serena/` content is included in this report. No Push, PR, lease issuance, real-grid PF, or Attempt 005 execution occurred.
