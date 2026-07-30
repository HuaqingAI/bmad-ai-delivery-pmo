# ARCHITECTURE-SPINE Brownfield Reality / Currentness Review v18

## Verdict

**PASS. Critical: 0. High: 0. Medium: 0. Low: 0.** 冻结稿中的已提交决策均能追溯到当前 repository raw bytes、注册契约或当前权威技术来源；未发现仍靠断言成立的 implementation-blocking reality/currentness 缺口。五项用户问题在现有 production code 中仍可观察，且文档把 brownfield 事实、target design、design-fixture evidence 与 production conformance 四者正确分开。v17 的 trusted evaluation time 与 owned risk/decision fact 两项 High 已由 protocol、registry、schema、两套 runner 和直接正反例共同闭合。

## Frozen Review Target

- Review date: 2026-07-25 (Asia/Shanghai).
- Repository: `HEAD=a5d873e0ad3e7d60e7157f76096c6ac65085bee3` on `master`.
- Architecture spine raw SHA-256: `e8743002b5b7a5b012d5dd416d4a3d7378ad171e1484d9c143ed19f17a0cbfb8`.
- Registry raw SHA-256: `3e72d1148a84fe6e3a1b39845b918d527e414fe2099efe7d606a1c8bf97f9fcd`.
- Protocol raw SHA-256: `f13331f08c1dfa914ff02342146ffbf3122b5aeb419ceb1bbb7fec6309cdd990`.
- Vector suite raw SHA-256: `7c2aa9392f4662b124f2bb21fd77b57e1e4454ad09ff10da13062735f7cb833c`.
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

All nine diagnosis sources are among the registry's 23 raw source pins, and all 23 pins match current repository bytes. The five diagnoses remain current production behavior, not claims that the target architecture has already shipped.

| Reported problem | Current raw production evidence | Reality judgment |
| --- | --- | --- |
| meeting-sync cannot mutate an existing action's owner/status | `sync_meeting.py:266-304` normalizes meeting items without an action identity/revision operation; `:1371-1381` emits create-shaped action rows without `action_id`, `operation`, or expected revision. | Accurate. AD-1/AD-2 describe replacement behavior, not current implementation. |
| `wdr_update` appends text rather than changing Panel-read current fields | `sync_meeting.py:812-821` appends directly to each WDR; `:1244-1264` renders a new `## Meeting Sync Update` block. It does not patch the existing Identity/Project Status labels consumed downstream. | Accurate. The typed status-intent/shared-engine path remains target design. |
| Panel inspect does not validate changed live facts | `management_panel.py:1120-1173` compares current HTML, embedded model/manifest, immutable bundle, resources, and artifact audit. Its call to `audit_panel_artifacts()` does not pass live `source_inputs`; the optional live input checks in `panel_audit.py:670-690` therefore do not run. | Accurate. Current inspect proves artifact integrity, not business freshness. |
| WDR/ledger drift detection is incomplete | `adp-state-prepass.py:911-958` builds an ID-set cross-check; owner/text/due fields are collected but not content-compared. `:1154` suppresses the cross-check entirely when the active ledger is empty. | Accurate. The registered sidecar/content verdict is target design. |
| Canonical audit output drops actionable action IDs | `audit_state.py:2951-2998` builds the public finding without `action_id` or `action_ids`; `:3001-3014` uses those fields only inside identity derivation. | Accurate. v2 `entity_refs`/repair batches remain target design. |

The intended implementation modules `skills/adp-fact-transaction`, `skills/adp-wdr-mutation`, and `skills/adp-panel-refresh` remain absent. That agrees with spine `status: draft`, registry `implementation_conformance_status=pending`, and the implementation plan; their absence is not represented as completed production work.

## v17 High-Finding Closure

### Trusted evaluation time and evidence expiry

- Protocol sections 2, 6, and 9 require a non-candidate `host-secure-clock-v1` context for release acceptance, release transition, open, inspect, and publication.
- Python `release_gate_accepts()` now takes the external context and evaluates both the Python review deadline and root current validity against `evaluation_time` (`python_runner.py:1623-1635`, `:1700-1706`). The durable set loader separately requires `accepted_at <= evaluation_time` (`:1843-1863`). Node implements the same checks.
- Direct vectors preserve July evidence while advancing only evaluation time: current boundary, `2026-09-01` support-review expiry, root expiry, unavailable clock, and release-transition expiry/recovery cases. Both registered `strict-writer-fence-activation` and `live-inspect-semantics` scopes include runtime policy, evidence trust, and the external time path.

This closes the earlier false-green condition in which old signed evidence could remain valid indefinitely merely because its historical `executed_at` was inside the old policy window.

### Owned risk-flow and decision facts inside the fact fence

- Registry contains `owned-fact-command/1.0.0`, grants risk review only `owned_facts`, and defines four unique target profiles: risk-flow JSON, workstream decisions, decision log, and business-decision packets.
- `fact-receipt-attribution/1.0.0` and the dedicated `owned-fact-command-semantics/1.0.0` both include the owned command, target profiles, native authority, lock, paths, byte proof, generation, receipt, and restart semantics.
- Direct fixture coverage includes valid risk-flow and decision graphs plus forged producer, wrong target/root, schema substitution, extra target, stale generation, denied operation, and after-byte substitution negatives (`CONFORMANCE-VECTORS.json:742-751`). Both runners dispatch the same registered validators and passed the same IDs.

This closes the earlier choice between rejecting the declared owner and bypassing the mandatory generation fence.

## Contract and Count Reconciliation

Raw registry inventory agrees with the spine, protocol, analysis plan, memlog, runners, and result receipts:

