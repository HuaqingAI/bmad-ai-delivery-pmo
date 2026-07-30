# Adversarial Reviewer Gate v17

## Verdict

**FAIL.** The v17 package materially improves the previous design, but it is not yet an interoperable strict-publication substrate. A conforming runtime can still report a false `fresh`, a stale activation context can still authorize a fact mutation, the first-publication branch cannot enter the mandatory strict lineage closure, and repair restart has no durable receipt-discovery contract. The gate therefore remains open.

| Severity | Count |
| --- | ---: |
| Critical | 0 |
| High | 7 |
| Medium | 5 |
| Low | 1 |

## Two Independent Implementations

The following units are constructed without sharing hidden runner behavior:

- **Unit H (handler-first):** treats the registered Python/Node semantic handlers as the executable interpretation of each AD. It accepts the caller-supplied inspect trace, composes `strict_activation_control_semantics`, derives repair retry state in memory, checks trust-root validity at receipt execution time, and requires the fixed lineage descriptor set.
- **Unit P (protocol-first):** implements the prose literally. It invokes the complete strict writer-fence gate during inspect, checks the current activation epoch and attestation for every mutation, reloads durable repair receipts after restart, treats expired roots as immediately inactive, and gives first publication an absence-aware lineage.

| Surface | Unit H | Unit P | Incompatibility |
| --- | --- | --- | --- |
| Live inspect | Calls activation control plus lineage/leaf checks | Calls the complete strict writer-fence closure declared by protocol and registry | H returns `fresh` for a fully rebound false ledger attestation that P rejects |
| Fact authority | Validates capability epoch but ignores context activation epoch/attestation | Requires context epoch and attestation to equal locked live state | H accepts a stale post-rollback context that P rejects |
| First publication | Requires normal `before-pointer` and `before-panel-state` lineage objects | Omits those objects or records typed absence because both preimages were absent | The lineage indexes cannot cross-read; H cannot build its own strict lineage from the valid first branch |
| Repair restart | Uses process memory or a local directory scan to find prior receipts | Uses a locally chosen deterministic batch-to-receipt index | Both satisfy “durable matching receipt” at the AD level but choose incompatible discovery truth |
| Release evidence replacement | Treats the loader's map as the exact current namespace and removes old bytes | Retains content-addressed historical bytes and scopes closure to the current set | One side rejects the other's store as `extra`; crash recovery has no shared transition truth |
| Native principal | Hashes effective UID/SID plus one local service-manager representation | Hashes effective UID/SID plus a different service-manager attestation representation | Both match the profile words but provision different principal IDs |
| Flow-graph time | Sets nested `state.as_of`/allocation `as_of` from selection policy | Sets them from the processed snapshot/window time | Both obey the stated `source_as_of` equality rule, but produce different same-generation flow bytes |

The first three rows expose direct contradictions between the AD/protocol and the registered executable model, so no unit can obey every normative layer simultaneously. The remaining rows are genuine holes where both units can plausibly obey every AD yet fail to interoperate.

## High Findings

### H1 - Live inspect can return a false `fresh` without executing the declared strict closure

AD-12 requires live inspect to close the current root, fact, ledger/state/action-flow, every WDR/state/sidecar, receipts, pointer, Panel, activation, release evidence, and writer inventory (`ARCHITECTURE-SPINE.md:149`). Protocol section 6 says live inspect is a composition of `strict-writer-fence-activation/1.0.0` and that its actual authority/read set must equal the full transitive scope (`WDR-AND-TRANSACTION-PROTOCOL.md:86`). The registry declares that composition and lists the strict validator plus its inputs (`CONTRACT-REGISTRY.json:779-780`).

The reference handler does not call `strict_writer_fence_activation_semantics`; it calls only `strict_activation_control_semantics` (`python_runner.py:6112-6135`). That smaller function checks release evidence, activation/capability identity, and writer inventory, but not the attestation's ledger, root, workstream, fact, publication, and lineage summary. `inspect_trace` is merely a caller-provided list of labels compared to another list (`python_runner.py:6073-6077,6127-6133`), not instrumentation of actual contract/path reads.

A direct probe against the current runner changed `attestation.ledger.ledger_state_id`, recomputed the attestation ID, rebound the strict activation document to the new attestation, and recomputed its state ID. `live_inspect_semantics` still returned `fresh`. The complete strict validator rejects the same false summary at its `actual_summary` comparison (`python_runner.py:2435-2452`). This defeats the Panel's primary freshness claim.

**Required correction:** make live inspect call the registered complete strict validator or share one implementation of the full closure. Replace the string trace with an instrumented resolver whose actual contract/path reads are compared to a registry-derived transitive read set. Add fully rebound ledger, root, WDR-state, sidecar, refresh-receipt, and publication-receipt substitutions to the live-inspect vectors.

