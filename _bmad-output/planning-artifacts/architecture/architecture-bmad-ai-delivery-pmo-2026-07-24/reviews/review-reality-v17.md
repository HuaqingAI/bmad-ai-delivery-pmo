# ARCHITECTURE-SPINE Brownfield Reality / Currentness Review v17

## Verdict

**FAIL. Critical: 0. High: 2. Medium: 0. Low: 1.** The zero-Critical/zero-High gate is not met. The package is internally reproducible at 512 vectors and the v16 Roadmap, refresh-status, Node 23, durable release-evidence, source-time, and first-publication corrections are present in current raw bytes. Two implementation-blocking reality gaps remain: evidence expiry is evaluated against the historical receipt timestamp rather than the current gate/inspect time, and the strict fact fence still has no typed-command/attribution path for the risk-flow and decision facts that the architecture explicitly keeps under direct risk-review ownership.

## Frozen Review Target

- Review date: 2026-07-25 (Asia/Shanghai).
- Repository: `HEAD=a5d873e0ad3e7d60e7157f76096c6ac65085bee3` on `master`.
- Architecture spine raw SHA-256: `b4f421c9a78514e8006e905dc43e9b5979f6259eb3a6d33758128b54380d4604`.
- Analysis plan raw SHA-256: `ed211f41ba30100668aad2b512870a2323b67c42ab202774c353c2f8205bb06f`.
- Pre-existing unrelated untracked paths were `skills/reports/adp-operational-artifacts-panel-refresh-plan.md` and `skills/reports/eval-runs/`; they were not modified.
- This reviewer created only this review file. No spine, companion, contract, fixture, runner, result, source, or production artifact was edited.

## High Findings

### H1 - The support-review deadline and trust-root expiry never expire previously signed evidence

The committed policy is time-sensitive. Raw registry sets CPython `support_review_before=2026-09-01T00:00:00Z` (`CONTRACT-REGISTRY.json:5-7`), and protocol section 9 says receipts must be rejected once that time is reached unless the registry policy has been reviewed (`WDR-AND-TRANSACTION-PROTOCOL.md:106`). Protocol section 2 likewise says an expired production root must immediately return implementation conformance to `pending` (`WDR-AND-TRANSACTION-PROTOCOL.md:23`).

Both executable release gates compare those limits only to the receipt's historical `executed_at`:

- Python `release_gate_accepts()` has no evaluation-time/current-time input. It parses `row["executed_at"]`, rejects Python only when that old value is at or after `support_review_before`, and checks root `not_before/not_after` against the same old value (`python_runner.py:1618-1620`, `:1674-1677`).
- Node has the same behavior: `releaseGateAccepts()` has no evaluation clock and compares `runtimePolicy.support_review_before` and root validity only to `row.executed_at` (`node_runner.mjs:817-840`).
- The durable release set has `accepted_at`, but the loader verifies its shape, identity, registry binding, paths, and bytes without using it as a policy-evaluation clock (`python_runner.py:1776-1805`; Node `node_runner.mjs:898-920`). Live inspect composes this loader and therefore inherits the same gap.
- The sole deadline vector does not test passage of time. It rewrites and re-signs the Python receipt itself with `executed_at=2026-09-01T00:00:00Z` (`CONFORMANCE-VECTORS.json:899`; `python_runner.py:7786-7789`; `node_runner.mjs:4759-4760`). A July receipt is never replayed with a September gate time. There is no current-root-expired vector.

Consequently, on 2026-09-01 the checked July evidence still passes because its signed execution occurred on 2026-07-24. The same root can continue authorizing previously signed receipts after its `not_after`. That contradicts the stated lifecycle policy and allows strict open/inspect to remain authorized by stale runtime policy indefinitely.

**Required correction:** give release acceptance and every strict live inspect a trusted, explicit evaluation-time input that is not taken from candidate evidence. Check `evaluation_time < support_review_before`, current root validity/retirement, and release-set acceptance chronology independently from `receipt.executed_at`; bind the gate evaluation into an appropriate mutable acceptance/inspect receipt without making candidate-controlled time authoritative. Add fixed-time vectors that keep the July receipt unchanged but advance the gate beyond 2026-09-01 and beyond a root's `not_after`, plus boundary and registry-policy-update positives in both runners.

### H2 - Direct risk-flow and decision-fact writes cannot enter the mandatory strict fact fence

The architecture deliberately keeps these writes in scope. AD-1 says risk review directly owns risk-flow and decision facts and that every projection-relevant fact commit is journaled by the fact coordinator and advances fact generation (`ARCHITECTURE-SPINE.md:79-83`). Protocol section 2 repeats direct risk ownership (`WDR-AND-TRANSACTION-PROTOCOL.md:20`), and section 4 explicitly includes decision and risk-index commits in the mandatory shared-lock/journal/fact-generation rule (`WDR-AND-TRANSACTION-PROTOCOL.md:60`). The flow-graph profile then consumes `views/risk-flow.json` as a fact-generation-bound leaf (`CONTRACT-REGISTRY.json:1010-1017`). This is not an optional peripheral file.

