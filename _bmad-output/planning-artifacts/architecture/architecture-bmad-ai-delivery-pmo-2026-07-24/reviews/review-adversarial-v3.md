# Architecture Spine 对抗性增量复审 v3

审查对象：最新 `ARCHITECTURE-SPINE.md` 及其三份 normative companions：

- `contracts/CONTRACT-REGISTRY.json`
- `contracts/panel-sync-contracts.schema.json`
- `contracts/WDR-AND-TRANSACTION-PROTOCOL.md`

审查范围仅限 v2 剩余 High 的闭合情况，以及三份新增规范自身引入的 High。

## 结论

**FAIL**。

本轮已经实质关闭 v2 中的大部分纯语义歧义：pinned raw hashes 均与实际文件匹配；JCS、版本协商、WDR collection/marker renderer、root/symlink、immutable refresh inputs、publish fence、repair nonce 与 batch 顺序都获得了明确规则。但 schema、registry 和 protocol 之间仍有数个不可同时满足或无法通过 wire contract 表达的冲突，且 transaction recovery 仍有事实已提交但 generation 未推进的崩溃窗口。它们会导致合法输入无法投影、依赖漏验、stale Panel 发布或 stale repair 被执行，不能降为文档完善项。

## 验证结果

- 三份 pinned artifact 的实际 SHA-256 分别为 `c2108e35...a7b4`、`8a874a4...e29`、`ff754d59...51dc`，与 spine 和 registry 完全一致。
- registry 与 schema 均可由标准 JSON parser 解析。
- 本轮未运行 Draft 2020-12 metaschema validator；当前环境没有 `jsonschema` 或 Ajv。以下发现均来自跨文件可直接证明的约束冲突，不依赖 metaschema validator。

## v2 High 闭合状态

| v2 High | 状态 | 证据 |
| --- | --- | --- |
| Normative schema、hash、negotiation、JCS | 部分闭合 | artifact/hash/协商已固定；内部 contract completeness 仍有冲突 |
| WDR replace/serialization、全文件 CAS、marker renderer | 部分闭合 | collection 与 marker 主路径已唯一化；target/renderability/meeting append 仍不完整 |
| Manifest/root/dependency completeness | 部分闭合 | root 与 immutable blob 已闭合；registry role 与 schema role 冲突，derivation/affects 仍非机器契约 |
| Refresh generation、ABA、final publish fence | 部分闭合 | happy path 已闭合；producer receipt 与 crash recovery/panel pointer 持久化仍有空窗 |
| Repair token、authorization、batch transaction | 部分闭合 | nonce 与授权规则已明确；revision shape、wire envelopes 与 rollback token 状态仍冲突 |

## Remaining High Findings

- `CONTRACT-REGISTRY.json:91-126` 的 `required_roles` 使用 `fact:action-ledger`、`config:selection-policy` 等细粒度值，而 `panel-sync-contracts.schema.json:162-174` 的 `dependencySource.role` 只允许 `fact|config|audit|evidence`。一个 manifest 无法逐值表达 registry 要求的 role，orchestrator 只能自行发明“前缀映射”或全部拒绝。应让 schema 接受 registry 的 exact role IDs，或把 role 拆成有独立 schema 的 `category` 与 `source_kind`，并在 registry 中使用相同 shape。

- `CONTRACT-REGISTRY.json:91-126` 的 dependency derivation 是自然语言字符串，`panel-sync-contracts.schema.json:173` 的 `affects` 只要求任意 `/` 开头字符串；三份 artifact 中没有 machine-readable DAG、selection-policy schema 或 Panel binding map。两个 orchestrator 可对“all receipts selected”或“bound by panel catalog”得到不同 leaf set和阻断范围，却都声称遵循 profile。应把 enumerator、direct upstream kinds、selection keys 和 allowed affects pointers 固化为结构化 registry 数据，并为 panel catalog/selection policy pin schema/hash。

- `panel-sync-contracts.schema.json:108-133` 只要求 `workstream_id` 为非空字符串，未限制为 normalized physical workstream ID，也未禁止 `program`、`..`、slash 或 control characters；WDR protocol 只约束 dependency path，不约束 command target 到 physical path 的映射。一个 writer可拒绝 `../x`，另一个可拼接后写出 workstream root。应为 workstream ID 发布统一 definition，并让 shared engine在解析路径前按该 definition fail closed。

- `panel-sync-contracts.schema.json:61-75` 允许 create 缺少 `due_trigger`，且 `owner/action/due_trigger` 接受空值、换行和 NUL；但 `actionProjectionRecord` 在 `:209-221` 强制 `due_trigger`，WDR protocol `:16,22-24` 又拒绝这些不可渲染值。于是一个 schema-valid action 可以先成功写入 ledger，随后永久无法生成合法 sidecar/marker。应让 action command 的可投影字段复用 renderer-compatible string definition，create 明确要求 due/trigger 或规定唯一 canonical placeholder，并在事实 commit 前验证 projection preconditions。

