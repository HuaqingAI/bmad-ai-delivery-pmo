# ARCHITECTURE-SPINE Brownfield Reality / Currentness Review v9

## Verdict

**FAIL. Critical: 0. High: 4.** v9 correctly preserves the five reported brownfield symptoms as target-state work, keeps production conformance `pending`, and reproducibly passes its declared design checks. It materially closes most v8 gaps, including exact command-to-action delta attribution, transaction-kind role sets, repair binding reconstruction, nonce transitions, real pointer resolution for ordering rules, and cross-runtime UTF-16 JCS ordering. Four executable false-green paths remain: current canonical status may change while the v1-visible model remains stale, `include_workstreams="all"` may silently omit a physical workstream, a fact journal may commit a different receipt from the one being validated, and journal image locators are not actually bound to their journal.

## Frozen Review Target

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `0839f33e8335e37d2e3a5b8a678f9226c5908d3e6b586099fbd8b79e768f885e` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `b2a74fd906105365c29ef947dcdf5512cb4e487763a5e3f94dff5e5e7a409708` |
| `contracts/CONTRACT-REGISTRY.json` | `175bc4f4ad88c0e80e1d0f55559b8dd263a36e700d08880ce7af41f529954487` |
| `contracts/panel-sync-contracts.schema.json` | `890846bec1dd502e9cb516e7b9d63e623d5dd1e83b7b447a0e6b5424b856b939` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `1da9a75d12f913ab041a3eec6aa847b5184d52f4f5ab6d100e12e61160c03236` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `46dfc65182148d53bf6b8bd9a6f7abf626f67c0da30fcf82a97629390d81b6c6` |
| `contracts/fixtures/PANEL-V1-COMPATIBILITY.json` | `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` |
| `contracts/conformance/python_runner.py` | `cc7ff6d0022f8dfaac4de4ab46b43acaaf7ebcc15e565beeb2b5c6072224a8e8` |
| `contracts/conformance/node_runner.mjs` | `24e6c0df148917f7ea51844b375a73f3372cb503cc3f06c00d5ac12332eec748` |
| `contracts/conformance/python-result.json` | `010d659dc4862e850390d43394051015828255421bd4d424915bae3423b576bf` |
| `contracts/conformance/node-result.json` | `d0c4ff6c5aa03dac3bf5b7949689faaf48dd15bcf5401745d7b81d78f985147a` |

The package did not change during this review. This reviewer created only this review file.

## Critical Findings

None.

## High Findings

### H1 - The same-generation composition gate still permits stale v1-visible status

The new composition rule reads Program Status only from `/extensions/panel_v1_source`, then invokes the pinned composer (`python_runner.py:1223-1249`; registry `panel_v1_composition`). That extension is an unconstrained object and has no semantic equality or derivation rule tying it to `workstream_current`, `overall_status`, `summary`, `progress`, or `flow_state` (`panel-sync-contracts.schema.json:1390-1406`). Node makes the same assumption by comparing the extension and model to the static compatibility fixture (`node_runner.mjs:760-768`). Envelope generation equality proves that the stale extension was packaged this generation; it does not prove that it represents this generation's current fields.

I changed the schema-valid canonical row to `progress="LATEST CURRENT PROGRESS"` and `blockers=["LATEST BLOCKER"]`, left `extensions.panel_v1_source` and `model_v1` unchanged, rebuilt all envelope/manifest/receipt identities, and reran the registered semantics. The new values were absent from `model_v1`, while `managementPanelPayloadV2` schema validation, `panel_v1_composition_valid`, complete lineage, and publication eligibility all returned true. This is the original lag symptom in a fully accepted v9 graph.

**Required fix:** either define and validate a deterministic canonical-status-to-v1-input transform so `panel_v1_source` cannot disagree with the canonical fields, or make `model_v1` explicitly non-current and add a renderer/consumer conformance rule proving all current-field UI reads come only from `/sync/canonical/status/workstream_current`. Add a complete negative vector that changes current progress/blockers/risks while retaining the old extension and recomposed old model.

### H2 - `include_workstreams="all"` derives scope from the output being audited

`publication_eligibility_semantics()` sets `included = status_ids` when the policy says `all`, then compares status/audit/drift back to that derived set (`python_runner.py:333-356`; Node equivalent `node_runner.mjs:257-272`). It does not derive the authoritative physical set from a catalog or generation leaves. Lineage validation only checks that each consumed source exists in the generation envelope; it never checks the reverse closure that every selected physical WDR/sidecar leaf has been consumed (`python_runner.py:1298-1331`; `node_runner.mjs:797-816`). `generation.panel_catalog_id` is also not recomputed against a `panelBindingCatalogV1` document in either publication validator.

I built a schema-valid `include_workstreams="all"` generation containing `l1-checkout` and `l1-payments` physical leaves, while canonical status, audit, drift, manifests, and receipts contained only `l1-checkout`. Policy, generation, and Panel schemas passed; lineage and publication eligibility both returned true. The existing omission vector removes a drift row from an explicit one-workstream fixture and does not cover this circular `all` case.

**Required fix:** resolve `all` from an independently hashed physical workstream catalog under the generation read lock, require the resolved scope to be nonempty, bind `panel_catalog_id` to the validated catalog bytes, and require exact equality across selected physical WDR/sidecar leaves, status IDs, audit IDs, drift rows, manifests, and receipts. Add full/subset/empty `all` vectors and an unconsumed generation-leaf negative.

### H3 - Fact receipt bytes are not bound to the journal's receipt target

