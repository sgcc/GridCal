# Attempt 004: fresh-process PF metric replay root-cause audit

**Audit outcome: `ROOT_CAUSE_IDENTIFIED`.** The three discrepancies are caused by order-dependent binary64 summation. The PF executor builds StableId-indexed dictionaries in PyPSA component-index order; the PF evidence serializer writes those dictionaries in encoded StableId order. Attempt 004 calculated and saved the metrics from the former order, then recalculated them after loading the latter order. Reordering the unchanged, persisted PF scalars back to the original component-index order reproduces **all** saved metrics and the saved metric SHA-256 exactly. This audit did not run PF, change code, or accept the failed policy.

## Scope and evidence bindings

The reviewed branch was at `16c9208e1cfa6a96045348f73592d22b354ef38e` (tree `d145129909b5064a1909d90f11df598f43239ea1`). The source contracts were the original retained-fidelity freeze, revision 2 (`25394674ba28e7de013bd3cc57da080155443109b77ec7f9eba7c8d57a8d2b04`), and the Attempt 004 lease (`d3271be40cc2d59da358276217904d357c73d72416e32e6ca6d45bbea8aa2ba5`). The committed Attempt 004 STOP summary has SHA-256 `866221a956a2100a58b279e2d0386c4766d741295530f94244222a5e8e06c6f5`.

Read-only evidence was taken from the existing `BACKBONE_500` durable run. The following relative artifact names and byte SHA-256 values identify the material examined without publishing source paths or device identities:

| Artifact | SHA-256 |
| --- | --- |
| `pf/source.json` | `c14f160d664ec3282061c7f43b4b47a823fba79fded68bffd073638b36230ae7` |
| `pf/candidate.json` | `de72c80c5dc6a427cb176ade5c415a74aed9b40bee73a4828d3d2dc46b54f32b` |
| `metrics/fidelity.json` | `beb78e73cbac398d61e16779f3307958b0eb671275e5be1731f3f7f20740b524` |

`BACKBONE_500` remains `FAILED / FRESH_PROCESS_REPLAY_INVALID`. `BACKBONE_220` and `BACKBONE_100` remain `NOT_RUN`. The stored `PF_FIDELITY_WITHIN_TOLERANCE` decision is **unaccepted**, because final durable replay failed. No accepted compression or physical-equivalence claim follows from this audit.

## Frozen comparison and Gate semantics

| Contract layer | Frozen or implemented behavior | Audit observation |
| --- | --- | --- |
| Numeric replay tolerance | Revision 2 and the lease record absolute `1e-12`, relative `1e-10`. | Each of the three scalar pairs satisfies `math.isclose` with these limits. |
| PF evidence content | `retained-pf-execution-result/v1` binds the complete solved scalar payload by `result_sha256`; reload verifies it. | Source and candidate payloads serialize, deserialize, and reserialize exactly, including their result digests. |
| Metric evidence | The freeze explicitly lists the numeric metric digest among replay checks. `metric_set_digest` hashes the complete calculated metric set as canonical JSON. | The stored digest matches the **stored** metric JSON exactly; the newly calculated metric set has a different digest. Key sorting during hashing cannot undo different floating-point sums. |
| Actual Attempt 004 final Gate | `verify_policy_artifacts` requires exact equality of `metrics/fidelity.json` metric digest, metrics dictionary, threshold digest, status, and resolved thresholds. It does not apply the declared numeric tolerance at this check. | The first failing check is `ValueError("fidelity replay")`, wrapped as `CANDIDATE_PROVENANCE_INVALID: durable readback`, then reported by the independent subprocess as `FRESH_PROCESS_REPLAY_INVALID`. The subsequent numeric-observation comparison was not reached. |
| Admission and identity | PR-022 acceptance uses HARD thresholds. Candidate identity is computed before PF metrics. Retained-network additions are diagnostic only. | Both stored and recalculated threshold digest are `d775de9e5a66480ff61c20fe9c631bcd5a8654989df0b597736207123cff803d`; both computed admissions are `PF_FIDELITY_WITHIN_TOLERANCE`. The relative-loss scalar is a HARD observation, the absolute-loss scalar is a DIAGNOSTIC observation, and total active-balance difference has no threshold. All three belong to the complete metric evidence digest; none is added to candidate identity. |