- `panel-sync-contracts.schema.json:128-130` 定义了 `meeting_history_append`，但 WDR protocol 除 file-generation CAS 外没有规定 append 的目标 section grammar、entry renderer、canonical ordering、重复 command idempotency或多行/空字符处理。两个 shared engine 可在同一 file lock 下安全地写出不同 physical bytes。应为 meeting append 增加独立 typed record和 canonical renderer，并定义 section absence/duplicate、重放和与同批 current-field patch 的顺序。

- WDR protocol `:36` 要求 producer receipt 精确列出 consumed blob IDs 与 staged predecessor IDs，但 registry 没有 producer-receipt contract，`refreshReceiptStatusV1` 在 schema `:303-319` 只有 upstream-shaped `nodes`，不能表达 leaf blob handles、input profile identity 或 exact consumed set。由于 AD-11 又禁止未注册 wire shape，orchestrator无法以 contract-valid receipt执行该检查。应增加 generation envelope 与 producer receipt schema/registry entry，并绑定 leaf handles、staged predecessors、profile、selection/registry hash和 node output ID。

- WDR protocol `:41-43` 在全部 target hash 命中后先持久化 `committed` marker，再递增 `fact_generation` 并写 receipt；但 recovery 只定义“无 marker”以及“有 marker且 target mismatch”，没有定义有 marker、targets正确但 generation/receipt 尚未持久化的崩溃窗口。reader可能把已变事实与旧 generation 一起暴露，绕过 refresh fence。应把 expected before/after fact generation纳入 journal，并要求 recovery 对 committed+targets-match 幂等补齐 generation与唯一 receipt后才允许读取。

- WDR protocol `:37` 声明 current pointer 替换与 `panel_generation` 增量为原子 CAS，却没有定义其 durable representation、journal、before/after hash或 crash recovery；现有 refresh receipt schema也不记录 published pointer ID/after generation。崩溃可落在 pointer 已替换但 generation未增，或 generation已增但 pointer仍旧的状态。应为 Panel publication提供与第 6 节等价的单事务 protocol和 receipt schema，而不是只定义并发语义。

- `ARCHITECTURE-SPINE.md:113` 与 WDR protocol `:49` 要求 token 绑定全部 source/action/WDR/file/fact revisions，但 `repairBatch.revisions` 在 schema `:261-269` 只有一个未定义语义的 `ledger` scalar、一个 WDR revision、file/fact generation，没有 per-action revision map或 source fingerprints/revisions。多个 action 的 batch 无法证明每个 exact action仍是 dry-run看到的版本。应把 revisions改为按 action ID排序的 revision records，并绑定所有实际 repair read-set 的 source keys/fingerprints。

- `auditFindingRepairV2` 在 schema `:274-300` 把 `repair` 放宽为任意 object/null，`repairBatch.operation` 在 `:257` 也是任意 string，batch没有 typed mutation payload。两个 status-sync reader可对同一 schema-valid repair支持不同 operation或从 finding自然语言反推 update，重新引入原问题。应为每个 operation使用 discriminator + closed schema，并把 status-sync可直接 dry-run的完整 typed command纳入 batch digest。

- WDR protocol `:47-50` 要求 dry-run/apply token、partial-success receipt和 retry cursor，但 registry/schema没有 dry-run request/result、apply request、nonce binding record或 repair-run receipt contract。跨进程 reader无法协商 exact wire shape，审计也无法验证 applied/failed batch与 token消费。应为这些 envelope增加 registry entries，且 receipt必须携带 applied batch IDs、failed batch、retry cursor、nonce status和 journal/transaction identity。

- WDR protocol `:50` 同时规定 rollback 把 nonce恢复为 `unused`，又规定 token不得在 rollback 后重复使用。实现 A 会允许同 token在 expiry 前重试，实现 B 会使其失效，两者分别遵守其中一句。应选择唯一状态机：若 rollback 后必须重新 dry-run，则转为 `invalidated`；若允许同 payload重试，则删除“rollback 后不得重复使用”并规定 reservation attempt counter。

- `ARCHITECTURE-SPINE.md:137` 要求任何 contract 修改通过 golden/conformance fixtures，但本 architecture package 中没有任何 fixture或conformance artifact，也没有 registry entry pinning fixture hashes。上述 role、action→renderer、journal crash和token rollback冲突因此无法被共同测试捕获。应至少提供跨实现 golden vectors与故障注入矩阵，并由 registry固定 suite版本/hash，作为 contract negotiation的发布门。

## 关闭判据

- Registry role/profile 与 dependency manifest schema 使用同一可机器比较的数据模型；selection 和 affects derivation不依赖自然语言解释。
- 所有 schema-valid action/WDR command都能得到唯一 canonical fact/sidecar bytes，且 target path不能由自由字符串逃逸或命中 virtual scope。
- Generation/producer/panel publication receipts均有 pinned wire schema；fact commit、generation和current pointer在所有 crash point可唯一恢复。
- Repair batch完整绑定 per-entity read-set，operation是 closed typed command，nonce rollback只有一个合法后继状态。
- 两个独立实现通过同一 pinned conformance suite后，对依赖枚举、WDR bytes、refresh generation、repair token和crash recovery得到完全相同结果。
