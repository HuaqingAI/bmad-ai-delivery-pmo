# Independent Adversarial Architecture Review v12

Target: `ARCHITECTURE-SPINE.md`

Lens: construct two one-level-down implementations that obey the written ADs and registered shapes but still diverge in shared state, mutation, projection, publication, or recovery behavior.

## Verdict

**FAIL.** The architecture is not ready to finalize. Two core freshness gates can accept a stale or false-green Panel, and four additional contracts still permit incompatible implementations.

| Severity | Count |
| --- | ---: |
| Critical | 2 |
| High | 4 |
| Medium | 0 |
| Low | 0 |

## Critical Findings

### C-1 - WDR current fields are not semantically bound to `program-status.workstream_current`

**Divergence.** AD-4 freezes the read inventory, envelopes, hashes, same-generation links, Panel bindings, and the final browser read set. None of those checks establishes that a Program Status row is the deterministic projection of the selected WDR's current fields.

- Implementation A reads `delivery-record.md` and maps its current `Progress`, `Blockers`, `Risks`, `Dependencies`, `Current ADP status`, and `Current BMM phase` into `workstream_current`.
- Implementation B performs the same instrumented WDR read, includes the new bytes in `source_preview`/manifest, but carries forward the previous snapshot's schema-valid `workstream_current` rows. It therefore has the exact allowed/actual read set, a new source fingerprint, a same-generation envelope, and valid Panel bindings while showing the old values.

Both can satisfy the literal registered gates. The v2 consumer only proves that a change already present at `/sync/canonical/status/workstream_current` reaches HTML; it does not prove that a WDR-only change reaches that pointer.

**Evidence.** AD-4 specifies exact reads and Panel consumption at `ARCHITECTURE-SPINE.md:99-101`, but the Program Status row carries values without a WDR fingerprint/revision binding (`contracts/panel-sync-contracts.schema.json:649-662`) and its payload only requires a schema-valid `workstream_current` plus independent `source_preview` (`contracts/panel-sync-contracts.schema.json:1545-1560`). The registry binds the consumer to the already-produced row (`contracts/CONTRACT-REGISTRY.json:635-645`) and has no WDR-to-current semantic validator among the eight validators (`contracts/CONTRACT-REGISTRY.json:545-553`). The conformance vector `panel-v2-consumer-current-fields-visible` mutates the Panel payload, not a physical WDR (`contracts/fixtures/CONFORMANCE-VECTORS.json:631`).

**Required fix.** Register a semantic validator that, from the generation's selected WDR blobs and pinned WDR grammar, derives the exact row set and exact values for all current fields and compares them byte-for-byte with `program-status.workstream_current`. Bind each row to WDR fingerprint, WDR revision, and file generation (or an equivalent unambiguous source reference). Add an end-to-end vector that mutates only a physical WDR current field, executes the actual Program Status producer and Panel consumer, and rejects a producer that carries the prior row forward.

### C-2 - The drift gate can declare `in-sync` without comparing ledger, sidecar, or WDR action content

**Divergence.** AD-5 says the drift producer reads the three action representations and prevents missing active actions, retained terminal actions, and owner/text/due divergence. The registered verdict contains only file fingerprints, an asserted row status, and finding IDs; it does not contain or bind the expected/actual action sets. The semantic rule only fixes selected-row coverage and the overall roll-up.

- Implementation A parses ledger records, deterministically selects the actions relevant to each workstream, compares every active record to the sidecar and exact WDR marker, and reports a missing action as `drift`.
- Implementation B reads the same required files, copies their fingerprints into the verdict, emits one row per selected workstream with `status: in-sync`, and sets `overall_status: in-sync` without performing the record comparison.

Both satisfy the schema, exact read-set instrumentation, selection coverage, and current publication eligibility checks. Implementation B is also behaviorally equivalent to the current reference `drift_semantics` handler, which checks only row coverage and asserted status. A stale WDR action projection can therefore pass the publication gate.

**Evidence.** AD-5's intended prevention and read requirements are at `ARCHITECTURE-SPINE.md:103-107`; protocol section 5 repeats only coverage/roll-up at `contracts/WDR-AND-TRANSACTION-PROTOCOL.md:58`. The drift schema has no expected/actual records or WDR-state binding (`contracts/panel-sync-contracts.schema.json:591-619`). The registered publication validator trusts drift status (`contracts/CONTRACT-REGISTRY.json:545-546`). The only positive drift vector supplies workstream/status/overall assertions (`contracts/fixtures/CONFORMANCE-VECTORS.json:572`). The Python reference handler implements only coverage plus status roll-up (`contracts/conformance/python_runner.py:567-573`).

