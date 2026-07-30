# Architecture Spine Rubric Review

## Gate verdict

**未通过，需修订后再进入实现拆分。** 机械 lint 为 0 finding，且 spine 已正确选择单向状态传播、single writer、typed mutation、live fingerprint 和显式 refresh orchestrator 作为主干；但语义门禁仍有 **5 个 High、2 个 Medium**。其中最重要的是 legacy action 兼容规则与现有 brownfield 行为冲突、跨 action/WDR mutation 的原子边界没有进入 spine、WDR/ledger drift 未被明确接入 Panel publication gate，以及 repair batch 没有强制保留 exact action IDs。

本评审只评审以下两个制品，不修改原文：

- `ARCHITECTURE-SPINE.md`
- `ANALYSIS-AND-OPTIMIZATION-PLAN.md`

机械检查说明：标准命令因环境缺少 `uv` 未能启动；使用同一脚本 `python3 .agents/skills/bmad-architecture/scripts/lint_spine.py --workspace ...` 执行，结果为 `ok: true`、`total_findings: 0`。这只证明不存在 placeholder、AD ID 重复、Binds/Prevents/Rule 缺失等机械问题，不代表语义门禁通过。

## Tiered findings

### H1 - AD-9 将既有 legacy `action_id` 更新误归为 create，违背棕地事实和 AD-2

- **证据：** Spine 第 114-118 行规定“legacy action item 缺 `operation` 时仅按 `create` 兼容”。但伴随报告第 58-62 行明确记录：当前 `status-sync` 已支持携带 exact `action_id` 的 ledger row update，只是 partial patch semantics 不安全。现有代码 `sync_status.py:544` 读取 `action_id`，`sync_status.py:840-846` 按 ID 查找既有行，`sync_status.py:818-825` 合并更新。
- **为什么是 High：** 两个下游实现可以合理地产生完全相反的迁移行为：一个把“缺 operation + 有 action_id”当 create，另一个沿用现状当 update。前者可能重复注册、碰撞 ID 或把旧更新静默改变语义，直接破坏 AD-2 所声称防止的“更新被误注册为新 action”。这也不符合 checklist 的“ratifies rather than contradicts a brownfield codebase”。
- **建议处置：Discuss + fix。** 把 legacy 分支按输入来源和 target identity 明确拆开：仅“meeting-sync v1 且无 target ID”的 action item 可兼容为 create；“有 action_id 但无 operation”的既有 status-sync 输入应进入显式 legacy-update 迁移路径或 fail-visible `migration-required`，且 unknown ID 不得 create。同步规定迁移期、告警与退出条件。

### H2 - Spine 未固定同一 status-sync batch 内 action 与 WDR mutation 的事务边界

- **证据：** AD-2 只规定单个 action patch 的 revision 原子递增（第 72-76 行），AD-3 只规定 WDR patch 语义（第 78-82 行）；Consistency Conventions 仅区分 fact mutation receipt 与 projection refresh receipt（第 129 行）。伴随报告第 169 行却要求 status-sync 在一个 staged transaction 中更新 WDR current fields、daily log、action ledger/action-flow 和 receipt；验收矩阵第 327 行进一步要求 stale action revision 时“整批 blocked；ledger/WDR 均不部分提交”。
- **为什么是 High：** 会议通常同时确认 action owner/status 和 WDR Progress/Blockers/Risks。如果 action revision 冲突但 WDR 已提交，或 WDR patch 失败但 ledger 已提交，就会制造本轮方案正要消除的事实漂移。由于 spine 没有决定 all-or-nothing 的范围，独立实现可以分别选择 per-entity commit、per-file commit 或 whole-batch commit，且都自认为符合现有 AD。
- **建议处置：Fix。** 增加或扩展 AD，明确 status-sync batch 的 validation、staging、commit 边界：所有 fact targets 先校验；任何 expected revision、schema 或 authorization gate 失败则事实文件零写入；ledger、WDR current fields、必要的 durable receipt 原子提交。另行明确 action-flow/WDR `Next actions` 是同事务内 materialization，还是事实提交后的 projection refresh，避免与 AD-6/Publication convention 冲突。

### H3 - `repair_batches` 没有被约束为携带 exact action IDs，核心 repairability 仍可能丢失

