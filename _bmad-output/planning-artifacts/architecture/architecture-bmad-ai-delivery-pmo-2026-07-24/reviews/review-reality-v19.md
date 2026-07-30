# ARCHITECTURE-SPINE Brownfield Reality / Currentness Review v19

## Verdict

**PASS. Critical: 0 / High: 0 / Medium: 0 / Low: 0.** Every committed decision and versioned technology claim in the reviewed spine is traceable to current repository bytes, the raw contract registry, the normative schema/protocol/suite, fresh executable conformance evidence, or a current authoritative source. The five reported production problems are still observable in the pinned brownfield code. The document also keeps current production behavior, target architecture, design-fixture evidence, and future production conformance separate; it does not claim that the target modules or strict publication mode are deployed.

## Severity Totals

| Severity | Count |
| --- | ---: |
| Critical | 0 |
| High | 0 |
| Medium | 0 |
| Low | 0 |

## Frozen Review Target

- Review date: 2026-07-25 (Asia/Shanghai).
- Repository: `HEAD=a5d873e0ad3e7d60e7157f76096c6ac65085bee3` on `master`.
- Architecture spine raw SHA-256: `677b6df331c2fde6d6192be61ce03d39529b9fdf9cb2223a15f72f79de20e6b5`.
- Registry raw SHA-256: `82fd15723a618f3edf75881c9304f34f92c83683a44d64f1bbaa263835ee7ce7`.
- Schema raw SHA-256: `5c3f4c916042afeea9d038839d6cbe7c694859737c27794b17268b908f85491e`.
- Protocol raw SHA-256: `ef1fe1a7aa65a148a76620581003dc7a55f2c870a2a1ae175d76bc660a9af7fb`.
- Vector suite raw SHA-256: `4c8ca5565db78b5e54dc6fbc6a9e6f85ba1f7a73e6e2f98c976fa1500d3f0794`.
- Pre-existing unrelated untracked paths were `skills/reports/adp-operational-artifacts-panel-refresh-plan.md` and `skills/reports/eval-runs/`; they were not modified.
- This reviewer created only this review file. No spine, companion, memlog, contract, fixture, runner, result, source, or production artifact was edited.

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

All 23 registry source pins match the current raw repository bytes. The five diagnoses therefore remain current production observations rather than stale snapshots.

| Reported problem | Current raw production evidence | Reality judgment |
| --- | --- | --- |
| meeting-sync cannot mutate an existing action owner/status | `skills/adp-meeting-sync/scripts/sync_meeting.py:266-304` normalizes meeting items without an action identity, operation, or expected revision. `:1371-1381` emits create-shaped action data without `action_id`, `operation`, or expected revision. | Accurate. AD-1/AD-2 specify target command behavior, not current implementation. |
| `wdr_update` appends text instead of changing Panel-read current fields | `sync_meeting.py:812-821` appends directly to each WDR; `:1244-1264` renders a new `## Meeting Sync Update` block. It does not patch the existing Identity/Project Status labels. | Accurate. The typed intent and shared WDR engine remain target design. |
| Panel inspect does not validate changed live facts | `skills/adp-management-panel/scripts/management_panel.py:1120-1173` validates the HTML, embedded model/manifest, immutable bundle, resources, and artifact audit but does not pass live `source_inputs`. The corresponding live hash/recomposition checks in `skills/adp-state-audit/scripts/panel_audit.py:670-690` are conditional on those inputs. | Accurate. Current inspect proves artifact integrity, not live business freshness. |
| WDR/ledger projection drift is incompletely detected | `skills/adp-agent-program-lead/scripts/adp-state-prepass.py:911-958` compares action ID sets but does not compare owner/text/due content; `:1154` suppresses the cross-check when the active ledger is empty. | Accurate. AD-5's sidecar/content verdict and empty-ledger behavior remain target design. |
| Canonical audit output lacks directly actionable action IDs | `skills/adp-state-audit/scripts/audit_state.py:2951-2998` builds the public finding without `action_id`/`action_ids`; `:3001-3014` uses those values only in identity derivation. | Accurate. AD-7's v2 entity refs and repair batches remain target design. |

## Registry, Hash, and Count Reconciliation

The current raw registry inventory exactly matches AD-11 and the protocol/analysis companion:

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

The suite contains 627 vector IDs and 627 unique IDs. Registry pins for the schema, protocol, suite, Python runner, Node runner, and Panel v2 consumer equal the current raw bytes. Both result receipts bind the current registry/schema/protocol/suite hashes, have empty failed-ID sets, and contain the same complete passed-ID set.

| Artifact | Raw SHA-256 |
| --- | --- |
| Panel v1 compatibility fixture | `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` |
| Python runner | `af522cb4280bd221996babda45e76316e6712ee2bdaf672a48986133879f743b` |
| Node runner | `99baf1fb498f7b2a6c5ce3975ae388f4e8edcca406847ec0755db63c9a3d79d7` |
| Panel v2 consumer | `eaf497fbf79eaedface1408b1ea1679aec7d7e85709fd9e3a386550855bbc423` |
| Python result | `d1a8b73d84e016368d01f2ee2c01b8b9c2fd11c3f655583d8d42efda619932bb` |
| Node result | `bb810362fe8237aca24aa4191fc8dcbada33e428f4f12771606ff19c5e879b3e` |

## Node/Python Platform Claims

