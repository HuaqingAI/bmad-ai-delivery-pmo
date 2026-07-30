# Architecture Spine Adversarial Review v5

## Frozen Review Base

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `976904b3702c932a9e79f6e0d54721c7590cc2a9341c44fb42e171645dba96db` |
| `contracts/CONTRACT-REGISTRY.json` | `7b6403d9c9e8734e32556dc3555de5c8fb43f4411a7e379b6cb31d2fc5861d9e` |
| `contracts/panel-sync-contracts.schema.json` | `3b11b8c86fcb5b7272dd86576afdce10c50745489e8224e78acc89ff1e430bf8` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `b86bba208688eeeb5c70b04202437c23ecfced8e3143f6a4bf95dcbc5623c434` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `fd2f6bfbdcc4710851fc22211cfcca000e40391bc103f46ef3978ea53b8fe4a9` |

Registry 中固定的 schema、protocol、suite、runner 和 result raw hashes 均与磁盘文件一致。Python 与 Node runner 使用固定 `executed_at` 重跑后与已提交 result 文件 byte-for-byte 相同；两份 result 的 `result_id` 也可由各自去除 `result_id` 后的 JCS bytes 重新得到。两者都报告 32 passed、0 failed。这只能证明当前 runner 可复现，不能弥补下列 contract 与证据缺口。

## Verdict

**FAIL。** v4 的 projection self-invalidation、multi-batch token 和不存在的 meeting policy source 已关闭，但当前冻结集仍允许独立实现对 action identity、projection identity/shape、Panel binding、journal recovery、legacy ingress 和 repair batch 得出不同结果。最严重的是 conformance gate 并未执行它声称证明的 schema/registry/protocol 规则，因此 32/32 不能作为 AD-11 的 release evidence。

## Findings

- Conformance runner 没有加载 registry、没有执行 JSON Schema、没有做 contract negotiation，也没有把 `$SCHEMA_SHA256` / `$REGISTRY_SHA256` substitution tokens 替换成真实 hash。suite 中的 create command 和 generation envelope 因而携带不符合 `sha256` pattern 的字面量 token，仍被两个 runner 判为通过；Node 只在 `node_runner.mjs:17-25,45-145` 做按 vector ID 分支的局部断言，Python 在 `python_runner.py:40-157` 做同构断言。实现 A 可以完全忽略 unknown fields/contract hash，实施 B 可以错误计算 receipt identity，两者仍能得到同一 32/32。release gate 必须对 substitution 后的完整 fixture 执行 registry negotiation、对应 anchor 的 schema validation、cross-field semantic validator和 expected whole-output comparison。

- 两个所谓 independent adapters 是相同 decision table 的逐语言移植，并未调用任何 production adapter；`windows-model` 只是命令行字符串，Node runner 不检查 `process.platform`，`first-create-absent-target` 只验证 fixture 写着 `primitive == durable_create`，symlink/reparse/durability vectors 也只检查预填的 `expected_error`。因此 POSIX 实现 A 使用不安全的 follow-open、Windows 实现 B 从未调用 `CreateFileW`/`ReplaceFileW`，仍都能出具当前 evidence。结果 receipt 应记录 runner/implementation artifact、OS/runtime/volume capability，并由 fault-injected filesystem harness 实际执行每个 durability boundary；两套实现还需要不同 codebase/maintainer provenance，而不是镜像断言。

- Registry 没有为 `state-audit`、`program-status`、`roadmap`、`flow-graph`、`meeting-pack`、Management Panel payload 或 Panel immutable manifest 注册 wire schema；现有 contracts 只覆盖 dependency manifest/receipt、drift verdict和 pointer（`CONTRACT-REGISTRY.json:114-168,328-350`）。producer A 可以输出 `{workstreams: [...]}`，producer B 可以输出 keyed object，只要各自给任意 bytes 计算 projection ID，二者都能生成 schema-valid manifest/receipt；Panel builder 随后只能依赖未固定的 brownfield shape。AD-4、AD-6、AD-8 和 AD-11 要求的 `artifact_integrity`、`business_freshness`、`source_as_of` 也没有可验证的 Panel manifest contract。应注册每个 canonical payload及其 identity field，并让 binding catalog 指向这些 schema 的具体版本/hash。

- Binding catalog 把所有 whole-projection `source_pointer` 写成 `/`（`CONTRACT-REGISTRY.json:345-350`），schema 只要求字符串以 `/` 开头（`panel-sync-contracts.schema.json:564-589`），protocol 没有指定 pointer dialect。RFC 6901 中根 pointer 是空字符串，`/` 指向名称为空的成员；binder A 按 RFC 6901 会找不到数据，binder B 把 `/` 当自定义 root sentinel 会成功，两者都没有违反一个已固定的 pointer 标准。应明确采用 RFC 6901 并把 root 改为 `""`、放宽 schema，或注册一个明确将 `/` 定义为 root 的 ADP pointer dialect和 vectors。

