# Architecture Spine Adversarial / Data-Integrity Review v8

## Frozen Review Base

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `f9ebcf3aabc2ecf3b67d736585b4188d3199fa2a3419c96dba3332b8106c830a` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `1bad52f7bfb28754c71e888928f01367a105cdfb0771d0919bc27071a2976818` |
| `contracts/CONTRACT-REGISTRY.json` | `222e7bc0b01f86ff6396ef630452170b28073c6c6f9bf8ee0da9909ab88c0e50` |
| `contracts/panel-sync-contracts.schema.json` | `d11b05146d1a8f88a5209c9e93591032d0453083f4ba6923ac3d3fe63b9c37a6` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `0545c52d42aa7e58d714457b6054b53994e7f76ae665f50b71454141e7b722b2` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `4ccfe6388bbbdcffac6250c90b99924a6b28d36fe598a31acf829cbc4c243a30` |
| `contracts/fixtures/PANEL-V1-COMPATIBILITY.json` | `74b4787a48955862622e0a5616a539cef73b44b15d703c7fa4febfaee49dfbb9` |
| `contracts/conformance/python_runner.py` | `906c155562306f8d3c228ac1339040c6e91baa40a66ecf0e79771f02975f87c8` |
| `contracts/conformance/node_runner.mjs` | `83e74c4adf3b958f6a1f12f1e9b90977db0eb2128a5ca202090572fd064f31a5` |
| `contracts/conformance/python-result.json` | `4757356132ce20b2cb4061aa18e015d74e85389ec592b513c2ab66fee6f41958` |
| `contracts/conformance/node-result.json` | `20b4a2294ff407d6c9d21bf10ddc26e5cf154f1bd0f98f04d3b21dadaf1486c7` |

The input remained unchanged across the review. The registry contains 40 contract anchors, 11 pinned source/compatibility artifacts, 7 profiles, 7 outer bindings, 4 nested bindings, 15 DAG edges, 24 ordering rules, and 5 semantic validators. The suite has 132 unique vector IDs. Both runners independently reproduced 132 passed / 0 failed into `/tmp`, byte-for-byte matching the checked-in results; their passed-ID sets are identical, and both result IDs independently recompute. The Panel v1 fixture also regenerates byte-for-byte. These are design-fixture checks only; no native durability inference was made.

The mandatory spine lint passed through the same script with system Python because `uv` is unavailable.

## Verdict

**FAIL. Critical: 0. High: 7.** The v7 changes close the obvious schema shapes and add named validators, but the executable gate still accepts stale visible Panel content, an empty/subset publication scope, unconsumed live leaves, command/receipt disagreement, invalid panel journals, and repair bindings derived from out-of-band data. The release-ID uniqueness fix itself is closed.

## Critical

None.

## High

### H1 - Additive Panel v2 does not bind the visible v1 model to the same-generation canonical payload

`managementPanelPayloadV2` requires both `model_v1` and `sync`, but no contract or semantic rule relates `model_v1.data.status|roadmap|flows|meetings` to `sync.canonical` (`panel-sync-contracts.schema.json`, `managementPanelPayloadV2`). `panel_v1_compatibility_valid()` validates the v1 schemas and a static fixture's keys/hashes, then checks current fields only in `sync.canonical.status.workstream_current`; it never proves that the deployed v1 rendering surface was recomposed from those current inputs (`python_runner.py:629-656`; `node_runner.mjs:176-192`). The accepted fixture itself carries a v1 model generated on `2026-07-13` beside `sync.source_as_of=2026-07-24`, and the two status payloads are unequal.

Two AD-compliant implementations can therefore diverge: producer A recomposes `model_v1` from the current generation before embedding it; producer B preserves the previous valid v1 blob byte-for-byte and only updates `sync.canonical`. Both satisfy the additive/nested schemas and publication predicate, but the unchanged v1 UI can show different, stale data. This directly reopens the Management Panel lag problem.

**Required fix:** define a same-generation composition binding for every v1 consumer pointer (or change the renderer contract to consume only v2 canonical fields), validate source-to-target equality/allowlist semantics against the actual generation, and add disagreement vectors where `model_v1` is valid but stale.

### H2 - Publication eligibility is internally consistent but not bound to the generation selection scope

