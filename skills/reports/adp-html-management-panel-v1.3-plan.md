---
title: 'ADP HTML 管理面板与可靠性 v1.3 规划'
status: 'complete'
module_name: 'AI Delivery PMO'
module_code: 'adp'
module_description: '让多工作流 AI 交付项目的状态、计划偏差、风险、会议闭环和证据以可审计且适合管理阅读的方式呈现。'
architecture: 'Hybrid: one Program Lead agent over deterministic fact, status, meeting, audit, and static management-panel workflows'
standalone: true
expands_module: ''
skills_planned:
  - adp-management-panel
  - adp-plan-baseline
  - adp-program-status
  - adp-state-audit
  - adp-status-sync
  - adp-roadmap-sync
  - adp-flow-graph
  - adp-risk-dependency-change-review
  - adp-meeting-pack
  - adp-meeting-sync
  - adp-agent-program-lead
  - adp-project-kickoff
  - adp-setup
config_variables:
  - management_panel_history_periods
  - management_panel_default_view
  - management_panel_archive_mode
created: '2026-07-13'
updated: '2026-07-13'
---

# ADP HTML 管理面板与可靠性 v1.3 规划

## Vision

ADP v1.3 把 v1.2 已稳定的 canonical status、roadmap 和 meeting distillate 转化为一个可直接双击打开的静态 HTML 管理面板。项目负责人用它持续查看总体与各交付线进度、计划差距和后续预测，FDE 晨会和业务双周会直接切换到各自视图过会；长表格、宽字段、历史比较和来源追溯不再挤在单一 Markdown 阅读路径中。

HTML 是派生消费面，不是第二套事实或算法。它必须与 Markdown/JSON 对同一项目给出完全一致的状态、置信度、偏差和会议结论，并在数据过期、缺失或审计阻断时明确降级。v1.3 同时关闭 v1.2 review 暴露的可靠性债务，使更易读的界面不会放大不可信数据。

### Contract Ownership

| Artifact | Owns | Wins on conflict |
| --- | --- | --- |
| 本计划 | 模块架构、skill 边界、配置、release gates、build order | 对实现计划和 capability brief 优先 |
| `DESIGN.md` | 视觉 tokens、排版、颜色、组件外观 | 对 mockup、wireframe 和实现样式优先 |
| `EXPERIENCE.md` | IA、行为、状态、交互、无障碍、journeys | 对 mockup、wireframe 和实现行为优先 |

## Architecture

继续采用一个 `adp-agent-program-lead` 加多个确定性 workflow 的混合架构，不新增第二个 agent。新增 `adp-management-panel` 作为唯一 HTML owner：它只消费经过审计的 `program-status.json`、`roadmap.json`、meeting-pack distillate、有限历史快照和 artifact metadata，生成自包含 HTML 与 manifest；不解析底层 WDR，不推断计划，不重算状态或进度，也不回写项目事实。总体、各交付线、计划差距和未来周期预测必须先由 `adp-program-status` 形成 canonical progress projection，面板不得从 milestone 列表临时聚合出第二套结果。

进度模型明确分为两个独立维度：**加权里程碑完成度**回答“完成了多少”，**计划健康度**通过 overall status、门禁、关键路径和日期 variance 回答“是否按计划”。二者共享 lineage 但不互相覆盖；高完成度不能把 blocked/off-plan 项目显示为正常，低完成度也不能在尚未到计划节点时自动判为落后。

面板同时提供两种互补表达：**数值进度**以 bullet chart、milestone 阶梯趋势和各 L 对比呈现 actual/planned/forecast；**流程进度**以 approved baseline 的 milestone/gate 及其 dependency 关系呈现当前位置、并行路径、汇聚、条件分支和返工回路。流程图不是第四个受众视图，也不是另一套计划；它是三个既有视图各自裁剪后的 visualization mode，所有节点、边、状态和计数必须来自版本化 canonical projection。

```text
baseline / WDR / decisions / risks / readiness / meeting archive
  -> adp-state-audit
  -> adp-program-status
  -> adp-roadmap-sync (timeline)
  -> adp-flow-graph (topology + state + scoped overlays)
  -> adp-meeting-pack (scenario distillates + flow subgraph selection)
  -> canonical JSON / immutable snapshots / meeting distillates
  -> adp-management-panel
  -> immutable panel bundle JSON + views/management-panel/index.html
```

面板采用无服务静态架构。Python renderer 把 allowlisted 派生数据、有限周期历史、CSS 和 JavaScript 安全嵌入单一 `index.html`；用户通过 `file://` 直接打开。上游事实变化后运行 panel refresh，renderer 原子替换文件；浏览器手动刷新即可读取新版本。首版不运行 server、不访问网络、不加载 CDN、不依赖前端构建工具。

### Reliability Gates

- **P0，HTML 前置数据可信度：** 非有限权重、preview token 稳定性、future actual、陈旧/未来 signal、snapshot identity、snapshot 原子发布、audit pair 原子发布、Markdown escaped pipe、status-sync 多文件一致性、action status enum、meeting date、meeting 并发、malformed intake、receipt input hash、quoted YAML `#`。任一未完成，不得宣布 panel 数据链 production-ready。
- **P1，消费与运维契约：** management Markdown lineage 校验、legacy Program Lead CLI 兼容策略、stale baseline lock 恢复。可以与 panel renderer 并行开发，但必须在 v1.3 模块验证前关闭。
- **P2，后续硬化：** 超过 v1.3 既定 150-node/250-edge fixture 的超大图、超大历史集、丰富打印排版和 portfolio 聚合只保留扩展点；v1.3 仍必须满足基础布局性能、fallback 及不丢 warning、identity 和 core facts 的打印合同。

### Memory Architecture

沿用 ADP 单一共享 memory。新增内容全部位于派生视图和可选归档区，不创建新的事实源：

```text
_bmad-output/adp/memory/
  snapshots/
    program-status/
    flow-graph/
      <flow-graph-id>.json      # topology + state + scoped overlays 的不可变图快照
    management-panel/
      <panel-id>.json           # 每次成功 refresh 的不可变 panel bundle
      <panel-id>.html           # 仅显式归档或会议归档生成
  views/
    program-status.json
    roadmap.json
    flow-graph.json
    meeting-packs/
    management-panel/
      index.html               # 可替换的最新静态面板
```

默认 refresh 先幂等创建 immutable panel bundle JSON，最后以一次 `os.replace` 更新 `views/management-panel/index.html` 作为唯一 commit point。`--archive` 根据 `management_panel_archive_mode` 写入带稳定 panel ID 的不可变 HTML；归档不是事实源，随时可由其 lineage 指向的 canonical snapshots 重建。

### Memory Contract

| 文件 | 用途 | 主要读取者 | 唯一写入者与规则 |
| --- | --- | --- | --- |
| `views/management-panel/index.html` | 三视图静态消费面 | 项目负责人、晨会、双周会 | `adp-management-panel`；原子替换，可再生 |
| `snapshots/management-panel/<panel-id>.json` | 面板 schema、panel ID、所选 history、source hashes、snapshot IDs、locale、生成器版本 | panel、audit、Program Lead | `adp-management-panel`；immutable，同 ID 幂等，不同内容不碰撞 |
| `snapshots/management-panel/<panel-id>.html` | 显式或会议归档的不可变静态视图 | 审计、历史回看 | `adp-management-panel --archive`；同 ID 幂等，不覆盖不同内容 |
| `snapshots/program-status/*.json` | 跨周期比较的 canonical 历史 | panel、status、meeting-pack | 仍由 `adp-program-status` 独占 |
| `views/roadmap.json` | canonical timeline、日期 variance 与 baseline diff | panel、meeting-pack、audit | `adp-roadmap-sync`；保持既有 timeline boundary，不读取 action/risk 形成图计数 |
| `snapshots/flow-graph/*.json` | topology、执行/健康双轴、scoped overlays 与 identity 的不可变图快照 | panel、meeting-pack、audit | `adp-flow-graph`；同 ID 幂等，不覆盖不同内容 |
| `views/flow-graph.json` | 最新 canonical flow graph projection | panel、meeting-pack、Program Lead | `adp-flow-graph`；原子替换，只消费 approved baseline/status 与显式关联 facts |
| `views/meeting-packs/<scenario>/*.json` | 会议窗口、信息预算、会议场景数据 | panel、meeting-sync | 仍由 `adp-meeting-pack` 独占 |

