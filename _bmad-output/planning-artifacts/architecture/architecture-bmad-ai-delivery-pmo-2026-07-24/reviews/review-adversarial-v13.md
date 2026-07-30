# Independent Adversarial Architecture Review v13

Target: `ARCHITECTURE-SPINE.md` and its normative contract package.

Lens: construct two one-level-down implementations from the written ADs, protocol, registry, schema, fixtures, and reference handlers, then look for cases where both can claim conformance while producing different facts, revisions, projections, publication decisions, or recovery behavior.

## Verdict

**FAIL.** The v12 freshness fixes are materially present, and both checked-in runners report the same 346 passed vectors with zero failures. The package is still not ready to finalize: five High-severity divergence points remain in the mutation and strict-rollout paths. The current `draft` status and `implementation_conformance_status=pending` must remain.

| Severity | Count |
| --- | ---: |
| Critical | 0 |
| High | 5 |
| Medium | 3 |
| Low | 0 |

## High Findings

### H-1 - The documented first brownfield migration cannot pass the registered fact-attribution path

**Divergence pair.** Implementation A follows the protocol: it treats a missing legacy WDR state as revision/generation `0/0`, accepts the legacy section order, migrates the WDR, and journals a WDR `replace` plus a WDR-state `create`. It similarly parses the pinned 12-column or brownfield 20-column action ledger and writes the canonical 21-column ledger on first mutation. Implementation B follows the executable reference path: it requires a pre-existing schema-valid WDR state and an already-canonical WDR before every patch, and its ledger parser accepts only the canonical 21-column form. A migrates brownfield facts; B blocks the same facts. The package currently calls both behavior sets conformant.

**Evidence.** The migration requirement is explicit in the protocol at `contracts/WDR-AND-TRANSACTION-PROTOCOL.md:42`, `:45`, and `:51`, and in AD-3 at `ARCHITECTURE-SPINE.md:95`. The target derivation makes every patch target a `replace`, including the missing WDR state (`contracts/conformance/python_runner.py:1497-1501`; Node equivalent `contracts/conformance/node_runner.mjs:920-924`). Fact attribution then requires `before_wdr_state` and `complete_wdr_valid(before_wdr)` before applying a patch (`contracts/conformance/python_runner.py:2013-2021`; Node `contracts/conformance/node_runner.mjs:1227-1235`). The only ledger parser begins from the exact v2 preamble/header (`contracts/conformance/python_runner.py:215-251`; Node `contracts/conformance/node_runner.mjs:173-200`). The lone legacy WDR vector calls a standalone reorder helper and never carries the legacy preimage through journal/proof/receipt attribution (`contracts/fixtures/CONFORMANCE-VECTORS.json:297`; Python dispatch `contracts/conformance/python_runner.py:3924-3926`). There is no ledger migration vector.

**Required fix.** Define command-derived mixed target operations for first migration, including absent WDR-state and any required sidecar creation. Add a registered legacy grammar/adapter for the exact 12- and 20-column headers and deterministic mappings. The fact-attribution handler must accept only the pinned legacy preimages at expected `0/0`, derive the canonical after images itself, and reject all other malformed preimages. Add full journal/proof/receipt vectors for first WDR migration and both ledger migrations in both runners.

### H-2 - WDR revision semantics in the protocol and executable validators contradict each other

**Divergence pair.** Implementation A follows the written two-counter model: a history-only or owned-section command increments only `file_generation`, a current-field command increments both counters, and a byte no-op increments neither. Implementation B follows both reference handlers and their positive vectors: every patch increments both `wdr_revision` and `file_generation`, including Meeting Sync History and Checkpoint Sync Log writes. The next status command therefore uses different WDR CAS values, and identical command sequences produce different state and receipt identities.

**Evidence.** The normative rule says `wdr_revision += 1` only when a current field is present, always increments file generation for a real file change, and does not increment on a pure no-op (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:42`). The implementation plan repeats that history/owned-section writes increment generation only (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:215`). The fixture builder hardcodes `4/7 -> 5/8` for all WDR patches (`contracts/conformance/python_runner.py:1760-1761`; Node `contracts/conformance/node_runner.mjs:1063-1065`), and attribution unconditionally requires both `+1` (`contracts/conformance/python_runner.py:2019-2020`; Node `contracts/conformance/node_runner.mjs:1233-1234`). The suite explicitly accepts that behavior for history-only and owned-section commands (`contracts/fixtures/CONFORMANCE-VECTORS.json:515-516`) and has no no-op case.

