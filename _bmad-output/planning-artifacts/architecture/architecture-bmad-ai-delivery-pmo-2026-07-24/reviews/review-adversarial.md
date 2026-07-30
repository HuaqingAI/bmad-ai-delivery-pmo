# Architecture Spine 对抗性审查

审查对象：`ARCHITECTURE-SPINE.md`

审查方法：把该文档当成下游团队唯一共同的 build substrate，分别实现两套端到端状态同步单元。两套实现均逐字满足 AD-1 至 AD-9，再检查它们能否交换 command、读取同一 durable state、得到同一 freshness 结论、执行同一 invalidation DAG，并生成可互换的 repair batch。

## 结论

当前 spine 能表达正确方向，但还不能约束出唯一、可互操作的实现。它是一组架构原则，不是完整协议。两个团队可以在不违反任何 AD 的情况下，对 action revision 的存放位置、WDR collection 的代数、marker 解析、semantic fingerprint、root 解析、required gate、freshness 状态、finding shape 和 repair 原子性作出不同决定，最终对同一事实给出不同 Panel、不同 drift finding 和不同修复结果。

因此该 spine 暂不能作为独立下游单元的实现边界直接冻结。需要补齐规范 schema、状态机、规范化算法、依赖注册表、快照发布协议和 conformance fixtures；否则“单 writer”和“live fingerprint”只把漂移从自由文本层搬到了 contract interpretation 层。

## 对抗性输入

两套实现接收同一个逻辑更新：

```json
{
  "schema_version": "2.0",
  "command_id": "CMD-20260724-001",
  "operation": "patch",
  "action_id": "ACT-20260720-003",
  "expected_revision": 7,
  "set": {
    "owner": " FDE-B ",
    "status": "blocked"
  },
  "evidence": {
    "source": "meetings/2026-07-24-sync.md#M-004",
    "observed_at": "2026-07-24T10:20:00+08:00"
  }
}
```

同批次还包含一个 WDR patch：

```json
{
  "target_id": "l1-checkout",
  "set": {
    "progress": {"replace": "联调完成，等待支付回归证据"}
  },
  "collections": {
    "blockers": {"mode": "add", "items": ["支付沙箱权限未开通"]},
    "risks": {"mode": "remove", "items": ["回归窗口压缩"]}
  },
  "refresh_actions": true
}
```

环境中同时存在 `project root/workstreams/l1-checkout/wdr.md` 和 `memory root/workstreams/l1-checkout/wdr.md`。会议归档在 refresh 期间又向 WDR evidence/history 区块追加一段文字，但 `Project Status` current fields 没有变化。`Next actions` 中另有一个包含两个合法 marker 的损坏 entry。

## 两套独立实现

### 实现 A：结构化状态优先

- `adp-status-sync` 是唯一事实 writer；meeting-sync 只生成 intent。
- action revision 作为 action ledger 的显式列保存，patch 只在该行 revision 等于 `expected_revision` 时提交，成功后加一。
- owner 在写入前 trim 并解析为组织目录中的 canonical handle，因此上例写入 `FDE-B`。
- WDR collection 被解释为按规范化文本去重的集合；`add` 是幂等 union，`remove` 删除全部语义相同项。
- managed action marker 必须是 entry 末尾唯一、大小写敏感的 `[action_id:ACT-*]`。含两个 marker 的 entry 被判 malformed；该 entry 不被投影器认领，但审计仍会从 active ledger 集合发现对应 action 缺失。
- semantic fingerprint 只覆盖 producer 声明消费的 current-field AST，排除 evidence/history 和 Markdown 排版；路径无 root 标记时优先 project root。
- invalidation DAG 来自 orchestrator 的中央 registry；`required gates` 只包含当前 Panel selection 实际绑定的节点。
- `business_freshness` 是 `{state, checked_at, reasons}` 对象；`source_as_of` 是逐 source 映射。
- finding reference 使用 `{"entity_type":"action","id":"ACT-*"}`；每个 workflow/workstream 生成一个原子 repair batch，成员按 entity ID 排序。
- legacy create 的 action ID 从 command/evidence fingerprint 派生，重放得到同一个 ID。

