# ARCHITECTURE-SPINE Brownfield Reality / Currentness Review v20

## Verdict

**PASS. Critical: 0 / High: 0 / Medium: 0 / Low: 0.** The five reported production problems remain directly observable in the current pinned brownfield sources. The v20 package consistently presents the proposed synchronization contracts, transaction substrate, strict activation lifecycle, and panel-refresh workflow as target architecture rather than deployed behavior. Every normative hash, registry inventory count, source pin, checked result, regression count, platform claim, and named runtime/standard claim reviewed here is reproducible from current bytes or a current authoritative source. Production remains fail closed.

## Severity Totals

| Severity | Count |
| --- | ---: |
| Critical | 0 |
| High | 0 |
| Medium | 0 |
| Low | 0 |

## Frozen Review Target

- Review date: 2026-07-30 (Asia/Shanghai).
- Repository: `HEAD=a5d873e0ad3e7d60e7157f76096c6ac65085bee3` on `master`.
- Architecture spine raw SHA-256: `88dee897e5a648e887495192198f756e8f5d7388fa23e9df4d1e2db97056569e`.
- Analysis/runbook raw SHA-256: `77212bdc9951595d684705612a6356bc24c4ee27cd67a8cd00e2ed302337f55a`.
- Registry raw SHA-256: `07069e6d8d5bf118205d456a9d45816cdd3da77d5c27d1b37d38eb614c77623a`.
- Schema raw SHA-256: `30c89a0f345fab0673bb303a06a80cfa3bc287747f73a283be92076c51708416`.
- Protocol raw SHA-256: `d6075713bced415d0214e13ae59f50dc565ff159d25e1f48be16f307b97f3781`.
- Vector suite raw SHA-256: `20abc93c9c7dad281896680ce639c0ec54396e8c7dc89c51ed95a4c06e56d2bb`.
- Pre-existing unrelated untracked paths were `skills/reports/adp-operational-artifacts-panel-refresh-plan.md` and `skills/reports/eval-runs/`; they were not modified.
- This reviewer created only `reviews/review-reality-v20.md`.

## Tiered Findings

### Critical

None.

### High

None.

### Medium

None.

### Low

None.

## Brownfield Reality: Five Reported Problems

All 23 registered source artifacts match the current raw repository bytes, so the diagnoses below are current production observations rather than stale evidence.

| Reported problem | Current raw production evidence | Reality judgment |
| --- | --- | --- |
| meeting-sync cannot mutate an existing action owner/status | `skills/adp-meeting-sync/scripts/sync_meeting.py:266-304` normalizes items without an action identity, operation, or expected revision. `:1371-1381` emits create-shaped action data without `action_id`, `operation`, or revision CAS. | Accurate. Action mutation v2 remains a target contract. |
| `wdr_update` appends history instead of changing Panel-read current fields | `sync_meeting.py:812-821` appends to each WDR; `:1244-1264` renders a new `## Meeting Sync Update` block instead of replacing Identity/Project Status labels. | Accurate. Typed status intents and the shared WDR engine are not deployed. |
| Panel inspect does not verify changed live facts | `skills/adp-management-panel/scripts/management_panel.py:1120-1173` verifies current HTML, embedded objects, immutable bundle, resources, and artifact audit without supplying live `source_inputs`. `skills/adp-state-audit/scripts/panel_audit.py:670-690` performs source hash/recomposition checks only when those inputs are supplied. | Accurate. Current inspect establishes artifact integrity, not live business freshness. |
| WDR/ledger drift detection is incomplete | `skills/adp-agent-program-lead/scripts/adp-state-prepass.py:911-958` compares action ID sets but not the complete owner/text/due/status record. `:1154` skips the cross-check when the active ledger list is empty. | Accurate. Complete sidecar/content drift and empty-ledger checks remain target behavior. |
| Canonical audit output lacks directly actionable action IDs | `skills/adp-state-audit/scripts/audit_state.py:2951-2998` constructs the public finding without `action_id` or `action_ids`; `:3001-3014` uses those fields only in identity details. | Accurate. Audit finding v2 and repair batches remain target behavior. |

