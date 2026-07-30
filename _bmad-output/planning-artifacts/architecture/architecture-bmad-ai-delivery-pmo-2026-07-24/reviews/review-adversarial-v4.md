# Architecture Spine 对抗性增量复审 v4

审查对象：落盘前重新读取的当前 `ARCHITECTURE-SPINE.md`，以及以下固定快照：

- `contracts/CONTRACT-REGISTRY.json`
- `contracts/panel-sync-contracts.schema.json`
- `contracts/WDR-AND-TRANSACTION-PROTOCOL.md`
- `contracts/fixtures/CONFORMANCE-VECTORS.json`

审查范围仅限 v3 High：role/profile/DAG/binding 机器契约、target/renderability/meeting renderer、producer/fact/panel receipts 与 crash 窗口、repair read set/wire/token 状态、实际 conformance vectors。

## Snapshot Verification

四项实际 raw-byte SHA-256 与指定评审快照精确一致：

| Artifact | Verified SHA-256 |
| --- | --- |
| Registry | `84700ee0e9cb93155e53a8ba51ec21e959e0d4ab1fb5c770d6ef96baddbee5ba` |
| Schema | `2c358067ec23a535de59e6d663cc55e653747eee999a899ce46bd5508fdc990e` |
| Protocol | `db31b7374a1fbd7500130090ad946fdc7978e73b2d885fb70e924447a6bcc86b` |
| Conformance vectors | `79c3b1b251e916cc0277e7609f915444fbae9dd5dfe743b74d6027f5fecb9dda` |

Registry 中所有 `schema_pointer` 均能解析到 schema bundle 的现有 `$defs`。四份 JSON artifact 可由标准 JSON parser 读取。

## Verdict

**FAIL**。

v4 已关闭 v3 中最表层的缺失项：category/source_kind shape 对齐、结构化 profile/DAG/binding、workstream/renderer-compatible strings、meeting record、generation/producer/fact/panel receipts、完整 repair read set、terminal invalidated token，以及 pinned vectors 都已经出现。但这些新增 contract 之间仍存在无法同时满足的拓扑、transaction 和 token 语义；现有 vectors 也只覆盖算法片段，无法阻止两个实现分别通过后在关键 wire/crash 路径分叉。

## v3 High Closure

| v3 High | 状态 | 本轮结论 |
| --- | --- | --- |
| role/profile/DAG/binding | 部分闭合 | role shape 已统一；DAG 有无 profile 节点、self-leaf 和 meeting binding collision |
| target/renderability/meeting renderer | 部分闭合 | target/string schema 已补；writer authority、field mapping 和 whole-file meeting placement仍不唯一 |
| producer/fact/panel receipts与crash | 部分闭合 | receipt schema与journal target已补；runtime state/journal/rollback receipt仍不能跨实现恢复 |
| repair read set/wire/token | 部分闭合 | typed command/read set/token terminal状态已补；multi-batch token和跨字段等式仍冲突 |
| conformance vectors | 部分闭合 | fixture 已存在并被 pin；覆盖不足且没有两套实现通过的可验证结果 |

## Remaining High Findings

- `CONTRACT-REGISTRY.json:214-230,321-335` 把 `action-flow` 和 `risk-flow` 定义为 same-generation DAG nodes/direct upstreams，但 `projection_input_profiles` 中没有这两个 projection 的 profile，registry contracts 中也没有其 generation producer contract。orchestrator 既不能按 profile枚举它们的 leaves，也不能生成同 generation producer receipt；复用旧文件又违反 same-generation rule。应为两者增加完整 profile/producer mapping，或明确降级为 leaf source并从 DAG/direct upstream中移除。

- `CONTRACT-REGISTRY.json:268-287` 的 state-audit profile 以 `views/**/*.json` 枚举 `existing-view`。这会把本轮写入的 `views/lineage/**.dependency.json`、canonical projection JSON和 `views/management-panel/refresh-status.json` 纳入起始 leaf set，形成 state-audit 对自身及其 downstream 输出的隐式反向依赖。final live-leaf compare会因 refresh自身写入而失败，或实现者被迫私自排除文件。应列出 exact可读 legacy views并显式排除 lineage、current generation outputs和mutable refresh status。

- `CONTRACT-REGISTRY.json:232-238,357-370` 把所有 meeting-pack 实例的整份 source `/` 绑定到同一个 `/data/meetings`，同时 profile要求 `one-per-meeting-kind`；binding没有 key selector、merge mode或stable ordering。两个实现可分别覆盖最后一个 pack、生成数组或按 kind 建对象。`panelBindingCatalogV1` 也允许该歧义，protocol提到的 `panel-bindings-v1` 又不在 registry enumerator inventory中。应为多实例 binding固定 `key_from`/merge semantics，并让 catalog与 registry exact map可机器等值验证。

- `wdr-command/1.0.0` 没有携带 authenticated writer/workflow identity。Registry `:47-52` 只说明哪些组件可以写整个 contract，schema `wdrPatchV1` 允许同一 envelope同时包含 current fields、meeting history和owned sections；protocol `:20-21` 却要求 engine执行逐 workflow owner/mode matrix。命令经 intake/queue 持久化后，engine无法仅凭 payload证明它来自 meeting-sync、status-sync还是risk review，错误 producer可提交越权 field且仍通过 schema。应将 issuer identity与授权 scope纳入签名/receipt-bound envelope，并由 engine按 registry matrix验证。