**Required fix.** Define and register the exact drift algorithm: active-status set; workstream membership from `routing_scope_id` and `affected_workstreams`; canonical action order; terminal exclusion; exact owner/action/due/status/revision comparison; WDR marker parsing; sidecar ledger fingerprint, WDR revision, and file-generation checks; and finding generation. Extend the verdict with deterministic expected/actual action IDs or comparison digests sufficient to revalidate the conclusion. Add false-green vectors that mutate each ledger field, omit an active row, retain a terminal row, substitute a fingerprint, and assert `in-sync`; all must fail before publication.

## High Findings

### H-1 - Accepted status intents are not causally or field-for-field bound to WDR commands

**Divergence.** A status intent can contain several current-field mutations, while a Status Sync batch carries only an independent set of `accepted_intent_ids` and an independent array of WDR patches.

- Implementation A accepts an intent containing `progress` and `blockers` and emits one status-sync-authorized WDR command containing both fields and the intent evidence.
- Implementation B lists the same intent ID as accepted but emits a WDR command containing only `progress` (or emits an unrelated patch for the same workstream). It still "validates then emits a WDR command," has valid authorization, and its final fact transaction passes attribution.

No registered rule states whether acceptance is all-or-nothing, defines precedence between multiple intents, or proves the exact accepted intent field/evidence union equals the emitted command set. The two implementations leave different WDR facts and Panel values.

**Evidence.** AD-1 says status-sync validates an intent then issues a command but does not define the mapping (`ARCHITECTURE-SPINE.md:79-83`). `statusMutationIntentV1` defines the input fields (`contracts/panel-sync-contracts.schema.json:460-486`), while `statusSyncBatchV2` leaves accepted IDs and patches unrelated (`contracts/panel-sync-contracts.schema.json:446-457`). No semantic validator scopes either contract (`contracts/CONTRACT-REGISTRY.json:545-553`). The `meeting-status-intent-routed` vector checks only origin, issuer, and the field-name subset (`contracts/fixtures/CONFORMANCE-VECTORS.json:322`; reference handler `contracts/conformance/python_runner.py:3071-3072`).

**Required fix.** Add a registered `status-intent-application` semantic contract. It must define precedence, all-or-partial acceptance, explicit per-field disposition/rejection reasons, exact evidence propagation, workstream grouping, command ordering, and a bidirectional intent-to-command mapping. Add omission, substitution, cross-workstream, evidence-drop, and conflicting-intent vectors.

### H-2 - Ordinary `refresh_actions` has neither a bound ledger snapshot nor a normative projection-membership algorithm

**Divergence.** `refresh_actions` changes WDR, WDR state, and action sidecar, but the command CAS covers only WDR revision/file generation and the ordinary fact proof carries only business-target before/after bytes.

- Implementation A acquires the fact lock and rebuilds from the latest ledger, including active actions whose `routing_scope_id` equals the workstream or whose `affected_workstreams` contains it.
- Implementation B freezes a ledger snapshot when the command is queued, or includes only routing-scope matches. If another action transaction commits before apply, B can write a stale/different sidecar and WDR while still satisfying the WDR CAS, exact three-target set, schema, renderer, receipt, and fact-generation increment.

Repair has an explicit ledger read set and cross-check, but the ordinary mutation path does not. Drift could eventually catch this only after the incorrect fact has committed, and C-2 shows the present gate need not catch it.