The publication predicate checks only the drift document's own `selected_workstreams` versus its own rows (`python_runner.py:221-280`; `node_runner.mjs:132-201`). Both arrays may be empty, `overall_status="in-sync"`, and the predicate returns eligible even while `sync.canonical.status.workstream_current` is non-empty. It also does not cross-check the drift `selection_policy_id` with the generation envelope, manifest, producer receipt, Panel catalog, or canonical status workstream IDs. The lineage helper likewise never compares manifest and receipt selection IDs (`python_runner.py:702-726`).

This is a concrete false-green: an implementation can audit a strict subset or empty scope, report every selected row in sync, and publish unrelated current rows. The v7 drift/missing/malformed vectors only mutate a row inside the self-declared scope; none exercises selection omission.

**Required fix:** make publication validation consume the generation envelope and selection policy, derive the exact selected physical workstream set, and require equality with drift rows, audit scope, canonical status rows, manifests, and receipts. Reject empty scope unless the resolved policy is genuinely empty.

### H3 - Registry-derived DAG/read-set/ordering validation is still synthetic and false-green

The claimed complete lineage fixture writes `sources=[]` and `consumed_sources=[]` for every producer even though six profiles declare live leaves (`python_runner.py:659-698`; `node_runner.mjs:297-337`). `projection_lineage_semantics()` validates predecessor handles but never validates source leaves or exact actual/allowed reads. The positive read-set check is a tautology, `resolved_read_set(profile) == resolved_read_set(profile)`, while both negative cases mutate one synthetic sample rather than a producer document (`python_runner.py:827-843`). `registry-dag-change-invalidates-every-edge` invokes the same static edge-equality helper as the inventory vector; it does not change an input or observe invalidation. The ordering helper never resolves `contract` or `pointer`, never validates a schema-valid instance, and does not implement NFC or null-first tuple ordering (`python_runner.py:474-524`).

Consequently a producer that omits every live source can obtain the same 132/132 receipt. A typo in any of the 24 ordering pointers also remains green.

**Required fix:** build schema-valid complete documents for all profiles/rules, materialize resolved source records from a generation envelope, compare them to instrumented actual reads and manifest/receipt sources, mutate every DAG source and observe the declared downstream plan, and execute each ordering pointer with canonical, permuted, duplicate, NFC, and null-key cases.

### H4 - Fact authorization attribution does not bind action deltas to the authorized command

The validator authenticates the command fingerprint and copies journal targets, but its final action check only verifies revision arithmetic (`python_runner.py:408-440`; `node_runner.mjs:293-311`). It does not require one delta for an action command, `delta.action_id == command.action_id`, operation equality, `before_revision == expected_revision`, changed fields equal the command `set`, or evidence fingerprints equal command evidence. `factMutationReceiptV1.action_deltas` has no `minItems`.

A complete schema-valid fixture with the patch command unchanged, `action_deltas=[]`, and a recomputed `receipt_id` was accepted by `fact_attribution_semantics=True`. A receipt may therefore authenticate a real command while omitting or misreporting the exact mutation the user needs to audit and repair.

**Required fix:** derive the expected delta set from the authorized command/batch and compare exact IDs, operations, revisions, changed fields, and evidence fingerprints; reject missing, extra, or duplicate deltas and add those complete-document vectors.

### H5 - Journal semantics accepts transaction-kind-invalid targets and non-commit markers

`journal_fixture("panel")` contains only roles `business`, `fact-generation`, and `receipt`, yet `journal_semantics()` accepts it as a valid panel journal. The validator checks receipt count but never enforces the role/target closure required for a panel publication (`projection`, `panel`, `pointer`, `panel-state`, receipt), or the corresponding fact/repair sets (`python_runner.py:300-352`). It also accepts a recomputed marker with `state="prepared"` exactly like `committed`, and does not require the locator path to be the prescribed journal-local `images/<order>-before|after`.

This yields the second explicit incompatible implementation pair: implementation A emits a durable `prepared` marker before target application and treats it as non-commit state; implementation B emits no marker until the unique commit point. Both are admitted by the schema/semantic validator, but the recovery rule "有marker必须all-after" gives them opposite crash behavior. Likewise, one panel adapter may publish only generic business/generation roles while another requires the full projection/pointer set.

