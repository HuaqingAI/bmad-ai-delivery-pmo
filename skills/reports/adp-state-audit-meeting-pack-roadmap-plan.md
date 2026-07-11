---
title: ADP State Audit, Meeting Pack, and Roadmap Extension Plan
module: AI Delivery PMO
module_code: adp
status: complete
created: 2026-07-08
updated: 2026-07-09
owner: hth
document_language: Chinese
---

# ADP 状态质检、会议视图包与 Roadmap 扩展方案

## 1. 结论

这次扩展不应该新建一个脱离现状的“报表系统”，而应该沿着现有 ADP 的状态链路继续加固：

1. 以 `adp-agent-program-lead/scripts/adp-state-prepass.py` 作为状态扫描底座，新增状态质检层，输出可追踪的 audit JSON/Markdown。
2. 会议视图包不作为事实源，只作为针对会议场景的派生包；会前生成，会后继续回到 `adp-meeting-sync` 和 `adp-status-sync` 完成闭环。
3. Roadmap 不由 Program Lead 猜测，而从 WDR、checkpoint sync、action ledger、decision packet、readiness/risk/L0 gate 中抽取；无来源的日期和里程碑必须显示为 `TBD` 或低置信度。
4. `weekly-report.md`、`project-lead.md` 当前在真实项目中仍是占位视图，需要先补齐生成逻辑，再把它们纳入会议或管理场景。
5. 这是一次模块级增强，不是单脚本补丁。`adp-state-audit`、`adp-meeting-pack`、`adp-roadmap-sync` 应作为独立 workflow skill 进入模块能力表；`adp-agent-program-lead` 改为消费这些产物，而不是包办所有生成逻辑。

### 1.1 工程范围与本次边界

这次计划必须明确分层，否则会变成一次性重写 ADP。

| 层级 | 本次是否进入计划 | Skill 影响 | 说明 |
| --- | --- | --- | --- |
| 质量门 | 必做 | 新增 `adp-state-audit` | 所有报告/会议包/Roadmap 生成前先跑，解决陈旧、遗漏、重复、交叉、冲突、未闭环。 |
| 会议包 | 必做 | 新增 `adp-meeting-pack` | 覆盖 FDE 1/3/5 晨会和双周业务例会两个关键场景。 |
| Roadmap | 若双周业务会进入本次则必做 v1 table；否则 P1 | 新增 `adp-roadmap-sync` | 双周会需要可信 timeline；v1 只交付可追溯表格和 unscheduled milestones，Gantt/HTML 后续增强。 |
| Program Lead 读出 | 必改 | 修改 `adp-agent-program-lead` | 从直接拼报告改成消费 audit、meeting pack、roadmap，并补 `project-lead.md`/`weekly-report.md`。 |
| Memory scaffold | 必改 | 修改 `adp-project-kickoff` | 增加 audits、meeting-packs、roadmap 初始模板和可选 WDR Roadmap section。 |
| 注册安装 | 必改 | 修改 `adp-setup` | 把新增 workflow 注册进 `module-help.csv` 和 setup 安装产物。 |
| 既有同步链路 | 小改 | 修改 checkpoint/status/meeting/risk/readiness 相关 skill | 只补 metadata 和回写链路，不在本次重写核心流程。 |
| HTML 控制台 | 延后 | 可新增或扩展展示层 | 等 audit/meeting/roadmap 数据契约稳定后再做。 |

本次最小闭环是：`adp-state-audit` + `adp-meeting-pack --scenario fde-morning` + `adp-project-kickoff` 模板更新 + `adp-setup` 注册 + `adp-agent-program-lead` 消费关系调整。若双周业务会进入本次交付，则 `adp-roadmap-sync` 的 v1 timeline table 必须前置进入本次；否则双周业务会只能显示 `TBD/unscheduled`，不能承诺 timeline。

## 2. 已核实的现有 ADP 状态

### 2.1 模块注册能力

来源：`skills/adp-setup/assets/module-help.csv`。

当前 ADP 已注册 10 个能力：

| Skill | 当前职责 | 关键输出 |
| --- | --- | --- |
| `adp-setup` | 安装/更新 ADP module config、help entries、agent roster | `_bmad` 配置与 module help |
| `adp-project-kickoff` | 初始化 ADP shared memory、schemas、L0、decisions、daily、views | `_bmad-output/adp/memory` scaffold |
| `adp-workstream-register` | 创建/规范化 Workstream Delivery Record | `workstreams/{id}/delivery-record.md` |
| `adp-bmm-checkpoint-sync` | 把 BMM checkpoint 同步进 WDR，不复制 BMM 产物 | WDR checkpoint/status 更新 |
| `adp-meeting-sync` | 把会议/线下/钉钉听记输入归类并闭环 | meeting archive、raw evidence、daily、decision、WDR update、business packet、status-sync intake |
| `adp-status-sync` | 应用 owner status、stale check、action ledger upsert | WDR status、daily、`actions/action-ledger.md`、WDR `Next actions` |
| `adp-risk-dependency-change-review` | 评审跨线风险、依赖、blocker、change | `views/risk-matrix.md`、`views/dependency-map.md`、business decision packet |
| `adp-l0-reference-sync` | 索引 L0 artifact，抽取 gates/contracts/NFR/evidence rules/impacts | `l0/*` summaries 和 WDR gap suggestions |
| `adp-acceptance-readiness-review` | 从 WDR/evidence/decision/readiness/L0 评分 acceptance/cutover readiness | `views/acceptance-readiness.md/html`、`views/cutover-readiness.md/html` |
| `adp-agent-program-lead` | 综合项目状态、FDE action、readiness、risk/dependency、weekly report、gap coaching、L0 impact、decision closure | `views/*` 派生读出 |