HTML embedded manifest 与 immutable bundle 必须携带 `panel_schema_version`、`panel_id`、`generated_at`、`as_of`、`reporting_period`、`baseline_revision`、`program_status_snapshot_id`、`topology_id`、`state_snapshot_id`、`overlay_snapshot_id`、`flow_graph_id`、meeting pack IDs、source fingerprints、input/artifact audit IDs、locale、generator version、`layout_id`、ELK/layout version/license/hash，并逐字段一致。canonical graph identity 不含浏览器布局；panel ID 才同时包含 flow graph identity 与布局资源/config hash，避免内容身份和 presentation identity 混淆。

### Cross-Agent Patterns

- Program Lead 负责解释“为什么显示这个结论”、路由 refresh/open/archive，但不拼 HTML、不读写浏览器状态。
- `adp-management-panel` 直接调用时同样可用；interactive 返回面板路径，headless 返回稳定 JSON 运行结果。
- panel 发现 canonical input 缺失或陈旧时只返回 recovery workflows，不回退读取底层事实自行补算。
- `adp-flow-graph` 是 graph JSON 的唯一 writer；roadmap、meeting-pack、panel 和 Program Lead 只能消费或裁剪，不能重新聚合状态/计数。
- FDE/双周会视图消费 meeting-pack distillate，继续服从会议窗口、信息预算和不可裁剪类别；panel 不能绕过 meeting-pack 直接扩充主会场内容。
- source drill-down 展示 canonical reference、fingerprint、rule ID 和可复制路径；首版不从浏览器写回任何 action、decision、WDR 或 meeting receipt。

## Skills

### `adp-management-panel`

**Type:** workflow（新增）

**Core Outcome:** 从同一组 canonical 派生数据生成可直接打开、可筛选比较、可追溯且不重算结论的三视图静态管理面板。

**The Non-Negotiable:** HTML 与 manifest 中的状态、置信度、偏差、会议窗口和 lineage 必须逐字段来源于已验证的 canonical JSON；不得在 JavaScript 或模板中建立第二套业务判断。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Compose panel model | 校验并组合当前状态、roadmap、两个会议场景和有限历史 | project root、memory root、可选 as-of/history limit | panel model JSON、warnings、recovery workflows |
| Render static panel | 生成 file:// 可直接打开的自包含三视图页面 | panel model、locale、default view | immutable panel bundle + `views/management-panel/index.html` |
| Quantitative progress | 以 bullet chart、阶梯趋势和各 L 对比呈现同一完成度、计划差距和预测 | canonical progress projection、overall status/variance、reporting calendar | 负责人完整摘要/趋势/矩阵、FDE delta、双周会异常优先摘要 |
| Progress flow visualization | 以并行 lane、gate/milestone 节点和 dependency 边显示当前位置、状态与显式关联计数 | canonical `flow_graph`、view/window/filter、source lineage | 负责人完整/折叠图、FDE 当前窗口子图、双周会关键路径/决策门禁图 |
| Project lead view | 持续查看总体状态、门禁、关键路径、偏差、风险、决策和趋势 | canonical status/roadmap/history | 可筛选、排序、折叠、比较的负责人视图 |
| FDE meeting view | 直接过晨会窗口、delta、blocker、承诺、到期和升级 | latest confirmed FDE distillate | 会议模式视图，不扩张信息预算 |
| Business meeting view | 直接过双周会计划偏差、门禁、决策、readiness 和业务影响 | latest business distillate、roadmap | 结论先行的管理会议视图 |
| Source drill-down | 从每个结论下钻到 lineage、rule 和来源 | source references、fingerprints、audit IDs | 只读 source drawer、复制路径 |
| Compare periods | 对比有限个 immutable status snapshots | current snapshot、N 个历史 snapshot | overall/gate/milestone/variance delta comparison |
| Resolve meeting readiness | 区分项目状态、会议可用性和材料生命周期 | audit、freshness、window、meeting-sync receipt | ready/degraded/blocked + current/pre-meeting/post-sync lifecycle |
| Refresh / inspect / archive | 原子刷新最新面板、检查已有产物或按 distribution profile 归档 | mode、expected panel ID、archive profile | 运行结果 JSON、可选 immutable HTML/redaction manifest |

**Memory:** 只读 canonical views、snapshots、meeting distillates 和 audits；只写 management-panel 派生产物。

**Init Responsibility:** 目录缺失时创建派生视图目录；输入缺失时不生成伪空面板，返回最小 recovery chain。

**Invocation Modes:** interactive、headless。**Operations:** refresh、inspect、archive；默认 refresh。

**Tool Dependencies:** Python 3.10+、现代浏览器，以及随 skill 固定版本分发并校验 checksum/license 的 ELK.js browser bundle；用户运行/refresh 不需要 Node.js 或 npm，仍为零 CDN、零 server、零网络请求。

**Design Notes:** 使用系统字体与内联静态资源；所有源文本通过 JSON-safe embedding 与 DOM `textContent` 输出，禁止把源事实拼入可执行 JavaScript 或 `innerHTML`。ELK.js 只接收无业务语义的节点尺寸/拓扑并返回坐标与 edge sections；原生 SVG 通过 `createElementNS`/`textContent` 构造，状态、计数、交互和来源由 panel model 驱动，不使用 Mermaid runtime、HTML labels 或布局库事件。panel model 先结构校验，再渲染；immutable bundle 先落盘，current HTML 最后单点提交，再由 artifact audit 验证 embedded manifest/bundle/hash 一致性。

---

### `adp-plan-baseline`

**Type:** workflow（可靠性扩展）

**Core Outcome:** 面板展示的计划事实可安全创建、更新和恢复。

**The Non-Negotiable:** 非有限权重永不进入 baseline；流程图节点只能引用 approved milestone/gate，关系只能引用同 revision 的稳定 plan item ID；preview token 跨 preview/execute 稳定；中断后的 stale lock 有可审计恢复路径。

**Capabilities:** validate finite weights；stable preview identity；stale-lock inspect/recover；flow topology validation；legacy dependency normalization；stable relationship identity。

**Inputs / Outputs:** vNext 保留 milestone/gate 为唯一 plan-item 节点，并允许 gate 使用可选 `workstream_id`（program-level gate 使用 `program`）。旧版 `dependencies: ["ITEM-ID"]` 兼容归一化为 dependency edge；vNext dependency object 至少包含稳定 `edge_id`、`predecessor_id`、`relation_type`（`dependency|aggregation|conditional|rework|informational`）、可选 label/condition 和 source lineage。需要汇总的目标 milestone/gate 使用 `predecessor_rule: all`；v1.3 不实现 `any/quorum`。输出 baseline JSON/Markdown、revision diff、topology fingerprint 和明确 rule/recovery；关系不得携带 status、计数或展示颜色。

---

### `adp-program-status`

**Type:** workflow（可靠性与消费契约扩展）

**Purpose / Core Outcome:** 将 approved baseline、经过审计的 WDR milestone 事实和 reporting calendar 转化为唯一 canonical status/progress snapshot，使 panel、roadmap 和 meeting-pack 消费相同的当前完成度、计划差距、预测和可比性结论。