- **证据：** AD-7 第 102-106 行要求 finding 保留 typed `entity_refs`，audit root 按 workflow/workstream 输出 deterministic `repair_batches`，但没有规定 batch 必须携带 `finding_ids`、exact `action_ids`、schema/version 或可直接交给 status-sync dry-run 的 payload。伴随报告第 229 行明确要求 batch 保留 `finding_ids` 和 exact `action_ids`；第 284、334 行分别以“可直接得到 exact action ID 和 status-sync repair payload”“repair batches 可直接 dry-run”为完成门。
- **为什么是 High：** 实现者完全可以生成只含 workstream + `refresh_actions` 的 batch，并声称满足 AD-7；这样用户提出的“审计结果没有直接携带具体 action ID，难以批量修复”只在单条 finding 层部分修复，batch 层仍需回查或解析。`typed` 一词本身不足以形成可互操作合约。
- **建议处置：Autofix。** 在 AD-7 或 Consistency Conventions 中固定 additive audit contract 版本，以及最小字段：每个 finding 的 `finding_id/entity_refs/source_path/repair`；每个 batch 的 `workflow/workstream/finding_ids/action_ids/update`。规定 `action_ids` 去重、排序、来自 finding entity refs，repair payload 可原样进入 status-sync dry-run，且 audit 只计划不授权 apply。

### H4 - WDR/ledger drift 的 publication gate 和严重度判定没有闭合

- **证据：** AD-5 第 90-94 行只绑定 ledger、WDR、prepass、state-audit；AD-7 只绑定 state-audit 与 status-sync。Capability Map 第 171 行也只将 drift alert 放在 prepass + state-audit。Spine 没有明确规定 Management Panel/refresh orchestrator 必须消费 canonical drift result。第 128 行又把 drift 定为 blocked 或 degraded，判定条件仅写“是否影响当前展示”。伴随报告第 96、206 行则明确要求 Panel pre-render gate 消费 consistency evidence，并由 Panel 调用 state-audit canonical drift result。
- **为什么是 High：** live fingerprint match 只说明投影输入没有在投影之后变化，并不能证明 WDR `Next actions` 当时就与 ledger 一致。若 Panel gate 只执行 AD-4 fingerprint 校验，`missing_in_wdr`、`orphaned_in_wdr` 或 content mismatch 仍可随一个“fresh”projection 发布。不同 producer 也会对 blocked/degraded 自行判断。
- **建议处置：Fix。** 明确 required gate：refresh/publish 必须消费 state-audit 的 canonical drift verdict；为每种 drift 固定默认等级与“是否影响当前展示”的机器判据。建议 `missing_in_wdr`、`orphaned_in_wdr`、`duplicate_marker` 默认 blocked；`content_mismatch` 由 dependency manifest 的 display-consumed fields 判定；manual entry 永不作为 drift。非展示范围 drift 只能 degraded，并进入 repair queue。

### H5 - 版本化合约只被口头要求，未固定双方可收敛的版本与兼容矩阵

- **证据：** AD-2 标题称“版本化 command”，Consistency Conventions 第 125 行只列出 `schema_version`；Stack 第 137 行写“JSON contracts | versioned per producer”。伴随报告第 113、290、366 行明确建议 meeting sync plan v2 和 status-sync batch v2，但 spine 没有采用具体 contract ID/version，也没有规定 producer/consumer 的兼容矩阵。
- **为什么是 High：** meeting-sync 和 status-sync 是独立构建单元，恰好属于 good-spine checklist 要防止的一级下钻分叉。一个可以输出 `sync_plan_schema_version: 2.0`，另一个可以只识别 `schema_version: 2` 或另一个 envelope；同样的问题也会发生在 projection dependency manifest 和 audit repair contract。只说“versioned”并不能保证互操作。
- **建议处置：Discuss + fix。** 固定 v2 合约名称、版本字段、major-version 拒绝规则、minor additive 规则和 legacy v1 路由；至少覆盖 meeting mutation intake、status-sync batch/receipt、projection dependency manifest、audit finding/repair batch。若版本号仍未批准，应列为 blocking open question，而不是留给实现。

### M1 - AD-5 没有完整固化伴随方案已经定义的 drift taxonomy

- **证据：** AD-5 第 93-94 行覆盖 active 缺失、terminal 残留、ID/owner/text/due 漂移及 empty active set，但没有显式要求同一 WDR 内 marker 唯一，也没有定义 `missing_in_wdr/orphaned_in_wdr/content_mismatch/duplicate_marker/manual_entry` 的 canonical kind。伴随报告第 196-202 行已经给出完整 taxonomy 与默认处理。
- **影响：** 审计实现可能只做集合相等和字段相等，漏掉重复 marker；另一实现可能把 manual entry 当成 orphan，从而破坏 split ownership。
- **建议处置：Autofix。** 将这五类 canonical kind 及 marker uniqueness 写入 AD-5，保持 manual entry 的人工 ownership，不要求自然语言匹配。

### M2 - 运行与环境维度只有局部规则，缺少可执行的并发、可观测性和恢复边界