### 实现 B：Markdown 和 producer manifest 优先

- `adp-status-sync` 同样是唯一事实 writer；meeting-sync 只生成 intent。
- action revision 保存在 durable JSON sidecar 中，并通过 status-sync 的 transaction journal 与 Markdown ledger 一起提交；成功 patch 同样加一。
- owner 被视为用户提供的有意义字符串，保留两侧空格以避免 writer 擅自改写 evidence，因此上例写入 ` FDE-B `。
- WDR collection 被解释为有序多重列表；`add` 追加一项，`remove` 只删除第一个逐字相等项，重复项合法。
- marker 可出现在 entry 任意位置；同一 entry 有多个 marker 时最后一个获胜。审计对其选择的 managed entry 和全部 active ledger action 做比较。
- semantic fingerprint 覆盖完整规范化 Markdown AST，包括 evidence/history，但忽略纯排版空白；路径无 root 标记时优先 memory root。
- invalidation DAG 由各 producer manifest 合并；`required gates` 包含 registry 中所有 canonical projection，即使当前 Panel selection 没有展示它。
- `business_freshness` 是 `fresh|stale|not-verifiable|migration-required` 字符串；`source_as_of` 是本轮 inspect 的单一时间戳。
- finding reference 使用 `{"kind":"action","key":"ACT-*"}`；同一 workflow/workstream 可按 operation 分成多个 batch，成员按 source path/line 排序并逐 batch 提交。
- legacy create 使用当日 ledger 的下一个 `ACT-YYYYMMDD-NNN`，command receipt 保证同一实例重放 no-op。

## 逐 AD 字面合规证明

| AD | 实现 A | 实现 B | 仍可漂移的原因 |
| --- | --- | --- | --- |
| AD-1 | 只有 status-sync 修改 ledger/WDR | 只有 status-sync 修改 ledger/WDR | “一个 workflow”没有规定一个 executor、锁域或 durable representation |
| AD-2 | 显式 create/patch、exact ID、revision、非空 set、evidence，成功 revision +1 | 同样满足，revision 位于 sidecar | command/schema 类型、revision 存放和规范化未定义 |
| AD-3 | scalar replace，collection 为集合代数，Next actions 仅 refresh_actions | scalar replace，collection 为有序多重列表，Next actions 仅 refresh_actions | add/remove 的 identity、重复、顺序和 remove 基数未定义 |
| AD-4 | projection 带版本和 semantic source fingerprints，inspect 重算 | 同样满足 | semantic canonicalization、依赖闭包、root 选择和 profile version 未定义 |
| AD-5 | 严格 marker grammar，损坏 entry 阻断并报告 active action 缺失 | 宽松 grammar，最后 marker 获胜并继续比较 | entry 边界、marker 数量、大小写、escaping、duplicate policy 未定义 |
| AD-6 | 中央 registry 计算 DAG，只 gate 当前 selection | producer manifest 合并 DAG，gate 全部 canonical projection | DAG 权威源、required gate、selection 和 impact policy 未定义 |
| AD-7 | typed refs 为 entity_type/id，单原子 batch | typed refs 为 kind/key，按 operation 分批 | typed 只描述意图，没有规范 shape、排序、batch identity 和失败语义 |
| AD-8 | integrity/freshness 分开，freshness 为对象，as-of 为 map | integrity/freshness 分开，freshness 为枚举，as-of 为 scalar | 输出字段名称存在，但类型、状态格、时间含义和聚合规则未定义 |
| AD-9 | legacy 仅 create，缺 fingerprint 则 migration-required | 同样满足 | legacy ID 分配、dedupe/idempotency scope 和 migration receipt 未定义 |

## 可观察的分歧