**The Non-Negotiable:** 完成度与计划健康度不得互相覆盖；future/unaudited actual、陈旧/未来 signal 不得驱动当前结论；缺失 forecast 不得回退为 planned；baseline/scope 不可比时不得生成连续趋势；previous snapshot 改变 delta 时必须改变 identity；不可变 snapshot 不得留下截断文件。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Progress eligibility gate | 只让已审计、时间有效且满足 completion criteria 的实际完成进入计算 | approved baseline、milestone actual/evidence/source、input audit、as-of | eligible/excluded milestone set、rule IDs、exclusion reasons |
| Current completion projection | 生成总体与各交付线的加权里程碑完成度和项目贡献 | eligible actuals、milestone weights、workstream IDs | actual/planned/completion gap、project weight、completed contribution |
| Planned and forecast series | 在 reporting-period 边界生成历史 planned/actual 与未来 forecast 阶梯序列 | reporting calendar、planned dates、valid forecast dates、as-of | horizon points、forecast completion、coverage percent/status、next milestones |
| Revision comparability | 阻断未经 rebase 的跨 baseline/scope 连续趋势 | current/previous snapshot、baseline revision、scope/weight fingerprints、correction lineage | comparable/revision-changed/scope-changed/no-predecessor disposition |
| Execution/health projection | 对 plan items 和 relationships 分别产生执行位置与健康度，不让浏览器把 ready、in-progress、risk 或 blocked 混成单一状态 | baseline topology、explicit milestone source status、actual/forecast、canonical risk/blocker、condition/decision facts | node `execution_state` + `health_state`、relationship state/health、active branch、rule IDs |
| Stable snapshot publication | 将 status、progress、lineage 和 delta 原子发布为一致产物 | validated model、locale、previous snapshot | immutable snapshot、current JSON/Markdown、weekly/project-lead views、artifact metadata |
| Panel projection contract | 为 roadmap、meeting-pack 和 panel 暴露版本化且无需重算的 progress 对象 | canonical status snapshot | `progress_schema_version`、overall、`by_workstream`、series、lineage、recovery |

**Inputs:** approved program baseline 与 weighting；WDR milestone planned/forecast/actual、completion criteria、source/evidence；通过完整性检查的 input audit；as-of、reporting period/cadence；可选 previous immutable snapshot。缺任一必要权重、审计或 scope identity 时不得降级为精确百分比。

**Outputs:** `program-status.json`/immutable snapshot schema vNext、Markdown status/weekly/project-lead 派生视图、稳定 run result、warnings/findings/recovery workflows。除 `progress` 外还输出 plan-item `execution_state`/`health_state`、relationship state/health 与 rule lineage，供 `adp-flow-graph` 组合。`progress` 至少包含：

```text
basis = weighted-milestone
measurement_status = measurable | partial | not-measurable | blocked
actual_completion_percent
planned_completion_percent
completion_gap_pp
project_weight_percent / completed_contribution_pp   # by_workstream
forecast_points[] = horizon_date + forecast_completion_percent
                    + forecast_coverage_percent + forecast_coverage_status
comparability = comparable | baseline-revision-changed | scope-changed | no-predecessor
scope_revision + weighting_fingerprint + value_lineage
```

**Formula and exceptional semantics:**

- Actual 只累计 `actual_date <= as_of`、completion criteria 已定义且 actual source/evidence 通过 input audit 的 milestone 权重。
- Planned 在每个 horizon 累计 `planned_date <= horizon` 的同 scope 权重；actual/planned/forecast 都是 milestone 阶梯序列，不做线性插值。
- `completion_gap_pp = actual_completion_percent - planned_completion_percent`；日期偏差继续使用 `variance_days`，二者禁止混名或互相替代。
- Forecast 在每个未来 horizon 累计已完成权重以及 `forecast_date <= horizon` 的未完成权重。coverage 分母为全部未完成权重，分子为具有有效 forecast 的未完成权重；剩余权重为零时 `forecast_coverage_percent = null`、status=`complete`，不执行除零也不伪装为普通 `100%` coverage。
- L1/L2/... 的线内完成率按该线权重归一化，同时输出其 project weight 和 completed contribution；L0 无 approved milestone weighting 时只输出 readiness/gate coverage，不进入完成率数组。
- 相同 inputs、locale、as-of 和 previous snapshot 必须产生相同值与 identity。实际完成度允许因有审计 lineage 的事实纠错而下降；无 correction/retraction lineage 的下降是 finding，不以“永不下降”掩盖真实纠错。
- baseline revision、scope 或 weighting fingerprint 改变时保留断点并标记不可直接比较；只有显式 rebase 产物才能恢复连续趋势。
- Node execution state 为 `planned|ready|in-progress|complete|not-applicable`：eligible actual -> complete；显式 source status `in-progress` -> in-progress；未完成且全部 active required predecessors 已满足 -> ready；仍等待 predecessor -> planned。`ready` 只表示可开始，不冒充正在执行；并行 lane 可以同时存在多个 ready/in-progress 节点。
- Node health state 独立为 `on-plan|at-risk|blocked|indeterminate`，来自 canonical constraint/risk/blocker rules。节点可以同时是 `in-progress + at-risk` 或 `ready + blocked`；UI 不得用单一枚举丢失其中一个维度。
- Relationship state 为 `inactive|pending|active|satisfied|indeterminate`，health 仍为 `on-plan|at-risk|blocked|indeterminate`。aggregation 只参与目标节点 `predecessor_rule: all`，不改变来源 lane 的继续条件；conditional/rework 只有在显式 condition/decision fact 存在时激活，否则 indeterminate。panel 不得从颜色自行选择分支。

**Design Notes:** 进度算法全部位于 Python canonical workflow，不在 roadmap renderer、HTML template 或 JavaScript 重复实现。字段名显式携带 `percent`/`pp` 单位，展示层本地化 label 不改变机器字段。当前 v1.2 `weighted_completion_percent` 迁移到 vNext `progress` 时提供兼容读取或确定的迁移错误。

**Relationships:** 在 `adp-plan-baseline`、`adp-status-sync`、risk/dependency review 和 `adp-state-audit` 之后运行；其 snapshot 是 `adp-roadmap-sync`、`adp-flow-graph`、`adp-meeting-pack`、`adp-management-panel` 和 Program Lead readout 的唯一进度/节点状态输入。下游只能筛选、编排和解释，不得重新聚合 milestone 权重或状态。

---

### `adp-state-audit`

**Type:** workflow（扩展）

**Core Outcome:** 在生成前验证 panel inputs，在生成后验证 HTML、manifest、embedded data、locale、freshness 和 lineage。

**The Non-Negotiable:** malformed intake 必须成为 finding；缺少 receipt input hash 不得视为已处理；JSON/Markdown/HTML 多产物发布失败不得留下互相不一致的“最新”组合。

**Capabilities:** panel input audit；panel artifact validation；malformed intake reporting；receipt identity validation；atomic artifact set validation；baseline topology/relationship validation；flow graph reference/count/lineage validation；ELK asset checksum/license/version validation。

---

### `adp-status-sync`

**Type:** workflow（可靠性扩展）

**Core Outcome:** panel 刷新前，action、daily 和 WDR 事实不会处于自相矛盾的部分提交状态。

**The Non-Negotiable:** 先验证全部更新再提交；unsupported action status 显式失败，不静默降级为 open。

**Capabilities:** strict enums；preflight validation；transaction-like staged writes；failure receipts/recovery；允许 WDR milestone source status `in-progress` 并要求 started-at/source evidence；显式保存 action 的 `Related Plan Items`/`Related Flow Edges`、`Status Changed At` 和 `Done At`，拒绝未知、跨 revision 或占位 ID。open/in-progress action 计入 pending，done 只在对应 window 内计入 processed，blocked 单列；cancelled 不计入 processed。

---

### `adp-meeting-sync`

**Type:** workflow（可靠性扩展）

**Core Outcome:** 会议视图的增量锚点和闭环事实不会因非法日期或并发同步失真。

**The Non-Negotiable:** meeting date 必须是真实日期；同一或不同 meeting instance 并发更新共享记录时不得丢写、重复写或互相覆盖。

**Capabilities:** semantic date validation；per-project/meeting locks；unique temporary paths；replay/concurrency tests。

---

### `adp-roadmap-sync` 与 `adp-meeting-pack`

**Type:** workflows（panel producer integration）

**Core Outcome:** 为 panel 提供版本化、稳定、场景明确的 JSON 输入，而不是额外 HTML owner。

**The Non-Negotiable:** 不在 panel 集成中改变既有状态算法、会议窗口或信息预算；JSON schema 变更必须版本化并有兼容测试。

**Capabilities:** `adp-roadmap-sync` 继续只输出 timeline、baseline diff、日期 variance、panel-ready schema metadata 和 lineage，消费 status schema vNext 但不读取 action/risk 形成图计数。`adp-meeting-pack` 消费 `adp-flow-graph`，按 confirmed meeting window/critical path/information budget 选择 subgraph 与 window counts，不重算 topology、execution/health state 或关联关系。