## Planned Versus Deployed Boundary

- `ARCHITECTURE-SPINE.md` is still `status: draft`; AD-1 through AD-12 and the Structural Seed are build substrate, not a rollout declaration.
- Planned modules `skills/adp-fact-transaction`, `skills/adp-wdr-mutation`, and `skills/adp-panel-refresh` do not exist in the production `skills/adp-*` tree.
- The operator section is explicitly titled `面板更新操作手册（实现落地后）`. Its first paragraph says the architecture package has not modified production skills and that exact CLI spelling must be fixed by implementation stories.
- The runbook does not falsely claim that `panel-refresh detect`, `refresh --dry-run`, `refresh --apply`, strict live inspect, or action-ID repair commands are available now. It also states that current inspect success must not be interpreted as live freshness.
- The post-implementation route is operationally coherent: producer dry-run/apply, status-intent convergence, optional `refresh_actions`, panel-refresh detect/plan/apply, then live inspect; drift is repaired per exact batch and action IDs before refresh is retried.
- Static legacy Panel behavior remains available, but no text upgrades it to live-fresh/current behavior before migration, conformance evidence, activation, publication, and inspect closure succeed.

## Fail-Closed Production State

The raw registry remains authoritative and fail closed:

- `.conformance_suite.implementation_conformance_status = "pending"`.
- `.evidence_trust.trust_roots = []`.
- Minimum production trust roots remains 2.
- The checked Python and Node results both say `evidence_kind=design-fixture-check`, `platform=posix-design-model`, and `native_durability_exercised=false`.
- AD-11, AD-12, protocol section 9, and the analysis plan consistently require separate production adapters, independent implementation/build identities, native POSIX fault injection, native Windows CI, and reviewed trust-root provisioning before strict publication.

There is no design receipt, mock trust root, or producer-supplied status that can authorize production strict mode.

## Six v19 Corrections: Prose-to-Executable Reconciliation

1. **Receipt-derived activation lifecycle prefix CAS:** `activationLifecycleIndexV1` stores step ordinal, transition/operation, predecessor receipt, registered receipt path, and exact receipt hash. Protocol section 9 and AD-12 require rollback create followed by four exact-prefix replacements. Both runners reject forged receipts, broken prefixes, first-step replace, uncommitted receipt indexing, wrong order, and recovery boundary substitutions.
2. **One typed finding identity across drift, audit, and repair:** `driftFindingV1` defines the action and non-action finding body; protocol section 8 fixes `finding_id=SHA256(JCS(body))`. AD-5/AD-7 and `auditFindingRepairV2` require state-audit to preserve that ID and exact action identity rather than synthesizing literal IDs. Both runners validate the drift-to-audit-to-repair graph and reject missing, extra, duplicate, or normalized-collision action refs.
3. **Producer-supplied exact status intents:** producer commands carry their complete `status_intents`; `mutationIntentOutboxV1.entries[].intent` embeds the exact typed document. Protocol section 2 forbids deriving intent payloads from history text, producer identity, or templates. The Python and Node handlers reject command-intent omission, exact intent digest substitution, emitted status substitution, and required outbox-target omission.
4. **Complete aggregate intent consumption:** a status-sync WDR command carries every sorted content-hash `consumed_intent_id`, and one aggregate patch must consume the complete same-workstream accepted set in the same journal while preserving unrelated rows. The schema/protocol/runners reject omitted, extra, terminal, cross-workstream, split-patch, or representative-only consumption.
5. **Repair claims derived from exact current bytes:** dry-run reads registry-derived ledger, ledger-state, WDR, WDR-state, and sidecar bytes under the fact lock, revalidates ledger state, derives presence/revision from actual rows, and recomputes the typed drift. The runners reject absent claims for present rows, present claims with wrong revisions, invented drift, substituted snapshots, and orphan records that are not proven by the exact ledger fingerprint.
6. **Deterministic repair-attempt handoff and recovery:** protocol section 8 derives attempt transaction/journal identity from business transaction/journal plus the actual terminal marker and optional recovery receipt IDs/raw hashes. Both runners independently derive the same identity and execute fresh-process fault probes after the business marker and at every attempt target boundary, requiring one sequence and one repair receipt without rerunning terminal business work.