- **证据：** Spine 已决定显式 refresh、静态 `file://` 限制、失败保留旧 current Panel、dirty receipt（第 100、112、129、179、182 行），这是好的起点。但没有定义并发 refresh/mutation 的锁或 CAS 规则、orchestration receipt 的恢复/保留策略、freshness 指标的 owner，以及影响当前展示的时延/SLO。伴随报告第 270、308、314-317 行已有可重试 receipt 和 metrics 方向。
- **影响：** 在单进程 happy path 下不阻塞，但并发 meeting/status sync 与 panel refresh 时可能出现 TOCTOU：gate 校验通过后源又变化，旧内容仍被发布。不同 workflow 还可能对“last successful refresh”和 pending invalidation 的计算口径不一致。
- **建议处置：Defer to explicit open items 或补充 AD。** 至少固定 publish 前二次 fingerprint/CAS、同一 current Panel 的单写者/互斥规则、dirty receipt 的 retry identity；freshness SLO 与 metrics ownership 可作为有 revisit condition 的 Deferred，不应整维度静默。

## Good-spine checklist

| 检查项 | 结论 | 说明 |
| --- | --- | --- |
| 固定一级下钻的真实分叉点且无遗漏 | **部分通过** | 五个用户问题均有 AD 映射，但 batch atomicity、drift publication gate、contract compatibility 仍未固定。 |
| 每个 AD 可执行且能阻止 stated divergence | **部分通过** | AD-1/2/3/4/6/8 主方向可执行；AD-7 类型约束不足；AD-9 与既有行为及 AD-2 冲突。 |
| Deferred 不会导致实现分叉 | **通过** | watcher/queue/DB/fuzzy entity resolution/离线 archive 验证均有清楚边界；未发现 Deferred 本身泄漏核心决定。 |
| Named technology 已验证为当前且版本明确 | **部分通过** | Panel schema 1.0.0 与代码一致；Python `>=3.10` 与 CI 仅测 3.10 相符，但未给出验证日期/支持上界；`versioned per producer` 不是可执行版本。 |
| 棕地架构得到 ratify 而非矛盾 | **未通过** | Single writer、既有 parser/projection 边界大体被继承；AD-9 对现有 exact-ID update 的兼容语义构成冲突。 |
| 伴随输入的能力均已覆盖 | **部分通过** | Capability Map 覆盖五项问题及 refresh，但丢失 staged transaction、drift taxonomy/gate、repair batch exact IDs 等完成条件。 |
| 继承 parent spine 时无弱化/冲突 | **不适用** | 未声明 parent spine。 |
| 所有本 altitude 维度均 decided/deferred/open | **部分通过** | mutation、projection、freshness、repair、publication 已覆盖；contract evolution 和 operational concurrency/recovery 仍有静默维度。 |

## 五项用户问题对账

| 用户问题 | Spine 覆盖 | 结论 |
| --- | --- | --- |
| meeting-sync 不能 mutation existing action owner/status | AD-1、AD-2、AD-9 | **方向正确但未闭合。** exact ID、partial set、expected revision 已覆盖；legacy rule 反而可能把已有 ID 更新误归 create。 |
| `wdr_update` 不更新 Panel 读取字段 | AD-1、AD-3 | **主体覆盖。** typed WDR patch、current state/evidence 分离正确；仍需把 action + WDR 同批原子性固定。 |
| Panel 不验证 live source | AD-4、AD-6、AD-8 | **主体覆盖。** live fingerprints、integrity/freshness 分离、open 前 inspect 正确；应补 TOCTOU/CAS 与 required source/display policy。 |
| 缺少 WDR/ledger projection drift alert | AD-5、AD-7 | **部分覆盖。** empty-ledger 与字段漂移已进入 Rule；taxonomy、duplicate marker、Panel gate 和等级判据缺失。 |
| 审计没有 action ID，难以批量修复 | AD-7 | **部分覆盖。** finding 层 entity refs 正确；batch 层 exact action IDs 和直接 dry-run payload 未成为强制合约。 |

## Positive observations

- Design paradigm 清楚且与问题根因匹配：事实 mutation 与 read-side projection 分离，避免把 Panel 变成第二事实源。
- AD-1 的 single writer、AD-2 的 omitted-not-default、AD-4 的 live fingerprint、AD-8 的 integrity/freshness 分离，都是足以约束实现的高价值 invariant。
- AD-6 将 orchestration 放在独立 workflow，且规定 dirty hint 不决定正确性、失败不覆盖 current Panel，正确划分了事实成功与视图刷新失败。
- Deferred 克制，没有用数据库、消息队列、watcher 或模糊 action 匹配扩大本轮范围。
- Capability Map 使五项问题与 AD 的表面覆盖关系可追踪，适合作为修订后的验收索引。

## Recommended gate exit

在进入 stories/implementation 前，至少完成 H1-H5 的 spine 修订，并用伴随报告验收矩阵逐条反向验证。M1 可随 H4 一起直接吸收；M2 至少转成带 revisit condition 的显式 open/deferred 项。修订后重新执行 lint，并再次做一次 brownfield compatibility + data-integrity reviewer pass。
