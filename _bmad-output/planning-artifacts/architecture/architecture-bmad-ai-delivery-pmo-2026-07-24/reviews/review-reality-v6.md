# ARCHITECTURE-SPINE Brownfield Reality Review v6

## Verdict

**FAIL。** v5 的五个 High 已实质关闭：existing action patch、`program` routing、UTC 秒规范化、legacy WDR section 迁移、absent-ledger orphan repair 以及 design-only publication fence 都已进入冻结合约；但本轮仍有三个 High。它们不是“production adapter 尚未实现”，而是 target-state wire truth 仍允许错误迁移或 stale/semantically empty projection 被认定为合规。

## Frozen Review Target

本轮评审固定在以下 raw-byte hashes；写入本评审前复验未变化：

| Artifact | SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `a2b6e97c447adaf108539d42f47d3727532af66723cc0e578d5e97ae14187e42` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `b763091093b4e27b748f046d04736f498f40c5d7624204f7aadb94156b7100eb` |
| `contracts/CONTRACT-REGISTRY.json` | `5630c8ff49a2b3173150be3835ba2bd6297d74dbe2a73439f57b6df5713dd1c8` |
| `contracts/panel-sync-contracts.schema.json` | `a858fb31c06e4bd2aab5f02ef54cba1b5f4d6e028aecfda95a452960e78ecf73` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `5cf7ed4e1b249ec994a77e70b5691d6a66e1b9eb4711d11221e1aae3fcccabc2` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `e6cafdb8e8f6b04e286b136ba225cb7e7e6eca757e208a2edc5e42f84f1961c6` |
| `contracts/conformance/python-result.json` | `7d36c52e7b156eba8dbd4655c4eaaa65ac817e940e729ea8d46b902fa440a24b` |
| `contracts/conformance/node-result.json` | `2083ede0d1194261b8f3cad718aa1bac52b6a0a3d231a02f7f7683a4369807d6` |

## High Findings

### High 1 - Legacy `wdr_update` cannot deterministically become a current-field status intent

The pinned v1 meeting grammar has only one free-text `wdr_update` value plus generic item status/owner fields; it contains no target-field discriminator, collection mode, or separate progress/blocker/risk/dependency values (`skills/adp-meeting-sync/references/sync-plan-schema.md:42-53`). A single sentence therefore cannot be deterministically mapped to the typed `set` required by `statusMutationIntentV1` (`contracts/panel-sync-contracts.schema.json:410-437`).

The target state nevertheless says legacy `wdr_update` is converted to both a status intent and a history command (`ARCHITECTURE-SPINE.md:131`; `ANALYSIS-AND-OPTIMIZATION-PLAN.md:373`). The registry names `meeting-sync-v1-mapping-v1` but does not define this mapping (`contracts/CONTRACT-REGISTRY.json:403-417`), and the legacy vectors cover action identity, timestamp, aliases and program routing but no v1 WDR-text conversion (`contracts/fixtures/CONFORMANCE-VECTORS.json:232-263`). The structured v2 example is safe (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:170-196`), but it does not make old free text typed.

**Impact:** two conforming adapters can choose different current fields or collection modes for the same deployed meeting plan. A guessing adapter can destructively replace blockers/risks; a history-only adapter silently fails the promised current-field update.

**Required fix:** make legacy v1 `wdr_update` history/evidence-only unless an additive, explicitly typed status payload is present. Otherwise return a pinned `LEGACY_STATUS_INTENT_REQUIRED` gap and require v2 classification before mutation. Specify the exact adapter behavior and add positive/negative vectors proving that free text alone never mutates current fields.

### High 2 - Readiness projections remain mutable direct leaves outside the same-generation DAG

`adp-acceptance-readiness-review` derives `views/acceptance-readiness.md` and `views/cutover-readiness.md`, and can also rewrite each selected workstream's `readiness.md` (`skills/adp-acceptance-readiness-review/scripts/render_readiness_report.py:358-366,475-491`). Current roadmap explicitly declares both view files as render sources (`skills/adp-roadmap-sync/scripts/render_roadmap.py:58-75`), and meeting-pack parses them preferentially before its audit/prepass fallback (`skills/adp-meeting-pack/scripts/render_meeting_pack.py:1594-1624`).

The registry preserves both mutable views as direct `readiness-evidence` leaves for roadmap and meeting-pack (`contracts/CONTRACT-REGISTRY.json:584-585,627-628`), but the 16-edge DAG has no readiness producer (`contracts/CONTRACT-REGISTRY.json:460-476`). This contradicts the stated root cause that a canonical view must not become a later input and the invariant that current projections are not same-round leaves (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:18,31`). It also conflicts with the brownfield audit boundary: derived `views/` are not source of truth (`skills/adp-state-audit/SKILL.md:28-32`). Merely moving the readiness writer under the fact coordinator does not establish that its content was regenerated from the current WDR/evidence/L0 generation.

