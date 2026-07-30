# Architecture Spine Rubric Review v6

## Gate Verdict

**FAIL。** 机械 lint、冻结 raw hashes、40 个 contracts、8 个 read profiles、8 个 payload bindings、16 条 DAG edge、12 条 canonical ordering rule，以及两份 58/58 design-fixture receipts 均可复核；v5 的 meeting current-field authorization、legacy WDR section migration、orphan repair、refresh run/status 拆分等主要问题也已在文字与基础 schema 中关闭。但 good-spine gate 仍有 **4 个 High、0 个 Critical**：WDR action sidecar 在 fact ownership 与 refresh DAG 中角色冲突，required drift verdict 无法读取它声称比较的实际 sidecar；WDR create wire 没有给 engine 提供必须验证的 logical input；新增 payload schemas 仍不足以约束 Panel 实际消费的嵌套 shape；fact receipt 无法携带 AD-1 明确要求的 initiating producer/capability 或 P0-B 的 action mutation delta。

`implementation_conformance_status=pending`、`native_durability_exercised=false` 和 strict publication 尚未启用均已如实声明，是正确的 release prerequisite；本评审没有把“生产 adapter 尚未实现”本身计为 finding。

## Frozen Review Target

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `a2b6e97c447adaf108539d42f47d3727532af66723cc0e578d5e97ae14187e42` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `b763091093b4e27b748f046d04736f498f40c5d7624204f7aadb94156b7100eb` |
| `contracts/CONTRACT-REGISTRY.json` | `5630c8ff49a2b3173150be3835ba2bd6297d74dbe2a73439f57b6df5713dd1c8` |
| `contracts/panel-sync-contracts.schema.json` | `a858fb31c06e4bd2aab5f02ef54cba1b5f4d6e028aecfda95a452960e78ecf73` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `5cf7ed4e1b249ec994a77e70b5691d6a66e1b9eb4711d11221e1aae3fcccabc2` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `e6cafdb8e8f6b04e286b136ba225cb7e7e6eca757e208a2edc5e42f84f1961c6` |
| `contracts/conformance/python-result.json` | `7d36c52e7b156eba8dbd4655c4eaaa65ac817e940e729ea8d46b902fa440a24b` |
| `contracts/conformance/node-result.json` | `2083ede0d1194261b8f3cad718aa1bac52b6a0a3d231a02f7f7683a4369807d6` |

## Verification

- Architecture lint: **PASS，0 findings**。
- Registry consistency: **PASS**。40 个 contract pointer/anchor 全部解析；8 个 profile 与 8 个 payload binding 按 projection kind 一一对应；profile direct-upstream 推导出的 DAG 与 16 条 registry edge 完全相等；12 条 ordering rule 可解析。
- Binding hashes: **PASS**。document-workspace schema 与 external flow-graph schema 的 raw hash/pointer 均与 registry 相符。
- Pinned brownfield sources and runner hashes: **PASS**。两套 runner 均实际检查这些 pins，registry 中的 runner hashes 与磁盘 bytes 相符。
- Design fixture replay: **PASS at declared strength**。Python semantic run为 58 passed / 0 failed，重建 result bytes hash 为 `7d36c52e...`；Node runner为 58 passed / 0 failed，stdout bytes hash为 `2083ede0...`。两份 `result_id` 均可由去除自身字段后的 canonical bytes 重算。
- Evidence classification: **PASS**。receipt 均标记 `design-fixture-check` 和 `native_durability_exercised=false`；registry 保持 `implementation_conformance_status=pending`。

## High Findings

### H1 - Required WDR/ledger drift verdict cannot read the actual action sidecar it claims to validate

AD-5 states that `workstreams/<id>/action-projection.json` is the status-sync-owned durable sidecar and that state-audit must compare ledger, sidecar, and exact rendered WDR before emitting the required drift verdict (`ARCHITECTURE-SPINE.md:103-107`). The contract seed likewise names `adp-status-sync` as the WDR action projection producer (`:183`).