- RFC 8785 不会重排 array，但所有 content identities 都依赖带有语义集合的 arrays：selection policy 的 include/exclude/meeting kinds、generation roots/leaf sources、manifest sources/upstreams、producer receipt consumed sets、Panel pointer projections以及 repair read set。protocol 只固定单个 glob enumerator 内的 path 排序，没有固定跨 category/root/instance 的总 comparator，也没有规定 caller-provided selection arrays是否先规范化。相同选择把 `include_workstreams` 与 `meeting_kinds` 反序即可得到不同 `policy_id`；同一对 roots 反序也得到不同 generation hash。orchestrator A 按 profile declaration order连接，B 按 `(root_instance_id,path)` 全局排序，均能声称完整且 JCS-valid，却产生不同 generation/manifest/pointer IDs。每个 identity-bearing array必须规定唯一排序键、duplicate truth table和 normalization stage，并用 permutation vectors证明 identity 不变。

- Action create command 没有 `action_id`（`panel-sync-contracts.schema.json:144-163`），protocol/registry也没有 action ID allocator、command-to-action mapping或 ledger sequence state。status-sync A 可分配 `ACT-0001`，status-sync B 可分配 command-hash-derived ID；两者都满足 uppercase hyphenated stable ID、同 command replay no-op和单 writer规则，但 meeting archive、audit findings、WDR markers及批量修复会引用不同 action ID。必须固定 deterministic allocation algorithm和collision resolution，或让 create command携带由可信 producer预分配且由 single writer验证的 exact ID。

- `action-ledger-mutation` 注册的 schema 实际是 ingress `actionCommandV2`，没有 action ledger durable-state schema、Markdown row renderer、row ordering、revision serialization、action-ID allocation state或 durable command receipt index（`CONTRACT-REGISTRY.json:73-80`；`ARCHITECTURE-SPINE.md:82-86,146`）。writer A 可原位更新并保留历史顺序，writer B 可按 action ID重排整表；两者得到相同 logical actions/revisions却产生不同 raw ledger fingerprint，从而触发不同 generation和repair tokens。应 pin ledger parser/renderer与byte-exact fixtures，并注册 command-id/fingerprint/result index 的 durable schema和transaction target。

- WDR engine 对同一个 create command提交相同 bytes，但 create producer 从 workstream registration facts生成这些 bytes 的算法仍未固定。Registry只 pin placeholder template raw hash；没有 placeholder substitution schema、escaping、artifact-table/list ordering、missing-value rendering、Created timestamp source或 rendered record validator grammar。producer A 可保留 intake artifact顺序，producer B可排序；两者都能提交带 H1、required labels和正确 section order的 canonical whole-file command。单个 hand-authored `create-byte-exact` vector只重新 hash fixture 自带的 `rendered_record`，没有从 logical input渲染，也没有比较 template expansion。应注册 create-input contract、deterministic renderer及多组 input-to-exact-bytes vectors，并让 runner实际执行 renderer和engine validator。

- 一个 `wdrPatchV1.set` 可同时携带多个 current fields、history records和多个 owned section mutations（`panel-sync-contracts.schema.json:218-250`），但 protocol 的“current-field mutation递增 revision/generation、history/section只递增 generation”没有说明混合 command 或多 field 是每 command递增一次还是每 logical mutation递增。schema也允许同一 owned section重复出现，以及 meeting records使用相同 `(observed_at,entry_id)`；tie rejection/dedup/order未定义。engine A 对整个 command增量一次并按 wire order处理，engine B按每字段/section增量并在 sort tie时按 command ID处理，都能满足逐项措辞，却产生不同 sidecar revisions或 whole-file bytes。应固定 command-level revision transition、same-section multiplicity和 meeting key uniqueness/collision truth table，并增加 mixed-patch vectors。

- Protocol 声称 prepared journal manifest 固定 staged bytes、before images、receipt target和排序（`WDR-AND-TRANSACTION-PROTOCOL.md:54-58`），但 `transactionJournalManifestV1` 的每个 target只有 root/path/before hash/after hash，没有 before-image path、after-image path/blob ID、target role、apply order或 adapter operation（`panel-sync-contracts.schema.json:633-642,720-735`）。recovery implementation A可以约定 `targets/<index>.before`，B可以约定 content-token目录；各自能恢复自己写的 journal，却无法恢复另一个实现留下的 prepared journal，直接违反 shared wire truth 的目的。manifest 必须携带或由 registry算法唯一推导全部 image/blob location、role、operation和 total order，且跨实现互相 prepare/recover。

- Recovery 要求无 commit marker且 before/after混合时“全部恢复 before”，而 `mutationTarget.before_sha256` 允许 `null`，这包括首次创建 WDR、sidecar、receipt和generation file；adapter primitives却只有 `durable_create` 与 `durable_replace`，没有 durable remove/restore-absence（protocol `:56-58`; schema `:633-642`）。POSIX recovery A可能 unlink+directory fsync，Windows recovery B可能 rename到 tombstone后删除；crash point和残留语义完全未固定，且现有 fault matrix只证明 fixture声称 first-create使用 create，不测试 create后rollback。应定义 `durable_remove`/tombstone contract、Windows/POSIX guarantees和 every-flush fault vectors。