**Evidence.** AD-1/AD-5 require a separate ordered transaction and ledger-backed sidecar (`ARCHITECTURE-SPINE.md:83`, `ARCHITECTURE-SPINE.md:103-107`); protocol section 4 fixes the three write targets but not a read CAS (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:49-51`). `wdrPatchV1` has WDR CAS and `refresh_actions: true`, but no expected ledger revision/fingerprint (`contracts/panel-sync-contracts.schema.json:352-384`). `factMutationProofV1` contains only business artifacts (`contracts/panel-sync-contracts.schema.json:1106-1130`). The ordinary reference attribution validates sidecar shape/revisions and renders its supplied summaries but does not cross-check ledger bytes (`contracts/conformance/python_runner.py:1312-1335`); that cross-check exists only in repair (`contracts/conformance/python_runner.py:2454-2455`).

**Required fix.** Define the relevant-action selection and ordering algorithm, bind `refresh_actions` to an exact ledger revision/fingerprint read under the same fact lock, carry that read preimage or immutable blob reference in the proof/receipt, and reject apply if it changed. Reuse the same validator for normal refresh and repair. Add a queued-refresh/second-action race and routing-vs-affected membership vectors.

### H-3 - Canonical projection storage paths are hidden runner behavior, not registry truth

**Divergence.** Pointer rows require `canonical_path`, and publication validates exact targets, but the registry only fixes pointer/state/receipt paths. It does not define the generation directory, projection filename, or instance-key encoding.

- Implementation A publishes `views/generations/<generation-hex>/<kind>-<instance>.json`.
- Implementation B publishes the same immutable envelopes at `snapshots/panel/<generation-token>/<kind>/<instance-token>.json`, then places those valid paths in the pointer and journal.

Both provide unique relative paths, content hashes, exact journal targets, and atomic recovery. They cannot consume/recover each other's pointers under an "exact path" validator. The reference harness silently chooses A, so passing its fixture tests depends on behavior absent from the registry/protocol.

**Evidence.** AD-6 says projection/Panel targets are journaled but only pointer/state/receipt paths come from the registry (`ARCHITECTURE-SPINE.md:109-113`). `runtime_paths` has no projection or Panel immutable path template (`contracts/CONTRACT-REGISTRY.json:5-16`), while `projectionPointerRef.canonical_path` accepts any relative path (`contracts/panel-sync-contracts.schema.json:1190-1192`). The semantic validator advertises "exact-target-path" without a path algorithm in its scope (`contracts/CONTRACT-REGISTRY.json:551`). The Python harness invents the format at `contracts/conformance/python_runner.py:2790` and reasserts it at `contracts/conformance/python_runner.py:2905`.

**Required fix.** Put projection/Panel immutable path templates and instance-key/filesystem-token rules in `runtime_paths` (including null-instance representation), include them in the publication validator scope, and add cross-implementation known answers plus alias/collision vectors. Do not leave this algorithm only in a reference runner.

### H-4 - Several durable identity-bearing action arrays have no canonical ordering or duplicate rule

**Divergence.** JCS preserves array order. The schema permits unconstrained order for `action-ledger-state.actions`, `action-ledger-state.applied_commands`, `action-flow-index.actions`, each action's `affected_workstreams`, and `wdr-action-projection.actions`; none appears in the registry's canonical ordering/identity-set tables.

- Implementation A sorts action records by `action_id`, commands by `command_id`, and affected workstreams by UTF-8 NFC bytes.
- Implementation B preserves ledger/insertion order (or uses routing discovery order).

Both emit schema-valid records that describe the same facts, yet calculate different `state_id`, `index_id`, sidecar raw hash, inventory ID, fact proof, and downstream generation identity. A reader enforcing its own canonical convention rejects the other implementation's durable state.

**Evidence.** The unconstrained arrays are at `contracts/panel-sync-contracts.schema.json:259-284`, `contracts/panel-sync-contracts.schema.json:298-310`, and `contracts/panel-sync-contracts.schema.json:565-579`. AD-11 claims all identity-affecting array rules are registry-derived (`ARCHITECTURE-SPINE.md:139-143`), but the complete registered list at `contracts/CONTRACT-REGISTRY.json:566-593` contains none of these pointers. The conformance "all-ordering-rules" vectors exercise only registered rows (`contracts/fixtures/CONFORMANCE-VECTORS.json:444-451`), so they certify the incomplete registry rather than completeness over identity-bearing arrays.

**Required fix.** Inventory every array reachable from an identity-bearing document. Register deterministic order and duplicate policy for the five arrays above (and any further set-like arrays found), distinguish truly sequence-semantic arrays explicitly, and add multi-action/multi-command/multi-workstream permutations to both runners. Add a closure check that fails when a schema-marked set-like array lacks a registry rule.

## Reviewer Conclusion

The architecture has strong transaction, capability, inventory, lineage, and publication mechanics, but the two most important business transformations remain asserted rather than proven: WDR current fields into Program Status, and ledger/sidecar/WDR equality into the drift verdict. The spine must remain `draft`; rerun the independent gate after all Critical and High findings are closed with normative contracts and negative vectors.