| 观察点 | 实现 A | 实现 B |
| --- | --- | --- |
| action owner | `FDE-B` | ` FDE-B ` |
| action revision durable shape | ledger row 的 `Action Revision` | JSON sidecar 的 revision map |
| blocker 重放两次 | 仍一项 | 出现两项 |
| risk remove | 删除全部规范化匹配 | 仅删第一个逐字匹配 |
| 双 marker entry | malformed，并使相关 active action 报 missing | 归属最后一个 action ID |
| history-only append | current-field fingerprint 不变 | full-AST fingerprint mismatch |
| 同名相对路径 | 读取 project root | 读取 memory root |
| 未展示 meeting-pack stale | 可 degraded 后发布当前 selection | required gate 失败，阻断发布 |
| freshness JSON | 对象 | 字符串 |
| repair reference | `entity_type/id` | `kind/key` |
| repair 批次 | 每 workstream 一个事务 | 同 workstream 多 operation 批次 |
| legacy action ID | 内容派生 ID | 当日顺序 ID |

以上差异不是实现 bug；它们都是 spine 留给实现者的合法选择。两套系统单独运行时都可以通过各自测试，但 producer A 的 projection/finding/receipt 不能被 consumer B 稳定消费，反之亦然。

## Findings

- `ARCHITECTURE-SPINE.md:72-82` 只列 command 必需概念，没有发布规范 JSON Schema。`schema_version` 的类型和协商方式、`evidence` 的 cardinality、`set` 的允许字段、字段类型、null/empty/omitted 行为、unknown-field policy 都可由 producer 自行解释。应把 action-command、wdr-patch 和 mutation-receipt schema 作为 spine 的 normative artifacts，固定 `$id`、版本兼容规则和拒绝策略。

- `ARCHITECTURE-SPINE.md:132-139` 以 “existing ADP schemas” 代替精确依赖，但未绑定 action-ledger/WDR schema 的路径、版本或 content hash。下游可引用不同时间点的“现有 schema”，尤其 Action Revision 目前不在列出的 durable field contract 中。应在 spine 中固定 schema registry、contract version 和迁移前后最低支持版本。

- `ARCHITECTURE-SPINE.md:66-70` 的 single writer 是逻辑所有权，不是并发控制。两个 status-sync 进程仍可同时通过 revision 检查并覆盖 ledger/WDR。应规定锁/CAS 域、锁顺序、transaction journal、crash recovery 和重复 executor 的 fence token；否则一个 writer workflow 仍然会 lost update。

- `ARCHITECTURE-SPINE.md:72-76` 没有规定 revision 的 aggregate、初值、持久化位置、整数范围和 exact increment 规则，也没有规定 create 的 revision 与 ID 冲突行为。revision 放 ledger row、sidecar 或 receipt chain 都字面合规，却不能互换。应固定 per-action revision 的 canonical field、legacy 初始化、`after_revision = before_revision + 1`、overflow/invalid 值和 ledger rewrite 的原子边界。

- `ARCHITECTURE-SPINE.md:78-82` 给 action patch 加了 optimistic concurrency，却没有给 WDR patch 等价 precondition。两个会议可从同一 WDR 状态生成 `replace/add/remove`，后执行者会覆盖前者而不触发 stale revision。应增加 `expected_wdr_revision` 或 `expected_current_fields_fingerprint`，明确 patch 的 read-set/write-set 和 conflict response。

- `ARCHITECTURE-SPINE.md:78-82` 没有定义 collection algebra。集合还是列表、顺序是否有语义、如何计算 item identity、是否去重、`remove` 删除一个还是全部、找不到 item 是 no-op 还是 blocked 均未规定。应对每个 WDR collection 固定 canonical item schema、normalization、ordering、dedupe key 和 add/remove 真值表。

- `ARCHITECTURE-SPINE.md:78-82,124-125` 没有逐字段 empty/null contract。原则只说 omitted 与 empty 不同，但实现者仍不知道 `progress:""` 是清空、非法还是 replace 为真正空值，`blockers:[]` 在 replace/add/remove 下各意味着什么。应发布字段矩阵并在 schema 中用明确类型和 `minItems`/nullable 约束表达。