---

### `adp-risk-dependency-change-review`

**Type:** workflow（flow overlay contract 扩展）

**Core Outcome:** active risk/dependency 具备稳定 identity、生命周期、时间和显式 plan-item/edge 关联，可被 flow graph 计数而不依赖文本猜测。

**The Non-Negotiable:** risk badge 只能来自稳定 Risk ID、canonical active/closed state 和已验证关系；workstream/owner/文本相似不能自动映射到节点或边。

**Capabilities:** stable risk/dependency IDs；`Related Plan Items`/`Related Flow Edges`；opened/updated/closed timestamps；active-as-of 与 reporting-period delta projection；unknown/cross-revision reference rejection；unmapped preservation。

**Inputs / Outputs:** 输入为 confirmed risk/dependency/change facts、baseline revision 和 source evidence；输出版本化 risk/dependency JSON/Markdown、stable IDs、lifecycle timestamps、related IDs、source lineage 和 recovery findings。只写风险/依赖事实，不写 graph、颜色或 badge counts。

**Relationships:** 在 `adp-flow-graph` 之前运行；`adp-status-sync` 仍独占 action ledger，`adp-program-status` 独占 health judgment，`adp-flow-graph` 只聚合显式关系。

---

### `adp-flow-graph`

**Type:** workflow（新增，deterministic producer）

**Core Outcome:** 将 approved baseline topology、canonical execution/health state 和 scoped action/risk overlays 组合为唯一可审计 `flow-graph.json`，供 panel 与 meeting-pack 消费。

**The Non-Negotiable:** workflow 不改变计划、状态、风险或 action；不从文本推断映射；不产生浏览器坐标、颜色或 ELK layout。缺失/冲突引用必须进入 unmapped/finding，不得静默丢弃或自动重连。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Topology projection | 从 approved baseline 生成 lanes、milestone/gate nodes 和 typed relationships | baseline revision、plan items、dependencies、predecessor rule | normalized topology、`topology_id`、topology lineage/findings |
| State join | 给 node/edge 附加正交执行位置与健康度 | program-status snapshot、plan-item/relationship IDs | `execution_state`/`health_state`、relationship state/health、rule lineage |
| Scoped overlay aggregation | 按固定 scope 聚合显式关联的 action/risk/blocker/processed facts | action ledger、risk/dependency output、as-of/reporting period、related IDs | `active_as_of` 与 `reporting_period` counts、overlay items、unmapped counts |
| Identity and publication | 分离 topology/state/overlay identity 并原子发布 current/immutable outputs | normalized graph、source fingerprints、audit | `topology_id`、`state_snapshot_id`、`overlay_snapshot_id`、`flow_graph_id`、snapshot/current JSON |
| Subgraph support | 为下游提供无需重算的 lane/critical/exception/window selection facts | graph nodes/edges、critical path、overlay timestamps | stable selectors、source-preserving subgraph inputs |

**Inputs:** approved baseline vNext；canonical program-status snapshot；action ledger（含 related IDs、status-change/done timestamps）；risk/dependency JSON（含 stable IDs/lifecycle/related IDs）；input audit；as-of/reporting period；可选 previous immutable graph snapshot。

**Outputs:** `snapshots/flow-graph/<flow-graph-id>.json`、`views/flow-graph.json`、headless run result、warnings/findings/recovery。graph 至少包含 lanes、nodes、edges、两类标准 overlay scope、allowlisted overlay items、unmapped summaries、source fingerprints 和四类 identity。meeting window counts 由 meeting-pack 对 allowlisted overlay timestamps 做场景选择，不回写 canonical graph。

**Count semantics:** `active_as_of.pending` 为 as-of 时 open/in-progress action，`active_as_of.blocked` 为 blocked action，`active_as_of.risk` 为 active risk；`reporting_period.processed` 只统计 `Done At` 落入 reporting period 的 action。cancelled 不计 processed。计数变化只改变 `overlay_snapshot_id/flow_graph_id`，不得改变 `topology_id`。

**Design Notes:** 保持 graph producer 与 ELK renderer 分离；graph 不包含 layout coordinates、presentation colors 或 redaction decisions。`layout_id` 由 panel 基于 topology、固定 node dimensions、locale、ELK version/options 生成。风险/action 映射不可用时仍发布明确 degraded graph 与 unmapped counts，是否 blocked 由 audit disposition 决定。

**Relationships:** 在 baseline/status-sync/risk review/program-status/input audit 之后运行；roadmap 与它并列消费 baseline/status，但互不读写；meeting-pack、management-panel、Program Lead 和 state-audit 消费其输出。

---

### `adp-agent-program-lead`

**Type:** agent（路由扩展）

**Core Outcome:** 用户可通过一个入口刷新、打开、解释和归档管理面板。

**The Non-Negotiable:** agent 只消费 manifest/canonical status 并路由 workflow，不自己渲染页面或接受陈旧 Markdown 作为 canonical view。

**Capabilities:** panel readiness check；refresh/open/archive routing；view-specific explanation；legacy CLI compatibility decision and migration warning。

---

### `adp-project-kickoff` 与 `adp-setup`

**Type:** workflows（安装与升级扩展）

**Core Outcome:** fresh/update 安装具备 panel 目录、配置、help entry、资源检查和非破坏升级报告。

**The Non-Negotiable:** setup/kickoff 不覆盖已有 HTML、manifest、snapshot 或项目事实；旧项目只补缺失结构并报告首次 refresh prerequisites。

**Capabilities:** scaffold directories；register `adp-management-panel`；collect config；inspect assets/browser-independent runtime；v1.2 -> v1.3 migration report。

## Configuration

保留 v1.2 的四个变量。新增：

| Variable | Prompt | Default | Result Template | User Setting |
| --- | --- | --- | --- | --- |
| `management_panel_history_periods` | 面板默认嵌入多少个历史状态周期？ | `12` | `management_panel_history_periods: {value}` | team |
| `management_panel_default_view` | 直接打开面板时默认进入哪个视图？ | `project-lead` | `management_panel_default_view: {value}` | user/team |
| `management_panel_archive_mode` | 何时保存不可变 HTML 归档？ | `meeting-only` | `management_panel_archive_mode: {value}` | team |

允许值：default view 为 `project-lead|fde-morning|business-biweekly`；archive mode 为 `explicit|meeting-only|always`。单次 archive 还必须选择 `internal-full|shareable-summary` distribution profile；它是运行时安全边界，不作为容易被遗忘的长期团队默认。输出路径和单次历史数量可由 CLI override。

## External Dependencies

- Python 3.10+，沿用现有 ADP runtime。
- 现代桌面浏览器，要求支持 ES2020、CSS Grid、`details`、`dialog` 或等价无脚本 fallback。
- ELK.js browser bundle 作为固定版本第三方资源随 skill 分发并嵌入 self-contained HTML，仅负责 layered/compound layout、ports 和 orthogonal edge routing；manifest/audit 记录版本、license 和 SHA-256。用户运行与 panel refresh 不需要 Node.js/npm。
- 不需要 Mermaid runtime、Web server、数据库、CDN、字体服务、图表服务或第三方网络请求。Mermaid 仅是需求示意，不是 runtime contract。

## UI and Visualization

### Shared shell

- 顶部固定状态带：project、as-of、overall status、confidence、baseline revision、freshness、last generated。
- 三个视图共享 canonical progress model 和字段语义，但不强制共享同一个进度组件或信息密度。负责人和双周会显示完成度/计划/差距/forecast；FDE 晨会只显示 confirmed meeting window 内的 progress delta 和受影响 milestone。任一数值不可度量时显示原因和缺失覆盖率，不显示 `0%`。
- 三段式视图切换：项目负责人 / FDE 晨会 / 业务双周会；切换只改变编排，不改变事实。
- 全局筛选：workstream、status、owner、period；支持清除与结果计数。
- source drawer：显示 source reference、fingerprint、snapshot/audit/rule IDs 和复制路径。
- 静态 stale state：manifest 与输入不一致、缺 meeting pack、低置信度、audit blocked 均使用明确状态条和 recovery workflow，不显示“正常绿色”。
- Meeting readiness `ready|degraded|blocked` 与 artifact lifecycle `current-derived|pre-meeting-snapshot|post-sync-official` 独立于 project status，所有 meeting view、print 和 archive 持续可见。