The raw wire package has no legal transaction for that owner:

- The 52 registered contracts define action, WDR, and bootstrap-migration commands, but no risk-flow or decision-fact mutation command (`CONTRACT-REGISTRY.json:302-380`; complete contract list ends at `release-evidence-set`).
- `fact-receipt-attribution/1.0.0` scopes action/WDR contracts and derives either action-ledger or WDR target bytes; it does not include a risk or generic fact command (`CONTRACT-REGISTRY.json:766-770`).
- The reference dispatcher recognizes only WDR and bootstrap schema IDs and treats every other command as action. Target derivation is consequently either the three action targets or WDR/state/sidecar paths (`python_runner.py:2802-2808`, `:2833-2844`; Node mirrors this split).
- The strict writer spec for `adp-risk-dependency-change-review` is `delegated_only=true` with empty allowed fields and sections (`CONTRACT-REGISTRY.json:68`), so its declared direct fact ownership cannot be authorized through the WDR field/section capability model.
- The 512-vector fact graphs cover action, WDR status/history/owned-section/Roadmap/refresh-actions, and bootstrap. They contain no complete risk-flow/decision command, exact target proof, capability authorization, receipt, or restart recovery graph.

A conforming strict implementation therefore has only two choices: reject the current risk owner's direct writes, or let them bypass the coordinator. The second choice permits `views/risk-flow.json` or decision facts to change without fact-generation advancement while refresh freezes its supposedly fenced leaf set. That reopens the source-race/freshness failure AD-1, AD-4, and AD-12 are meant to close.

**Required correction:** register a closed typed command and schema-bound fact-state/target contract for risk-flow and decision facts (or a closed generic fact command whose producer/path/schema allowlist is raw-registry-derived), grant least-authority scopes to the risk writer, and extend fact attribution to derive exact roots, paths, operations, CAS, before/after bytes, generation, journal, and receipt. Add positive risk-flow plus decision-fact graphs and wrong-producer/path/schema/extra-target/stale-generation/restart negatives. Audit the other authoritative non-WDR writers named by protocol section 4 under the same rule rather than treating writer-attestation inventory as proof of transaction coverage.

## Low Finding

### L1 - The registry invents day-level precision for Python 3.10 end of support

Raw registry records `python_3_10_security_support_ends=2026-10-31T00:00:00Z` (`CONTRACT-REGISTRY.json:6`). The authoritative Python Developer's Guide version table gives Python 3.10 end of security support only as `2026-10`, linked to PEP 619; it does not establish October 31 as a normative instant. This field is currently informational and the earlier September review deadline is the actual gate, so the mismatch is Low rather than High.

**Recommended correction:** cite the authoritative schedule and store the supported precision (`2026-10`) or remove the unused exact timestamp. Do not present an inferred last day of month as an upstream guarantee.

## Closed v16 Findings

- **Roadmap authorization:** `wdrPatchV1` now has a dedicated `roadmap` replace payload; `wdr_field_section_map` maps it only to Roadmap; status-sync alone receives the field, and owned-section substitution/heading injection are negative vectors (`CONTRACT-REGISTRY.json:69,124-136`; `python_runner.py:6422-6428,7163-7167`).
- **Refresh status path:** spine, plan, protocol, registry, and both handlers consistently use `state/panel-refresh-status.json` (`ARCHITECTURE-SPINE.md:125`; `ANALYSIS-AND-OPTIMIZATION-PLAN.md:484`; `CONTRACT-REGISTRY.json:99`; protocol section 6).
- **Node allowlist branch:** Node 23 now has an explicit rejection vector and both handlers execute it (`CONFORMANCE-VECTORS.json:885-890`; `python_runner.py:7780-7785`; `node_runner.mjs:4756-4757`).
- **Durable release evidence:** a registered release-evidence set and exact receipt/blob paths exist, loader closure rejects missing/extra/unindexed bytes, and attestation/live inspect bind the set ID. H1 concerns temporal validity after loading, not storage closure.
- **Source time and publication bootstrap:** same-generation `source_as_of` equality and the absent pointer/state dual-create first-publication branch are present in spine, protocol, schema, registry semantics, and both handlers.

## Brownfield Reality

All 23 registered source pins match current repository raw bytes. The five reported problems remain observable in the unchanged production implementation and are correctly represented as behavior to replace, not as completed work:

| Reported problem | Current production reality |
| --- | --- |
| Meeting sync cannot mutate existing action owner/status | Meeting v1 intake still emits create-shaped updates without an exact revision-bearing patch identity; status merge behavior remains the brownfield path. |
| `wdr_update` appends history but does not update Panel current fields | Meeting sync still appends a Meeting Sync Update block; status-sync remains the current-field writer. |
| Panel inspection does not validate changed live source facts | Production Management Panel inspection still validates artifact identities without the proposed restart-safe live-leaf closure. |
| WDR/ledger projection drift detection is incomplete | Production prepass remains ID-oriented and does not provide the proposed complete typed content verdict, including the empty-active-ledger case. |
| Canonical audit output loses actionable IDs | Current audit source still drops the raw `action_id` from canonical public finding output before repair formatting. |