The spine, analysis plan, protocol, schema, registry, fixtures, and both runners now agree on all six points. No v19 literal-finding, synthesized-intent, partial-consumption, self-asserted repair-read, free attempt-ID, or disconnected lifecycle-index language remains normative.

## Registry, Hash, and Count Reconciliation

| Registry surface | Raw count |
| --- | ---: |
| Schema contracts | 66 |
| Pinned source artifacts | 23 |
| Dependency enumerators | 9 |
| Projection profiles / payload bindings | 7 / 7 |
| Nested payload bindings / Panel bindings | 4 / 6 |
| DAG edges | 15 |
| Typed array ordering / identity-set ordering | 56 / 20 |
| Semantic sequences | 3 |
| Runtime paths | 60 |
| Owned-fact target profiles | 4 |
| Source-time bindings | 8 |
| Semantic validators | 20 |

All counts equal AD-11, protocol section 9, and the analysis plan. All 66 registry contract pointers resolve to the expected schema anchors. All 23 source pins match raw repository bytes. Registry pins for schema, protocol, suite, Python runner, Node runner, and Panel v2 consumer equal current raw bytes.

| Artifact | Raw SHA-256 |
| --- | --- |
| Panel v1 compatibility fixture | `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` |
| Python runner | `ad85a146f588abbd33d5043f86001afdeb9275e9ad1127df9059a4d7c75fb9d9` |
| Node runner | `dc1e29141bcffe334e8ddc9d2aa700e2a57956401ee378f6abab15ef11cdca5e` |
| Panel v2 consumer | `eaf497fbf79eaedface1408b1ea1679aec7d7e85709fd9e3a386550855bbc423` |
| Python result | `dc3116c3587456f433b0ab3dba00cf4b3fc00df2b6bf6bc1bec8af7c6267f4f1` |
| Node result | `e05171cdce1269cb231cb29358f43db83f515aa48e1d408373a0bc3b9cca8277` |

The suite has 643 passed IDs, 643 unique IDs, and the Python/Node passed sets are identical. Fresh fixed-time replays produced byte-identical checked result files:

- Python result ID: `sha256:439984e9ecf0a3dd6c90dad45b3a965917d358a6ed8040cd6fa8ef7978283605`.
- Node result ID: `sha256:515114ffe360a394327b0e0d0d9604597a411a7e0fd5ced935a2a194aef0416a`.

## Platform Claims

- The checked Python design receipt was reproduced with `/usr/bin/python3`, CPython 3.9.6 on Darwin. Its executable SHA-256 `bdea59019a38eb6600cc9e71e984a97fedadc406448431281e7657030f54987e` and runner build digest match current bytes.
- The checked Node design receipt was reproduced with Node 24.16.0 on Darwin arm64. Its executable SHA-256 `1ee75375e33b94fc34b3b19aede049e11dae90efb63b374dc96d6bdace70c4b8` and runner build digest match current bytes.
- Python 3.9 is deliberately outside the production policy. Its use for a semantic design-fixture replay does not satisfy or weaken the implementation release gate.
- Brownfield regression tests that use modern union annotations were run under available CPython 3.12. No document claims those tests pass under unsupported Python 3.9.
- Neither checked receipt claims native Windows execution, production adapter coverage, real filesystem fault injection, or native durability.

## Deterministic Evidence

