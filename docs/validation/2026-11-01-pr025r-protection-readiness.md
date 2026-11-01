# PR-025R protection and action-readiness contract

This remediation is based on `32f578e842ed5160aec02416d1ad996b20a66fc2`.
The frozen PR-025A Stage2A result remains `FAIL / STRUCTURAL_INVALID /
NOT_ACCEPTED` with evidence digest
`13a39a0468e19f80218dbbbfb48ccb531bded3c1cb2ac508d50412275446fab2`.
This document does not supersede that experiment.

## Bus roles

| Source | Role | Required mapping |
| --- | --- | --- |
| Transformer and Link terminals | Identity anchor | Terminal maps to itself; eligible free Bus may map into it. |
| `preserve_terminal_bus` and identity-preserved Bus | Identity anchor | Source Bus maps to itself; other constraints still apply. |
| `preserve_bus_as_singleton` | Strict singleton | Bus maps to itself; no incoming members. |
| PV/Slack Generator Bus and ShuntImpedance Bus | Strict singleton | Bus maps to itself; no incoming members. |
| Identity-preserved Line endpoints and protected cycle endpoints | Strict singleton | Endpoints map to themselves; no incoming members. |
| Protected parallel corridor endpoints | Strict singleton | Endpoints map to themselves; no incoming members. |

The effective protection resolver combines configured/profile protection,
terminal anchors, strict rules, protected parallel members, L+ source-ledger
identity, and `cannot_link`. It binds the resolved contract and the explicit
source ledger with semantic digests before action readiness or materialization.
The PyPSA materializer consumes the resolved parallel plan. Energy planners
continue to reserve anchors as separate clusters; explicit adaptive action
replay may absorb eligible radial Buses into an anchor.

`ACTION_CATALOG_READINESS` checks the active action set before candidate
materialization. A rejected attempt has a deterministic build-attempt
fingerprint, failure stage/code, and sorted anonymous offending identity
fingerprints. Its materialized-candidate fingerprint is null. The v2 evidence
schema persists the source ledger; replay rejects historical v1 evidence
explicitly rather than reinterpreting its candidate fingerprint.

## Private historical catalog compatibility audit

The audit loaded frozen S1, the frozen sidecar/catalog/config, and the
authoritative source ledger in read-only mode. It called action readiness only.
No candidate materialization, controller iteration, or power flow was run.

| Measure | Count |
| --- | ---: |
| Historical active actions checked | 135 |
| Catalog-ready actions | 125 |
| Catalog-rejected actions | 10 |
| `STRICT_SINGLETON_CONFLICT` rejections | 10 |
| Rejected actions targeting protected cycle endpoints | 10 |
| Of those, also targeting protected parallel endpoints | 7 |

The 10 remaining rejections arise from independently strict cycle protection.
They are not Transformer-anchor-only conflicts. The audit is a compatibility
diagnostic, not a Stage2B result or an accepted reduced candidate.

Projection fidelity: `NOT VALIDATED`. Physical equivalence: `NOT CLAIMED`.

## Offline implementation checks

| Check | Result |
| --- | --- |
| Full source suite | 598 passed, 0 failed |
| Clean installed-wheel adaptive/replay suite | 81 passed, 0 failed |
| `compileall` with a detached bytecode directory | PASS |
| `git diff --check` | PASS |
| `uv lock --check --offline` | PASS |
| sdist SHA-256 | `71c95da92e2457a7a57e1ab62269b793d17e817429313efcc87f4706a612aca7` |
| wheel SHA-256 | `a4038f73aa236089863d3790473036a682adc6f6851b46fc7738ab6ae2c80f30` |

The development branch retains package version `0.6.0` and unchanged
dependency versions. Because v2 evidence makes a public schema boundary
explicit, the recommended release version is `0.7.0` after review; this
remediation does not change the version.
