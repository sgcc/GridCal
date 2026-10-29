# ADR-009: Protected parallel circuit identity through multi-voltage quotient

_Design for Phase 1.5 follow-up; proposed for review, 2026-10-30._

Status: proposed for review. This ADR authorizes no implementation until reviewed.
It refines [ADR-008](008-multi-voltage-quotient.md) for protected parallel
Lines. It does not change the existing quotient or `cycle_corridors` Gate in
this design-only submission.

## Context and decision

The PR-017 source audit found 194 parallel Line corridors in Beijing
`BPA10056814` (191 exact double circuits and three with at least three
circuits). Of these, 91 are cycles solely because parallel active Lines share
two terminals. Jilin `BPA10056819` has 138 parallel corridors (137 exact double
circuits and one with at least three), including 59 parallel-only cycles. Every
audited parallel member has a distinct original `L ` card and circuit ID;
neither fixture has an L+ false parallel, adapter duplicate, or unresolved
corridor. L+ extends a same-key L card with terminal reactor values; the
adapter projects those values to `ShuntImpedance`, never to another Line.

The current PyPSA quotient maps Beijing's 392 source Lines in parallel
corridors to 194 target Lines. It collapses all 91 parallel-only cycles, so
the final state is `MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID` with reason
`PROTECTED_PARALLEL_CYCLE_MULTIPLICITY_LOST`. The three reported Bus counts
1392, 1395, and 1525 remain unaccepted candidates. The AC-only readiness and
PSDCase-to-PyPSA structural projection pass; physical fidelity has not been
evaluated.

**Decision:** The first implementation of this ADR may use only
`preserve_protected_members`. It must preserve every member of a protected
parallel corridor as a distinct Line with the same source and target StableId.
It may continue the existing ordinary quotient behavior for unprotected
corridors. `preserve_all_members` and an explicitly selected
`aggregate_corridor` policy remain unsupported until separate review.

## Terms and topology predicates

All graph predicates use static `active` Line state in a single canonical
voltage layer. A corridor is keyed by the unordered pair of endpoint Bus
StableIds and that canonical layer. Original connected component/subnetwork
and endpoint region labels constrain the busmap but do not split the identity
of a corridor, including one that crosses a region boundary. Line orientation
is retained separately for semantic checks. Transformer and Link edges do
not create an AC Line corridor. L+ is never a corridor member.

| Term | Contract |
|---|---|
| `parallel_corridor` | A same-layer endpoint pair with at least two distinct source Line StableIds, irrespective of active state. A source card or name resemblance alone does not establish membership. |
| `member` | One source Line StableId, original ordered terminals, circuit ID if supplied by an authoritative source ledger, and its complete Line attributes. Duplicate StableIds or duplicate raw source-card identities fail preflight. |
| `active_member` | A member whose static `active` value is true. Only these members contribute to the current active topology and cycle classification. |
| `inactive_member` | A member whose static `active` value is false. It remains an inventory object and mapping obligation but does not create an active edge. |
| `parallel_only_cycle` | At least two active members connect a pair that is a bridge in the active simple Line graph. Removing parallel multiplicity destroys this multigraph cycle. |
| `simple_graph_cycle` | The pair is a non-bridge edge of the active simple Line graph. At least one alternative simple path exists; this predicate may coexist with parallel multiplicity. |

The existing near-linear active-Line corridor helper supplies the simple graph,
bridge set, and active multiplicities. The two predicates are not mutually
exclusive: a parallel corridor on a triangle is both a multigraph cycle and a
`simple_graph_cycle`; a double circuit between otherwise disconnected buses
is `parallel_only_cycle`. The audit label `both` means active multiplicity is
at least two and `simple_graph_cycle` is true. An active single Line in a
triangle is `simple_graph_cycle` only. Inactive members never turn a bridge
into a cycle. Classification is recomputed at the Builder input and after
reload, without changing the existing `cycle_corridors` predicate.

## Strategy contract and first authorization