### H2 - Fact attribution ignores the activation epoch and attestation carried by native authority

The authority context schema requires `activation_epoch` and `attestation_id` (`panel-sync-contracts.schema.json:1295-1318`). AD-1 requires rollback to invalidate old authority and the registry's fact validator scope explicitly includes strict activation state and writer-fence attestation (`ARCHITECTURE-SPINE.md:83`; `CONTRACT-REGISTRY.json:769`). Protocol section 2 says the independent context binds activation, attestation, and capability epochs (`WDR-AND-TRANSACTION-PROTOCOL.md:21`).

`fact_attribution_semantics` receives no live activation state or attestation. Its authority checks compare profile, path, lock, principal, raw capability bytes, and `capability_epoch`, but never inspect `authority_context.activation_epoch` or `authority_context.attestation_id` (`python_runner.py:3459-3497`). The fixture even emits `attestation_id=null` (`python_runner.py:3448`).

A direct probe changed a valid context to `activation_epoch=99` and an arbitrary non-null attestation ID, recomputed `context_id`, and the current fact-attribution validator still returned `true`. After a strict rollback, Unit H can therefore authorize with a context that Unit P correctly rejects.

**Required correction:** under the same exclusive fact lock, load and validate the registered activation state and attestation, compare both context fields byte-for-byte, define the permitted legacy/migration behavior, and add stale-epoch, null-attestation-in-strict, and fully rebound context vectors for ordinary mutation, recovery, and repair.

### H3 - A valid first publication cannot satisfy the mandatory strict lineage contract

AD-6 and protocol section 5 correctly define first publication as simultaneous absence of pointer/state followed by two creates (`ARCHITECTURE-SPINE.md:113`; `WDR-AND-TRANSACTION-PROTOCOL.md:80`). The publication validator represents those preimages as `None` and accepts the branch (`python_runner.py:5577,5601-5623,5690-5700`).

Strict lineage is not absence-aware. The lineage schema only permits `before-pointer` as a normal `panel-current-pointer/1.0.0` document and `before-panel-state` as a normal `panel-state/1.0.0` document (`panel-sync-contracts.schema.json:1474-1514`). The builder unconditionally dereferences and serializes both (`python_runner.py:5905-5906`); the expected descriptor set unconditionally requires both (`python_runner.py:5955-5956`); and restart loading unconditionally retrieves them (`python_runner.py:6030-6033`). Passing the valid first-publication graph into this path necessarily attempts to treat `None` as a document.

The `panel-first-publication-idempotent` vector is also only another freshly constructed absent-state graph; it does not replay after pointer/state and the committed receipt exist (`python_runner.py:5577`). Thus the 512/512 result does not close first publication through strict activation and fresh-process inspect.

**Required correction:** define a conditional generation-1 lineage descriptor set with a typed absence proof, or explicitly make the journal's null preimages the sole absence evidence. Add an end-to-end vector that starts with no pointer/state, publishes generation 1, builds and reloads its lineage in a fresh process, activates strict, inspects fresh, then retries the same transaction against existing state as a no-op.

### H4 - Repair restart has no durable way to discover the committed prefix

AD-7 requires restart to skip committed batch A and retry B from current facts (`ARCHITECTURE-SPINE.md:119`). AD-10 and the idempotency convention similarly rely on a durable matching receipt index (`ARCHITECTURE-SPINE.md:137,161`; `WDR-AND-TRANSACTION-PROTOCOL.md:25`). The registry provides receipt path templates keyed by a transaction token, but no fact/repair receipt index, batch-to-transaction mapping, or receipt-store enumerator (`CONTRACT-REGISTRY.json:100-104`). Repair transaction IDs remain freely selected by the executor (`python_runner.py:4809`).

The advertised restart test does not reload on-disk state. It creates three graphs, then records receipts in a local `durable_receipts` dictionary keyed by a derived group tuple (`python_runner.py:5191-5228,5235-5259`). Neither nonce files nor receipt files are reopened through registry paths after a process boundary.

Unit H may scan `receipts/repair`, Unit P may derive a deterministic transaction ID from the batch, and a third implementation may maintain an index. All can claim the AD's durable semantics while selecting different prior truth and reapplying or skipping different batches.

**Required correction:** register a durable receipt index (or a deterministic batch/command-to-transaction algorithm), its ownership, journal target, ordering, enumeration, and recovery semantics. The conformance test must terminate the first process and let a fresh process discover A, B's invalidated nonce, and the retry cursor only from registered disk paths and raw bytes.

### H5 - Expired production trust roots remain accepted indefinitely