| Registry surface | Raw count |
| --- | ---: |
| Schema contracts | 59 |
| Pinned source artifacts | 23 |
| Dependency enumerators | 9 |
| Projection profiles / payload bindings | 7 / 7 |
| Nested payload bindings / Panel bindings | 4 / 6 |
| DAG edges | 15 |
| Typed array ordering / identity-set ordering | 51 / 16 |
| Semantic sequences | 3 |
| Runtime paths | 51 |
| Owned-fact target profiles | 4 |
| Source-time bindings | 8 |
| Semantic validators | 17 |

The fixture contains 568 vector IDs and 568 unique IDs. Registry protocol, suite, schema, runner, and result bindings all match current raw hashes. The 17 registered validator IDs, algorithms, ordered scopes, and handler IDs are exact-set checked by both runners; source-time bindings include Panel, audit, status, roadmap, all meeting instances, flow state, every flow scope, and refresh receipt.

## Deterministic Evidence

| Gate | Observed result |
| --- | --- |
| Architecture lint | 0 findings |
| Draft schemas | 9/9 JSON files parse and declare `https://json-schema.org/draft/2020-12/schema` |
| Source pins | 23/23 raw hashes match current repository bytes |
| Vector inventory | 568 IDs, 568 unique |
| Python fixed replay | CPython 3.12.13, 568 passed / 0 failed; byte-identical to checked-in result |
| Node fixed replay | Node 24.16.0, 568 passed / 0 failed; byte-identical to checked-in result |
| Cross-runtime vector IDs | Identical |
| Brownfield regressions | 205/205 passed: meeting-sync 31, status-sync 29, state-audit 63, Management Panel 28, panel-audit 12, state-prepass 10, panel-model 6, Panel contracts 26 |
| Program Lead additions | 17/17 passed |
| Panel v1 regeneration | Byte-identical; SHA-256 `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` |

Normative/result hashes also agree with the frozen documents:

| Artifact | Raw SHA-256 |
| --- | --- |
| Schema bundle | `0349ac3224d6ffba27aa5fffc5843e93790838cfb9375440450ce51d0a96c58e` |
| Python runner | `fb8e299a2a909427af7888eb2727e0cd2ef3358027e7cba4d3b71b915efb5a29` |
| Node runner | `70fb642a640a8dccedb22a6c0d8322ee7c3ee5245c5af852a32938a39f1e310b` |
| Panel v2 consumer | `eaf497fbf79eaedface1408b1ea1679aec7d7e85709fd9e3a386550855bbc423` |
| Python result | `f0de178dc4eb28cb859a605cbc3670fb863da3c96a741c3b92fbbe876e4f48e0` |
| Node result | `6ffe9c6179d255f6da9e66dc88ca34b2077e282ce99da89f6b720410e10e460e` |

The Python checked-in receipt intentionally binds its recorded CPython 3.12.13 executable. Running the same suite under the host's default CPython 3.9.6 also produced 568/568, but its provenance and result identity differed as expected; it was not used as byte-replay evidence.

## Production / Design Evidence Boundary

- Both checked-in receipts are explicitly `design-fixture-check`, use `posix-design-model` / `windows-design-model`, and state `native_durability_exercised=false`.
- Raw registry remains `implementation_conformance_status=pending`, production `trust_roots=[]`, and requires at least two roots after reviewed provisioning.
- Release acceptance still requires two distinct implementation IDs, all-distinct build IDs, native POSIX and native Windows evidence, real POSIX fault injection, native Windows CI, current trusted time, and exact full-suite/hash/signature closure.
- The Node result was generated on `darwin-arm64`; `windows-design-model` is not misrepresented as native Windows evidence.
- Spine AD-11/AD-12, protocol section 9, analysis plan, and memlog consistently say the design package cannot authorize strict production publication.

Therefore this review's PASS means the architecture's reality/currentness gate is clear. It does not mean native implementation conformance or production rollout is complete.

## Standards and Runtime Currentness

| Named primitive | Current authoritative check (2026-07-25) | Judgment |
| --- | --- | --- |
| Node.js 22 / 24 | Official schedule lists v22 Maintenance LTS through 2027-04-30 and v24 Active LTS until 2026-10-20, EOL 2028-04-30. v26 is Current and not yet LTS. <https://github.com/nodejs/Release/blob/main/schedule.json> | Restricting production receipts to 22 or 24 is current and conservative. |
| CPython 3.10 floor | Python Developer's Guide lists 3.10 in security status with EOL precision `2026-10`; PEP 619 says security fixes until approximately October 2026. <https://devguide.python.org/versions/> <https://peps.python.org/pep-0619/> | Registry correctly stores month precision and enforces an earlier 2026-09-01 review boundary with trusted current time. |
| JSON Schema | Official Draft 2020-12 meta-schema returns `application/schema+json` and identifies the declared URI. <https://json-schema.org/draft/2020-12/schema> | Current published dialect and correctly pinned. |
| JCS / JSON Pointer / Ed25519 | RFC 8785, RFC 6901, and RFC 8032 remain published RFCs. <https://www.rfc-editor.org/rfc/rfc8785> <https://www.rfc-editor.org/rfc/rfc6901> <https://www.rfc-editor.org/rfc/rfc8032> | Existing primitives; runner known-answer paths remain appropriate. |
| Windows durability APIs | Microsoft documentation remains live for `LockFileEx`, `ReplaceFileW`, `MoveFileExW`, and `FlushFileBuffers`. <https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex> <https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilew> <https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw> <https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers> | Named APIs exist and fit the stated local-filesystem model; native correctness remains deliberately gated on native evidence. |

## Gate Decision

The brownfield reality/currentness reviewer clears the frozen draft at **0 Critical / 0 High**. No architecture correction is required from this lens. Keep production roots empty and implementation conformance `pending` until the separately specified native adapter evidence passes; that residual implementation work is already explicit and is not a defect in the frozen design.