The fact attribution validator recomputes authorization, command fingerprint, receipt identity, generations, business targets, and exact action delta, but it never locates the role=`receipt` target or compares that target's `after_sha256` with the actual receipt bytes (`python_runner.py:533-565`; Node `factAttributionSemantics`). The generic journal validator checks locator hash against the target's declared hash, not against a supplied receipt document (`python_runner.py:434-475`). Panel and repair have separate graph checks for receipt bytes; fact mutation does not.

The current positive fact fixture itself demonstrates the gap: both `journal_semantics` and `fact_attribution_semantics` return true, while the journal receipt target says `sha256:8888...8888` and the actual canonical fact receipt hashes to `sha256:3e0880745d496232030f6e8b159d61d27ecaba2f09678f74de9553b03e52bec4`. Thus the committed audit artifact may differ from the exact-ID/action delta receipt validated out of band.

**Required fix:** register a complete fact-transaction graph validator that consumes command, capability registry, journal, committed marker, fact receipt, and generation state; require one exact receipt target/path, `target.after_sha256 == SHA256(JCS(receipt))`, transaction/journal/authorization equality, and marker linkage. Replace the current happy fixture with a byte-bound graph and add receipt substitution/path/hash negatives.

### H4 - Image locator checks accept another journal's recovery images

Protocol section 7 requires images inside the journal at `images/<apply_order>-before|after`. The executable validator only checks `path.endswith("images/<order>-before|after")` (`python_runner.py:458-463`; Node equivalent), while fixtures use the generic path `journals/images/...` without a transaction/journal directory. There is no journal root/path field from which locality can be proven.

I changed a fact journal locator to `other-journal/images/0-before`, recomputed manifest and marker identities, and `journal_semantics` still returned true. Recovery can therefore restore bytes belonging to another transaction even though the validator claims journal-local image closure.

**Required fix:** include a canonical journal directory/root handle in the manifest and require exact locator paths beneath that directory, or derive an exact filesystem-safe journal directory from `journal_id`/`transaction_id`. Add foreign-journal, parent-alias, and same-suffix locator negatives in both runners.

## Brownfield Reality Check

The production diagnosis remains accurate and is not hidden by the target contracts:

- Meeting v1 normalizes action owner/status but has no action identity or operation, and its status handoff remains create-shaped (`skills/adp-meeting-sync/scripts/sync_meeting.py:274-304,1371-1403`). Status-sync can locate an exact ID, but `ActionUpdate.status="open"` and unconditional status assignment still make owner-only reuse unsafe (`skills/adp-status-sync/scripts/sync_status.py:102-123,840-846,907-920`).
- `wdr_update` still renders a `Meeting Sync Update` history block (`sync_meeting.py:1244-1270`); current Project Status fields are updated by status-sync (`sync_status.py:1458-1474,1523-1545`). The typed-intent/shared-engine direction is therefore the right ownership fix.
- `inspect_current()` still validates embedded resources, bundle identity, and artifact audit without reloading WDR/ledger leaves (`skills/adp-management-panel/scripts/management_panel.py:1120-1173`). Static `file://` limitations and the future live-inspect boundary are stated accurately.
- The prepass/audit path can discover exact ledger/WDR disagreements, but production still lacks the target same-generation drift sidecar/publication gate.
- Raw disagreements carry `action_id`, yet `canonical_finding()` still omits it from the public finding while using it only inside identity details (`skills/adp-state-audit/scripts/audit_state.py:2282-2303,2951-3014`). Exact repair IDs remain target-state, not deployed behavior.

Accordingly, the architecture addresses all five symptoms directionally, but H1-H3 prevent the frozen contracts from yet proving that current status and exact receipt evidence reach the published Panel without omission or substitution.

## Verification Evidence

- Architecture lint: **PASS, 0 findings**.
- Draft 2020-12 schema compilation: **PASS** for the target bundle, Panel model v1, Panel manifest v1, flow-graph v1, progress v3, and flow-state v1.
- Registry closure at rest: **PASS** for 40 anchors, 12 source/compatibility pins, 7 profiles, 7 outer bindings, 4 nested bindings, 15 DAG edges, 24 ordering rules, and 7 semantic validators. Every raw pin resolves on disk.
- Compatibility regeneration: **PASS**, byte-for-byte identical, through the pinned production `panel_model.compose_panel()` path.
- Python design runner: **169 passed / 0 failed**, byte-for-byte identical to the checked-in result; result ID `sha256:8d34ee5ea71c2fb54c73e08d4d98e16cec3d37f02734d4c893161f7a243ca821`.
- Node design runner: **169 passed / 0 failed**, byte-for-byte identical to the checked-in result; result ID `sha256:cd167c111b1de9945a9d180fab97a3220e457bcc9b1d38b727fc8bf60fa3d067`.
- Brownfield regressions: **205/205 passed**: meeting-sync 31, status-sync 29, state-audit 63, management-panel 28, panel-audit 12, state-prepass 10, panel-model 6, and Panel contract 26.

These successful checks are reproducible but do not invalidate the four complete-document counterexamples above.

## Evidence Boundary

The package does not overstate deployment status. Both main documents remain `status: draft`; the registry says `implementation_conformance_status: pending`; both receipts say `evidence_kind: design-fixture-check` and `native_durability_exercised=false`; native POSIX fault injection, native Windows CI, and two production adapters remain explicit future release gates. No production `skills/adp-*` module was changed by this architecture package. Strict publication must remain disabled until H1-H4 are closed and the later production evidence gate is satisfied.
