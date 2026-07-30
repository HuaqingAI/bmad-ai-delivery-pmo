# Architecture Spine Good-Spine Rubric Review v8

## Gate Verdict

**FAIL. Critical: 0, High: 3.** The spine covers all five reported business failure modes and its additive Panel v2, publication eligibility, complete outer/nested bindings, repair graph, and distinct implementation release rule materially close five of the eight v7 High findings. The gate still cannot pass because the claimed exact projection read-set evidence is tautological and accepts empty source lineage, Panel publication has no transaction-kind-complete journal/receipt proof, and the normative capability-ID algorithm disagrees with both reference implementations.

The two checked-in results remain correctly labeled `design-fixture-check` with `native_durability_exercised=false`; they are not treated as production conformance evidence in this review.

## Frozen Review Target

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

This reviewer created only this review file. No design, contract, fixture, runner, or production file was modified.

## Verification

- Architecture lint: **PASS, 0 findings**.
- Raw pins: **PASS**. The five normative artifact hashes in both main documents match disk. All 11 registry source/compatibility pins match disk.
- Registry inventory: **PASS at rest**. 40 contracts, 11 pins, 7 profiles, 7 outer bindings, 4 nested bindings, 15 DAG edges, 24 ordering rules, and 5 semantic validators.
- Compatibility fixture: **PASS and reproducible**. Regeneration through the brownfield `panel_model.compose_panel()` path is byte-identical to the checked-in fixture. This remains compatibility design evidence, not production conformance.
- Reference replay: **PASS at declared strength**. Python and Node each reproduce 132 passed / 0 failed; generated result bytes are identical to the checked-in receipts. Both receipts bind the current registry/suite/schema/protocol hashes and remain non-native design fixtures.
- Deferred scope: **PASS**. Action Center, background push/watcher, database migration, fuzzy action matching, and offline live verification do not leak a decision required for this feature.
- Brownfield direction: **PASS with the High exceptions below**. Existing ownership, WDR layout, Panel v1 information architecture, progress-v3/flow-state-v1, cross-platform support, and migration-required rollout are preserved rather than silently replaced.

## Critical Findings

None.

## High Findings

### H1 - v7 H5 is not closed: the exact read-set proof is tautological and the whole lineage fixture consumes no leaves

AD-4 and AD-11 require `actual reads == registry-derived allowed reads`; Protocol section 5 requires every declared source to be consumed, and section 9 says the design runner must derive and compare each profile's resolved allowed/actual read set (`ARCHITECTURE-SPINE.md:101,143`; `WDR-AND-TRANSACTION-PROTOCOL.md:55,86`). The implementation of `profile-read-set-exact-all` instead compares `resolved_read_set(profile)` to the same function call for the same profile in both runners (`python_runner.py:834`; `node_runner.mjs:569`). The missing/extra cases mutate a detached list, not an instrumented producer read trace.

The claimed whole envelope/lineage proof makes the gap concrete: every manifest is built with `sources: []` and every producer receipt with `consumed_sources: []`, even for profiles with required fact/config/evidence sources, and `projection_lineage_semantics()` never compares those arrays with the profile (`python_runner.py:687,694`; `node_runner.mjs:416,418`). It therefore accepts a complete current Panel lineage that consumed zero live leaves. The 24 ordering checks likewise use detached synthetic key objects rather than schema-valid documents at each registered pointer (`python_runner.py:510-525`; `node_runner.mjs:326-337`).

**Impact:** a producer can omit a WDR/ledger/config leaf or skip contract-level array normalization and still obtain all 132 vector IDs. That is the stale-Panel and identity-divergence path v7 H5 required the gate to close.

**Required fix:** run each profile through an instrumented resolver with separately derived allowed and captured actual reads, include those exact source records in manifest and receipt lineage, and reject missing/extra reads there. Exercise each ordering rule on a schema-valid registered document at its RFC 6901 pointer, including noncanonical order and duplicate-key negatives.

### H2 - v7 H6 is only partially closed: Panel publication has no transaction-kind-complete journal and receipt validator

AD-6 requires canonical projections, Panel, current pointer, panel state, and publication receipt to advance in one journal; AD-10 requires the registered journal validator to enforce the transaction boundary (`ARCHITECTURE-SPINE.md:113,137`). The schema has independent `panelStateV1`, `panelCurrentPointerV1`, and `panelPublicationReceiptV1` documents (`panel-sync-contracts.schema.json:1036,1061,1180`), but none is in a registered cross-document publication semantic validator. `journal_semantics()` validates generic image/order/identity and receipt-count rules only (`python_runner.py:323-353`; Node equivalent `node_runner.mjs:238-263`); it does not require transaction-kind-specific roles or bind the receipt's generations/targets and pointer IDs to the journal, pointer, and state.