**Required fix:** make the journal validator transaction-kind aware, bind exact target-role/cardinality sets, enforce locator paths, and define separate prepared versus committed/rolled-back marker state transitions and recovery behavior. Test every state at every apply boundary using complete manifests.

### H6 - Repair graph identity trusts an out-of-band binding preimage and is not a complete transaction graph

`repair_graph_semantics()` hashes `graph["binding_input"]` supplied by the caller instead of reconstructing that object from the validated audit, dry-run request, batch, read set, principal/scopes, roots, outcome, and pinned contract hashes (`python_runner.py:562-626`; `node_runner.mjs:358-390`). Changing that out-of-band principal to `attacker`, recomputing the binding digest and nonce state ID, while leaving the validated dry-run/apply principal as `operator-1`, still returned `True`. The existing binding mismatch vector changes only one digest without recomputing linked identities, so it misses this substitution.

The validator also selects the first audit batch rather than resolving `dry_request.batch.batch_id`, has no issuance time with which to enforce the 15-minute maximum, verifies only a final nonce state rather than the CAS transition, and does not include the repair journal plus fact/repair receipts despite AD-10's two-receipt transaction requirement.

**Required fix:** register a schema for the binding preimage or reconstruct it internally, select the wire batch by exact ID, include journal/fact/repair receipts and nonce transition evidence in the graph, and add recomputed-substitution, non-first-batch, expiry, transition, and receipt/journal mismatch vectors.

### H7 - The two reference runners implement different non-JCS property ordering

AD-11 and protocol section 1 require RFC 8785 JCS for every identity, but Python uses `json.dumps(sort_keys=True)` while Node sorts JavaScript strings (`python_runner.py:18-20`; `node_runner.mjs:10-13`). Those algorithms differ from each other on RFC 8785's UTF-16 property-name ordering boundary. For `{"\uE000":1,"😀":2}`, Python emitted `{"\uE000":1,"😀":2}` with SHA-256 `871954...`, while Node emitted `{"😀":2,"\uE000":1}` with SHA-256 `28c95d...`. The suite has one ASCII-key JCS vector, so both pass.

Thus two candidate adapters can pass the exact release suite yet disagree on command, projection, manifest, batch, token, and receipt identities containing such keys. This is a direct wire incompatibility, not native durability evidence.

**Required fix:** use a verified RFC 8785 implementation in both runners and production adapters, and add RFC 8785 number/string/property-order edge vectors including supplementary-plane versus BMP keys, escaping, and invalid Unicode handling.

## Verified Closures And Evidence Boundary

- The Panel v2 outer shape is additive and both Panel v1 model and manifest schemas are pinned; the remaining High is same-generation semantic equivalence, not missing shape.
- The selected-row `drift|missing|malformed`, blocked-audit, and stale-freshness cases now block publication; the remaining High is omitted/foreign selection scope.
- All 7 outer and 4 nested schema bindings resolve to pinned raw hashes, and malformed outer payloads are rejected.
- Distinct `implementation_id` and distinct `adapter_build_id` are both enforced, with duplicate implementation/build vectors. v7 H4 is closed.
- Authorization references, five semantic validator registrations, and negative journal/repair vectors exist. The remaining findings are executable cross-document gaps demonstrated above.
- Registry/schema/protocol/vector/compatibility pins, 132 unique IDs, checked-in result IDs, and Python/Node pass sets are internally consistent.
- Both checked-in results remain correctly labeled `design-fixture-check` with `native_durability_exercised=false`; native POSIX fault injection, native Windows CI, and production-adapter receipts remain pending and were not inferred from fixtures.

## Exit Conditions

1. Bind the deployed v1-visible model and publication selection to the same generation/canonical inputs.
2. Replace synthetic registry closure checks with complete source-bound documents and real per-edge/per-pointer mutations.
3. Bind fact deltas exactly to authorized commands.
4. Close transaction-kind journal roles, marker states, image paths, and recovery semantics.
5. Reconstruct the complete repair binding/transaction graph from validated wire documents.
6. Replace both canonicalizers with RFC 8785 and add cross-runtime edge vectors.
7. Regenerate the complete hash/result chain and rerun the independent final gate on one frozen target.