### Progress semantics and contract

- **双层判断：** 加权里程碑完成度回答“完成了多少”；overall status、gate、critical path 和 `variance_days` 回答“是否按计划”。两个结论并列显示，任何完成百分比都不得覆盖 blocked/off-plan 状态。
- **实际完成进度：** 只统计达到 completion criteria 且已有 actual/evidence 的 milestone 权重；总体分母为全部适用 milestone 权重，各交付线分母为该线全部适用 milestone 权重。实际值是证据化完成度，不以主观填报百分比补齐。
- **计划完成进度：** 截至指定日期，按 approved baseline 中 `planned_date <= date` 的 milestone 权重累计；与实际完成使用同一 scope、权重和分母。
- **曲线含义：** actual、planned 和 forecast 都是 milestone 权重驱动的阶梯序列，不在两个 milestone 之间做线性插值，也不冒充工时消耗或连续 earned value。
- **完成差距：** `completion_gap_pp = actual_completion_percent - planned_completion_percent`，单位为百分点；面板不把 `completion_gap_pp` 与日期 `variance_days` 混成一个指标。
- **未来预计进度：** 对未来报告周期边界，以已完成 milestone 加具有有效 forecast date 的未完成 milestone 权重形成 forecast series；不得静默用 planned date 替代缺失 forecast。coverage 为“有有效 forecast 的未完成权重 / 全部未完成权重”，必须与 forecast 同时显示；项目已完成时 coverage 为 not-applicable/complete。
- **各 L 口径：** L1/L2/... 按 workstream 归一化，并与该线在项目中的总权重/已完成贡献并列展示，避免小权重交付线的 `100%` 被误读为与大权重交付线等量；没有 milestone 或权重的交付线显示 not measurable。L0 当前是 BMM 管理的参考基线/门禁来源，默认展示 gate/readiness coverage，不给出虚假的完成百分比；仅当 L0 上游提供获批 milestone 权重和 actual evidence 时才进入同一进度口径。
- **不可用与纠错边界：** baseline weighting 未启用、权重不完整、snapshot stale、scope/revision 不可比或审计阻断时，不显示精确进度或连续趋势；保留 milestone count、缺口原因、revision 断点和 recovery。完成度因受审计事实纠错而下降时明确标记 correction，不伪装为正常趋势波动。
- **Canonical model：** `program-status.json` 输出明确带单位的 overall 与 `by_workstream` 当前值、历史 actual/planned series、未来 forecast points、coverage status、comparability、scope/weighting identity 和逐值 lineage；panel 只格式化、筛选和下钻。

### Quantitative progress visualization

- 主图不用环形进度条。环形图无法在同一尺度上清晰比较 actual、planned 和 forecast，也难以呈现负 gap、coverage 和 revision 断点。
- **横向 bullet chart：** actual 使用实心进度条，planned 使用同一 `0..100%` 轴上的明确标记线，next-period forecast 使用点线延伸与端点标记；数值区固定显示 actual、planned、`completion_gap_pp`、forecast + coverage。overall status/关键门禁在相邻独立区域显示，不用进度色替代项目状态。
- **Milestone 阶梯趋势：** planned、actual 和 forecast 使用同一时间/百分比坐标，保持 milestone 驱动的阶梯形；forecast 使用不同线型，baseline revision/scope change 使用断点和 revision label，不跨断点画连续趋势。
- **各 L 对比：** 表格或对齐条形行显示 project weight、completed contribution、线内 actual/planned/gap、next-period forecast/coverage、日期 variance 和 status。L0 行显示 gate count/readiness，不显示完成率。
- 所有图形与旁边的可读数值、表格和 source anchors 共享同一 DOM 数据；图形不是唯一信息载体。筛选某个 L 后，bullet/趋势/表格同步改变 scope 并持续显示 scope identity。

### Progress flow visualization

- 流程模式与数值进度并列存在，通过视图内 segmented mode control 在“进度摘要 / 流程图”之间切换；它不新增第四个顶层受众视图。负责人默认可看完整图，FDE/双周会默认保持各自摘要，需要时进入裁剪后的流程模式。
- **数据边界：** 节点只来自 approved baseline milestone/gate；lane 来自 workstream/program 归属；边只来自同 revision 的 dependency object。普通“开发/测试”等阶段若不是 milestone/gate，只能作为 edge label/phase annotation，不获得虚假 status 或完成率。
- **布局与渲染：** ELK layered layout 使用 RIGHT direction、model order、compound lanes/ports 和 orthogonal routing 计算坐标；原生 inline SVG DOM 绘制节点、连线、marker、badge 和 label。相同 graph identity/filter/layout version 必须得到稳定布局；布局库升级会改变 generator/layout version 和 panel ID。
- **节点语义：** milestone 使用圆角终态节点，gate 使用菱形/明确 gate icon。execution state 以 fill/主要 label 表达：complete=绿色、in-progress=蓝色、ready=蓝色轮廓、planned=中性灰、not-applicable=弱化；health state 以独立 border/icon/text 表达 on-plan/at-risk/blocked/indeterminate。一个节点可同时显示 `in-progress + at-risk` 或 `ready + blocked`，不得压成单一颜色状态；ready/in-progress 共同构成 current frontier。
- **连线语义：** dependency 为实线，aggregation 为虚线并指向使用 `predecessor_rule: all` 的汇总目标，conditional 显示条件 label，rework 使用回路/返工 label，informational 使用轻量点线。relationship state 与 health 分别用线型/强调和风险文本表达；panel 不从节点颜色推断边状态或选择分支。
- **计数徽标：** 节点或连线上支持 `pending|processed|risk|blocked`，但默认只显示非零 blocked/risk/pending，processed 仅在 current frontier、所选节点或详情中出现，避免四类 badge 铺满全图。计数必须标明 scope（active-as-of、reporting period 或 meeting window），只来自显式 `Related Plan Items`/`Related Flow Edges`；点击/聚焦打开过滤后的 source drawer。unmapped 计数在图外独立显示。
- **视图裁剪：** 项目负责人显示 program spine + 可折叠 L lanes，可聚焦单个 L、关键路径或异常路径；FDE 只显示 confirmed meeting/action window 涉及的节点、边和一层上下游上下文；业务双周会显示 program spine、关键路径、异常 L、关键门禁和待决策分支，不默认展开所有工程节点。
- **超宽与响应式：** 默认可见节点预算不超过 40；150-node/250-edge 只作为性能/完整展开测试，不是默认信息密度。默认 fit-to-width program overview，提供 lane filter、fit/reset、zoom/pan、展开/折叠和 current-frontier 导航；窄屏/400% reflow 降级为按 lane 分组的纵向 stage list。节点使用按 kind/density 固定的尺寸与受控行数，完整 label 进入 tooltip/detail，避免系统字体测量或计数变化导致全图抖动。
- **无障碍与 no-JS：** SVG 使用 title/desc、role 和可感知 focus，同步提供语义化 lane/stage list 与 source anchors；键盘用户可从列表选择节点并定位图形。JavaScript 关闭或 ELK layout 失败时，展示预渲染的依赖顺序表、当前节点、状态与计数，不显示空白画布。
- **打印/归档：** 打印当前 flow scope，展开 legend、identity、状态/计数说明和来源摘要；完整 program overview 与单 L detail 可分页，避免把超宽全图缩成不可读缩略图。归档内嵌 graph model、ELK asset/version/hash 和 fallback，不依赖网络。

### Project lead view

定位为持续控制与诊断视图，回答“现在完成多少、按今天本应完成多少、差距发生在哪条线、为什么、下一周期会到哪里、流程走到哪里”。进度摘要模式首屏固定为四项答案（actual、planned、completion gap、next-period forecast + coverage）与横向 bullet chart，并在相邻位置独立显示 overall status/关键门禁；随后显示 milestone 阶梯趋势和各交付线进度矩阵。流程模式显示完整 program spine、可折叠 L lanes、current frontier（ready/in-progress）、critical/异常路径和 scoped 节点/边计数。未来第 2-3 个报告周期、目标/预测日期、Top 偏差、roadmap、风险/依赖/readiness、待决策和完整 workstream 表进入第二阅读层。点击某个 L 或 flow node 再展开里程碑、原因和来源。