The intended implementation modules `skills/adp-fact-transaction`, `skills/adp-wdr-mutation`, and `skills/adp-panel-refresh` remain absent. That is consistent with draft/pending design status. It is not production proof.

## Deterministic Evidence

| Gate | Observed result |
| --- | --- |
| Architecture lint | 0 findings |
| Registry inventory | 52 contracts; 23 source pins; 9 enumerators; 7 profiles; 7 payload bindings; 4 nested bindings; 6 Panel bindings; 15 DAG edges; 47 typed ordering rules; 15 identity-set rules; 3 semantic sequences; 44 runtime paths; 14 validators |
| Vector inventory | 512 IDs, 512 unique |
| Python fixed replay | CPython 3.12.13, 512 passed / 0 failed; byte-identical to checked-in `python-result.json` |
| Node fixed replay | Node 24.16.0, 512 passed / 0 failed; byte-identical to checked-in `node-result.json` |
| Cross-runtime IDs | Passed-vector sets identical |
| Brownfield tests | 205/205 passed (179 module tests + 26 Panel contract tests) |
| Program Lead additions | 17/17 passed |
| Panel v1 regeneration | Byte-identical; SHA-256 `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` |
| Draft schemas | Nine JSON files parse and declare `https://json-schema.org/draft/2020-12/schema` |
| Source pins | 23/23 raw hashes match |
| Production trust boundary | `trust_roots=[]`; minimum roots 2; `implementation_conformance_status=pending`; spine `status: draft` |

Current normative hashes also match the spine and registry pins:

| Artifact | Raw SHA-256 |
| --- | --- |
| Registry | `f02b7af8867c846f7d13fbcf2e295fc06abf1c841fb6d0441319acf7240e1f26` |
| Schema bundle | `18841ac0824ef24eac64336a71f42d50d75945aff79db0b020805b18f03c64c9` |
| Protocol | `0dd17ab3978419929610f6c54ca5b052bff0b2a2bb36f9fbdc4a59f736e280b3` |
| Vector suite | `73dcbd57ed422230029865f69183baaec4d8eba1d1b1255298a7a41737a4ea62` |
| Python runner | `acad24c7e01aad290339013a3d751f79eabbcedf57601dd3f877477a8c314e61` |
| Node runner | `5f9dc284c8151552058bd2ced550cdb6b1791976749c57e7788156cdb8b4fe16` |
| Python result | `2148736f104d05ae20157c4d7efbb1aa94f9f8b2f0a5fd279ba1f65a9aa96034` |
| Node result | `cbd91e9f2474fbf458f473fd256d295babdb9ece40c9f88d3be4d291fce20d4f` |

Both receipts remain honestly marked `design-fixture-check` with `native_durability_exercised=false`; neither can authorize production.

## Standards and Runtime Currentness

- The official Node.js release schedule lists v22 in Maintenance LTS until 2027-04-30 and v24 in Active LTS until maintenance begins 2026-10-20, with EOL 2028-04-30. v26 is Current until its planned 2026-10-28 LTS transition. Restricting production to majors 22 and 24 is current and appropriate on 2026-07-25: <https://github.com/nodejs/Release/blob/main/schedule.json>.
- The Python Developer's Guide lists 3.10 in security support through `2026-10`; the 2026-09-01 review trigger is appropriately early, but H1 means the executable gate does not actually trigger with wall-clock passage: <https://devguide.python.org/versions/>.
- The official Draft 2020-12 meta-schema remains published at the declared URI and the package's nine schema documents use it: <https://json-schema.org/draft/2020-12/schema>.
- RFC 8785 JCS, RFC 6901 JSON Pointer, and RFC 8032 Ed25519 remain published standards. Both reference implementations execute canonicalization, pointer, and signature known-answer paths: <https://www.rfc-editor.org/rfc/rfc8785>, <https://www.rfc-editor.org/rfc/rfc6901>, <https://www.rfc-editor.org/rfc/rfc8032>.
- Microsoft still documents `LockFileEx`, `ReplaceFileW`, `MoveFileExW`, and `FlushFileBuffers`; the named Windows primitives exist and fit the declared local-filesystem durability model. Native correctness remains intentionally unproven until real Windows evidence is accepted.

## Gate Decision

Do not set `ARCHITECTURE-SPINE.md` to `status: final` and do not treat the package as strict-production implementation-ready. Add a non-candidate-controlled evaluation clock to release/inspect policy and close direct risk-flow/decision-fact mutation under the same typed command, native authority, journal, generation, and receipt model. Then regenerate all dependent pins/results and rerun the full gate. Production roots must remain empty and implementation conformance must remain `pending` until the separate native evidence requirements pass.