| Strategy | Meaning | First implementation |
|---|---|---|
| `preserve_protected_members` | Keep all members of each protected corridor as separate target Lines. Unprotected corridors continue through the existing ordinary quotient and many-to-one branch map. | **Only authorized strategy.** It becomes the multi-voltage protected-parallel policy when implemented. |
| `preserve_all_members` | Keep all members of every parallel corridor, including inactive members, with singleton endpoints. | Parseable future option, but enabling it raises `UnsupportedFeatureError`. |
| `aggregate_corridor` | Explicitly permit corridor-level Line aggregation, subject to the existing cycle Gate and future proof of all affected asset semantics. | Parseable future option, but enabling it raises `UnsupportedFeatureError`. Ordinary aggregation of unprotected corridors under the first strategy is still allowed. |

For `preserve_protected_members`, a corridor is protected if it is a
`parallel_only_cycle`, any member has explicit
`preserve_component_identity`, or active and inactive members are mixed.
Explicit Line identity protection applies to the whole corridor. A declared
L+ association does not increase Line multiplicity; it independently
requires its base Line and terminal Shunts to survive exactly or a typed
capability failure. Thus an L+ Line that would otherwise be aggregated or
absorbed is promoted to identity preservation, even if its corridor has only
one Line. This prevents a terminal reactor from being silently reassigned to
an aggregated Line.

Every endpoint of every protected corridor, and of any L+ base Line promoted
to identity preservation, is a mandatory singleton. Preflight checks the
stable-ID busmap: each endpoint maps alone to one target Bus, endpoints stay
distinct, in the same canonical voltage layer and domain, and no other Bus
shares either target. An invalid plan fails before materialization. The
shared planner floor and `requested/planned/actual` cluster accounting must
include these new singletons; no silent count adjustment is permitted.

## Builder and Pipeline ownership

`ProtectedParallelLineHandler` is a pure in-memory Builder collaborator. It
receives the simplified network, authoritative parent sidecar, validated
partition, protection, canonical layer map, and external Line circuit and
L+ association ledgers. It never receives the raw original network and never
writes files. The Pipeline owns original-to-simplified map composition,
input immutability, persistence, reload, and final status as in ADR-008.

```mermaid
flowchart LR
    accTitle: Protected Parallel Line Quotient Flow
    accDescr: Protected Line members are detached from a cloned layer, ordinary Lines pass through the existing quotient, and preserved members are reconnected before persistence and final validation.

    source["Simplified layer and sidecar"] --> classify["Classify active corridors"]
    classify --> singleton["Validate singleton endpoints"]
    singleton --> detach["Detach protected Lines and linked L+ Shunts"]
    detach --> quotient["Existing public PyPSA quotient on remainder"]
    quotient --> reconnect["Reconnect exact members and associations"]
    reconnect --> preliminary["Builder preliminary checks"]
    preliminary --> persist["Pipeline maps, NetCDF and ledgers"]
    persist --> reload["Reload and final structural Gate"]
```

The handler's steps are deterministic and fail closed:

1. Derive corridors from source Bus and Line StableIds, static active state,
   exact canonical voltage layers, and source circuit ledger. Reject missing
   bindings, duplicate member identities, unsupported populated dynamic
   fields, ambiguous circuit IDs in a source that promises them, and
   unresolvable L+ ownership.
2. Compute protected members and enforce singleton endpoints before calling
   any quotient. Record each protected source Line's ordered target Bus
   StableIds and `LineSemanticHash` input digest.
3. Clone each layer through public PyPSA APIs. Detach all protected members,
   including inactive members of a protected corridor, and all Shunts owned
   by their L+ associations from that clone. Keep an immutable in-memory
   ledger of complete static rows, supported dynamic tables, type references,
   sidecar bindings, active state, and external circuit/association metadata.
   The caller's network and sidecar remain unchanged.
4. Run the existing `LayerQuotientMaterializer` and public
   `network.cluster.spatial.*` wrapper on the remaining layer. Its mapping
   handles unprotected Lines, including many-to-one and
   `absorbed_internal` outcomes. No change to the PyPSA quotient algorithm
   or existing cycle Gate is authorized here.