### FDE morning view

定位为当日执行闭环视图，回答“自上次会后变了什么、今天卡什么、谁今天做什么”。首屏固定显示已确认窗口、since-last-meeting progress delta、当前 completion gap、今日 blocker/承诺/到期/跨线升级及涉及 L；不常驻显示总体长期 forecast 或全量交付线排名。流程模式仅显示窗口内节点/边、一层依赖上下文和关联计数；只有 forecast milestone 落入 confirmed meeting/action window 或直接影响今日 blocker/commitment 时才出现。历史 roundtable、长期趋势、完整 flow graph、source inventory 和被信息预算裁剪的条目进入折叠区；会议模式提供大屏可读密度，但不隐藏 audit/confidence。

### Business biweekly view

定位为管理判断与资源/范围决策视图，回答“是否按计划、差多少、哪些交付线拖动结果、下个双周预计到哪里、需要管理层决定什么”。首屏以 overall status/主要驱动原因为结论，配套显示 actual/planned/completion gap、下一双周 forecast + coverage、目标/预测日期、Top 异常交付线和待决策；第二个未来周期和完整矩阵进入展开层。流程模式只显示 program spine、关键路径、异常 L、关键门禁与决策/返工分支，节点/边计数聚焦业务影响，不默认铺开工程细节。后续显示 readiness、business impact、roadmap、风险依赖、上次会议闭环和周期比较。

### Visual and interaction floor

- 工作型浅色界面，使用中性灰白表面、深色正文和有限状态色；状态色必须同时有文本/图标，不依赖颜色单独传达。
- 8px 以内圆角，页面 section 不做漂浮卡片；单项摘要、状态块和 drawer 才使用有边界容器。
- 系统字体、0 letter spacing、稳定表格列宽与 panel 高度；不使用渐变、装饰性大标题、图像或营销式 hero。
- 键盘可完成视图切换、过滤、排序、展开和关闭 drawer；焦点顺序与阅读顺序一致，WCAG 2.2 AA。
- 所有源文本只能通过 text node 输出；HTML/CSS/JS 模板不得在 runtime 直接插值源文本。embedded JSON 必须转义 `<`、`</script>`、U+2028/U+2029；SVG 只通过 allowlisted `createElementNS` 元素/属性和 `textContent` 构造，禁止 `foreignObject`、event attributes、external href 和 source-provided CSS。
- View navigation 以真实 anchors/section IDs 作为 no-JS baseline；JS enhancement 保留 direct hash、Back/Forward 和 reload。Source evidence 先预渲染为 native `details`/anchors，再增强为 dialog。
- Data tables 使用 caption、column/row headers、sortable header button 与 `aria-sort`；移动端 labeled rows 的 labels 必须在 DOM 中。Sticky regions 共享 offset，在 zoom/narrow layout 停用叠加 sticky。
- Workbench density 使用 14/13/12px body/table/meta；meeting presentation density 至少使用 18/16/14px 与 44px controls，并在 1280x720、1920x1080 投屏验收。
- Meeting mode 对 status/quality badge、icon button、sortable header、view/filter/compare/source controls 使用显式 presentation token override；computed-style tests 禁止 load-bearing 内容残留 12px/36px workbench token。
- Minimum print contract 只打印当前 view/filter，展开 core facts、重复 table headers、移除 scroll clipping/sticky，并保留 status/confidence/freshness/audit/readiness/lifecycle、snapshot/pack identity 和 filter summary。
- UI state 使用 versioned allowlisted URL hash，不写入 source text 或 absolute paths；clipboard 失败时提供 selectable-text/manual-copy fallback。
- Direct view hash 是 no-JS baseline；JS 初始化后归一化到 versioned hash。Free-text search 不持久化；no-JS print 使用 `:target`，无 target 默认 project-lead。
- Archive 明确 `internal-full|shareable-summary` distribution profile；manifest 记录 allowlist/redaction，绝不把“offline”默认解释为“可外发”。
- `shareable-summary` 对 flow graph 执行 node/edge/owner/count/source allowlist；被隐藏的节点或边不得自动重连，必须显示“部分拓扑已隐藏”和 redaction count，避免生成看似完整但事实错误的依赖路径。

### Validation matrix

必须覆盖 Chrome/Edge `file://`、JavaScript on/off、mouse/keyboard/screen-reader smoke、Back/Forward/direct/versioned/malformed hash、中文 search、print-to-PDF；1920x1080、1280x720、768px、320 CSS px、desktop 200% zoom 与 400% reflow；长中文、长英文、混合文本、长路径/ID、100-row table；fresh/low-confidence/stale/degraded/blocked/indeterminate/schema mismatch/no predecessor；weighting disabled/invalid、无 milestone 的交付线、L0 无进度口径、forecast coverage full/partial/none/complete、零 remaining weight、baseline scope/revision change、显式 rebase、带/不带 lineage 的 actual correction、actual/planned/forecast `<=` 边界日期；confirmed/needs-confirmation/pre-meeting/sync-failed/post-sync-official meeting lifecycle；normal/forced-colors/reduced-motion；meeting computed styles 与 tooltip hover/focus/Esc。

进度 contract 还必须以 property/golden tests 验证：所有百分比位于 `0..100`；`completion_gap_pp` 恒等于 actual 减 planned；总体 completed contribution 等于各 L contribution 之和；同 inputs/identity 输出稳定；同 baseline 且没有 correction 时历史 actual 阶梯不倒退；有审计 correction 时允许下降并保留 lineage；缺 forecast 不得使用 planned 填充；scope/revision 不可比时不输出连续 delta；JSON、Markdown、roadmap、meeting distillate 与 panel 对同一 snapshot 逐值一致。

流程图 contract 必须覆盖：单/多 L 并行 lane、program-level gate、dependency/aggregation/conditional/rework/informational、`predecessor_rule: all`、显式 rework cycle、跨 lane/跨 hierarchy 边、孤立节点、未知/重复/cross-revision edge ID、非法 dependency cycle、缺 condition fact、baseline revision diff、unmapped overlays；node execution/health 与 relationship state/health 可正交组合，ready 不冒充 in-progress。active-as-of 与 reporting-period scope 的 pending/processed/risk/blocked 计数必须与 canonical sources 逐项相等，cancelled action 不冒充 processed。

identity tests 必须分别验证：拓扑变化只改变 `topology_id` 及其下游 identity；状态变化不改变 topology；计数变化只改变 `overlay_snapshot_id/flow_graph_id`；locale/node dimensions/ELK config 只改变 panel `layout_id`。相同 topology/filter/layout identity 的 node order、coordinates 与 routed edges 稳定；ELK asset missing/tampered/layout reject/timeout 时降级为语义 stage list，不发布空白图或旧图。

浏览器测试还必须覆盖：bullet/阶梯/流程三种图的长 label、badge 0/1/99+、图例、lane 折叠、fit/reset/zoom/pan、节点 focus/source drawer、筛选后 scope identity、forced colors、打印分页；默认不超过 40 visible nodes，150-node/250-edge fixture 仅测试完整展开性能且不得阻塞主交互或产生布局重叠。恶意 label/edge text 不得生成 `script`、`foreignObject`、event attribute、external href 或 source CSS；JavaScript off、layout failure 和 320px/400% reflow 均能读取等价依赖顺序、双轴状态、scoped counts 和来源。`shareable-summary` fixture 必须验证隐藏节点不重连、redaction count/manifest 可见且内部 ID/owner/count/source 不泄漏。

## Setup Extensions