- `ARCHITECTURE-SPINE.md:35,70,82,129` 没有定义同一 status-sync batch 内 ledger、WDR current fields、`refresh_actions` 与 mutation receipt 的一致性边界。事实 mutation 和 projection receipt 分开并不能回答中途崩溃后哪一半可重放。应规定 staged transaction/saga 状态机、commit point、补偿策略、receipt 的 before/after fingerprint，以及 retry 如何识别已提交的子操作。

- `ARCHITECTURE-SPINE.md:90-94` 只有 marker 示例，没有 grammar。entry 如何分隔、marker 是否必须唯一/末尾/大小写敏感、ID 是否 trim、如何 escaping、多个 marker 或重复 ID 如何处理，都影响 ownership。应发布 grammar 和 canonical renderer，并规定 malformed、duplicate-within-entry、duplicate-across-entry 的统一 finding 与阻断行为。

- `ARCHITECTURE-SPINE.md:90-94` 使用 “active” 和 “terminal” 却不在 spine 内绑定状态枚举与状态机。外部 action-ledger 文档虽有当前枚举，但 spine 未固定其版本，也没有声明未知/未来状态的 fail-closed 行为。应绑定 canonical status enum、allowed transition matrix、active/terminal partition 和 unknown-status policy。

- `ARCHITECTURE-SPINE.md:84-88,127` 没有定义 semantic fingerprint 的规范化算法。解析后的 current fields、完整 Markdown AST、换行规范化后的 bytes、是否包含 evidence/generated_at、list 顺序、Unicode NFC/NFKC 都可产生不同但自洽的 SHA-256。应强制 `fingerprint_profile`，按 source kind 规定 parser/version、included fields、canonical serialization 和 golden vectors。

- `ARCHITECTURE-SPINE.md:84-88` 没有规定 `source_fingerprints` 是 direct dependency 还是 transitive closure，也未规定同一 source 多次引用、projection-to-projection lineage、删除依赖和 unknown dependency 如何表示。producer 可以遗漏间接变化而仍声称其直接源匹配。应给每个 projection kind 固定 dependency manifest schema和 closure 算法。

- `ARCHITECTURE-SPINE.md:127` 允许路径相对 project root 或 memory root，却没有在路径值中携带 root identity。同一路径在两个 root 都存在时，任一 precedence 都字面合规。应改为 `{root:"project|memory",path:"..."}`，拒绝无 root 的新 contract，并规定 symlink、case sensitivity 和 canonical realpath 检查。

- `ARCHITECTURE-SPINE.md:96-100` 没有提供 machine-readable DAG registry。图中边、producer manifest、selection policy、projection identities 谁是权威，以及版本升级如何改变边都不明确。应发布带 node ID、owner、inputs、outputs、contract versions 和 refresh command 的 registry，并把 registry fingerprint 纳入 orchestrator receipt。

- `ARCHITECTURE-SPINE.md:88,100,128` 把阻断交给“是否影响当前展示”和 “required gates”，但没有定义 impact 分析。A 可以只 gate 可见 binding，B 可以 gate 全部 canonical projection，二者分别得到 publish/degraded 与 blocked。应固定 Panel selection 到 projection/field 的 dependency map、severity lattice、unknown-impact fail-closed 规则和每种 mismatch 的 gate outcome。

- `ARCHITECTURE-SPINE.md:84-100` 没有定义一致性快照。orchestrator 可以在 T1 hash WDR、T2 构建 program-status、T3 hash ledger、T4 源又变化，然后把来自不同时间切片的 projection 一起发布。逐文件重算 live fingerprint 仍可能在没有全局 read barrier 时通过。应规定 snapshot token 或两阶段全量 fingerprint revalidation，任何源在 build window 变化都废弃 staging generation 并重算 invalidation set。