### 2.2 真实项目 ADP memory 现状

来源：`D:\ProgramData\git\repository\github\huaqingai\shopify-migration\_bmad-output\adp\memory`。

实物目录计数：

| 目录 | 文件数 |
| --- | ---: |
| `actions` | 3 |
| `daily` | 6 |
| `decisions` | 12 |
| `intake` | 32 |
| `l0` | 9 |
| `meetings` | 34 |
| `schemas` | 5 |
| `views` | 18 |
| `workstreams` | 56 |

已发现 14 条 workstream：

`l0-foundation-platform`, `l1-transaction-loop`, `l2-order-amount-closure`, `l3-payment-refund`, `l4-fulfillment-inventory`, `l5-data-migration-sync`, `l6-content-seo-compliance`, `l7-cms`, `l8a-growth-data-bi`, `l8b-customer-ops-identity`, `l9-gray-release-rollback`, `l10-tidewe-theme`, `l11-piscifun-theme`, `l12-operations-control-plane`。

### 2.3 当前 prepass 能力

核实命令：

```powershell
python skills/adp-agent-program-lead/scripts/adp-state-prepass.py `
  D:\ProgramData\git\repository\github\huaqingai\shopify-migration `
  --capability "global project readout" `
  --output %TEMP%\adp-prepass-global-current.json