5. Reconnect each detached Line exactly once after the layer quotient and
   before cross-layer Transformer reconnection. Resolve its ordered terminal
   names through the singleton busmap and target sidecar. Copy the complete
   static row, supported dynamic values, and referenced `LineType` row via
   public APIs. Generate collision-free backend names deterministically from
   StableIds; backend names need not equal source names. Restore linked L+
   Shunts at the translated terminal Buses and restore their sidecar bindings.
6. Add one `branch_map` row per protected source Line with
   `source_id == target_id` and `mapping_type="preserved"`. Add one preserved
   `device_map` row per linked L+ Shunt. The target sidecar binds those exact
   StableIds to reconnected backend objects. No protected member may also
   appear in an ordinary quotient mapping, have two target rows, or become
   `absorbed_internal`. Produce a `ReductionRecord` with counts, strategy,
   input/output signatures, and rejection evidence.

If public PyPSA APIs cannot round-trip a required Line or Shunt attribute,
the handler raises a typed capability error. A private API requires a new
compatibility ADR before use. The Builder returns only
`PreliminaryStructuralEvidence`; the Pipeline persists maps and ledgers,
reloads NetCDF and Parquet, then computes the final structural result.

## Line identity and complete semantic hash

The authoritative source Line StableId is the sidecar ID, not a DataFrame
position, PyPSA backend name, or inferred equipment name. For a preserved
member, the target sidecar must carry the identical StableId. The Parquet
`branch_map` and its read-only `line_map` projection must contain exactly one
row with `mapping_type="preserved"`; composed L0-to-final mappings must also
resolve that source to the same target ID. A renamed backend Line is allowed
only when these identities and the complete semantics match.

`LineSemanticHash` canonically encodes all of the following, with explicit
field names and types:

| Input | Canonical rule |
|---|---|
| Identity and terminals | Source Line StableId; ordered `bus0`, `bus1` translated through the singleton busmap to target Bus StableIds. The output uses target-sidecar Bus StableIds in the same order. Compare direction as well as endpoint identity. |
| Static Line row | Every public static Line attribute, including `active`, r/x/g/b, length, rating, geometry or other supported fields, and `type`. Exclude only regenerated `sub_network` and documented nonsemantic serializer metadata; unknown populated fields are preserved or rejected. |
| Dynamic Line data | Every populated supported input attribute, exact snapshot and investment-period index/order, explicit value-presence mask, static fallback, and effective dense values. Dense access runs on detached views so it cannot mutate the caller's network. Unknown populated dynamic attributes fail. |
| Type reference | Exact `LineType` name and canonical hash of its full referenced row; missing or conflicting type rows fail. Referenced `Carrier` metadata, if present for a supported Line field, is copied and verified under the same rule. |
| Circuit provenance | Exact external circuit ID and authoritative source-card identity when supplied. Missing and blank IDs are distinct; real BPA `L ` sources require a nonblank, unique circuit ID within their corridor. Programmatic PyPSA fixtures may explicitly mark circuit ID unavailable. |

Canonical encoding sorts attribute keys, preserves terminal and snapshot
order, normalizes equivalent finite numeric representations and a typed
missing-value marker, and includes value type for strings, booleans, enums,
and numbers. It does not round or use a physical tolerance to hide changed
Line parameters. The input digest is compared with the reconnected in-memory
Line and again with the clean NetCDF reload. Snapshot and investment
weightings remain separate whole-network Gate checks from ADR-008. A dynamic
`active` value that changes topology relative to static `active` is an
unsupported feature until a snapshot-topology Gate is designed; other
supported dynamic Line values must round-trip exactly under the canonical
comparison.

## L+ association and state preservation

`LPlusAssociation` is an external ledger, separate from the Line multiplicity
graph. Each row binds one base Line StableId, the source L+ card identity,
terminal side `FROM` or `TO`, the corresponding Shunt StableId, signed source
reactor value, and the Shunt's full supported static/dynamic semantics. It is
created from exact source keys and parser provenance, never device names.
There may be zero, one, or two terminal rows for a base Line; a duplicated
side or orphan association fails. L+ is never counted as an active Line or
used to increase corridor multiplicity.