The registry simultaneously models `wdr-action-projection` as a refresh DAG node derived from ledger, WDR, and WDR file state (`CONTRACT-REGISTRY.json:498-508`), then makes that generated node the direct predecessor of the drift verdict (`:511-521`). Crucially, the drift-verdict profile does **not** enumerate `action-projection.json`; only the later state-audit profile does (`:538`). Under AD-4's `actual reads == registry-derived allowed reads`, the drift producer is forbidden from reading the actual sidecar. Yet the verdict schema requires a `sidecar_fingerprint`, and AD-5 makes that verdict the strict publication gate.

Two interpretations are therefore possible and incompatible: treat the status-sync sidecar as the authoritative fact leaf, or regenerate a same-generation projection and compare only ledger/WDR. The latter can report `in-sync` without validating the persisted sidecar; the former violates the pinned read profile.

**Disposition: Fix.** Choose one role. The direct design is to keep `action-projection.json` as a status-sync-owned fact sidecar, enumerate it in the drift profile, and remove the same-named regenerated DAG node; alternatively define a separately named immutable snapshot node that reads the actual sidecar. In either case, make the drift verdict's ledger/WDR/sidecar fingerprints derivable from its allowed inputs and add a changed-sidecar invalidation/vector.

### H2 - WDR create command cannot carry the logical input the engine is required to validate

AD-3 requires WDR create to be generated from schema-valid logical input and the pinned renderer (`ARCHITECTURE-SPINE.md:91-95`). Protocol section 3 goes further: the engine validates the input ID, template hash, rendered exact bytes, and rendered hash before commit (`WDR-AND-TRANSACTION-PROTOCOL.md:38-40`).

The logical input is a registered object with `input_id` (`panel-sync-contracts.schema.json:191-224`), but `wdrCreateV1` contains only `rendered_record`, `rendered_sha256`, template data, and evidence (`:338-356`). It has neither an embedded logical input nor an input reference/hash. Registry ownership confirms that `workstream-create-input` is read only by `adp-workstream-register`, while the shared engine reads only `wdr-mutation` (`CONTRACT-REGISTRY.json`, contracts `workstream-create-input` and `wdr-mutation`). The vector runner can perform the check only because the fixture stores `create_input_without_identity` beside, not inside, the command.

A conforming engine can verify the rendered hash but cannot independently re-render or validate the claimed logical input. Trusting producer-rendered bytes contradicts the stated engine gate; retrieving an out-of-band input invents an unregistered dependency.

**Disposition: Fix.** Embed a schema-valid `create_input` in `wdrCreateV1`, or include a content-addressed immutable input handle whose contract, reader, and transaction lifetime are registered. Bind `workstream_id`, `input_id`, template, rendered bytes, and command identity cross-field, and run the create vector through the command alone.

### H3 - The “schema-bound payload” gate is nominal for the fields Panel and downstream producers actually consume

AD-4 and AD-11 promise that a schema-invalid canonical payload cannot produce a receipt and that the registry prevents implementations from choosing incompatible field shapes (`ARCHITECTURE-SPINE.md:97-101,139-143`; Protocol `:56`). The eight binding records exist and their hashes resolve (`CONTRACT-REGISTRY.json:486-494`).

However, the new schemas leave their consumer-critical bodies unconstrained:

- `programStatusPayloadV2.progress` and `flow_state` are arbitrary objects (`panel-sync-contracts.schema.json:1184-1197`), despite the brownfield repository already owning detailed progress v3 and flow-state v1 schemas that roadmap consumes.
- `roadmapPayloadV2` accepts arbitrary objects in both milestone arrays (`:1200-1214`).
- `meetingPackPayloadV2.boards` is an arbitrary object (`:1216-1232`).
- `managementPanelPayloadV2` accepts arbitrary `audit`, `action_projection`, status/roadmap/flow/meetings objects and arbitrary three view objects (`:588-620`).

Protocol says consumers may read only schema-declared fields, but none of the nested fields they need are declared. Conversely, treating arbitrary nested properties as readable allows `{}` or incompatible shapes to be schema-valid and receive a projection receipt. This recreates the producer/Panel contract divergence AD-11 is meant to prevent.

**Disposition: Fix.** Reference and pin the existing progress/flow schemas, register complete milestone/meeting-pack/Panel composition shapes or exact pinned brownfield schemas, and declare every field a downstream producer or Panel renderer reads. Add negative vectors proving empty/wrong nested shapes are rejected and an envelope-level vector proving projection kind, payload schema ID/hash, payload bytes, and receipt remain bound.