Protocol section 2 says a removed, expired, or compromised root immediately returns implementation conformance to `pending` (`WDR-AND-TRANSACTION-PROTOCOL.md:23`). The release gate instead checks root `not_before/not_after` only against the immutable receipt `executed_at` (`python_runner.py:1674-1677`). Neither release acceptance nor live inspect compares root validity to `accepted_at`, `inspected_at`, or a current trusted clock.

A direct probe set both current roots' `not_after` to `2026-07-24T04:00:00Z`, generated correctly signed receipts at `2026-07-24T03:00:00Z`, and evaluated the gate after that expiry. `release_gate_accepts` returned `true`. A strict Panel can therefore remain authorized by roots the protocol says are already inactive.

**Required correction:** separate signature-time validity from current root activity. The release-evidence-set publisher and every strict open/inspect/publish must reject when the current trusted time is outside an active root interval or the root is no longer in the raw registry. Add post-expiry and clock-unavailable vectors; clock unavailability must be `unverifiable`/`migration-required`, never fresh.

### H6 - “Atomic” release-evidence publication has no transaction or recovery contract

AD-12 and protocol section 9 require receipts/blobs to be stored first and `state/release-evidence/current.json` to be atomically published, while any unindexed/extra/missing bytes fail closed (`ARCHITECTURE-SPINE.md:149`; `WDR-AND-TRANSACTION-PROTOCOL.md:104`). The registry provides only the current-set path and content-addressed receipt/blob templates (`CONTRACT-REGISTRY.json:105-107`). `releaseEvidenceSetV1` has no generation, previous-set CAS, journal ID, or transition receipt (`panel-sync-contracts.schema.json:2057-2096`). No journal kind admits release-evidence targets.

The design loader receives an abstract map and requires its keys to equal exactly the current set's paths (`python_runner.py:1795-1843`). On a real replacement, retaining old content-addressed evidence makes it `extra`; deleting old evidence before or after the current-set swap creates different crash windows. There is no registered rule that lets two implementations converge on recovery or garbage collection.

**Required correction:** define a journaled release-evidence transition with current-set CAS/generation, staging and terminal marker paths, crash recovery, and post-commit garbage collection. Scope directory closure explicitly to the selected set or define an indexed historical store. Add fault injection at every receipt/blob/current-set/delete boundary on POSIX and Windows.

### H7 - The required activation rollback is called “registered” but has no registered transition

Strict capability lifecycle rejection is now clear, but the only recovery route is underspecified. AD-1 and protocol section 2 require a “registered activation transition” to legacy, an epoch increment, reviewed reprovision under the fact lock, full refresh, and a new attestation (`ARCHITECTURE-SPINE.md:83`; `WDR-AND-TRANSACTION-PROTOCOL.md:22`). The 52 contracts contain activation state, capability registry, and bootstrap migration, but no activation-transition command/receipt or capability reprovision transition. The registered journal role closures are only fact, panel, and repair (`WDR-AND-TRANSACTION-PROTOCOL.md:91`).

The strict handler models lifecycle rejection as the absence of a fixture field (`python_runner.py:2163-2168`); it does not execute rollback or reprovision. Two operators can therefore choose incompatible ordering and crash behavior for activation-state replacement, epoch increment, capability bytes, and old-attestation invalidation.

**Required correction:** register an administrative activation-transition contract and validator with native authority, expected activation/capability epochs, exact state/capability targets, journal/recovery behavior, and a durable transition receipt. Exercise crash/restart at rollback, reprovision, refresh, attestation, and re-enable boundaries.

## Medium Findings

### M1 - Flow-graph time is outside the same-generation source-time invariant

AD-6 and protocol section 5 enumerate payloads whose `source_as_of` equals selection `as_of`, but omit flow-graph (`ARCHITECTURE-SPINE.md:113`; `WDR-AND-TRANSACTION-PROTOCOL.md:78`). The flow profile consumes the selection `as_of` (`CONTRACT-REGISTRY.json:1012`), while its pinned brownfield schema has nested `state.as_of` and allocation `as_of` fields (`skills/adp-flow-graph/assets/adp-flow-graph-v1.schema.json:161-166,224-228`). The projection builder's time check deliberately covers only management-panel, state-audit, program-status, roadmap, and meeting-pack (`python_runner.py:5441-5445`).

Two flow producers can emit different nested times for the same immutable generation, and the Panel still claims a uniform `source_as_of`.

**Required correction:** register exact flow-graph JSON pointers that must equal selection `as_of`, or add an envelope-level `source_as_of` binding plus a semantic validator for the nested brownfield fields.

### M2 - Lineage does not carry the root or cardinality that AD-12 claims it closes