The numeric tolerance is a separate comparison rule; it does **not** authorize bypassing the frozen exact metric digest or artifact-content checks. Conversely, the exact metric check currently imposes a stronger reproducibility requirement on these three computed floats than the recorded scalar tolerance. This review preserves both recorded facts rather than treating a small difference as an automatic pass.

## Formulas and field-level difference

`compare_power_flow_results` uses `pf/source.json` and `pf/candidate.json` fields under `solved_values`. For each side, it calculates passive Line and Transformer loss as `sum(branch_p0[id] + branch_p1[id] for id in branch_p0)`, excluding Link and shunt contributions. It calculates total active balance as `sum(bus_p_mw.values())`. The first two reported metrics are `abs(source_loss - candidate_loss)` and that difference divided by `max(abs(source_loss), loss_denominator_floor_mw)`; the third is `abs(source_bus_balance - candidate_bus_balance)`. The frozen loss denominator floor is `1e-6 MW`. All three paths use Python `float` and built-in `sum`, with no StableId sort at the reduction point.

| Metric | Saved before replay | Fresh-process recalculation | Absolute difference | Difference / saved magnitude | Digest affected | Difference source |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `passive_branch_active_abs_diff_mw` | `0.051265713494160536 MW` | `0.05126571349421738 MW` | `5.684341886080802e-14 MW` | `1.108799916873932e-12` | Full metric SHA-256 | Source and candidate branch-loss sums use different iteration orders. |
| `passive_branch_active_relative_diff` | `0.000110974407528934` | `0.00011097440752905708` | `1.2307049910426082e-16` | `1.1089989290744629e-12` | Full metric SHA-256 | Derived from the two order-dependent loss sums; denominator and units unchanged. |
| `total_active_balance_abs_diff_mw` | `0.051265713293105364 MW` | `0.05126571329674334 MW` | `3.637978807091713e-12 MW` | `7.09631949582369e-11` | Full metric SHA-256 | Candidate Bus P sum changes when keys are sorted; source Bus order already matches. |

Saved metric SHA-256: `9d993017381c41750d4a18318f5e90020e9200d7552fbd6f2c3c93db90679c4a`. Recalculated metric SHA-256 in both independent new processes: `487e793cbceb6b31825a6ba19af41f3d2c65e7146b8adb30c0f3a66f2003f35f`. The threshold SHA-256, threshold values, and admission status did not change.

The intermediate totals isolate the reductions:

| Side and quantity | Component-index order, reconstructed | Persisted StableId order |
| --- | ---: | ---: |
| Source passive loss (MW) | `461.9597854648982` | `461.9597854648981` |
| Candidate passive loss (MW) | `461.908519751404` | `461.9085197514039` |
| Source Bus P sum (MW) | `461.9597854650492` | `461.9597854650492` |
| Candidate Bus P sum (MW) | `461.9085197517561` | `461.90851975175246` |

The source has 1,527 Bus P entries and 1,983 passive branch entries; the candidate has 1,392 and 1,743 respectively. Both stored PF payloads passed their content digests and exact numerical reserialization (26,580 solved scalar entries in total). The metric function's exact coverage checks passed. There is no evidence of a missing object, duplicate count, sign reversal, unit change, or changed electrical input in this mismatch.

## First divergence and reproduction

`PypsaAcPowerFlowExecutor._extract_solved_values` inserts results while iterating `network.buses.index`, followed by `network.lines.index` and `network.transformers.index`. The driver writes those results through `serialize_power_flow_result`, whose `_encode_values` returns `dict(sorted(encoded.items()))`. `read_power_flow_result` reconstructs dictionaries in that sorted order. The driver calculates the saved metrics from its original PF objects, while independent replay calculates them from the reloaded objects. JSON preserves each binary64 value here; it changes **iteration order**. `metric_set_digest` then correctly hashes the two different computed metric objects.

