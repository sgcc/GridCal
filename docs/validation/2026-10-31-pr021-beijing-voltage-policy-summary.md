# PR-021 Beijing Voltage Policy Matrix

> **Evidence binding.** Tested implementation commit `4b740b58f6c0e8e1b08134a4a6364279ce370864`; tree `1f3bc4f7be30af68f69013ef858c8bcb4830ac6e`; package `0.4.0`. Real-case results were produced from the tested implementation tree. Subsequent documentation-only commits do not alter the tested implementation.

Physical fidelity: `PHYSICAL_FIDELITY_NOT_YET_EVALUATED`.

## 🧾 Evidence lineage

| Layer | Commit or tree |
|---|---|
| Core implementation | `fbb06be524b4b00fdcd8a61048ce12e386a62c95` |
| Report implementation | `50a913f10622e246260877036d27b21303e0b5e3` |
| Tested package implementation commit | `4b740b58f6c0e8e1b08134a4a6364279ce370864` |
| Tested package implementation tree | `1f3bc4f7be30af68f69013ef858c8bcb4830ac6e` |


```mermaid
flowchart LR
  A[Core implementation] --> B[Report implementation]
  B --> C[Tested package tree]
  C --> D[Beijing structural evidence]
  D --> E[Documentation-only record]
```

The seven scenarios are bound to the tested package tree, not to the later documentation commit.

## 🧪 Package and run environments

- Package gate: `PR021_PACKAGE_0_4_0_GATE_PASS`; Python 3.13.9; 330 passed, 0 failed.
- Beijing run: Python 3.11.15, PyPSA 1.2.4, highspy 1.15.1.
- Both Python versions are within the supported range `>=3.11,<3.14`. Cross-version result equivalence was not tested.

## 🔌 Source and voltage-layer evidence

- Case: `BPA10056814`; DAT SHA-256 `09b565efc1d4550f3cf281b0723f93fc28be947a8da2c01c1b3208f7e72eb11e`.
- Source inventory: Bus 1527, Line 639, projected Transformer rows 1344, Generator 56, Load 219, ShuntImpedance 135, Link 0, StorageUnit 0, Store 0.
- Projected Transformer row basis: 96 two-winding rows plus 1248 projected three-winding T-leg rows from 416 groups, totaling 1344 projected rows. These are projected rows, not a count of physical transformers.
- Actual voltage layers (kV): 0.32, 0.6, 0.69, 0.8, 1, 1.14, 10.5, 13.8, 15.8, 16, 18, 20, 27.5, 37, 66, 115, 230, 525.
- 330, 110, and 35 kV layers were absent. Their numeric values remain valid cutoff thresholds; no missing layer was inferred and no voltage remapping was performed.

## 🧷 Parallel-corridor and L+ audit

- Parallel corridors: 194; double-circuit: 191; three-or-more members: 3; parallel-only cycles: 91.
- L+ associations: 18; finite source values: 11; blank source values: 7; parse mismatch: 0; malformed: 0.
- The source audit was consistent across all seven scenarios.

## 📊 Seven-scenario structural results

| Policy | Retained layers (kV) | L1 Bus/Line | Candidate Bus/Line | Accepted Bus/Line | Bus reduction | Line reduction | Final status |
|---|---|---:|---:|---:|---:|---:|---|
| BASE | — | 1392/504 | 1392/399 | 1392/399 | 8.8409% | 37.5587% | `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID` |
| CUT_35 | 37, 66, 115, 230, 525 | 1525/637 | 1525/637 | 1525/637 | 0.1310% | 0.3130% | `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID` |
| CUT_37 | 37, 66, 115, 230, 525 | 1525/637 | 1525/637 | 1525/637 | 0.1310% | 0.3130% | `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID` |
| CUT_110 | 115, 230, 525 | 1525/637 | 1525/637 | 1525/637 | 0.1310% | 0.3130% | `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID` |
| CUT_115 | 115, 230, 525 | 1525/637 | 1525/637 | 1525/637 | 0.1310% | 0.3130% | `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID` |
| CUT_230 | 230, 525 | 1396/508 | 1395/507 | 1395/507 | 8.6444% | 20.6573% | `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID` |
| CUT_330 | 525 | 1392/504 | 1392/399 | 1392/399 | 8.8409% | 37.5587% | `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID` |