After reconnect and again after NetCDF, sidecar, association-ledger, and
Parquet-map reload, the Gate verifies that each association still names its
base Line and Shunt StableIds, that the Shunt exists exactly once, and that
its Bus equals the base Line's translated `bus0` for `FROM` or `bus1` for
`TO`. It verifies the Shunt semantic hash and signed value; reversing Line
orientation without translating terminal side fails. If a nonprotected
Line's L+ association cannot be represented after ordinary aggregation, the
Line and Shunts are preserved or preflight fails. No L+ Shunt is silently
merged, moved across voltage, or dropped.

For a protected corridor, every active and inactive member is reconnected
with its original static `active` value and supported dynamic attributes.
Inactive members remain distinct inventory and mapping rows but do not count
as active cycle edges. Mixed active/inactive corridors are protected by the
first strategy. An all-inactive or all-active unprotected corridor may use
ordinary aggregation only when every source member has a complete branch-map
row and the output's static active state has an explicit, verifiable meaning;
otherwise preflight fails. No N-1 or future outage topology claim follows
from saving inactive equipment.

## Structural Gates and evidence

The existing `cycle_corridors` check remains a hard Gate with unchanged
meaning. The new named checks supplement it, first as Builder preliminary
evidence and then against the reloaded network, Parquet maps, sidecars, and
external ledgers in the Pipeline:

| Check | Failure condition |
|---|---|
| `protected_parallel_singleton_endpoints` | A protected endpoint shares a target Bus, disappears, crosses a voltage/domain boundary, or the two endpoints collapse. |
| `protected_parallel_member_coverage` | A source protected member is missing, duplicated, absorbed, aggregated, or mapped to a different StableId. |
| `protected_parallel_multiplicity` | Any protected corridor loses an active member or its target active multiplicity; compare member StableId multisets and active-state counts, not only cycle rank. |
| `protected_parallel_line_semantics` | Any input, in-memory, or reloaded `LineSemanticHash` differs, including ordered terminals, circuit ID, type, status, and dynamic data. |
| `protected_parallel_type_references` | Referenced `LineType` or supported Carrier rows are absent or semantically changed. |
| `l_plus_association_coverage` | A source association, base Line, Shunt, or preserved device-map row is absent or duplicated. |
| `l_plus_terminal_owner_consistency` | Terminal side, translated Bus, signed reactor semantics, or Shunt ownership differs after reload. |
| `parallel_evidence_round_trip` | Persisted/reloaded branch map, line projection, sidecar, circuit ledger, or L+ association differs canonically from the Builder output. |

Every check has a boolean and actionable detail. A construction or unsupported
input failure writes `FAILED` and failure evidence. A built/reloaded network
that fails any structural check writes
`MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_INVALID` with maps and evidence. Only a
complete final Gate may write
`MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID`. The Builder never writes that
state. The original network's input SHA-256 and canonical semantic signature
are checked before and after the Pipeline; arbitrary in-memory byte identity
is not required. No check can be waived because another check passes.

`CompressionOpportunityReport` adds per-layer and global counts for parallel
corridors by multiplicity, `parallel_only_cycle`, `simple_graph_cycle`, and
`both`; protected active/inactive members; singleton endpoints and overlaps;
L+ associated Lines and Shunts; ordinary aggregated corridors; rejected
candidates by reason; and protected-member retention cost in Bus and Line
counts. The report retains source/simplified/requested/planned/actual/final
counts and the shared planner structural floor. It reports candidate
compression as unaccepted whenever the final Gate is invalid. These counts
are structural opportunities, not physical savings or fidelity evidence.

## Test and confidential acceptance matrix

All automated tests use programmatic PyPSA networks and external sidecar and
association fixtures. They run offline and assert both expected success and
fail-closed behavior. Raw BPA cases are used only in separate local,
read-only acceptance runs; DAT/REF/MAP/log/pfo, raw card rows, and source
StableIds are not committed.