- The registry identifies both reference results as `posix-design-model`; both checked-in receipts carry that exact value.
- Python was executed on host `darwin` with CPython `3.12.13`; the recorded executable SHA-256 and runner build digest match the current executable and runner bytes.
- Node was executed on host `darwin-arm64` with Node `24.16.0`; the recorded executable SHA-256 and runner build digest match the current executable and runner bytes.
- Neither receipt claims Windows execution. Both explicitly set `evidence_kind=design-fixture-check` and `native_durability_exercised=false`.
- Fresh fixed-time execution produced byte-identical Python and Node result files, each with 627 passed and 0 failed vectors. The Python result ID is `sha256:d318032488ea2b1efc1bf24fe9f8d8f53329bd4539b53b9f6a9b822d609d5fb0`; the Node result ID is `sha256:9c0e8c7aefabd1b3cb464ac5383a9ad6321257e8f32dcf6425ab1471c9c9ce0d`.

This closes the earlier possibility of describing a Darwin Node run as a Windows design model or native Windows evidence.

## Repair Journal Topology

The spine, protocol, analysis plan, schema, registry, vectors, and both runner implementations agree on one topology:

1. The `repair` business journal contains business targets, fact generation, fact-command index, nonce, and exactly one fact receipt.
2. A separate `repair-attempt` journal contains exactly one repair-attempt-ledger target, one repair-index target, and one repair receipt.
3. The business journal cannot contain the repair receipt/index/attempt ledger, and the attempt journal cannot rewrite business/fact-generation/command-index/nonce targets.
4. The attempt journal must commit even when the business journal rolls back, preserving the invalidated/failure outcome without confusing it with a successful fact commit.

Schema `transaction_kind` includes distinct `repair` and `repair-attempt` values. The registered `transaction-journal-semantics/1.0.0` and `repair-graph-semantics/1.0.0` handlers in both runners enforce the exact target-role sets and are exercised by committed, blocked, invalidated/rolled-back, orphan, substitution, and partial-retry vectors. No remaining prose describes a single repair journal carrying both fact and repair receipts.

## Planned Versus Deployed Boundary

- The spine is marked `status: draft` during the review gate, and its AD rules/Structural Seed describe the target build substrate.
- Planned modules `skills/adp-fact-transaction`, `skills/adp-wdr-mutation`, and `skills/adp-panel-refresh` are absent from production. Their absence agrees with the architecture and implementation plan; it is not presented as completed rollout.
- Raw registry production state remains fail closed: `.conformance_suite.implementation_conformance_status="pending"` and `.evidence_trust.trust_roots=[]`.
- AD-11/AD-12 and protocol sections 9-10 explicitly require reviewed provisioning of at least two production roots, two independent implementation IDs and build IDs, native POSIX evidence with real fault injection, and native Windows CI before strict publication.
- Static legacy Panel behavior remains available, but the architecture explicitly denies live-fresh/current claims until migration, activation, release evidence, and inspect closure all pass.

The design package therefore does not authorize production strict publication and does not mislabel design fixtures as implementation conformance.

## Deterministic Evidence

| Gate | Observed result |
| --- | --- |
| Architecture lint | 0 findings |
| JSON Schema dialect | 9/9 repository schema files parse and declare `https://json-schema.org/draft/2020-12/schema` |
| Registry/source closure | 66 contract anchors and 23/23 raw source pins validate through both runners |
| Vector inventory | 627 IDs, 627 unique |
| Python fixed replay | CPython 3.12.13, 627 passed / 0 failed; byte-identical to checked-in result |
| Node fixed replay | Node 24.16.0, 627 passed / 0 failed; byte-identical to checked-in result |
| Cross-runtime passed IDs | Identical complete sets |
| Brownfield regressions | 205/205 passed: meeting-sync 31, status-sync 29, state-audit 63, Management Panel 28, panel-audit 12, state-prepass 10, panel-model 6, Panel contracts 26 |
| Program Lead additions | 17/17 passed |
| Program Status tests | 46/46 passed |
| Panel v1 regeneration | Byte-identical; SHA-256 `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` |

## Standards and Runtime Currentness

| Named primitive | Current authoritative check (2026-07-25) | Judgment |
| --- | --- | --- |
| Node.js 22 / 24 | The official release schedule lists v22 in Maintenance through 2027-04-30 and v24 in Active LTS until 2026-10-20, with EOL 2028-04-30. v26 is Current and is not scheduled for LTS until 2026-10-28. <https://github.com/nodejs/Release/blob/main/schedule.json> | Restricting production receipts to majors 22 and 24 is current and conservative. |
| CPython 3.10 floor | PEP 619 states that 3.10 receives source-only security updates until approximately October 2026. <https://peps.python.org/pep-0619/> | The registry's earlier `support_review_before=2026-09-01T00:00:00Z` is current, explicit, and enforced with non-candidate trusted evaluation time. |
| JSON Schema | The official Draft 2020-12 meta-schema remains available as `application/schema+json`. <https://json-schema.org/draft/2020-12/schema> | Current published dialect and correctly pinned. |
| JCS / JSON Pointer / Ed25519 | RFC 8785, RFC 6901, and RFC 8032 remain published standards. <https://www.rfc-editor.org/info/rfc8785> <https://www.rfc-editor.org/info/rfc6901> <https://www.rfc-editor.org/info/rfc8032> | The selected canonicalization, pointer, and signature primitives remain valid. |
| Windows durability APIs | Microsoft documentation remains live for `LockFileEx`, `ReplaceFileW`, `MoveFileExW`, and `FlushFileBuffers`. <https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex> <https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilew> <https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw> <https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers> | The APIs exist and fit the stated local-filesystem model; native correctness remains deliberately gated on native evidence. |

## Gate Decision

The reality/currentness lens clears the reviewed draft at **0 Critical / 0 High / 0 Medium / 0 Low**. No architecture correction is required from this reviewer. Keep production roots empty and implementation conformance `pending` until the separately specified native adapter evidence passes.