**Impact:** underlying readiness facts can change, state-audit/roadmap/meeting-pack can refresh, and the generation can still freeze an old readiness view whose bytes match the live file. The new manifest then certifies the stale derived value as a fresh leaf, recreating the Panel-lag defect inside the target architecture.

**Required fix:** either add acceptance/cutover readiness as schema-bound same-generation projection nodes with profiles, manifests, receipts and DAG edges, or remove these view reads and derive the boards from declared raw facts/current state-audit payload. Add a change-underlying-readiness-source vector showing the readiness producer and all consumers invalidate; the two `views/*-readiness.md` paths must not remain direct leaves.

### High 3 - The new payload schemas validate envelopes, not the business shapes consumed by Panel

The new bindings exist, but their nested business payloads remain effectively opaque: `programStatusPayloadV2` permits empty `progress` and `flow_state`; roadmap timeline items are arbitrary objects; meeting-pack `boards` is an unconstrained object (`contracts/panel-sync-contracts.schema.json:1184-1231`). The target Panel schema likewise accepts arbitrary objects under audit, action projection, status, roadmap, flow, meetings and each view (`contracts/panel-sync-contracts.schema.json:588-620`). The checked-in positive vectors intentionally pass `{}` for progress, flow state, boards and source preview (`contracts/fixtures/CONFORMANCE-VECTORS.json:91-132`).

This is weaker than the brownfield producers the design must migrate: program-status already has pinned progress-v3 and flow-state schemas, while roadmap and meeting-pack expose concrete fields that their consumers render. Protocol says a producer receipt is allowed after payload schema validation and consumers may read only schema-declared fields (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:55-56`), but these definitions do not declare those nested fields.

**Impact:** a production adapter can emit schema-valid but unusable payloads, receive a successful producer receipt, and reach atomic Panel publication. The promised schema gate therefore cannot distinguish a complete projection from one missing the actual progress, timeline or meeting boards.

**Required fix:** bind the complete Panel-consumed shape. Reuse the existing progress-v3/flow-state schemas and define concrete roadmap item, meeting-board and Panel view schemas with required identities/status/lineage fields. Add negative vectors for missing nested fields and compatibility vectors that transform representative current producer outputs without data loss.

## Verified Closures And Evidence

- The seven pinned brownfield source hashes match actual bytes, including the WDR template, meeting/status v1 grammars, ledger template, Panel schemas and flow-graph schema.
- Action create and patch carry exact IDs; partial patch preserves omitted fields; `program` is separated from physical WDR routing; offset/fractional meeting timestamps normalize to UTC seconds.
- Legacy status/checkpoint WDR order has a pinned first-patch migration; orphan WDR action repair supports `expected_present=false, revision=null`; journal targets now carry roles, apply order and before/after image locators.
- Panel no longer dereferences live paths from upstream payloads, and blocked producer receipts cannot claim an output.
- The declared brownfield regression scope passes 199/199: meeting-sync 25, status-sync 29, state-audit 63, management-panel 28, panel-audit 12, state-prepass 10, panel-model 6 and panel-contract 26. The additional six DingTalk meeting tests also pass but are not part of the stated 199 total.
- Both reference harnesses pass 58/58 when rerun against the frozen registry/schema/protocol/vectors. Receipts correctly say `evidence_kind: design-fixture-check` and `native_durability_exercised: false`; registry remains `implementation_conformance_status: pending`.
- Requiring production adapters, real POSIX fault injection and native Windows CI is an explicit release prerequisite, not a High by itself.

## Pass Conditions

1. Make legacy free-text `wdr_update` fail visible for current-field mutation and add exact adapter vectors.
2. Remove readiness derived views from direct leaves or add them as complete same-generation projection nodes.
3. Replace opaque nested payload objects with schemas for every field Panel/consumer logic reads, with representative migration and negative vectors.
4. Regenerate the raw-hash chain and rerun the reality gate on one frozen target.