The accepted counts equal the candidate counts for all seven rows. `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID` is a structural result only; it does not establish power-flow or physical fidelity.

## 🔁 Equivalent requested cutoffs

| Pair | Retained identity-set digest | Resolved fingerprint | L1, candidate, and final inventories | Mapping semantic digest | Final status |
|---|---|---|---|---|---|
| CUT_110 / CUT_115 | equal | equal | equal (L1 1525/637; candidate/final 1525/637) | equal | `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID` |
| CUT_35 / CUT_37 | equal | equal | equal (L1 1525/637; candidate/final 1525/637) | equal | `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID` |

For CUT_110/CUT_115 and CUT_35/CUT_37, the retained identity-set digest, resolved policy fingerprint, L1 inventory, candidate inventory, final inventory, mapping semantic digest, and final status are equal.

## ↩️ BASE compatibility and observations

- PR-021 BASE versus PR-020 Beijing BASE: S1 inventory equal; L1 inventory equal; L2/final inventory equal; canonical Parquet mapping comparison equal. This is structural compatibility evidence only.
- CUT_330 and BASE produced the same scenario inventories and compression fractions.
- CUT_230 changed Bus compression only slightly relative to BASE, while Line compression was lower.
- CUT_115 entered a structural-floor saturation region; the shared planner floor was 1,525 Bus.
- CUT_230 is an informative intermediate structural operating point. No optimality claim is made.

## 🔐 Artifact hashes and diagnostics

- Cross-scenario JSON SHA-256: `494ac84fe4261e70a7d499d22c70bcfef182fdce6accc6deb3aba72fcc3f1b8c`.
- Cross-scenario Markdown SHA-256: `ce3bb0084c03237d6bfa44774b8ad47f7b7f33e37792441aea65de94669cd67a`.
- Cross-scenario Parquet SHA-256:
  - `deltas.parquet`: `b36fa67c3db9707e5fdcefa8367ced447b73012f5b5a4938733d6060f0f8658a`
  - `device_transitions.parquet`: `8a62dc2230e4b30cc6a49b3495782f4fe507b6384c900dec7ed6c43fe9c4fbe3`
  - `gates.parquet`: `76d55e6df8121fa5b21a3ae56ed052e9d0ab576695f24f717a667d880e2337d3`
  - `runtime.parquet`: `ba52c485078b1f86ecd1c101cf98eb9294d36b50b00da8027f8c6447891fa0ac`
  - `scenarios.parquet`: `35991a72ca746c7a2baa13a2552d9b2366e2442b4d6e716c57ae11cb58b2522f`
  - `topology.parquet`: `b945e038cdd8b22505b354dc58271ba5ac0ee00f79ba575be09294b2c12e680e`
  - `voltage_layers.parquet`: `8c3ca543b49937a369f619738e6de9c12d21e5eb40cf37b60904eca219e9f98c`
- Parquet artifacts were locally round-trip validated.
- Frozen evidence bundle SHA-256: `504361226d11080ae8290acc3cc662eb83cb9a1d2f673e08f81f86eb9859229f`.
- sdist SHA-256: `01e864a9f4acc4cbb527c9e1671a6e6264d13e8bebe5addfbedb57cc846c9789`; wheel SHA-256: `014ef81468306743fb9367e3fbfcf83bd69a38bd4eba6570891713aa39afe6c6`.
- Known runtime diagnostic: PyArrow hardware sysctl queries were denied by the sandbox; Parquet readback and report validation completed.

## ⛔ Evidence boundary

- `PR021_REAL_CASE_GATE_PASS`; all seven final structural statuses are `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID`.
- `PHYSICAL_FIDELITY_NOT_YET_EVALUATED`.
- No power-flow analysis was run for this evidence record. No PF, AC-equivalence, VAR-equivalence, or OPF conclusion is made.
- The case results remain tied to the tested implementation commit and tree above. Later documentation-only commits do not change that binding.