| Programmatic case | Required assertion |
|---|---|
| Two active parallel Lines are the only cycle | Both members and their IDs survive; existing `cycle_corridors` and new multiplicity checks pass. |
| Three active parallel Lines | Three distinct preserved map rows, target Lines, and circuit IDs survive. |
| Active plus inactive member | Both survive with distinct identities and exact active states; only the active member enters the static cycle graph. |
| Different r/x/rating/LineType | Each complete row and referenced type survives; no parameter averaging. |
| Supported dynamic Line attribute and MultiIndex snapshots | Values, presence masks, snapshot indexes and weightings round-trip through NetCDF. |
| Non-singleton endpoint plan | Builder preflight fails before quotient with a named singleton reason. |
| Changed Line parameter, type, status, direction, or circuit ID | In-memory or final semantic Gate fails. |
| Source/target Line StableId differs | Identity and mapping Gate fails even if backend names and electrical rows match. |
| Parquet map corruption or dropped protected row | Reloaded mapping Gate fails with evidence. |
| NetCDF drops or changes protected Line/dynamic data | Final round-trip and Line semantic checks fail. |
| L+ on both terminals | Base Line, `FROM`/`TO` Shunt StableIds, signed values, and exact terminal Buses survive reload. |
| Nonprotected parallel corridor in a simple ring | Existing ordinary quotient may aggregate; many-to-one branch map is complete and existing cycle Gate still passes. |
| Original network mutation attempt | Input SHA and semantic signature detect a change; the normal run leaves the source unchanged. |
| Deterministic rerun | Same seed and input produce identical StableId maps, member/association ledgers, checks, and final structural status. |

| Private real-case run | Source expectations | Acceptance rule |
|---|---|---|
| Beijing `BPA10056814` BASE | 194 parallel corridors, 191 double, three larger, 91 parallel-only; zero L+ false parallels, duplicates, unresolved. | Re-run the AC-only and projection Gates, then the full structural Pipeline. All 91 protected corridors must retain their original active member identities after export/reload; final status must come from the Gate. Any residual failure remains invalid. |
| Beijing actual low-voltage-layer and below-110-kV policies | Same source audit counts; existing unaccepted candidates 1392, 1395, 1525 are historical, not targets to force. | Recompute floor, planned/actual counts and compression; verify protected members and L+ associations for each scenario. Do not report accepted compression unless final status is valid. |
| Jilin `BPA10056819` BASE and applicable low-voltage policies | 138 parallel corridors, 137 double, one larger, 59 parallel-only; zero L+ false parallels, duplicates, unresolved. | Establish a fresh quotient run and final persisted Gate; Jilin currently has no quotient target evidence, so no success is presumed. |

Record private input hashes, code/tree SHA, policy, seed, per-layer counts,
runtime, rejection reasons, full named Gate results, and audit artifact hashes.
Publish only aggregate statistics and reproducible commands. A clean
structural result for one fixture does not imply acceptance of another.

## Consequences, limits, and non-goals

Preserving protected members increases the minimum Bus count through
singleton endpoints and may reduce compression. If a requested cluster
target is below that floor, the run fails explicitly. The handler adds
identity-preserving Line rows while leaving unprotected corridor aggregation
and ADR-008's Transformer and multi-voltage contracts intact. It cannot
retroactively restore a protected source Line already absorbed during L1;
such an input fails preflight until the upstream topology stage preserves it.

This ADR does not authorize PF, N-1 certification, Ward/Kron, OPF, AFDPF-R,
or physical/electrical equivalence. It adds no `PF_VALID`, `N1_VALID`, or
equivalence state. A future passing run may claim only
`MULTI_VOLTAGE_QUOTIENT_STRUCTURALLY_VALID`, with
`PHYSICAL_FIDELITY_NOT_YET_EVALUATED` stated separately. Implementation and
real-case acceptance require a later reviewed change; PR-019 is not started
by this design.