- 模块版本升级到 `1.3.0`，plugin marketplace 同步版本。
- 安装 `adp-flow-graph`、`adp-management-panel`、risk overlay 扩展、HTML template/catalog、panel/flow graph schemas 与固定版本 ELK.js browser bundle；help registry 按 baseline/status-sync/risk/program-status/roadmap/flow-graph/meeting-pack/panel 顺序注册。
- kickoff 幂等创建 `views/management-panel/`、`snapshots/management-panel/`、`snapshots/flow-graph/`，不写 placeholder JSON/HTML 冒充有效产物。
- inspect-install-state 检查 skills、template、locale/catalog、panel/flow graph schemas、action/risk related-ID contract、ELK version/license/SHA-256、三个新配置值和 migration needs。
- update 模式保留已有 v1.2 memory；首次 panel refresh 只在 status、roadmap、flow-graph、meeting inputs 通过 audit 后生成。

## Integration

ADP 保持 standalone。HTML 面板只是 canonical artifacts 的新消费面，不依赖 BMM、Web 框架或外部服务。BMM checkpoint 等集成继续写入既有 ADP intake/facts，再由 baseline/status-sync/risk/status/flow-graph/audit/panel 链消费。

Markdown 与 JSON 不废弃：JSON 是机器契约，Markdown 是可 diff、可归档和无浏览器 fallback，HTML 是高密度管理阅读与会议操作面。三者必须由同一 canonical model 派生并共享 lineage。

## Creative Use Cases

- 会前负责人先在 project-lead 视图检查数据新鲜度，再切到会议视图，避免现场才发现 status stale。
- 会议中点击某个偏差打开 source drawer，不离开主视图即可确认 baseline revision、rule ID 和证据路径。
- 选择任意两个周期比较状态、门禁、里程碑 forecast 和 confidence 变化，回答“什么时候开始偏离计划”。
- 将会议时点归档为单文件 HTML，离线发送给无 ADP 环境的审阅者，同时保留 lineage 与生成版本。
- 未来 portfolio 面板可以汇总多个项目各自的 `program-status.json`，但 v1.3 不读取跨项目事实。

## Ideas Captured

本节保留 discovery provenance，不是实现合同；与结构化章节冲突时，以 Vision、Architecture、Skills、UI and Visualization 和 Build Roadmap 为准。

- v1.2.0 遗漏了 HTML 视图作为正式管理消费面；下一版本需要结合 baseline、program-status、roadmap、audit 和 meeting-pack 的新契约重新规划，而不是给旧 Markdown 套一层样式。
- Markdown 继续承担可移植、可 diff、可归档的叙事与审计摘要，但长表格、宽字段、跨周期比较、来源下钻和会议信息预算不应继续依赖单一 Markdown 页面承载。
- HTML 面板应从 canonical JSON 与稳定 lineage 渲染，不重新计算状态，不成为事实源，也不允许和 Markdown 得出不同项目结论。
- 下一版本计划需要同时吸收 `deferred-work.md` 中的可靠性债务，优先处理会破坏计划、快照、审计、状态同步或会议闭环可信度的问题。
- 面板的目标使用者至少包括项目负责人、业务双周会参与者和 FDE 晨会参与者；不同受众应共享事实层但拥有不同默认视图和信息密度。
- 需要明确 HTML 是静态可分发产物、本地交互式应用，还是两者组合；部署、安全、离线、归档和刷新语义将由此决定。
- 用户确认首版需要三个同级工作视图：项目负责人随时查看的持续管理视图、直接用于过会的 FDE 晨会视图、直接用于过会的业务双周会视图。
- 三个视图共享 canonical facts、状态结论和 lineage，但不能共享同一信息编排；项目负责人关注持续态势，晨会关注增量与今日闭环，双周会关注计划偏差与管理决策。
- 用户倾向日常操作以“刷新面板”为主，但在看清内容组成与交付架构前不预先决定是否还需要静态 HTML 再生成。
- 首版明确需要筛选、排序、折叠、来源下钻和跨周期比较；这些是解决长表格与历史信息淹没问题的核心能力，不是后续装饰性增强。
- 当前 canonical `program-status.json` 已可支撑共享页头和负责人视图：总体状态、置信度、目标日期、加权进度、门禁、里程碑、关键路径、Top 偏差、周期变化、audit 摘要和完整 lineage。
- 当前 `roadmap.json` 已可支撑时间线与比较：planned / forecast / actual / variance、baseline revision changes、未排期与未映射事项、决策阻塞、风险日期和排除项。
- 当前两类 meeting-pack distillate JSON 已可支撑会议视图：FDE 的确认窗口、周期 delta、blocker、承诺、到期和跨线升级；双周会的总体结论、门禁、Top 偏差、待决策、readiness、roadmap、业务影响和上次会议闭环。
- 用户指出现有视图说明虽然散落提到加权进度、planned/forecast/actual 和周期比较，但没有形成一眼可读的统一进度叙事；三个视图都需要共享总体进度事实，负责人和双周会还必须突出各交付线进度、actual-vs-planned gap 与后续周期 forecast。
- 进度百分比必须有严谨边界：L1/L2/... 可按 approved milestone weighting 归一化；L0 默认是参考基线/门禁维度而非由 ADP 管理的交付完成度，缺少上游权重与 actual evidence 时不得显示伪精确百分比。
- 跨角色评审确认方案方向成立，但要求把“完成了多少”和“是否按计划”拆为独立结论；共享 canonical model 不等于三个视图复用同一进度组件，尤其 FDE 晨会不常驻展示长期 forecast。
- 评审要求 canonical progress projection 在 panel model 之前冻结并实现，显式定义 forecast coverage、baseline revision 断点和纠错 lineage；同时修正两个易误导说法：完成差距字段使用 `completion_gap_pp` 而非 `schedule_gap_pp`，actual 只在无 correction 的同 baseline 历史中要求不倒退。
- 用户追加流程进度可视化：以 baseline milestone/gate 为节点、dependency 为边，显示并行 L、汇聚、条件分支、返工回路和 current path；节点/边用状态色+文本/图标，并显示显式映射的 pending/processed/risk/blocked 计数。Mermaid 示例只表达需求，不限定实现技术。
- 数值进度与流程进度必须同时保留：主数值图采用横向 bullet chart + milestone 阶梯趋势 + 各 L 对比，不使用环形进度条；流程图作为三个既有视图内的第二 visualization mode，负责人看完整/折叠图，FDE 看窗口子图，双周会看 program spine/关键路径/异常和决策门禁。
- 技术选择为固定版本、离线分发的 ELK.js layered layout + 原生安全 SVG DOM。布局库不接触业务状态；图形拓扑、双轴状态和 scoped overlays 由独立 `adp-flow-graph` canonical projection 产出，无明确 plan-item/edge 引用的 action/risk 不猜测映射，保留为 unmapped overlay。
- Workflow Builder 评审进一步拆分责任：execution position 与 health 正交；`ready` 不等于 `in-progress`；aggregation 的 `all` 完成规则属于目标节点；roadmap 保持 timeline owner，新增 `adp-flow-graph`；计数区分 active-as-of/reporting-period/meeting-window；topology/state/overlay/layout identity 分层；shareable graph 执行字段级脱敏且不重连隐藏路径。
- 已被后续决策替代的候选：仅绑定 loopback 的本地只读面板。最终选择为无 server 的自包含静态 HTML，并保留可选 immutable archive。
- 候选边界：面板不直接读取或暴露全部 ADP memory，不在浏览器重算状态，不直接修改事实；数据陈旧时明确显示 stale/blocked 并路由到对应生成 workflow。
- 候选交互：三个视图共用筛选、排序、折叠、跨周期选择和 source drawer；会议视图继续服从 meeting-pack 的窗口确认、信息预算、不可裁剪类别和稳定排序规则。

## Build Roadmap

### 1. Close P0 Data Trust Debt

按事实写入顺序修复 baseline -> status/snapshot -> audit -> status-sync -> meeting-sync。每项新增失败路径测试和跨进程/中断测试；完成后重跑全部 ADP tests。

**完成门：** deferred 中所有 P0 条目有回归测试；失败不留下部分事实、截断 snapshot、错配 artifact pair 或错误 receipt；future/stale 数据不能驱动 current status。

### 2. Freeze Canonical Progress Contract