AD-12 says each lineage descriptor is exact in kind, contract, root, path, instance, and cardinality (`ARCHITECTURE-SPINE.md:149`; `WDR-AND-TRANSACTION-PROTOCOL.md:86,104`). `generationLineageObjectBase` carries only kind, projection kind, instance key, contract name, object ID, relative path, and hash; `additionalProperties:false` forbids root and cardinality (`panel-sync-contracts.schema.json:1474-1485`). The runner compares only `(contract_name,path)` as descriptor values (`python_runner.py:5922-5957,6009-6015`).

**Required correction:** either add root role/root instance ID and cardinality to the wire descriptor and validation, or amend the AD to state the exact registry-derived proof that supplies them and test root/cardinality substitution explicitly.

### M3 - Native principal derivation still depends on an undefined service-manager attestation

The registry profile describes POSIX identity as “effective UID decimal plus service-manager attestation” and Windows identity as token SID/elevation/impersonation (`CONTRACT-REGISTRY.json:33-50`). The authority context carries only hashes and adapter IDs (`panel-sync-contracts.schema.json:1295-1318`); there is no schema, runtime path, or canonical preimage for the service-manager attestation, UID namespace/container behavior, executable handle identity, or Windows token serialization.

Even after H2 is fixed, two native adapters can derive different principal IDs from the same process while following the prose.

**Required correction:** pin platform-specific canonical authority preimages and the provenance/verification of each component. Native conformance must compare known OS identities and reject environment, container/user-namespace, symlink, replacement-executable, and impersonation substitutions.

### M4 - The Python support-review deadline is checked against receipt time, not current release time

Protocol section 9 says receipts are rejected once `support_review_before=2026-09-01T00:00:00Z` arrives unless the registry policy is reviewed (`WDR-AND-TRANSACTION-PROTOCOL.md:106`). The gate only rejects when the receipt's historical `executed_at` is after the deadline (`python_runner.py:1674-1676`). A July receipt remains accepted in September with the stale policy.

**Required correction:** evaluate the deadline against release-set acceptance and each strict open/inspect/publish time, with a trusted-clock failure mode, and add a vector that reuses a pre-deadline receipt after the deadline.

### M5 - “Independent implementations” is enforced only by self-declared IDs and build hashes

AD-11 relies on two independent production implementations (`ARCHITECTURE-SPINE.md:143`). The release gate requires distinct `implementation_id` and `adapter_build_id` values (`python_runner.py:1621-1629`) but does not bind a trust root to an allowed implementation identity, source/repository identity, or independent owner. The same signer can issue two renamed builds and satisfy the count.

**Required correction:** define what independence means operationally and bind signer/root policy to implementation ownership or reviewed source/build provenance. At minimum require distinct authorized signer identities and reject one root authorizing both counted implementations unless explicitly reviewed.

## Low Finding

### L1 - Spine update metadata predates the authoritative v17 run

The spine frontmatter remains `updated: '2026-07-24'` (`ARCHITECTURE-SPINE.md:10`), while the authoritative memlog contains v17 decisions and the deterministic gate on 2026-07-25. This does not affect content identity, but it makes human currentness checks misleading.

**Required correction:** update the date only when the gate eventually passes and the spine is finalized; keep `status: draft` until then.

## Evidence and Reality Check

- Current raw pins are internally consistent: registry `f02b7af8...1f26`, schema `18841ac0...c9`, protocol `0dd17ab3...80b3`, suite `73dcbd57...ea62`, Python runner `acad24c7...e61`, and Node runner `5f9dc284...4fe16`.
- Checked-in Python and Node receipts each contain 512 passed IDs, zero failed IDs, and identical passed-ID sets. Both remain `design-fixture-check` with `native_durability_exercised=false`.
- Raw registry production state is correctly fail-closed: `implementation_conformance_status=pending` and `evidence_trust.trust_roots=[]` (`CONTRACT-REGISTRY.json:17-18,153`). These values must not change to production-ready while this review has High findings.
- The intended strict runtime modules are still not the brownfield production implementation. This is consistent with `implementation_conformance_status=pending`; the 512-vector package is design evidence, not proof of native filesystem, OS authority, or fresh-process behavior.

## Gate Decision

Do not set `ARCHITECTURE-SPINE.md` to `final` and do not authorize strict production publication. Resolve H1-H7, regenerate all affected pins/results, and rerun the gate. Minimum acceptance probes are: fully rebound false-attestation live inspect, stale activation-context mutation, generation-1 first publish through fresh-process strict inspect and actual idempotent replay, disk-only repair prefix discovery, post-expiry trust inspection, release-evidence crash recovery, and rollback/reprovision crash recovery.