```

当前结果摘要（live smoke，2026-07-09；真实项目会继续漂移，验收不得硬编码这些数字）：

| 项 | 值 |
| --- | ---: |
| `ok` | `true` |
| `schema_version` | 2 |
| `sources_read` | 52 |
| `missing_sources` | 0 |
| `workstreams` | 14 |
| `actions` | 53 |
| `gaps` | 22 |
| `cross_reference_gaps` | 2 |
| `action_cross_check` | 14 |

已核实 prepass 当前能读：

- core files：`index.md`, `project-charter.md`, `cadence.md`
- L0 summaries：`l0/*`
- derived views：`views/project-lead.md`, `views/fde-actions.md`, `views/acceptance-readiness.md`, `views/risk-matrix.md`, `views/dependency-map.md`, `views/weekly-report.md`
- action ledger：`actions/action-ledger.md`
- daily logs：`daily/*`
- decision log 和 business decision packets
- 所有 WDR：`workstreams/*/delivery-record.md`

已核实 prepass 当前已经输出：

- WDR 字段缺口，例如 `blocker status is missing or TBD`、`progress is missing or TBD`
- 跨引用缺口，例如 `l3-payment-refund depends_on "T-22状态矩阵与订单状态编排"`，但目标不是已扫描 WDR
- action ledger 与 WDR `Next actions` 的交叉检查
- workflow routing 建议

这说明 v1 状态质检应复用 prepass，而不是重新写一套事实扫描。

### 2.4 当前视图状态

来源：真实项目 `views` 目录。

| View | 当前状态 | 方案影响 |
| --- | --- | --- |
| `views/fde-actions.md` | 约 27KB，已有内容，近期生成 | 可作为 FDE 晨会输入，但只能是派生输入；action 真源仍是 ledger |
| `views/acceptance-readiness.md/html` | 已生成，Markdown 约 42KB | 双周业务例会核心输入 |
| `views/cutover-readiness.md/html` | 已生成 | 双周业务例会和 cutover review 输入 |
| `views/risk-matrix.md` | 约 39KB，已生成 | FDE 晨会与业务例会都需要裁剪引用 |
| `views/dependency-map.md` | 约 360KB，过大 | 不能整份塞进会议包，必须按相关 workstream/blocked edge 筛选 |
| `views/project-lead.md` | 仍是占位模板 | 需要补真正生成逻辑 |
| `views/weekly-report.md` | 仍是占位模板 | 需要补真正生成逻辑 |

### 2.5 当前事实源契约

已核实文件：

- `skills/adp-project-kickoff/assets/adp-memory-templates/schemas/workstream-delivery-record.md`
- `skills/adp-project-kickoff/assets/adp-memory-templates/schemas/action-ledger.md`
- `skills/adp-project-kickoff/assets/adp-memory-templates/cadence.md`
- `skills/adp-status-sync/scripts/sync_status.py`
- `skills/adp-meeting-sync/scripts/sync_meeting.py`

关键约束：

1. WDR 是每条工作线的最小项目级同步面，不复制完整 PRD、architecture、story、code、validation。
2. BMM 产物仍是交付事实源；ADP 只索引和摘要项目级同步状态。
3. `actions/action-ledger.md` 是 ADP action source of truth；`views/fde-actions.md` 不能作为事实源。
4. workflow 应该生成 `intake/status-sync/*.json`，由 `adp-status-sync` 负责 ledger upsert、dedup 和 WDR `Next actions` 刷新。
5. 每个会议/线下同步/补录必须至少落为 daily log、decision、action、WDR update、business decision packet 或 explicit no-op。
6. `adp-meeting-sync` 当前已输出 `action_quality_audit`，说明 action 质量检查已有入口，可纳入状态质检。

## 3. 设计原则

### 3.1 事实源分层

| 层级 | 文件/产物 | 用途 | 能否作为事实源 |
| --- | --- | --- | --- |
| 交付事实 | BMM PRD、architecture、epic/story、code、validation evidence | 工作线实际交付依据 | 是，ADP 通过 WDR 索引 |
| 项目同步事实 | `workstreams/*/delivery-record.md` | 工作线项目级状态、依赖、风险、acceptance、evidence index | 是 |
| 行动项事实 | `actions/action-ledger.md` | 项目级 follow-up action | 是 |
| 决策事实 | `decisions/decision-log.md`、`decisions/business-decision-packets/*` | 已决事项、待业务拍板事项、风险接受/范围变更 | 是 |
| 会议证据 | `meetings/*`、raw evidence、`daily/*` | 会议归档和操作轨迹 | 是，但需经 meeting-sync 分类后进入 durable state |
| L0 约束 | `l0/*` | gate、contract、NFR、evidence rules、open questions | 是 |
| 派生视图 | `views/*` | 面向场景的读出 | 默认不是事实源，只能作为已生成摘要；报告必须能追溯到底层来源 |
| 会议包 | `views/meeting-packs/*` | 会前材料/议程/待决板 | 不是事实源 |

### 3.2 生成前必须先质检

所有会议包、weekly report、project lead readout、roadmap 都应遵循：

```text
prepass -> state audit -> scenario filter -> render view/pack -> source inventory -> recommended follow-up workflow
```

如果 audit 出现 blocking gap 或 conflict，报告可以生成，但必须以“带风险的读出”形式生成，不能把状态渲染为绿色。

派生视图参与质检时必须先过 freshness gate：`views/*` 比其底层事实源旧时，只能产出 `views_requiring_refresh` 或 `recommended_refreshes`，不能直接把派生视图和 WDR/ledger 的差异升级成 blocking conflict。只有当双方都是事实源，或派生视图明确新于其来源且保留 source lineage 时，才可进入 conflict 判定。

### 3.3 缺口、推断和事实必须分开

输出中每个判断需要标记：

- `fact`：直接来自 WDR、ledger、decision、meeting-sync、L0 等 durable state。
- `derived`：来自脚本计算或派生视图，例如 stale、cross-reference gap、ledger/WDR mismatch、readiness/risk/dependency view 摘要。
- `inference`：弱推断，只能作为建议，不能作为状态事实。
- `missing`：来源不存在或字段为空。

Roadmap 中尤其不能把 action due date 自动升级为 milestone date。

## 4. 状态质检能力设计

### 4.1 推荐落点

`state-audit` 不需要独立 agent，但需要独立 workflow skill。

推荐新增：

- `skills/adp-state-audit/SKILL.md`
- `skills/adp-state-audit/scripts/audit_state.py`

原因：

1. audit 是横向质量门，会被 FDE 晨会、双周业务例会、weekly report、project lead view、roadmap 复用。
2. audit 输出本身是可审计产物，应能被用户单独运行和归档。
3. audit 不需要 persona、长期记忆或对话身份，所以不适合做 agent。
4. `adp-agent-program-lead` 可以继续复用 `adp-state-prepass.py` 的扫描能力，但不应长期拥有 audit 的用户入口。

关系边界：

- `adp-state-audit`：拥有质检入口和 audit 产物。
- `adp-agent-program-lead`：消费 audit 结果，生成综合读出和 routing 建议。
- `adp-meeting-pack`：消费 audit 结果，生成会议包。
- `adp-roadmap-sync`：消费 audit/source inventory，生成 roadmap/timeline。

### 4.2 输入

| 输入 | 来源 | 必需性 |
| --- | --- | --- |
| prepass JSON | `adp-state-prepass.py` 输出 | 必需 |
| WDRs | `workstreams/*/delivery-record.md` | 必需 |
| Action Ledger | `actions/action-ledger.md` | 必需 |
| Decisions | `decisions/decision-log.md`, `decisions/business-decision-packets/*` | 必需 |
| Meetings/Daily | `meetings/*`, `daily/*` | 建议 |
| L0 summaries | `l0/*` | 对 migration/cutover 项目必需 |
| Readiness views/scorecards | `views/acceptance-readiness.*`, `views/cutover-readiness.*`, scorecard JSON | 对 acceptance/cutover 场景必需 |
| Risk/dependency views | `views/risk-matrix.md`, `views/dependency-map.md` | 对跨线协调场景必需 |

### 4.3 检查类别

#### A. Freshness

目标：避免状态陈旧。

检查项：

- WDR `Last status sync` 缺失、无法解析或超过阈值。
- action `Last Updated` 超过阈值且仍为 `open/in-progress/blocked`。
- readiness/risk/dependency view 比相关 WDR 或 action ledger 更旧。
- meeting archive 已生成，但后续 status-sync intake 未执行。
- weekly/project-lead view 仍是模板占位。

输出：

- `stale_sources`
- `stale_workstreams`
- `stale_actions`
- `views_requiring_refresh`

#### B. Completeness

目标：避免报告遗漏关键信息。

检查项：

- WDR 必填字段缺失：owner/status/progress/blockers/risks/dependencies/next actions/readiness/evidence/L0 references。
- Business Decision Packet 缺 background/question/options/impact/recommendation/deadline/owner/workstreams。
- Action 缺 owner、due/trigger、closure criteria、source、affected workstreams。
- readiness gap 没有对应 owner/action。
- dependency 没有 owner、target、解除条件。

输出：

- `blocking_gaps`
- `non_blocking_gaps`
- `missing_owner_items`
- `missing_evidence_items`

#### C. Consistency

目标：避免同一事实在多个地方表达不一致。

检查项：

- action ledger 与 WDR `Next actions` 不一致。
- WDR 标为 ready，但 readiness view 存在 blocking gap。
- decision packet 已 closed，但对应 action 仍 open，或反过来。
- dependency-map 显示 blocked，但相关 WDR blocker/risk 未体现。
- L0 gate 变更后，受影响 workstream 未刷新。

输出：

- `consistency_warnings`
- `source_disagreements`
- `recommended_refreshes`

#### D. Closure

目标：确保会议和业务待决项闭环。

检查项：

- meeting item 未分类到 daily/decision/action/WDR/business packet/no-op。
- `intake/status-sync/*.json` 生成后未被 status-sync 消费。
- business decision packet 超过 deadline 仍 open。
- blocked action 没有升级路径。
- no-op 缺 rationale。

输出：

- `unclosed_meeting_items`
- `open_business_packets`
- `unconsumed_intake_files`
- `escalation_candidates`

#### E. Merge Quality：重复、交叉、冲突

这直接回答“质检是否处理各线合并时可能存在的重复、交叉、冲突”。

##### 重复 Duplicate

定义：两个或多个 action/decision/packet 指向同一个实际事项，且 owner、source、closure criteria 高度相似。

已存在规则依据：

- action ledger schema 已规定：不要重复一个 `Source + Action`；跨多线事项应收敛成 `program` action + `Affected Workstreams`，除非每条线有不同 owner、due trigger 或 deliverable。

v1 检查：

- 同一 `normalized(action) + owner + due/trigger` 出现多次。
- 同一 source anchor 被多个 action 重复引用但 closure criteria 相同。
- 多条 workstream action 文本近似，且 affected workstreams 可合并。

处理方式：

- audit 只报 `duplicate_candidates`，不自动合并。
- 推荐走 `adp-status-sync update` 或专项 migration script 做确认式合并。

##### 交叉 Overlap

定义：多个 workstream 同时声明负责同一业务能力、接口、证据、验收项或业务确认，但没有主责/协作边界。

v1 检查：

- 多个 WDR 的 Scope/Acceptance/Dependencies/L0 references 出现同一 capability keyword。
- 多个 action 的 affected workstreams 覆盖相同能力，但 owner 不一致。
- dependency-map 中同一 target 被多线依赖，缺统一 owner。
- business decision packet 影响多线，但 WDR 未反映受影响范围。

处理方式：

- 输出 `overlap_claims`，要求指定 accountable owner、supporting workstreams、decision source。
- 不能自动选择主责。

##### 冲突 Conflict

定义：两个事实源对同一状态给出不兼容结论。

v1 检查：

- WDR `Current readiness=ready`，但 readiness view 有 blocking gap。
- WDR 说 dependency 已解除，dependency-map 仍 blocked。
- business packet 待拍板，但 WDR/weekly report 表述为已确认。
- action marked done，但 closure criteria 没有证据来源。
- L0 open question 未关闭，但相关 workstream 标记 baseline/ready。

处理方式：

- 输出 `conflicts`，包含双方 source、字段、值、建议 owner。
- blocking conflict 会把 `safe_to_generate_green_report=false`。

### 4.4 输出契约

建议每次运行输出两份：

```text
_bmad-output/adp/memory/audits/YYYY-MM-DD-{scenario}-audit.json
_bmad-output/adp/memory/audits/YYYY-MM-DD-{scenario}-audit.md
```

JSON 契约。这里的版本号是 audit 输出契约版本，不是 prepass 的 `schema_version=2`：

```json
{
  "audit_schema_version": 1,
  "prepass_schema_version": 2,
  "generated_at": "2026-07-08T00:00:00+08:00",
  "scenario": "fde-morning|business-biweekly|weekly-report|project-lead|roadmap",
  "memory_root": ".../_bmad-output/adp/memory",
  "safe_to_generate": true,
  "safe_to_generate_green_report": false,
  "report_confidence": "high|medium|low",
  "source_inventory": [
    {
      "path": "actions/action-ledger.md",
      "kind": "action-ledger",
      "modified": "2026-07-08T15:03:24+08:00",
      "status": "read"
    }
  ],
  "blocking_gaps": [],
  "warnings": [],
  "duplicate_candidates": [],
  "overlap_claims": [],
  "conflicts": [],
  "stale_items": [],
  "recommended_workflows": [
    {
      "workflow": "adp-status-sync",
      "reason": "register meeting actions from intake/status-sync/...",
      "command_hint": "adp-status-sync update ..."
    }
  ]
}
```

所有 `blocking_gaps`、`warnings`、`duplicate_candidates`、`overlap_claims`、`conflicts`、`stale_items` 的 item 至少包含：

```json
{
  "id": "stable-or-derived-id",
  "severity": "blocking|warning|info",
  "kind": "freshness|completeness|consistency|closure|duplicate|overlap|conflict",
  "source_type": "fact|derived|inference|missing",
  "sources": ["relative/path.md#anchor"],
  "workstreams": ["l3-payment-refund"],
  "owner": "TBD",
  "summary": "human readable issue",
  "recommended_workflow": "adp-status-sync"
}
```

`conflicts` 必须额外包含双方字段和值；若任一方是 stale derived view，则降级为 `recommended_refreshes`，不进入 `conflicts`。

Markdown 契约：

- 第一屏：能否生成、置信度、blocking gaps、conflicts。
- 第二屏：按 owner/workstream 汇总的修复建议。
- 附录：source inventory、派生规则、被跳过的来源。

## 5. 会议视图包设计

### 5.1 通用生成链路

```text
1. Run prepass
2. Run state audit with scenario
3. Select scenario-specific state slices
4. Render meeting pack Markdown
5. Optionally render HTML
6. After meeting: use adp-meeting-sync to ingest notes
7. Run adp-status-sync for action ledger updates
8. Refresh affected views
```

会议包目录建议：

```text
_bmad-output/adp/memory/views/meeting-packs/
  fde-morning/YYYY-MM-DD.md
  fde-morning/YYYY-MM-DD.html
  business-biweekly/YYYY-MM-DD.md
  business-biweekly/YYYY-MM-DD.html
```

会议包必须包含：

- `Generated from`：列出来源文件和 modified time。
- `Audit status`：引用本次 audit 文件。
- `Not a source of truth`：说明会议包是派生视图，会后变更必须回写 ADP durable state。
- `Post-meeting sync checklist`：提醒运行 `adp-meeting-sync`、`adp-status-sync`。

### 5.2 FDE 每周 1/3/5 晨会包

场景：内部高频推进会，目标是快速暴露阻塞、下一步动作、跨线依赖和需要项目负责人介入的事项。

输出：

```text
views/meeting-packs/fde-morning/YYYY-MM-DD.md
views/meeting-packs/fde-morning/YYYY-MM-DD.html
```

输入来源：

| 内容 | 来源 |
| --- | --- |
| 今日 action board | `actions/action-ledger.md`，不是 `views/fde-actions.md` |
| 每线状态 | `workstreams/*/delivery-record.md` |
| stale/missing/conflict | 本次 state audit |
| 风险/依赖摘要 | `views/risk-matrix.md`、裁剪后的 `views/dependency-map.md` |
| readiness exception | `views/acceptance-readiness.md`、`views/cutover-readiness.md` 中 blocking/high risk 部分 |
| 上次会议 closure | 最近 `meetings/*`、`daily/*`、`intake/status-sync/*` |
| L0 影响 | `l0/*` 和 prepass capability filter |

建议结构：

1. `Meeting Header`：日期、场景、覆盖 workstreams、audit result。
2. `Red / Amber Board`：blocking conflict、stale workstream、blocked action、business decision overdue。
3. `Today's FDE Action Board`：按 owner 分组，只列 open/in-progress/blocked，带 source 和 closure criteria。
4. `Cross-Line Dependency Board`：只列 blocked/at-risk dependency，不塞完整 dependency-map。
5. `Readiness Exceptions`：只列 readiness blocking/high risk gap。
6. `Decision / Escalation Needed`：需要 Sue/项目负责人/业务接口人介入的项。
7. `Workstream Roundtable`：每线 1 行，包含 progress、blocker、risk、next action、source。
8. `Post-Meeting Capture Checklist`：哪些项必须通过 meeting-sync/status-sync 回写。

不放入：

- 完整 readiness report。
- 完整 dependency-map。
- 无 owner、无 due、无 closure criteria 的“泛泛提醒”；这类进入 audit gap。

### 5.3 双周业务例会包

场景：与业务部门一起的例会，目标不是内部追人，而是做业务决策、确认范围、同步 readiness、暴露需要业务拍板的风险。

输出：

```text
views/meeting-packs/business-biweekly/YYYY-MM-DD.md
views/meeting-packs/business-biweekly/YYYY-MM-DD.html
```

输入来源：

| 内容 | 来源 |
| --- | --- |
| 业务待决事项 | `decisions/business-decision-packets/*`、`decisions/decision-log.md` |
| acceptance/cutover readiness | `views/acceptance-readiness.md/html`、`views/cutover-readiness.md/html`、scorecard JSON |
| 范围/变更/风险接受 | WDR `Scope or change notes`、decision packet、risk review |
| 跨线影响 | `views/risk-matrix.md`、裁剪后的 `views/dependency-map.md` |
| Roadmap/timeline | `views/roadmap.md` 或临时 timeline slice |
| 会议 closure | 上次 business meeting archive、daily、action ledger |

建议结构：

1. `Executive Snapshot`：整体状态、需要业务今天拍板的数量、readiness 风险、变更风险。
2. `Decision Board`：每个待决问题包含背景、选项、影响、推荐、deadline、source packet。
3. `Scope / Change Board`：新增/移出范围、风险接受、需要业务确认的 Day-1 边界。
4. `Readiness Board`：按 workstream 显示 acceptance/cutover readiness，突出阻塞项和业务 owner。
5. `Roadmap / Timeline`：里程碑、计划/预测/实际、置信度、依赖；无来源的日期显示 TBD。
6. `Cross-Line Business Impact`：只展示业务需理解的跨线依赖，不展示内部技术细节。
7. `Last Meeting Closure`：上次业务会议 action/decision 是否关闭。
8. `Post-Meeting Capture Checklist`：会后写入 decision packet、decision log、meeting sync、status sync。

### 5.4 甘特图/进度可视化

双周会可以加进度更直观的 timeline/Gantt，但 v1 不能直接承诺完整甘特。

建议分三档：

1. v1：`Roadmap / Timeline table`，稳定、可追踪、不会伪造日期。
2. v1.5：Markdown Mermaid Gantt，只渲染有明确 start/end 或 milestone date 的项。
3. v2：HTML 交互控制台，支持按 workstream/owner/status/filter 展示。

Mermaid Gantt 规则：

- 只有 `confidence=high|medium` 的条目进入 Gantt。
- `TBD`、只有 trigger 没有日期、或仅来自 action due 的项不画成进度条，只放在 `Unscheduled Milestones`。
- actual date 来自 evidence/checkpoint/decision closure。
- forecast date 必须来自 owner status/WDR roadmap/明确 decision，不由模型补。

## 6. 原有视图的使用场景矩阵

| View / Artifact | 场景 | 使用方式 | 事实源边界 |
| --- | --- | --- | --- |
| `actions/action-ledger.md` | FDE 晨会、周报、project lead、双周会 closure | action board 真源 | 是事实源 |
| `views/fde-actions.md` | FDE 晨会、FDE 自查 | 可作为人读摘要，但生成器应回读 ledger | 不是事实源 |
| `views/project-lead.md` | 项目负责人日常看板 | v1 需要补生成；应显示全局健康、top risks、next actions | 派生视图 |
| `views/weekly-report.md` | 周报、管理同步、对上汇报 | v1 需要补生成；不替代 FDE 晨会 | 派生视图 |
| `views/acceptance-readiness.md/html` | 双周业务会、验收评审、cutover 前检查 | 展示 readiness gap 和业务确认项 | 派生自 scorecard/WDR/evidence/decision/L0 |
| `views/cutover-readiness.md/html` | cutover review、业务例会 | 展示 cutover gate 和高风险缺口 | 派生视图 |
| `views/risk-matrix.md` | FDE 晨会、双周业务会、project lead | 按严重度/owner/业务影响裁剪 | 派生视图 |
| `views/dependency-map.md` | 跨线依赖专项、FDE 晨会异常部分 | 必须过滤，不整份引用 | 派生视图且体积过大 |
| `decisions/business-decision-packets/*` | 双周业务会核心材料 | 每个 packet 是一个可拍板议题 | 是事实源 |
| `decisions/decision-log.md` | 周报、双周会 closure、project lead | 已决事项和未决状态 | 是事实源 |
| `meetings/*` | closure review、会后回溯 | 只作为归档和 evidence，不直接替代 durable state | 是会议证据 |
| `daily/*` | weekly report、操作审计 | 补充时间线和操作轨迹 | 是操作证据 |
| `views/roadmap.md` | 双周业务会、project lead、周报 | 新增派生视图 | 派生视图 |

## 7. Roadmap 来源模型

### 7.1 Roadmap 不能从哪里来

不能从以下方式生成：

- 让 Program Lead 根据项目感觉编排日期。
- 把 action due date 全部当 milestone。
- 从会议纪要中的“下周/尽快/后续”直接推成具体日期。
- 从 dependency-map 的关系图自动推断项目排期。

### 7.2 v1 来源优先级

| 优先级 | 来源 | 可生成字段 | 置信度 |
| --- | --- | --- | --- |
| P0 | evidence/checkpoint/decision closure | actual date、completed milestone | high |
| P1 | WDR 中明确的 Roadmap section 或 milestone note | planned/forecast、owner、dependency | medium |
| P2 | `adp-bmm-checkpoint-sync --milestone` 输入 | milestone label、source checkpoint | medium，若无日期则 unscheduled |
| P3 | action ledger `Due / Trigger` | follow-up trigger、unscheduled item、not milestone by default | low/medium |
| P4 | readiness/cutover scorecard | gate readiness、blocking gap | medium |
| P5 | L0 freeze/gate summaries | gate constraints、decision gates | medium |
| P6 | risk/dependency review | dependency constraints、risk impact | medium |

### 7.3 WDR schema 扩展

当前 WDR schema 没有 Roadmap section。建议 v1 不强制所有 WDR 立刻补，但若双周业务会需要 timeline，则 v1 必须允许读取可选 section：

```markdown
### Roadmap

| Milestone | Type | Status | Planned | Forecast | Actual | Owner | Confidence | Depends On | Source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TBD | checkpoint | planned | TBD | TBD | TBD | TBD | low | TBD | TBD |
```

字段规则：

- `Milestone`：必须是可验证事件，不是普通待办。
- `Type`：允许值为 `checkpoint`, `business-decision`, `readiness-gate`, `cutover-gate`, `dependency-release`, `delivery-window`。
- `Status`：允许值为 `planned`, `at-risk`, `done`, `blocked`。
- `Planned`：计划日期，必须来自 WDR/owner update/decision。
- `Forecast`：预测日期，必须有来源；不能由 LLM 自行推断。
- `Actual`：完成日期，必须来自 evidence/checkpoint/decision/action close。
- `Confidence`：`high/medium/low`。
- `Source`：必须是相对路径或 source anchor。
- `Next actions` 只能作为 follow-up context；除非 action 明确引用一个已存在 milestone source，否则不能生成 `Planned` 或 `Forecast`。

### 7.4 Roadmap 输出

建议新增：

```text
_bmad-output/adp/memory/views/roadmap.md
_bmad-output/adp/memory/views/roadmap.json
```

`roadmap.json` 用于 HTML/Gantt 渲染；`roadmap.md` 用于人读和会议引用。

`roadmap.md` 结构：

1. `Source Inventory`
2. `Milestone Timeline`
3. `Unscheduled Milestones`
4. `At-Risk Dates`
5. `Blocked By Decisions`
6. `Blocked By Dependencies`
7. `Changed Since Last Roadmap`
8. `Excluded Items`：说明哪些 action/due 被排除，因为不是 milestone 或缺来源

## 8. 推荐新增/变更的 Skill 能力

### 8.0 Skill 变更总览

| 类型 | Skill | 本次变化 | 本次优先级 |
| --- | --- | --- | --- |
| 新增 | `adp-state-audit` | 独立 workflow，生成 audit JSON/Markdown，作为所有派生报告的质量门。 | P0 |
| 新增 | `adp-meeting-pack` | 独立 workflow，生成 FDE 晨会包和双周业务例会包。 | P0 |
| 新增 | `adp-roadmap-sync` | 独立 workflow，生成 `views/roadmap.md/json`；v1 只交付 timeline table 和 unscheduled milestones。 | 若双周业务会本次交付则 P0，否则 P1 |
| 修改 | `adp-agent-program-lead` | 消费 audit/roadmap/meeting pack；补 `project-lead.md`、`weekly-report.md` 生成。 | P0 |
| 修改 | `adp-project-kickoff` | 初始化 `audits/`、`views/meeting-packs/`、`views/roadmap.*` 模板，可选 WDR Roadmap section。 | P0 |
| 修改 | `adp-setup` | 注册新增 workflow，更新 module help。 | P0 |
| 修改 | `adp-bmm-checkpoint-sync` | 将 milestone 变成 roadmap 可识别的结构化来源。 | P1 |
| 修改 | `adp-status-sync` | 强化 action 去重/合并提示和 audit 可消费 metadata。 | P1 |
| 修改 | `adp-meeting-sync` | 补会议类型、meeting-pack 回写链路、status-sync intake 消费提示。 | P1 |
| 修改 | `adp-risk-dependency-change-review` | 输出更适合裁剪消费的 risk/dependency metadata。 | P1 |
| 修改 | `adp-acceptance-readiness-review` | 输出 gate metadata，供业务例会和 roadmap 引用。 | P1 |

### 8.1 `adp-state-audit`

落点：

```text
skills/adp-state-audit/SKILL.md
skills/adp-state-audit/scripts/audit_state.py
```

职责：

- 调用或读取 prepass JSON。
- 执行 freshness、completeness、consistency、closure、duplicate、overlap、conflict 检查。
- 输出 audit JSON/Markdown。
- 给出 recommended workflow，不直接修复状态。

示例命令：

```powershell
python skills/adp-state-audit/scripts/audit_state.py `
  D:\ProgramData\git\repository\github\huaqingai\shopify-migration `
  --scenario fde-morning `
  --output-dir _bmad-output/adp/memory/audits
```

### 8.2 `adp-meeting-pack`

落点：

```text
skills/adp-meeting-pack/SKILL.md
skills/adp-meeting-pack/scripts/render_meeting_pack.py
```

职责：

- 读取 audit JSON、prepass JSON、ledger、WDR、decision、readiness/risk/dependency。
- 按 `--scenario fde-morning|business-biweekly` 过滤。
- 输出 Markdown，后续可选 HTML。
- 包含 source inventory 和 post-meeting sync checklist。

示例命令：

```powershell
python skills/adp-meeting-pack/scripts/render_meeting_pack.py `
  D:\ProgramData\git\repository\github\huaqingai\shopify-migration `
  --scenario business-biweekly `
  --date 2026-07-08 `
  --audit _bmad-output/adp/memory/audits/2026-07-08-business-biweekly-audit.json
```

### 8.3 `adp-roadmap-sync`

落点：

```text
skills/adp-roadmap-sync/SKILL.md
skills/adp-roadmap-sync/scripts/render_roadmap.py
```

职责：

- 从 WDR、checkpoint traces、action ledger、decision packets、readiness、L0 gates、risk/dependency 中抽取 roadmap events。
- 输出 `views/roadmap.json` 和 `views/roadmap.md`。
- 严格标记 source 和 confidence。
- 不创建没有来源的 milestone。

示例命令：

```powershell
python skills/adp-roadmap-sync/scripts/render_roadmap.py `
  D:\ProgramData\git\repository\github\huaqingai\shopify-migration `
  --date 2026-07-08
```

### 8.4 `adp-agent-program-lead` 能力更新

当前注册描述里已有：

- project readout
- FDE action list
- readiness view
- risk/dependency synthesis
- weekly report generation
- gap coaching
- L0 impact sweep
- decision closure review
- workflow routing

建议追加或细化为：

- state audit gate
- FDE morning meeting pack
- business biweekly meeting pack
- roadmap/timeline generation

但不要拆成多个 agent。新增能力以 workflow 形式存在；Program Lead 是消费方和综合读出方。

## 9. 分阶段落地路线

### Phase 1：状态质检底座

目标：先保证报告质量。

范围：

- 新增 `adp-state-audit` workflow。
- 复用 `adp-state-prepass.py`。
- 输出 audit JSON/Markdown。
- 检查 stale、missing、ledger/WDR mismatch、cross reference、duplicate candidates、overlap claims、conflicts。
- 增加 tests，使用真实项目结构抽象 fixture。
- 更新 `adp-setup` 注册。
- 更新 `adp-project-kickoff` 初始化 `audits/`。

验收：

- 能在 shopify-migration 当前 ADP memory 上跑通。
- fixture 回归能复现固定快照中的 gap/category；live smoke 只要求关键类别不丢失，并记录当前 `sources_read/gaps/cross_reference_gaps/action_cross_check`。
- 能识别当前 action cross-check 结构，并保持 ledger/WDR mismatch 不被吞掉。
- 能识别 `project-lead.md`、`weekly-report.md` 仍是占位视图。
- audit 不会把 `fde-actions.md` 当 action 真源。

### Phase 2：FDE 1/3/5 晨会包

目标：覆盖高频内部推进场景。

范围：

- 新增 `adp-meeting-pack --scenario fde-morning`。
- 按 owner/workstream 输出 action board。
- 只展示高风险/阻塞 dependency 和 readiness exception。
- 加 post-meeting sync checklist。
- 更新 `adp-setup` 注册。
- 更新 `adp-project-kickoff` 初始化 `views/meeting-packs/`。

验收：

- 包里每个 action 都有 source 和 closure criteria；缺失则进入 audit gap。
- dependency-map 被过滤，不整份复制。
- 会后有明确 `adp-meeting-sync` / `adp-status-sync` 回写指引。

### Phase 3：Roadmap v1 / Timeline Table

目标：为业务例会提供可信 timeline 输入，但不牺牲事实可信度。

范围：

- 新增 `adp-roadmap-sync` workflow。
- 初始只输出 timeline table、unscheduled milestones 和 excluded items。
- 读取 WDR 可选 `### Roadmap` section、checkpoint/decision/readiness/L0/risk/dependency 来源。
- 不从普通 action due 自动生成 milestone；action 只能作为 follow-up context。
- 更新 `adp-setup` 注册。
- 更新 `adp-project-kickoff` 初始化 `views/roadmap.md/json`。

验收：

- 没有 source 的 milestone 不出现；缺来源的候选项只进入 `Unscheduled Milestones` 或 `Excluded Items`。
- action due 不自动变 milestone。
- 每个 roadmap item 有 source、confidence 和 source_type。
- Mermaid Gantt 不在本阶段交付。

### Phase 4：双周业务例会包

目标：覆盖业务部门例会。

范围：

- 扩展 `adp-meeting-pack --scenario business-biweekly`。
- 输出 decision board、scope/change board、readiness board、timeline slice、last meeting closure。
- 业务表达优先，不塞内部技术细节。
- 消费 `views/roadmap.md/json`；若 roadmap 不存在或 audit 不通过，只显示 `TBD/unscheduled`，不自行推断日期。

验收：

- 每个业务待决项必须来自 business decision packet 或 decision log。
- readiness/cutover 结论必须带 source。
- 未有来源的日期显示 TBD，不进入 Gantt。

### Phase 5：补齐原有视图生成

目标：让现有占位视图可用。

范围：

- 补 `views/project-lead.md` 生成。
- 补 `views/weekly-report.md` 生成。
- 两者都先通过 audit gate。

验收：

- project lead view 不再是 TBD 模板。
- weekly report 覆盖 cadence 要求：status summary、blocked workstreams、risk/dependency changes、decisions needed、readiness gaps、next actions。
- 若 audit 有 blocking conflict，weekly report 明确标红，不输出“全局正常”。

### Phase 6：HTML 控制台

目标：在 Markdown 方案稳定后再做。

范围：

- 聚合 meeting pack、audit、roadmap、readiness、actions。
- 支持按 owner/workstream/status/source 过滤。
- 不新增事实源。

验收：

- HTML 只读或明确回写路径。
- 所有卡片能跳回 source。

## 10. 测试策略

### 10.1 单元测试

建议新增：

```text
skills/adp-state-audit/scripts/tests/test_audit_state.py
skills/adp-meeting-pack/scripts/tests/test_render_meeting_pack.py
skills/adp-roadmap-sync/scripts/tests/test_render_roadmap.py
skills/adp-agent-program-lead/scripts/tests/test_consumes_adp_views.py
```

覆盖：

- stale source 检查。
- missing WDR fields。
- duplicate candidate detection。
- overlap claim detection。
- conflict detection。
- dependency-map filtering。
- business packet source requirement。
- roadmap no-source exclusion。

### 10.2 真实项目回归

以当前 shopify-migration ADP memory 做 smoke test：

```powershell
python skills/adp-agent-program-lead/scripts/adp-state-prepass.py `
  D:\ProgramData\git\repository\github\huaqingai\shopify-migration `
  --capability "global project readout" `
  --output %TEMP%\adp-prepass-global-current.json
```

期望：

- live smoke 记录当前计数，但不要把 `sources_read`、`gaps`、`cross_reference_gaps` 作为硬编码验收。
- fixture smoke 可以固定快照计数；真实项目 smoke 只断言关键类别不丢失。
- `missing_sources` 为 0 或被 audit 清楚解释。
- `workstreams` 为 14。
- `actions` 为 53。
- 已知 cross reference gap 不被吞掉。
- 已知 placeholder views 被报告。

### 10.3 非回归

新增脚本不能破坏：

- `adp-status-sync` ledger upsert/dedup。
- `adp-meeting-sync` action intake 和 `action_quality_audit`。
- `adp-acceptance-readiness-review` Markdown/HTML rendering。
- `adp-risk-dependency-change-review` risk/dependency 输出。

## 11. 风险与处理

| 风险 | 处理 |
| --- | --- |
| 报告看起来很完整，但来源陈旧 | audit gate 必须输出 freshness 状态；stale 时不能 green report |
| 会议包被当成事实源 | 文件头明确 not source of truth，会后必须 meeting-sync/status-sync |
| duplicate 自动合并误伤 | v1 只报 candidate，不自动合并 |
| overlap 自动选错 owner | v1 只要求人工确认 accountable owner |
| roadmap 伪造排期 | source/confidence 必填；无来源显示 TBD |
| stale 派生视图被误判为事实冲突 | 先执行 freshness gate；stale view 只触发 refresh，不触发 blocking conflict |
| live 项目 smoke 数字漂移导致假失败 | fixture 固定数字，真实项目只断言类别、关键 item 和最低结构约束 |
| dependency-map 太大 | meeting pack 只截取 blocked/at-risk/related edges |
| weekly/project-lead 仍是占位 | Phase 5 明确补生成，Phase 1 先作为 audit warning |

## 12. 推荐下一步

建议按以下顺序实现：

1. `adp-state-audit`
2. `adp-meeting-pack --scenario fde-morning`
3. `adp-roadmap-sync` v1 timeline table
4. `adp-meeting-pack --scenario business-biweekly`
5. `project-lead.md` 和 `weekly-report.md` 生成补齐
6. HTML 控制台

这个顺序的核心原因是：先提升状态可信度，再服务高频会议；业务例会若要 timeline，必须先有可追溯的 roadmap table。HTML 是展示层，应该等数据契约稳定后再做。