- `ARCHITECTURE-SPINE.md:100,124,130` 要求 atomic current Panel 与 content identity，却未定义 identity canonicalization、staging layout、current pointer、并发 publisher fencing 和 crash 后清理。两个 publisher 可以各自原子 rename，却让较旧 generation 最后成为 current。应规定 generation manifest、monotonic publish sequence/CAS、目录级 commit、fsync/rename 边界和 stale publisher rejection。

- `ARCHITECTURE-SPINE.md:102-106` 对 `entity_refs`、`repair` 和 `repair_batches` 只给名字，没有规范对象结构、允许 entity types、selector cardinality、batch ID、排序 key、去重 key、依赖关系和 partial failure 语义。两种 typed JSON 均可合法却不可互换。应发布 finding/repair schema，并给每个 repair 带 exact target、expected revision/fingerprint、stable command ID、preconditions 和 originating finding IDs。

- `ARCHITECTURE-SPINE.md:102-106` 要求 dry-run 和 authorization，但没有把 dry-run 结果绑定到 apply。源在 dry-run 与 apply 之间变化时，授权可能批准另一份 payload。应让 dry-run receipt 携带 exact payload fingerprint、target revisions、expiry 和 authorization subject，apply 必须逐项验证同一 receipt，失败后重新 dry-run。

- `ARCHITECTURE-SPINE.md:108-112` 没有定义 `artifact_integrity`、`business_freshness`、`pending_invalidations`、`source_as_of` 的类型和聚合状态机。布尔、字符串或对象都满足“分别输出”，但 consumer 无法统一 gate。应固定 enum/lattice、每-source finding、overall reduction、`checked_at` 与 source event `as_of` 的区别，以及 unverifiable/migration-required 对 publish 的影响。

- `ARCHITECTURE-SPINE.md:114-118,130` 对 legacy create 没有规定 command ID 的派生、ID allocation、dedupe scope、重放窗口和迁移 receipt。两个兼容实现会为同一 legacy item创建不同 ACT ID，甚至在跨机器重跑时重复。应固定 legacy adapter 的 deterministic envelope 和 identity algorithm，或明确 legacy create 必须先 materialize 为带 command ID 的 v2 command。

- `ARCHITECTURE-SPINE.md:70,76,106` 没有给 mutation authority 一个统一 contract。meeting evidence 可以合法生成 owner/status patch，但谁能提议、谁能批准哪些字段、status transition 是否需要额外 authority 只引用“现有 apply authorization”。应定义 principal、scope、allowed operations、evidence provenance、approval binding 和 receipt audit fields，且 direct mutation 与 repair 使用同一授权模型。

- spine 没有跨实现 conformance suite。只测每个 producer 自身可使 A/B 都绿灯，却无法发现 contract 漂移。应为 command、WDR algebra、marker grammar、fingerprint、root resolution、DAG invalidation、TOCTOU、finding/repair、legacy migration 和 atomic publication提供共享 positive/negative/golden fixtures，并规定所有 bound workflows 必须通过同一版本套件。

## 建议的关闭条件

spine 在满足以下可验证条件后才适合作为冻结的 build substrate：

- 每个跨 workflow envelope 都有带 `$id` 的 normative schema、版本协商和 unknown-field policy。
- 同一 action/WDR 输入在所有 bound workflow 中产生相同 durable fact、revision、receipt 和 error code。
- 同一 source fixture 在所有 producer 中得到相同 canonical fingerprint，root collision 必须被拒绝而不是按 precedence 猜测。
- invalidation registry 和 Panel binding map 可机器读取；给定 source delta，只存在一个 expected invalidation set 和 gate outcome。
- refresh 对 build-window 内源变化执行一致性切片验证，旧 publisher 无法覆盖新 current generation。
- audit finding 和 repair batch 可由 status-sync 直接消费，不需要字段适配或自然语言解析；dry-run/apply 由同一 payload fingerprint 和 revision 集绑定。
- 两个独立实现运行共享 conformance fixtures 后，projection identity、freshness result、drift findings、repair plan 和 published generation 全部一致。