The suite has a complete fact journal and a repair receipt-count fixture, but no complete Panel journal/publication graph (`CONFORMANCE-VECTORS.json:403-411`). The shared fixture also constructs `business` and `fact-generation` targets for every kind, so it cannot represent the required Panel publication target set (`python_runner.py:300-321`; `node_runner.mjs:219-236`).

**Impact:** a `transaction_kind=panel` journal can omit one or more canonical projections, the pointer, or panel state and still satisfy the registered validator. Independently schema-valid receipt/pointer/state documents can disagree on generation and target identities, permitting exactly the mixed-current publication AD-6 claims to prevent.

**Required fix:** add a Panel-publication semantic graph validator that validates a complete schema-valid journal, pointer, state, receipt, and all published projection handles; enforce the required role set, target equality, `after_panel_generation = before + 1`, pointer/state/generation equality, and receipt identity. Add valid and negative complete Panel transaction vectors.

### H3 - v7 H3 remains ambiguous: the normative capability-ID algorithm conflicts with both runners

Protocol section 2 says a capability ID is the JCS hash of the record after removing only `capability_id` (`WDR-AND-TRANSACTION-PROTOCOL.md:21`). The registered record also requires `authorization_record_digest` (`panel-sync-contracts.schema.json:127-130`). Both runners instead remove **both** `capability_id` and `authorization_record_digest`, then set and require the two resulting fields to be equal (`python_runner.py:283-285,371-373,419`; `node_runner.mjs:203-205,269,301`). Protocol section 4 only says the authorization digest is recomputed after removing unspecified "identity fields" (`WDR-AND-TRANSACTION-PROTOCOL.md:51`), so it does not resolve the conflict.

**Impact:** an implementation following the literal normative protocol calculates a different capability ID from both reference implementations and cannot produce receipts accepted by their fact-attribution validator. This is a wire-level authorization divergence in the exact area v7 H3 was intended to close.

**Required fix:** define one explicit canonical preimage for `capability_id` and one for `authorization_record_digest` (or collapse them into one field), update Protocol/schema semantics to match, and add a fixed known-answer vector plus unequal/forged digest negatives.

## v7 High Closure Status

| v7 High | v8 status |
| --- | --- |
| Publication eligibility rejects drift/missing/malformed/blocked audit | **Closed** |
| Whole outer/nested Panel payload and same-generation predecessor binding | **Closed for payload/predecessor shape; leaf lineage remains H1** |
| Fact attribution bound to authorization and mutation truth | **Partially closed; capability identity remains H3** |
| Distinct implementation and build IDs in release gate | **Closed** |
| Registry-derived DAG/read-set/ordering execution | **Open: H1** |
| Complete journal recovery semantics | **Partially closed; Panel publication graph remains H2** |
| Complete repair graph semantics | **Closed for the registered per-batch graph** |
| Additive, lossless Panel v1 compatibility | **Closed at design-fixture strength** |

## Good-Spine Checklist

| Checklist | Result | Notes |
| --- | --- | --- |
| Real divergence points covered | **Pass** | All five reported failure modes map to explicit ADs and rollout stages. |
| Every AD enforceable and prevents stated divergence | **Fail** | H1 and H2 leave AD-4/6/10/11 acceptance paths weaker than their rules; H3 gives two authorization identities. |
| Deferred does not leak required decisions | **Pass** | Deferred items are outside the target loop and have defensible revisit triggers. |
| Named technology/currentness | **Pass** | No unverified vendor dependency is bound; Draft 2020-12, RFC 8785, POSIX, and Windows boundaries are explicit. |
| Brownfield ratification | **Pass with H3 interop exception** | Additive Panel v2 and pinned v1 fixture preserve the deployed information architecture. |
| Operational/environmental envelope | **Fail** | Cross-platform durability is specified, but the Panel publication transaction proof is not closed. |
| Hash/count/receipt consistency | **Pass at declared design-fixture strength** | Hashes and counts reproduce; the semantic strength overclaimed by H1/H2 is the blocker. |

## Gate Exit

Do not finalize the spine while H1-H3 remain. After correcting the three contract/harness gaps, regenerate the raw-hash chain and both design-fixture receipts, then rerun the independent rubric, brownfield-reality, and adversarial reviewers against one frozen target. Production conformance, native POSIX fault injection, and native Windows CI remain later release prerequisites and are not substituted by this review.