- WDR protocol `:16-22` 没有给 schema property到physical label/section的完整映射。`wdrPatchV1` 包含 `status` 和 `phase`，但 pinned protocol只声明所有受管 label都位于 `## Project Status`；现有 WDR语义通常把 Current ADP Status/Current BMM Phase放在 Identity。meeting blocks虽有单块 renderer，却没有规定在整份 WDR canonical section order中的插入位置。不同 engine会产生不同 whole-file bytes。应固定每个 set key的exact heading/label以及meeting block region boundaries。

- Protocol `:22` 要求 meeting block有“一个尾随空行”，但 conformance vector `meeting-history-render` 的 `expected_block` 仅以单个 `\n` 结束，没有空白行所需的第二个 LF。follow-protocol实现输出 `\n\n`，follow-vector实现输出 `\n`，二者不能同时通过。应统一 normative bytes，并增加连续两个 meeting block及与相邻 canonical section合并的 whole-file vector。

- Protocol `:46-48,59` 固定了 `fact-generation.json`、`panel-state.json`、`current-pointer.json`、root registry和journal目录，却没有为这些 durable state文件或 prepared/target manifest提供 pinned schema。receipt只描述 before/after generation或pointer ID，不能让升级后的另一实现解析并恢复前一实现留下的 journal。应把 runtime state、journal manifest、marker和recovery receipt全部纳入 registry wire contracts；否则 crash-consistent语义只在单实现内成立。

- Fact receipt作为 transaction target的语义仍不闭合。Protocol `:46` 要求完整 fact receipt bytes在 replace前确定并进入 target manifest，schema `factMutationReceiptV1` 又含 `targets`；如果该列表包含 receipt自身，就需要在 receipt bytes内预先写入自身 after hash，形成不可计算的自引用；如果排除自身，`targets` 就不是完整 transaction target set。无 marker rollback时 protocol还要求写 `rolled_back` receipt，但该 bytes不是原 prepared after-image。应区分 `business_targets`与journal-owned receipt target，并为 rollback receipt规定独立、同样 journaled的恢复 transaction。

- `repairDryRunRequestV1` 在 schema `:622-635` 允许一次请求携带多个 batches，但 `repairDryRunResultV1` 只返回一个 token/binding digest，`repairApplyRequestV1` 又一次只应用一个 batch。Protocol `:52-55` 按每 batch nonce与transaction消费：第一个 batch commit后token已 consumed，后续 batch无法使用；若token绑定整个列表，单 batch apply又不匹配 binding。应把 dry-run限制为 `maxItems:1`，或返回按 batch ID索引的独立 token/results。

- Repair contract没有固定必要的跨字段等式：finding的 `repair`、batch `command`、batch `action_ids`、command `action_ids`和read-set `action_revisions`可分别引用不同 action；`repairReadSet.ledger_revision` 也没有对应 pinned action-ledger global revision state。不同 consumer可信任不同字段并修改不同目标。应规定并测试这些集合/command完全一致，或删除重复字段；同时为ledger global revision提供durable schema，若只依赖fingerprint则移除未定义 revision。

- `CONFORMANCE-VECTORS.json` 的 WDR patch和dependency inputs只是局部算法片段，并非带 contract/schema/hash/evidence/root/blob/affects的完整 schema-valid envelopes；suite也没有任何 expected policy/catalog/generation/manifest/producer receipt/repair batch JCS identity。两个 adapter可以完全跳过wire negotiation、cross-field invariants和receipt identity仍通过现有vectors。应增加完整positive/negative wire fixtures及expected JCS hashes。

- Journal fault matrix只有六个抽象 `before|after|neither`组合，没有把 fact generation、receipt、pointer、panel state作为具名targets，也没有覆盖每次file flush、directory flush、replace、marker写入和recovery receipt的fault point。它无法发现receipt自引用、rollback receipt或pointer/generation不同步。应按protocol每个durability边界生成fault-injection matrix，并验证最终bytes与receipt，而不仅是抽象状态标签。

- Suite声明 `minimum_independent_implementations: 2`，但当前package没有implementation IDs、runner、result artifacts、tool/version、platform矩阵或对suite hash的签名/receipt。AD-11要求“两套independent adapters通过”尚无可验证证据；声明数字不等于通过。应pin runner contract与至少两份独立result receipts，逐vector记录pass/fail和artifact hashes。

## Pass Conditions

- DAG中的每个 node都有唯一 profile/producer路径，任何 leaf enumerator不包含本轮自身/downstream/runtime输出。
- 多实例 meeting bindings、WDR writer authority、field-to-label和meeting whole-file placement都有唯一机器结果。
- Runtime state与journal可由另一实现解析恢复，receipt不自引用，rollback receipt也走明确transaction。
- Repair每个batch拥有独立token，所有重复selector/read-set字段由contract强制相等。
- 两个独立实现运行完整schema-valid vectors及全durability fault matrix，并产出被registry pin住的结果receipt。