| Gate | Fresh observed result |
| --- | --- |
| Architecture lint | 0 findings |
| JSON Schema dialect | 9/9 repository schema files parse and declare Draft 2020-12 |
| Registry/source closure | 66/66 anchors and 23/23 raw source pins valid |
| Registry inventory | `66,23,9,7,7,4,6,15,56,20,3,60,4,8,20` exactly as documented |
| Brownfield regressions | 205/205: meeting-sync 31, status-sync 29, state-audit 63, Management Panel 28, panel-audit 12, state-prepass 10, panel-model 6, Panel contracts 26 |
| Program Lead additions | 17/17 |
| Panel v1 regeneration | Byte-identical; SHA-256 `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` |
| Python fixed replay | 643 passed / 0 failed; byte-identical checked result |
| Node fixed replay | 643 passed / 0 failed; byte-identical checked result |
| Cross-runtime IDs | Identical complete 643-ID sets |
| Production gate | `pending`; trust roots empty |

## Standards and Runtime Currentness

| Named technology or primitive | Authoritative check on 2026-07-30 | Judgment |
| --- | --- | --- |
| Node.js 22 / 24 | The official Node release schedule lists v22 in maintenance through 2027-04-30 and v24 before its 2026-10-20 maintenance transition, with EOL 2028-04-30. v26 is Current and scheduled for LTS on 2026-10-28. <https://github.com/nodejs/Release/blob/main/schedule.json> | Restricting production receipts to LTS majors 22 and 24 is current and intentionally conservative; the package does not claim they are the latest Current release. |
| CPython 3.10 floor | PEP 619 still states security fixes are provided as needed through October 2026 and was updated in March 2026. <https://peps.python.org/pep-0619/> | The registry's `support_review_before=2026-09-01T00:00:00Z` precedes the support end and is correctly enforced as a fresh trusted-time gate. |
| JSON Schema Draft 2020-12 | The official meta-schema is live and self-identifies as `https://json-schema.org/draft/2020-12/schema`. <https://json-schema.org/draft/2020-12/schema> | Current published dialect and correctly declared by all nine reviewed schema files. |
| JCS / JSON Pointer / Ed25519 | RFC 8785, RFC 6901, and RFC 8032 remain published RFCs. <https://www.rfc-editor.org/info/rfc8785> <https://www.rfc-editor.org/info/rfc6901> <https://www.rfc-editor.org/info/rfc8032> | The named canonicalization, pointer, and signature primitives remain valid. |
| RFC 3986 percent encoding | RFC 3986 remains the published URI generic syntax standard. <https://www.rfc-editor.org/info/rfc3986> | The uppercase percent-encoding rule is a deterministic internal canonicalization layered on a current standard. |
| POSIX filesystem primitives | The current Open Group Issue 8 pages are live for `open`, `link/linkat`, `rename/renameat`, and `fsync`. <https://pubs.opengroup.org/onlinepubs/9799919799/functions/open.html> | The primitives exist; production durability behavior still correctly requires native implementation evidence. |
| Windows filesystem primitives | Microsoft documentation is live for `LockFileEx`, `ReplaceFileW`, `MoveFileExW`, and `FlushFileBuffers`. <https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex> | The APIs exist; the package does not substitute documentation or design vectors for native Windows CI. |

## Residual Conditions

- The local default Python is 3.9.6, which is unsupported by the proposed production policy and insufficient for some current brownfield test modules. Operators must use a supported interpreter for implementation and regression gates; CPython 3.12 was available and passed all affected tests.
- Native POSIX crash consistency, native Windows semantics, production adapter independence, trust-root provisioning, and strict activation are intentionally unproven. This is a release condition, not an undisclosed architecture gap.
- The current CLI can continue the legacy file/Panel workflow only. The new one-command refresh and repair lifecycle becomes operational only after P0/P1 implementation and production conformance.

## Gate Decision

The v20 package clears the brownfield reality/currentness lens at **0 Critical / 0 High / 0 Medium / 0 Low**. No correction is required from this reviewer. Keep `implementation_conformance_status=pending`, keep production `trust_roots=[]`, and retain the runbook's post-implementation label until the named production modules and native evidence actually exist.