冻结 `program-status` progress schema vNext、字段单位、actual eligibility、planned/forecast 阶梯公式、coverage、L1+ rollup、L0 边界、revision comparability、correction lineage、兼容和 recovery contract。先写 JSON fixtures、公式 property tests 和 golden outputs，不定义 panel 视觉模板。

**完成门：** overall/by-workstream actual、planned、completion gap、forecast、coverage、comparability 和不可度量原因具有明确 schema；边界/纠错/revision fixtures 通过；consumer 只需读取字段即可展示，不需要猜公式。

### 3. Implement Canonical Progress Projection

扩展 `adp-program-status` 解析、校验、projection、snapshot identity、原子发布、Markdown renderer、locale catalog 和兼容路径；让 roadmap/meeting-pack 先切换为消费 schema vNext。

**完成门：** 当前 v1.2 overall fixture 兼容或得到确定迁移结果；schema/property/golden tests 全部通过；相同 inputs 生成相同 projection/snapshot identity；无 lineage 的 actual 回退、future/unaudited actual、invalid weighting 和不可比 revision 正确 blocked/degraded；JSON/Markdown/roadmap/meeting outputs 逐值一致。

### 4. Freeze Canonical Flow Graph Contract

升级 baseline dependency contract：milestone/gate 是唯一节点，gate 可声明 workstream/program lane；legacy string dependency 兼容归一化，vNext dependency object 冻结 `edge_id`、predecessor、`dependency|aggregation|conditional|rework|informational`、condition/label/source/revision，汇总目标仅支持 `predecessor_rule: all`。冻结 program-status execution/health 双轴、status-sync action 时间/related IDs、risk stable IDs/relations、`adp-flow-graph` scopes/unmapped/recovery 和 topology/state/overlay identity；layout identity 明确留给 panel。

**完成门：** schema/compatibility/golden fixtures 覆盖并行、汇聚、条件和返工；每个 node/edge 可追溯到同 revision baseline，每个 execution/health/relation state 和 scoped count 可追溯到 canonical source；ready/in-progress、aggregation target rule、processed window 均无歧义；未知/重复/cross-revision 引用和非法 cycle 得到确定 finding/recovery。

### 5. Implement Canonical Flow Graph Projection

扩展 `adp-plan-baseline` validator/diff、`adp-status-sync` in-progress/started/done/related fields、`adp-risk-dependency-change-review` stable IDs/lifecycle/relations、`adp-program-status` execution/health projection；新建 `adp-flow-graph` 生成 topology/state/scoped overlays/current+immutable JSON；扩展 `adp-state-audit` graph/reference/identity checks，并让 meeting-pack 选择同一 graph 的场景子图。`adp-roadmap-sync` 保持既有 timeline boundary。

**完成门：** topology/state/overlay/flow graph identities 对各自 inputs 敏感且同输入幂等；ready 不冒充 active work，aggregation all 不阻塞来源 lane，conditional/rework 未确认时不选择分支；active/reporting counts 只来自显式引用和正确时间窗且 unmapped 不丢失；meeting window selection 不回写 canonical graph；roadmap fixtures 不因 graph 功能改变 action guardrail。

### 6. Define Panel Model and Artifact Contract

将已验证的 canonical progress/status、roadmap timeline、`flow-graph.json` 和 meeting distillate 映射为 panel schema；冻结三视图 section IDs、两种 visualization modes、manifest、panel/layout ID、历史/未来周期/flow scope 选择、localization、safe embedding、distribution redaction 和 recovery contract。panel model 只允许字段选择、排序、场景裁剪和 presentation hints，不含进度、状态、计数或图拓扑公式。

**完成门：** 相同 inputs 生成相同 panel model/panel/layout ID；不同 status/flow graph snapshot、meeting pack、history 或 flow scope selection 不碰撞；所有展示值可逐字段追溯到 status/flow schema；shareable hidden topology 不重连；panel fixtures 中不存在第二套状态、进度、计数或分支选择。

### 7. Build `adp-management-panel`

实现 compose/refresh/inspect/archive、静态 self-contained renderer、三视图、bullet/阶梯/flow visualizations 和交互。先交付 project-lead 的进度摘要与完整/折叠 flow，再接 FDE window subgraph 与 business program spine；共用 shell、筛选和 source drawer，但分别执行各自信息预算。

**完成门：** `file://` 直接打开；Chrome/Edge 无控制台错误；负责人首屏显示四项完成度、bullet chart、独立计划健康度，随后可切换阶梯趋势/完整 flow；FDE 不常驻长期 forecast 且 flow 只含窗口上下文；双周会首屏突出下一周期且 flow 只含 program/critical/异常/决策路径；三视图逐值/逐计数一致。JavaScript off/ELK failure 可读等价 fallback；筛选、排序、折叠、flow mode、lane collapse、fit/zoom/pan、节点下钻、跨周期比较、versioned hash、keyboard 和 print 有浏览器测试；1280x720/1920x1080 可读；200%/320px/400% reflow 不遮挡；HTML/SVG 注入 fixtures 不执行。

### 8. Extend Audit for Panel Inputs and Artifacts

增加 pre-render input gate 与 post-render HTML/manifest validation，验证 freshness、hash、locale、source lineage、progress/flow schema identity、ELK asset/version/hash、embedded model、SVG allowlist/fallback 和不可变归档。

**完成门：** stale/missing/collision/tampered/locale fallback/unsafe source/progress or graph mismatch/ELK asset mismatch fixtures 得到正确 disposition 与 recovery；meeting readiness/lifecycle/flow scope 可追溯；artifact audit 不改写归档；archive distribution profile 与 redaction manifest 可验证。

### 9. Integrate Meeting and Program Lead Journeys

meeting-pack 输出 panel-ready metadata；Program Lead 增加 refresh/open/archive routing；会议视图明确 pack ID/window，meeting-sync receipt 保留 panel ID（若归档）。

**完成门：** panel refresh 不推进 meeting cursor；FDE 只展示 confirmed window 内的 progress delta、相关 forecast milestone 和由 meeting-pack 选择的 flow subgraph/window counts；双周会 flow 不泄漏被信息预算裁剪的工程细节；meeting view 在 ready/degraded/blocked 与 pre-meeting/sync-failed/post-sync-official 下不产生假闭环；official archive 只在成功 meeting-sync receipt 后关联；三个视图对相同 snapshot 的 overall/progress/execution/health/scoped counts 完全一致。

### 10. Close P1 Operational Debt

完成 stale baseline lock recovery、management Markdown lineage 验证、legacy `render_program_views.py` CLI 兼容/迁移决策，并将其写入 help 与 release notes。

**完成门：** 旧调用要么继续工作，要么返回确定的迁移错误；stale lock 可区分 live owner 与 orphan；Program Lead 不接受陈旧 Markdown。

### 11. Update Kickoff, Setup and Module Validation

升级 1.3.0、注册 `adp-flow-graph` 与 risk/status schema extensions、配置/资源/目录，更新 module-help 和 marketplace，运行 fresh/update/headless/legacy migration/anti-zombie/installed inspection。

**完成门：** 16 个既有 setup gates 扩展覆盖 panel/flow schemas/ELK asset；Module Builder validator 0 finding；全部 ADP tests、quick_validate、script/path/license/hash scans 通过，历史 `.analysis` 噪声单独报告。

### 12. Real-Project Acceptance on `shopify-migration`

先升级副本或使用 output override，生成 baseline/status/roadmap/two meeting packs/panel；再以负责人、FDE 晨会、业务双周会三条真实 journey 验收。

**完成门：** 负责人无需阅读 Markdown 长表即可通过 bullet/阶梯/各 L 表区分完成度与计划健康度，并在 flow mode 区分 ready/in-progress、定位 critical/异常节点、边和 scoped counts；FDE 晨会只看到执行窗口及其 flow 上下文；双周会可通过 program spine/关键门禁完成异常归因与决策；浏览器刷新读取重新生成的静态文件；跨周期、revision/topology 断点和 source drill-down 正确；无网络/serve；同输入幂等；会议投屏、keyboard、screen-reader、no-JS/ELK failure fallback、print、200%/320px 和双语长内容通过；internal/shareable 归档离线打开并标明 distribution/lifecycle/layout identity，追溯到 canonical sources 且 redaction 不伪造路径。