### H4 - The only fact receipt schema cannot represent the ownership and action-delta audit promised by AD-1/P0-B

AD-1 requires every projection-relevant fact commit to be journaled by the coordinator and explicitly says the receipt records the initiating producer/capability (`ARCHITECTURE-SPINE.md:79-83`; Protocol `:49`). The implementation plan additionally requires status-sync receipts to record per-entity before/after revision, changed fields, and evidence (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:154-164,399-410`).

The registry has only `fact-mutation-receipt/1.0.0` for this boundary. Its closed schema contains transaction/journal IDs, fact-generation transition, generic mutation targets, and status, but no initiating producer, capability ID/epoch, entity ID, before/after entity revision, changed fields, or evidence (`panel-sync-contracts.schema.json:983-999`). `additionalProperties: false` prevents an implementation from adding them. No separate status-sync mutation receipt contract is registered.

The coordinator can therefore prove that bytes committed, but not which authorized semantic owner initiated them or that an owner-only action patch preserved omitted fields. That makes AD-1's delegated single-writer audit impossible on the pinned wire and leaves P0-B implementations to invent incompatible receipt extensions.

**Disposition: Fix.** Extend the fact receipt with authenticated initiating producer/capability epoch and typed entity deltas, or register a separate status-sync mutation receipt that is atomically linked to the fact receipt/journal. Pin its identity/order rules and add owner-only patch plus meeting-intent attribution vectors.

## Good-Spine Checklist

| Checklist | Result | Notes |
| --- | --- | --- |
| Fixes the real divergence points for the level below | **Partial** | Exact action patch, status intent routing, legacy WDR migration, live inspect, per-batch repair, and journal recovery are substantially fixed; H1-H4 leave four core boundaries non-convergent. |
| Every AD rule enforceable and prevents its stated divergence | **Fail** | AD-3 cannot be enforced from the create wire; AD-4/5 cannot derive the required drift verdict from allowed reads; AD-11's payload binding is too shallow; AD-1 receipt attribution is unrepresentable. |
| Nothing under Deferred leaks a required decision | **Pass** | Action Center, watcher/daemon, DB migration, fuzzy matching, and offline live freshness are safely outside the slice. |
| Named technology/current versions | **Pass** | Draft 2020-12, RFC 8785, Python >=3.10, POSIX/Windows semantics, and versioned contract artifacts are explicit; no unverified vendor dependency is bound. |
| Ratifies brownfield rather than contradicting it | **Partial** | Profiles now cover the cited source inventory and legacy layout/routing are addressed, but H3 fails to bind brownfield progress/flow contracts and H1 gives the existing action sidecar two roles. |
| Covers the source capabilities | **Partial** | All five reported user problems have a target path, but drift validation and action-mutation audit remain incomplete on the actual wire. |
| Parent-spine inheritance | **N/A** | No parent spine is declared. |
| Operational/environmental envelope | **Pass with release prerequisite** | Locks, generations, journals, recovery, path safety, inspect, status, POSIX/Windows primitives, and explicit pending implementation gate are covered. Production/native evidence remains a correctly declared prerequisite, not a design failure. |

## Verified Improvements

- Meeting/checkpoint/risk current-field changes now route through typed status intent and status-sync capability; direct meeting current-field mutation is explicitly rejected.
- Action create and patch both carry exact IDs; `program` is constrained to action routing, timestamps normalize to UTC seconds, and omitted patch fields preserve value.
- Legacy Roadmap/checkpoint/meeting section orders and first `Last status sync` insertion are pinned.
- Empty-ledger orphan repair, exact finding/action/read-set equality, durable nonce CAS, journal image locators, remove-to-tombstone, and per-batch retry are present.
- Refresh-run receipt and mutable refresh status are separate, and blocked producer receipts no longer require fabricated output IDs.
- The reference harnesses now load the registry, validate artifact/source pins and schema pointers, substitute contract hashes, and label their evidence strength honestly.

## Gate Exit

Close H1-H4 in registry/schema/protocol/vectors, regenerate the complete raw-hash chain and both design receipts, then rerun lint and all reviewer lenses. Strict publication must remain disabled and both main documents must remain `draft` until this design gate passes; production adapter, POSIX fault-injection, and native Windows evidence remain the subsequent implementation release gate.