- Journal 对 receipt 的 wire model互相矛盾：AD-10说 manifest覆盖全部 targets含 receipt，schema却把 `receipt_target_path` 放在 `targets` 之外且不要求该 path在 targets中，fault matrix又把 receipt当第三个 target。coordinator A可把 receipt同时列入 targets，B可把它视为隐式额外 target；两者产生不同 all-after判定。repair更不明确：一次 repair既需要 fact mutation receipt来证明 fact-generation transaction，又需要 `repairRunReceiptV1`，而 manifest只有单数 `receipt_target_path`，protocol未说明哪个是 commit target、另一个何时原子发布。应给 target明确 role并允许/要求确切 receipt集合，固定 fact/repair/panel每种 journal的 target cardinality和自引用排除规则。

- Writer capability registry 没有由 AD matrix唯一推导 initial document/capability IDs 的算法，也没有 capability lifecycle、rotation、revision/CAS或bootstrap receipt。schema只要求至少五条、允许重复 producer/capability，并让 `capability_id` 是任意 sha256-shaped string（`panel-sync-contracts.schema.json:94-117`）。bootstrap A可使用随机 secret hash，bootstrap B可使用 capability document content hash；两者授予相同 operation/field matrix但生成互不兼容的 issuer commands，prepared recovery也无法“重新取得同一 capability”。应固定 capability principal model、ID derivation、exact one-per-producer/uniqueness constraints、rotation/revocation和 transaction schema；如果 capability是secret bearer，不能把其可重放值直接放入 command。

- Legacy ingress仍不是 closed transform。meeting source grammar明确允许 `started_at` 带 `+08:00`（pinned `sync-plan-schema.md:12`），v2 evidence只接受秒精度 `Z`（schema `:42-45,119-128`），protocol说优先使用 started_at却没固定 UTC normalization/error。legacy command-ID input又包含“target id”，但 create在 status-sync分配前没有 action ID；adapter A可以用 meeting item ID，B可以用 workstream/未来 action ID。protocol还用 v1名 `text`/`due`描述 presence copy，而 v2字段是 `action`/`due_trigger`，只有两个局部 vectors覆盖部分 rename。应发布机器可验证的 v1 schemas和完整 mapping table，包括 offset/fraction normalization、create target identity、all aliases、missing/null/empty semantics与 golden command ID。

- Repair batching仍不能唯一构造。Protocol `:62` 写 findings按 `(workflow,workstream_id,operation,finding_id)` 排序并称“同一 tuple形成一个 batch”；由于 `finding_id` 唯一，字面实现 A会每 finding一批，而按 AD-7意图忽略最后一维的实现 B会按前三维聚合。`auditFindingRepairV2.findings` 本身又没有 workflow/operation字段，且 schema不要求 finding IDs全局唯一、不要求 finding `repair_batch_id` 等于引用它的 batch、不禁止同一 finding出现在多个 batches（schema `:506-545`）。两种 batcher可产生不同 action unions/tokens且都通过现有 cross-field vectors。应明确 sort key与group key分离，把 workflow/workstream/operation变为 typed finding字段，并加入全局 uniqueness、one-batch membership和双向 `repair_batch_id` invariants。

- Per-batch token状态机没有注册 durable nonce record schema、nonce index key、token hash/MAC binding representation、reserve CAS或recovery transaction关系；Protocol只列目录 `repair/nonces/` 和状态转移（`:54,64-65`），Registry contracts中没有 nonce state。status-sync instance A可按 token hash建文件并在 prepare时写 reserved，instance B可按 batch ID覆写一条记录；并发 dry-run/apply或 crash recovery会得到不同 replay结果。应注册 nonce-state contract和exact path tokenization，把 reserve/consume/invalidate纳入具名 journal targets，并用两个进程与 crash/retry vectors验证 single-use。

- Protocol要求 ledger、WDR、daily、meeting archive/cursor/receipt、decision、checkpoint/readiness和 action/risk indexes的每次 commit都进入 shared fact transaction（`:38-42`），但 `fact-mutation-receipt` 的 registry writers只有 `adp-wdr-mutation` 与 `adp-status-sync`（`CONTRACT-REGISTRY.json:225-231`），没有 fact coordinator、meeting-sync、checkpoint-sync、risk review或workstream register。meeting-only archive transaction实现 A可让 meeting-sync直接写 receipt（违反 registry writer list），实现 B可借用 WDR engine identity（错误归因但满足 writer list）。应把 receipt writer统一为 fact transaction coordinator并在 receipt中记录 authorized initiating producer/capability，或为每类 writer注册明确 delegation contract。

## Exit Conditions

- 为 canonical projection/Panel payload、ledger state、nonce state、capability state和完整 journal image layout补齐 registry-pinned schemas及 identity/ordering算法。
- 固定 action create ID、WDR create renderer、mixed-patch revision、legacy transform和 repair grouping/linkage的 byte/ID-exact semantics。
- 用真实 schema/semantic validators、production adapters和 fault-injected POSIX/Windows filesystem harness重建 conformance suite；fixture substitution 后必须 schema-valid。
- 要求实现 A prepare、实现 B recover/consume（以及反向）通过；仅两份同构 runner自报 32/32 不得满足 release gate。