**Required fix.** Register one exact counter-transition algorithm driven by the command-derived before/after WDR bytes and the current-field set. Pin behavior for same-value scalar patches, add/remove with no membership change, duplicate history replay, and unchanged owned-section replacement. Correct the fact handlers and add positive/negative vectors for current-only, history-only, section-only, mixed, and no-op commands.

### H-3 - Conflicting status intents can bypass conflict detection by binding to separate commands

**Divergence pair.** Given two accepted intents for one workstream that assign different values to `progress`, Implementation A binds both to one WDR command and rejects the conflict. Implementation B binds each intent to a separate WDR command. Each command then has a locally exact field/evidence union, so the registered semantic validator accepts the batch; command-ID ordering determines which value wins, and the WDR revision advances twice. Both obey the current rule that each intent has exactly one same-workstream binding and that conflicts are checked only among intents bound to the same command.

The same under-specification exists without a value conflict: one producer can emit one WDR command per workstream while another emits one per intent. They produce equal current values but different revisions, file generations, receipts, retry cursors, and failure boundaries.

**Evidence.** Protocol section 2 defines merging and conflict rejection only “绑定到同一command” and never requires one WDR patch per workstream (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:22`). The schema permits any number of WDR patches and bindings (`contracts/panel-sync-contracts.schema.json:480`). The Python handler accumulates conflicts by `command_id`, not workstream (`contracts/conformance/python_runner.py:972-988`); Node does the same (`contracts/conformance/node_runner.mjs:555-562`). The only conflict vector changes two intents already bound to the single fixture command (`contracts/fixtures/CONFORMANCE-VECTORS.json:596`), so it does not test split-command bypass. This also leaves expected WDR revision/file-generation chaining for multiple same-workstream commands unspecified under the stop-on-first-failure policy.

**Required fix.** Either require exactly one status-intent WDR patch per workstream per batch, or define a workstream-wide merge/conflict pass that runs before command grouping and a deterministic grouping rule. Pin the expected CAS chain for any permitted same-workstream multi-command case. Add vectors for conflicting and disjoint fields split across commands, same-workstream CAS at command N, partial commit, and retry from N.

### H-4 - Manual Next-actions preservation is both under-specified and violated by the references

**Divergence pair.** Even if both implementations honor “manual entries preserve original order” and “managed entries sort by action ID,” one can leave manual entries in their original slots while sorting only managed slots, and another can emit all manual entries first followed by sorted managed entries. Both preserve the two stated relative orders but produce different WDR bytes. The current references implement a third behavior: they replace the entire `Next actions` collection with sidecar summaries, deleting every manual entry.

This also conflicts with drift: the current algorithm compares the entire parsed WDR `Next actions` list to sidecar managed summaries, so any preserved manual entry necessarily becomes `wdr-content-mismatch`.

**Evidence.** Manual preservation is normative at `contracts/WDR-AND-TRANSACTION-PROTOCOL.md:56` and repeated in the contract seed at `ARCHITECTURE-SPINE.md:193`. `refresh_actions` instead replaces the whole label from `action_summaries` (`contracts/conformance/python_runner.py:818-819`; Node `contracts/conformance/node_runner.mjs:482`). Drift compares the full list to the sidecar actions only (`contracts/conformance/python_runner.py:1112-1114`; Node `contracts/conformance/node_runner.mjs:599-600`). No fixture contains a mixed manual/managed list.

**Required fix.** Define an exact partition/parser and total output order for manual and managed entries. Keep manual bytes stable under refresh, compare only the managed subsequence to the sidecar, and specify how malformed marker-like text is treated. Add refresh, drift, repair, duplicate, and idempotent-replay vectors containing manual entries before, between, and after managed entries.

### H-5 - The strict writer-fence migration attestation is not a wire contract or publication input

**Divergence pair.** After production conformance changes to `passed`, Implementation A considers a locally stored boolean plus a successful full-refresh receipt to be a “current, complete” migration attestation. Implementation B requires a content-addressed document listing every projection-relevant writer, capability epoch, upgraded ledger/WDR/sidecar fingerprint, fact generation, and resulting published generation. Both can claim to follow AD-12 because none of those fields, identity rules, freshness rules, storage paths, or completeness criteria is defined. A can enable strict mode while an unfenced writer remains.

**Evidence.** AD-12 and protocol section 9 make the attestation a mandatory strict-open/inspect/publication gate (`ARCHITECTURE-SPINE.md:145-149`; `contracts/WDR-AND-TRANSACTION-PROTOCOL.md:97-101`). The 44 registry contracts include `root-registry-state` but no writer-fence migration attestation; the root state contains only two root identities and creation time (`contracts/CONTRACT-REGISTRY.json:349`; `contracts/panel-sync-contracts.schema.json:1204`). No runtime path, semantic validator scope, publication-eligibility input, or conformance vector mentions the attestation. The only executable strict-related state is the static registry value `pending` (`contracts/CONTRACT-REGISTRY.json:48`).

**Required fix.** Add a registered, content-addressed migration-attestation contract and runtime path. Define the authoritative writer inventory, per-writer fence/capability evidence, ledger/WDR/sidecar migration evidence, fact generation, full-refresh/published-generation binding, expiry/invalidation conditions, rollback behavior, and a semantic validator used by strict open, inspect, and publication eligibility. Add missing/stale/subset/forged attestation, writer-after-attestation, local rollback, and re-enable-after-full-refresh vectors.

## Medium Findings

### M-1 - Refresh and drift do not bind all sidecar/WDR-state metadata to the target workstream and current renderer

The refresh validator derives ledger fingerprint/revision/actions but does not require the after sidecar's `workstream_id`, `renderer_id`, or `renderer_sha256` to equal command/registry values (`contracts/conformance/python_runner.py:2066-2072`; Node `contracts/conformance/node_runner.mjs:1254-1260`). The WDR-state check similarly omits command-derived equality for `workstream_id`, `record_path`, and `lifecycle` after a patch (`contracts/conformance/python_runner.py:2002-2021`). Drift validates schema and raw WDR fingerprint but never checks sidecar workstream/renderer binding or WDR-state path/lifecycle (`contracts/conformance/python_runner.py:1149-1158`; Node `contracts/conformance/node_runner.mjs:621`). One producer can retain the current renderer hash; another can copy an arbitrary hash-shaped value and still produce an `in-sync` verdict if action content matches. Bind every identity/lineage field in fact attribution and drift, then add rebound vectors.

### M-2 - Collection and managed-marker grammars still contain value collisions and partial parsing

The schema allows `"TBD"` as a real blocker/risk/dependency item and also allows an empty `values` list (`contracts/panel-sync-contracts.schema.json:14` and the `collectionPatch` definition). The references render an empty list as `TBD` and parse literal `TBD` as empty (`contracts/conformance/python_runner.py:743-793`), so `replace: ["TBD"]` and `replace: []` collapse to the same WDR bytes and Program Status value. In addition, Program Status accepts any Next-actions item with a managed-marker prefix rather than requiring the protocol's full marker grammar (`contracts/conformance/python_runner.py:872-879`; Node `contracts/conformance/node_runner.mjs:519-521`). Define a collision-free empty encoding, exact scalar trim/NFC behavior, and a full unambiguous marker escape/parser; add round-trip vectors for `TBD`, delimiter text, marker-like manual text, and owner/action/due delimiter characters.

### M-3 - Registry ordering closure omits identity-bearing nested arrays whose order the handlers nevertheless fix

The registry registers the top-level status batch arrays but not `intent_bindings/*/fields`; it also has no canonical-array rule for `fact-mutation-proof.business_artifacts` or `.read_artifacts`, or command/intent evidence arrays (`contracts/CONTRACT-REGISTRY.json:560-620`). The schema leaves these arrays order-permissive (`contracts/panel-sync-contracts.schema.json:480` and `:1188`). Nevertheless, the status handler requires UTF-8-sorted binding fields (`contracts/conformance/python_runner.py:978-980`) and fact attribution requires one hard-coded read-artifact order (`contracts/conformance/python_runner.py:2051`). A registry-only implementation preserves unregistered input order under protocol section 1, while a runner-matching implementation sorts/rejects it; command/proof identities and receipts diverge. Register every set-like nested array, explicitly mark truly sequence-semantic arrays, and add a schema-to-registry closure check rather than testing only the rules already present.

## Confirmed Closures From v12

The following v12 blockers are closed in the current package and were not reopened by this review:

- Program Status rows are now derived field-for-field from selected WDR bytes and WDR-state lineage.
- Drift now recomputes active routing-or-affected membership, full sidecar records, WDR lineage, and WDR action content.
- Ordinary and repair `refresh_actions` carry exact ledger and ledger-state read preimages and validate the same snapshot algorithm.
- Canonical projection and Management Panel immutable paths are registry-derived with known-answer vectors.
- The formerly missing action-state/flow/sidecar ordering rules are registered.

## Reviewer Conclusion

The architecture now addresses the original stale-Panel transformations, but the mutation substrate and strict activation gate are not yet convergent. In particular, the reference suite's 346/346 result currently certifies behavior that contradicts the WDR counter contract and does not exercise the first brownfield migration. Resolve all five High findings, add the named negative and lifecycle vectors, regenerate both result receipts, and rerun the independent gate before changing `status: draft` or registry production conformance.