The original PF objects no longer exist, so direct post-mortem inspection of their in-memory bytes is impossible. Nevertheless, recomputing from the persisted PF scalars in the exact backend component-index order reproduces every stored metric field and the saved metric digest byte-for-byte. Recomputing the same scalars in persisted StableId order reproduces every replayed value and its digest. This identifies the first numeric divergence as **reduction over reloaded dictionaries**, following an order change during PF-result serialization; it is not a scalar JSON precision loss or a digest implementation collision.

Read-only checks, with **zero PF solver calls**, were:

1. Two calculations in one process from the same reloaded PF evidence: identical three values and identical replay digest.
2. Two independent Python 3.11.15 processes using the locked PyPSA 1.2.4 / NumPy 2.4.6 environment: identical replay values and digest.
3. Exact JSON payload deserialize/reserialize and PF result-digest equality for source and candidate: both passed.
4. Reorder only the reloaded `branch_p0` and `bus_p_mw` dictionaries to the network component-index order: the **entire** metric dictionary and stored metric digest matched exactly.
5. A three-object, non-grid synthetic PF-result payload with the same serializer: branch loss and Bus P balance changed from `1e-13` to `1.1368683772161603e-13` solely after key sorting; its PF evidence digest remained valid.

## Classification and proposed repair for separate review

**Primary classification:** deterministic numerical-calculation inconsistency caused by implicit dictionary iteration order at two aggregate sums. The evidence validator correctly failed closed when recomputed content did not match the stored content. The complete-metric digest works as specified; its input calculation lacks a canonical reduction order. This is neither PF-data corruption nor a threshold/admission change.

**Minimum proposed implementation change (not made here):** in `packages/psdcalc-reduction/src/psdcalc/reduction/fidelity/metrics.py`, iterate canonical encoded StableIds for `_passive_branch_loss` and both `bus_p_mw` sums before using the existing `sum`. That matches the stable ordering already used by PF evidence serialization while keeping the formulas, binary64 operands, denominator floor, and threshold classification unchanged. Do not use a tolerance bypass, delete any metric from the digest, or change the saved PF values. Audit any other order-sensitive aggregate independently before expanding the patch. No change to the PF executor, serializer schema, MappingBundle, thresholds, or controller is proposed.

**Synthetic validation plan:** add focused cases to `packages/psdcalc-reduction/tests/test_pf_metrics.py` and `packages/psdcalc-reduction/tests/test_retained_pf_driver_evidence.py` for opposite insertion orders, serializer roundtrip, complete metric/digest equality, fresh-process replay, preserved artifact-tamper failure, and unchanged HARD admission. Include source and candidate with distinct Bus and branch orders. Assert no extra PF calls and unchanged candidate identity; existing historical Attempt 004 evidence must continue to fail closed and must not be rewritten.

`CONTRACT_CHANGE_REQUIRED = NO` for this canonical-order implementation repair: the frozen formulas, threshold set, full metric digest fields, and strict content check can remain intact. **A new versioned experiment freeze revision and a new execution lease are still required before any future real-grid attempt**, because changing `metrics.py` changes the implementation bound by the Attempt 004 lease. If a later proposal instead changes tolerance semantics, the metric digest field set, or evidence schema, it must be reviewed as `CONTRACT_CHANGE_REQUIRED = YES` before implementation. The present audit does not supply an Attempt 005 lease.

## Status boundary

- `REAL_GRID_PF_RUNS = 0` in this audit; persisted PF results were only read.
- `ATTEMPT005 = NOT_STARTED`; no new lease was requested.
- `BACKBONE_220 = NOT_RUN`; `BACKBONE_100 = NOT_RUN`; `Stage2D = NOT_RUN`.
- Attempts 001–004 and all original freeze, STOP, PF, and report evidence remain unchanged.
- Projection fidelity remains `NOT VALIDATED`; physical equivalence remains `NOT CLAIMED`.
