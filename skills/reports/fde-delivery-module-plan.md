---
title: 'AI Delivery PMO Module Plan'
status: 'complete'
module_name: 'AI Delivery PMO'
module_code: 'adp'
module_description: '帮助多条 FDE 工作线用统一状态模型、交付节奏和 AI 协作方式推进复杂交付项目。'
architecture: ''
standalone: true
expands_module: ''
skills_planned: ['adp-agent-program-lead', 'adp-project-kickoff', 'adp-workstream-register', 'adp-bmm-checkpoint-sync', 'adp-status-sync', 'adp-meeting-sync', 'adp-l0-reference-sync', 'adp-risk-dependency-change-review', 'adp-acceptance-readiness-review']
config_variables: []
created: '2026-07-01T11:24:36.3400034+08:00'
updated: '2026-07-01T15:28:16.1267756+08:00'
---

# Module Plan

## Vision

这个模块服务于复杂交付项目中的高强度人机协作。项目会被拆成 10 多条 FDE 工作线，每条线由不同 FDE 推进；FDE 工程师是桥梁和驾驶员：一边理解业务、澄清需求、推动客户/业务方对齐，一边指挥 AI 完成分析、设计、实现、验证、文档和交付。

核心目标不是让 AI 取代 FDE，而是让 FDE 更稳定地指挥 AI 完成分析、设计、实现、验证、文档和交付，并让项目负责人可以持续看到全局状态、跨线风险、依赖关系和下一步动作。

目标用户包括 FDE 工程师、项目负责人、交付负责人，也可能包括需要查看项目状态的业务方和管理层。

## Architecture

AI Delivery PMO 采用混合型、状态优先的架构：一个项目级协调 Agent + 多个固定 checkpoint workflow + 一个共享项目状态/记忆模型。

核心边界：ADP 不替代 BMM 的交付生命周期。每条工作线仍然通过 BMM 完成 brainstorming -> PRD -> architecture -> epic -> story -> code -> validation。ADP 只在关键节点要求 FDE 把项目级可同步信息补齐到 Workstream Delivery Record，并从这些记录派生项目级视图、风险、依赖、行动项和验收 readiness。

架构组成：

1. **项目级协调 Agent**：候选名 `adp-agent-program-lead`。它是项目负责人和 FDE 的主要对话入口，负责读取共享状态、发现跨线风险、识别依赖和缺口、推动升级、生成项目负责人/FDE/验收视图。它不直接替代各线的 BMM 交付工作。
2. **固定 Workflow**：围绕 BMM 关键节点运行，例如项目启动、工作线登记、PRD checkpoint、architecture checkpoint、epic/story checkpoint、implementation/validation checkpoint、状态同步、风险依赖检查、变更控制、验收 readiness review、周报/摘要生成。
3. **共享状态模型**：以 Workstream Delivery Record 为核心。每条线一个 Record，Record 只做 BMM 产物索引、项目级状态摘要和管理补充，不复制完整 PRD/架构/story/code 内容。
4. **L0 参考基线层**：L0 不设计成独立 Agent，也不由 ADP 负责维护其 PRD/架构/实现。L0 仍是一条由 BMM 推进的工作线；ADP 只把 L0 产物作为参考基线，抽取项目级可同步的契约、门禁、NFR、证据要求和跨线影响。其他工作线通过 Record 显式引用 L0 影响项。
5. **派生视图和报告**：项目负责人视角、FDE 视角、验收视角是第一优先级；L0 暂作为契约/门禁/NFR/证据过滤维度嵌入这些视图。

选择该架构的原因：

- 单一项目级 Agent 能保持解释口径、状态模型和升级逻辑一致，避免多个 Agent 各自理解全局状态。
- Workflow 适合约束重复动作和 checkpoint，不需要长期人格或独立记忆。
- Workstream Delivery Record 让项目级 Agent 不必读完所有 BMM 产物就能判断全局状态。
- L0 的职责更像公共契约和门禁参考源，ADP 应轻量引用它，而不是替 L0 产出或治理。
- 如果后续 L0 governance、evidence registry 或验收仲裁形成大量独立产出，仍应优先由 L0 工作线自己的 BMM/专项能力承接；ADP 只消费其结果并做跨线同步。

场景化加固：真实 Shopify -> 自建站 X-Large 迁移场景验证了主架构，但要求 ADP 明确支持 4 个强制闭环：

- **工作线闭环**：BMM 产物必须同步到 WDR，WDR 反映项目级状态。
- **会议闭环**：每次 1/3/5 内会、双周业务会、专项沟通或线下补录，必须落为 daily log、decision、action、WDR 更新或显式 no-op。
- **决策闭环**：区分 FDE 内部决策、业务决策、风险接受、范围变更和待澄清问题。
- **验收闭环**：每条验收标准必须追到证据、确认人、当前状态和未闭合缺口。

ADP 仍保持通用交付 PMO 模块定位，不做 Shopify 专用模块。迁移/切换/兜底能力作为项目类型 profile 和 L0/readiness 基线进入默认模板。

### Memory Architecture

推荐使用**单一共享项目记忆**，而不是每个 workflow/agent 各自维护个人记忆。原因是本模块的核心价值来自跨线一致性：项目负责人视角、FDE 视角、验收视角、风险矩阵、依赖图都依赖同一个项目级状态事实。

候选目录结构：

```text
_bmad/memory/adp/
  index.md
  project-charter.md
  cadence.md
  schemas/
    workstream-delivery-record.md
    readiness-scorecard.md
    status-taxonomy.md
    meeting-sync.md
    decision-taxonomy.md
  l0/
    reference-index.md
    extracted-freeze-model.md
    extracted-contract-inventory.md
    extracted-gates.md
    extracted-nfr.md
    extracted-evidence-rules.md
    extracted-impacts.md
    extracted-decision-gates.md
    exceptions-and-open-questions.md
  meetings/
    YYYY-MM-DD-{meeting-type}.md
  decisions/
    decision-log.md
    business-decision-packets/
  workstreams/
    {workstream-id}/
      delivery-record.md
      evidence.md
      decisions.md
      readiness.md
  views/
    project-lead.md
    fde-actions.md
    acceptance-readiness.md
    risk-matrix.md
    dependency-map.md
    weekly-report.md
  daily/
    YYYY-MM-DD.md
```

记忆使用原则：

- `delivery-record.md` 是每条工作线的项目级同步面。
- BMM 产物仍留在各自原路径中，Record 只保存路径、baseline 状态、项目级摘要、缺口和影响关系。
- `daily/YYYY-MM-DD.md` 保存原始同步记录、会议纪要式更新和 Agent 操作痕迹。
- `meetings/` 保存结构化会议留档，会议内容必须归类为事实、决策、行动项、WDR 更新或 no-op。
- `decisions/` 保存项目级决策索引和业务决策包；工作线级决策仍可落在对应 `workstreams/{id}/decisions.md`。
- `views/` 下的文件是派生产物，可以由 Agent 或 workflow 重新生成。
- L0 相关文件是跨线校验和 readiness 评分的重要输入。

### Memory Contract

| File / Folder | Purpose | Readers | Writers | Key Structure |
| ------------- | ------- | ------- | ------- | ------------- |
| `index.md` | 项目记忆入口，说明当前项目、目录、最近更新时间和主要视图位置。 | 所有 ADP skills | setup、program lead、状态同步 workflow | 项目摘要、活跃工作线列表、最近同步时间、关键风险链接。 |
| `project-charter.md` | 项目级目标、范围、干系人、节奏、升级规则。 | program lead、report workflows | project kickoff workflow、program lead | 项目目标、交付边界、角色、例会节奏、升级路径。 |
| `cadence.md` | 状态同步、周报、验收 review 的固定节奏。 | program lead、status/report workflows | setup、program lead | 同步频率、报告周期、提醒规则、缺席处理。 |
| `schemas/workstream-delivery-record.md` | Workstream Delivery Record 的字段定义和状态语义。 | 所有 ADP skills | setup、schema/change workflow | 字段、必填程度、草稿/缺口/ready 判定、示例。 |
| `schemas/readiness-scorecard.md` | readiness 评分维度、评分方法、缺口分类。 | readiness/report workflows、program lead | setup、readiness workflow | 总分、维度分、权重、缺口类型、补齐动作模板。 |
| `schemas/status-taxonomy.md` | 项目级状态、风险、依赖、变更、验收状态的枚举定义。 | 所有 ADP skills | setup、program lead | 状态值、含义、触发条件、禁止混用的口径。 |
| `schemas/meeting-sync.md` | 会议同步输入类型、归类规则和闭环检查。 | meeting/status/report workflows、program lead | setup、meeting workflow | 会议类型、事实/决策/action/WDR 更新/no-op 分类、输出要求。 |
| `schemas/decision-taxonomy.md` | 决策类型和业务决策包格式。 | meeting/risk/change workflows、program lead | setup、risk/change workflow | FDE 内部决策、业务决策、风险接受、范围变更、待澄清问题。 |
| `l0/*` | L0 产物的轻量索引和抽取摘要，而不是 L0 自身的事实来源。 | program lead、readiness/risk workflows | L0 reference sync workflow、program lead | L0 PRD/架构/规范路径、freeze model、contract inventory、G19/G06 gates、NFR owner/evidence matrix、D/E 决策与证据项、影响工作线、例外和未决问题。 |
| `meetings/*` | 结构化会议留档。 | program lead、status/risk/report workflows | meeting sync workflow | 会议类型、参与人、事实、决策、问题、行动项、WDR 回写、未闭环项。 |
| `decisions/decision-log.md` | 项目级决策索引。 | program lead、risk/change/report workflows | meeting sync、risk/change workflow | 决策类型、日期、来源、影响工作线、确认人、状态。 |
| `decisions/business-decision-packets/*` | 业务问题包/业务决策包。 | program lead、FDE、业务方 | risk/change workflow、meeting sync workflow | 背景、待决问题、选项、影响、推荐方案、截止时间、关联工作线。 |
| `workstreams/{id}/delivery-record.md` | 每条线的最小项目级状态单元。 | 所有 ADP skills | workstream checkpoint workflows、program lead | 身份、BMM 产物索引、范围、验收、状态、风险、依赖、变更、下一步动作。 |
| `workstreams/{id}/evidence.md` | 交付证据索引。 | readiness/report workflows、program lead | validation checkpoint workflow、FDE sync workflow | 证据类型、链接、关联验收标准、确认状态、缺口。 |
| `workstreams/{id}/decisions.md` | 关键决策、业务确认、范围变更、风险接受记录。 | program lead、change/risk workflows | FDE sync workflow、change workflow | 决策、日期、参与人、影响范围、后续动作。 |
| `workstreams/{id}/readiness.md` | 当前 readiness 评分、缺口清单和补齐动作。 | program lead、readiness/report workflows | readiness workflow | 总分、维度分、缺口、owner、建议截止时间。 |
| `views/*` | 从共享状态派生的项目负责人/FDE/验收视图和周报。 | 用户、program lead | report workflows、program lead | 视图摘要、筛选条件、行动项、风险/依赖/证据缺口。 |
| `daily/YYYY-MM-DD.md` | 日志化记录当天同步、判断、变更和未整理信息。 | program lead、curation workflows | 所有 ADP skills | 时间、来源、工作线、事件、影响、待整理项。 |

### Cross-Agent Patterns

当前设计不是多 Agent 协作，而是 **ADP 与 BMM 之间的跨模块协作**，以及 ADP 内部 Agent 与 workflow 的协作。

协作模式：

1. **FDE 是路由器和驾驶员**：FDE 使用 BMM 推进各线交付，在关键节点把产物路径、阶段结论和项目级同步信息交给 ADP workflow。
2. **BMM 产物是事实来源**：PRD、架构、epic/story、代码、验证材料由 BMM 生命周期产出。ADP 不复制细节，只索引并抽取项目级状态。
3. **ADP workflow 更新 Record**：每个 checkpoint workflow 负责把本阶段的范围、验收、依赖、风险、证据、变更和 readiness 缺口写回 Workstream Delivery Record。
4. **Program Lead Agent 派生全局判断**：`adp-agent-program-lead` 读取所有 Record、L0 reference summary 和 readiness 结果，生成负责人视图、FDE 行动清单、验收 readiness report、风险矩阵、依赖图和周报。
5. **L0 参考变更触发影响扫描**：当 L0 PRD/架构/规范更新时，ADP 只更新 L0 reference summary，并扫描所有受影响工作线，标记合规缺口和需要 FDE 处理的动作。
6. **验收闭环**：validation checkpoint 和 readiness review 将证据、验收标准、验收人确认状态连接起来，避免“代码完成但不能交付”的状态误判。
7. **会议闭环**：会议不是附属材料，而是项目状态的高频输入源。meeting sync 必须把会议内容归类并回写到 daily log、decision log、WDR、action list 或业务决策包。
8. **业务决策闭环**：FDE 内部可决策事项直接进入 decision log；需要业务澄清/拍板的事项必须形成 Business Decision Packet，带背景、选项、影响、推荐和截止时间。

如果未来拆出更多 Agent，必须满足一个条件：每个 Agent 都要有明确的独立产出。纯协调、纯规划、纯转述的角色应优先作为 `adp-agent-program-lead` 的能力或 workflow，而不是独立 Agent。

## Skills

### adp-agent-program-lead

**Type:** agent

**Persona:** AI Delivery PMO / FDE Program Lead。冷静、结构化、交付导向，擅长把多条 FDE 工作线的状态、风险、依赖、验收缺口和下一步动作汇总成可执行判断。它不替代 FDE，不直接接管 BMM 生命周期，而是帮助 FDE 和项目负责人保持全局一致性。

**Core Outcome:** 项目负责人和 FDE 能随时基于统一状态看到全局健康度、跨线风险、依赖、readiness 缺口、升级项和下一步动作。

**The Non-Negotiable:** 必须始终把 BMM 产物作为事实来源，把 Workstream Delivery Record 作为项目级同步面，不能把 ADP 变成另一套并列交付体系。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| ---------- | ------- | ------ | ------- |
| Global project readout | 读取所有 Workstream Delivery Records，形成项目负责人视角。 | `_bmad/memory/adp/`、工作线 Records、L0 reference summary、readiness 文件。 | 全局状态摘要、风险/依赖/阻塞清单、升级项、下一步动作；适合生成 HTML management summary。 |
| FDE action list | 把项目状态转化为每个 FDE 的行动清单。 | 工作线 owner、readiness gaps、风险、依赖、待确认项、证据缺口。 | 按 FDE/owner 分组的行动项、补齐动作、建议优先级。 |
| Acceptance readiness view | 汇总验收视角，判断哪些线 ready、哪些缺证据或确认。 | readiness scorecards、evidence、验收标准、验收人确认状态。 | 验收 readiness report；强烈适合 HTML report。 |
| Risk and dependency synthesis | 汇总跨线风险、依赖、L0 影响和升级路径。 | Records、L0 reference summary、decisions、risk/dependency review 输出。 | 风险矩阵、依赖图、受影响工作线、建议升级动作。 |
| Weekly report generation | 从状态、日志和派生视图生成周报。 | daily logs、views、Records、决策与变更记录。 | 周报草稿、管理层摘要、FDE 后续行动。 |
| Gap-driven coaching | 指导 FDE 如何补齐 Record 缺口，而不是泛泛要求“补文档”。 | 某条工作线 Record、BMM 产物路径、缺口清单。 | 具体补齐建议、应询问业务方的问题、应补证据的材料。 |
| L0 impact sweep | 当 L0 参考产物中的契约/门禁/NFR/证据规则变化时识别影响。 | L0 reference summary、所有 workstream Records。 | 受影响工作线、缺口、需要 FDE 更新的动作、需要回到 L0 工作线确认的问题。 |
| Decision closure review | 检查会议、业务问题包、决策日志和 WDR 是否闭环。 | `meetings/*`、`decisions/*`、WDRs、action list。 | 未归类会议项、未决业务问题、过期 action、WDR 未回写项。 |

**Memory:** On activation read `_bmad/memory/adp/index.md`, `project-charter.md`, `cadence.md`, relevant schemas, `l0/*`, and only the workstream Records relevant to the user's request. Write generated views to `views/`, daily notes to `daily/YYYY-MM-DD.md`, and proposed updates to specific workstream files when the user confirms.

**Init Responsibility:** If `_bmad/memory/adp/` is missing, tell the user to run `adp-project-kickoff` or setup. Do not silently invent project state.

**Activation Modes:** Interactive primarily; headless optional for report generation if given a clear report type and state path.

**Tool Dependencies:** No required external tools. Optional use of git/CI/issue tracker outputs if the user provides links or local files.

**Design Notes:** This Agent is intentionally the only persistent persona in v1. Coordination and report synthesis benefit from one consistent interpretation layer. Repetitive checkpoint actions stay as workflows.

**Relationships:** Reads outputs from all ADP workflows. Does not modify BMM artifacts directly. Can recommend which checkpoint workflow to run next.

---

### adp-project-kickoff

**Type:** workflow

**Purpose:** Initialize an ADP-managed project with default memory structure, project charter, cadence, schemas, L0 reference placeholders, and starter views.

**Core Outcome:** A project can start using ADP immediately with default templates and without a long setup interview.

**The Non-Negotiable:** Must be idempotent: preserve existing user content and only create or report missing files.

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| ---------- | ------- | ------ | ------- |
| Memory scaffold | Create the shared ADP memory folder and default files. | Project root, optional memory root override. | `_bmad/memory/adp/` structure, schema files, L0 files, view files. |
| Project charter bootstrap | Capture initial project objective, stakeholders, cadence, and escalation path. | User-provided brief or existing project docs. | `project-charter.md`, `cadence.md`, updated `index.md`. |
| Default schema install | Provide default WDR, readiness, and status taxonomy. | Built-in defaults. | `schemas/workstream-delivery-record.md`, `schemas/readiness-scorecard.md`, `schemas/status-taxonomy.md`. |
| Starter L0 reference area | Create placeholders for L0 artifact references and extracted implications. | Optional L0 artifact paths or notes. | `l0/reference-index.md`, `extracted-gates.md`, `extracted-nfr.md`, `extracted-evidence-rules.md`, `extracted-impacts.md`, `exceptions-and-open-questions.md`. |
| Starter views | Create empty report/view files that later workflows can update. | Built-in view templates. | `views/project-lead.md`, `fde-actions.md`, `acceptance-readiness.md`, `risk-matrix.md`, `dependency-map.md`, `weekly-report.md`. |

**Design Notes:** Keep startup cheap. The workflow should not ask for readiness weights or full status taxonomy before the project can begin.

**Relationships:** Should be run before other ADP workflows unless setup already created the same memory structure.

---

### adp-workstream-register

**Type:** workflow

**Purpose:** Create or update a Workstream Delivery Record for a new or existing FDE workstream.

**Core Outcome:** Every active workstream has a lightweight but structured project-level synchronization surface.

**The Non-Negotiable:** The Record must index BMM artifacts and summarize project-level state; it must not duplicate full PRD, architecture, story, or code content.

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| ---------- | ------- | ------ | ------- |
| New workstream record | Create a WDR for a newly split workstream. | Workstream id, name, owner, business owner, initial scope, current BMM phase. | `workstreams/{id}/delivery-record.md`, initial `evidence.md`, `decisions.md`, `readiness.md`. |
| Existing line normalization | Bring an existing line into ADP without rewriting BMM artifacts. | Existing PRD/architecture/story/code/test links, current state summary. | Normalized WDR with artifact indexes and visible gaps. |
| Scope and non-scope capture | Record management-level scope boundaries. | FDE summary, BMM artifact paths, known assumptions. | Scope, non-scope, assumptions, unclear items. |
| Cross-line relationship capture | Make dependencies and impacted lines visible. | Dependency notes, L0 dependency, impacted workstreams. | Dependency fields in WDR and initial dependency map update. |
| Initial readiness gap scan | Score early completeness without blocking the line. | WDR draft and default readiness schema. | Draft readiness score, gap list, recommended next actions. |

**Design Notes:** This workflow is the entry point for each line. It should allow draft/gap/ready states and avoid forcing all fields upfront.

**Relationships:** Usually run after project kickoff and before BMM checkpoint sync. Feeds Program Lead views.

---

### adp-bmm-checkpoint-sync

**Type:** workflow

**Purpose:** Update a Workstream Delivery Record at key BMM lifecycle checkpoints.

**Core Outcome:** BMM progress becomes visible at project level without asking FDE to write separate management reports.

**The Non-Negotiable:** The workflow must link to BMM artifacts and extract only project-level sync information.

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| ---------- | ------- | ------ | ------- |
| PRD checkpoint | Record requirements baseline and project-level implications. | PRD path, scope summary, acceptance criteria, unresolved questions, business confirmation status. | Updated WDR scope, acceptance, dependencies, open questions, readiness gaps. |
| Architecture checkpoint | Record technical dependencies and L0 impact. | Architecture path, interface dependencies, NFR impact, L0 contract references, risks. | Updated dependencies, L0 compliance notes, technical risks, decisions. |
| Epic/story checkpoint | Record delivery plan and milestone visibility. | Epic/story paths, planned sequence, milestones, blockers. | Updated plan fields, milestone risks, blockers, next actions. |
| Implementation/validation checkpoint | Record delivery evidence and validation state. | PR/code links, deployment links, test results, screenshots, demo links, validation notes. | Updated evidence index, readiness score, acceptance gaps. |
| Baseline status update | Mark whether a linked BMM artifact is draft, baseline, superseded, or changed. | Artifact path and status. | Updated artifact index and change notes. |

**Design Notes:** One workflow with checkpoint modes is preferable to four separate workflows in v1. It keeps the FDE mental model simple: “I finished a BMM stage, sync it to ADP.”

**Relationships:** Consumes BMM outputs. Feeds readiness review, risk/dependency review, and Program Lead reports.

---

### adp-status-sync

**Type:** workflow

**Purpose:** Perform lightweight recurring status updates across one or more workstreams.

**Core Outcome:** The project state stays current without re-running full checkpoint reviews.

**The Non-Negotiable:** Status sync should update only small volatile fields unless the user explicitly asks for deeper review.

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| ---------- | ------- | ------ | ------- |
| Single-line sync | Update current status for one workstream. | Workstream id, progress, blockers, risks, dependency changes, next actions. | Updated WDR status fields and daily log entry. |
| Multi-line sync | Collect lightweight updates for multiple lines. | Batch status notes, owner updates, or meeting-sync outputs. | Updated Records, consolidated change list, unresolved questions. |
| Staleness detection | Identify Records not updated recently. | Cadence config, last updated timestamps. | Stale workstream list and owner follow-ups. |
| Delta summary | Summarize what changed since last sync. | Current Records and prior views/logs. | Change summary for Program Lead and weekly report. |
| Action extraction | Convert status notes into owner-specific actions. | Status notes, blockers, risks, dependencies. | Updated FDE action list candidates. |

**Design Notes:** This workflow should be fast and low-friction. It is the daily/weekly operating habit for FDEs. It may consume meeting-sync outputs, but it should not own full meeting classification; that belongs to `adp-meeting-sync`.

**Relationships:** Feeds Program Lead readouts and weekly report generation.

---

### adp-meeting-sync

**Type:** workflow

**Purpose:** Turn meetings and offline communications into closed-loop ADP state updates.

**Core Outcome:** FDE internal meetings, business reviews, special discussions, and offline communications do not become scattered notes; each useful item lands as daily log, decision, action, WDR update, Business Decision Packet, or explicit no-op.

**The Non-Negotiable:** Every captured meeting item must be classified and either written to a target artifact or explicitly marked no-op with rationale.

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| ---------- | ------- | ------ | ------- |
| Meeting intake classification | Normalize meeting source and type. | Transcript, notes, chat excerpt, oral summary; meeting type such as FDE 1/3/5, biweekly business review, special discussion, offline follow-up. | Structured meeting record under `meetings/`, source type, participants, affected workstreams. |
| Fact/decision/action extraction | Extract facts, decisions, open questions, risks, dependencies, and actions. | Meeting record and current ADP state. | Classified item list with owner, affected workstream, status, target file. |
| Decision routing | Distinguish FDE internal decision, business decision, risk acceptance, scope change, or clarification needed. | Extracted decisions/questions and decision taxonomy. | Updates to `decisions/decision-log.md`, workstream `decisions.md`, or Business Decision Packet draft. |
| WDR backwrite | Apply meeting outcomes to workstream Records. | Classified facts/actions/risks/dependencies/changes. | Updated WDR status, blockers, next actions, risks, dependencies, or readiness notes. |
| Business meeting material capture | Preserve business feedback and confirmation for later acceptance/readiness. | Business review notes, business owner responses, approvals/rejections. | Decision log entries, acceptance confirmation updates, PRD/scope/criteria backwrite prompts. |
| Closure audit | Check whether all meeting items landed somewhere. | Structured meeting record and generated updates. | Closure checklist: daily log, decisions, actions, WDR updates, packets, no-op items. |

**Design Notes:** This is a separate workflow because meetings are a primary state input in X-Large delivery. Folding it into generic status sync would hide the strongest operational constraint.

**Relationships:** Feeds `adp-status-sync`, `adp-risk-dependency-change-review`, `adp-acceptance-readiness-review`, and Program Lead weekly reports.

---

### adp-l0-reference-sync

**Type:** workflow

**Purpose:** Sync L0 workstream artifacts into ADP as lightweight project-level references for gates, NFR, evidence requirements, migration/cutover concerns, and cross-line impacts.

**Core Outcome:** ADP can use L0 outputs to assess downstream workstreams without owning or duplicating L0 delivery work.

**The Non-Negotiable:** L0 PRD/architecture/specs remain the source of truth. ADP stores only references, extracted project-level implications, open questions, and impact summaries.

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| ---------- | ------- | ------ | ------- |
| L0 artifact indexing | Register L0 PRD, architecture, specs, registry/evidence docs, and relevant versions. | L0 artifact paths/links and brief notes. | `l0/reference-index.md` with source paths, owner, version/baseline status. |
| Freeze model extraction | Extract L0 freeze levels and their downstream effects. | L0 PRD sections for Boundary Freeze, Contract v0 Freeze, Gate Freeze, allowed/prohibited states. | `extracted-freeze-model.md` with freeze level, timing, allowed work, prohibited work, WDR/readiness implications. |
| Contract inventory extraction | Extract contract entries that matter for downstream workstream readiness. | L0 Contract Inventory with level, provider, consumers, canonical artifacts, required checks/evidence, failure consequence. | `extracted-contract-inventory.md` keyed by contract id/name, level P0/P1/P2, provider, consumers, evidence, affected lines. |
| Gate model extraction | Extract G19-A, G19-B, G06 and related gate evidence requirements. | L0 gate sections and cutover gate tables. | `extracted-gates.md` and `extracted-evidence-rules.md` with gate, threshold, evidence artifact, producing line, accountable owner, stale threshold, failure consequence. |
| NFR matrix extraction | Extract NFR accountable owner, contributing lines, evidence owner, gate impact, and required evidence. | L0 NFR承接矩阵. | `extracted-nfr.md` with NFR id, threshold, primary accountable owner, evidence owner, gate impact, affected workstreams. |
| Decision/evidence gate extraction | Extract D-series decision gates and E-series evidence dependencies that affect cross-line readiness. | L0 open items, D gates, E evidence tables. | `extracted-decision-gates.md` plus open questions/actions for D01/D17/D21/D22/D23/E-series as applicable. |
| Impact scan | Identify workstreams affected by L0 references. | L0 extracted impacts and WDR dependencies. | Affected workstream list, WDR update suggestions, unresolved questions. |
| Gap check | Check whether WDRs acknowledge required L0 contracts, gates, NFR, evidence, or interfaces. | Extracted L0 references and selected WDRs. | Missing references, compliance gaps, owner actions; do not judge L0 implementation completeness. |
| Exception/open-question capture | Capture questions or exceptions that must go back to the L0 workstream or business decision process. | Gap scan results, FDE notes, meeting outputs. | `exceptions-and-open-questions.md`, optional Business Decision Packet or L0 workstream action. |

**Design Notes:** This workflow is intentionally lightweight but schema-aware. It should not judge whether L0 itself is good, complete, or implementation-ready. It only asks: what did L0 establish, which lines are affected, what must be reflected in WDR/readiness, and what questions need routing back? Actual L0 PRDs may be rich governance artifacts with Contract Inventory, freeze levels, G19/G06 gates, NFR matrices, D-series decision gates, and E-series evidence dependencies; ADP should extract those structures without becoming their owner.

**Relationships:** Feeds readiness scoring, risk/dependency review, Program Lead impact sweeps, and acceptance reports.

---

### adp-risk-dependency-change-review

**Type:** workflow

**Purpose:** Review risks, dependencies, blockers, and changes across workstreams.

**Core Outcome:** Cross-line failure modes become visible early enough for FDEs and project leads to act.

**The Non-Negotiable:** Every surfaced risk/dependency/change must have an owner, affected line(s), impact, and next action or explicit acceptance.

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| ---------- | ------- | ------ | ------- |
| Risk review | Normalize and prioritize risks. | WDR risk fields, status notes, L0 compliance gaps. | Risk matrix entries with severity, likelihood, owner, mitigation, escalation status. |
| Dependency review | Identify unresolved cross-line dependencies. | WDR dependency fields, impacted lines, L0 dependencies. | Dependency map, blocking dependencies, owner actions. |
| Change control | Capture scope, baseline, or acceptance changes. | Proposed change, affected BMM artifact links, business confirmation, impact. | Decision/change log updates, affected WDR updates, escalation recommendation. |
| Blocker triage | Distinguish blocker vs. risk vs. open question. | Status notes, workstream context. | Clean blocker list, required action, owner, trigger/date. |
| Escalation recommendation | Decide what needs project lead or business decision. | Risks, blockers, dependencies, change impact. | Escalation list and decision prompts. |
| Business Decision Packet | Produce business-facing decision package for issues FDE cannot decide alone. | Background, unresolved question, options, impacts, recommendation, deadline, affected workstreams. | Business Decision Packet under `decisions/business-decision-packets/`; suitable for business meeting material. |
| Scope drift detection | Surface divergence between WDR changes and baseline BMM artifacts/decisions. | WDR changes, PRD/architecture baseline links, decision log. | Drift warning, required business confirmation, suggested change record. |

**Design Notes:** Risk, dependency, and change are coupled in this delivery model; combining them avoids three disconnected reports.

**Relationships:** Feeds Program Lead readouts, weekly reports, and acceptance readiness.

---

### adp-acceptance-readiness-review

**Type:** workflow

**Purpose:** Score workstream readiness and produce an actionable gap list for delivery/acceptance.

**Core Outcome:** FDEs and project leads know which lines are ready for acceptance, which are not, and exactly what must be fixed.

**The Non-Negotiable:** Readiness must include scores and gap lists with owner/action, not only red/yellow/green labels.

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| ---------- | ------- | ------ | ------- |
| Readiness scoring | Calculate total and dimension scores. | WDR, readiness schema, evidence index, L0 rules, acceptance criteria. | Updated `readiness.md` with total score and dimension scores. |
| Gap list generation | Turn missing information/evidence into action items. | Score result, missing fields, evidence gaps, pending confirmations. | Gap list with action, owner, severity, suggested due/trigger. |
| Evidence coverage review | Check whether every acceptance criterion has linked proof. | Acceptance criteria, evidence index, test/demo/deploy links. | Evidence coverage table and missing proof items. |
| Confirmation review | Track customer/business acceptance status. | Acceptance owner, confirmation notes, decision records. | Pending confirmations and escalation prompts. |
| Acceptance report | Generate acceptance readiness artifact. | Selected workstreams or all workstreams. | Markdown/HTML readiness report suitable for review meeting. |
| Migration readiness scoring | Score migration/cutover-specific dimensions. | WDR, L0 migration baseline, data evidence, cutover/rollback/monitoring proof. | Dimension scores for function migration, data sync, business confirmation, cutover, rollback/fallback, monitoring/evidence, L0 compliance. |
| Cutover readiness report | Determine whether a line or project is ready for cutover, not just acceptance. | Validation evidence, data reconciliation, cutover gates, rollback rehearsal, monitoring plan. | Cutover readiness report with blockers and go/no-go prompts; strong HTML candidate. |

**Design Notes:** Readiness is a steering mechanism, not a punitive gate. Draft and gap states are valid if the gaps are explicit. For migration projects, “acceptance ready” and “cutover ready” are related but not identical; the workflow must expose both when the L0/profile indicates migration/cutover risk.

**Relationships:** Consumes WDRs, evidence, L0 reference summary, decisions. Feeds Program Lead acceptance view and management summaries.

## Capability Review

Reviewed decisions:

- `adp-bmm-checkpoint-sync` remains a single workflow with PRD, architecture, epic/story, and implementation/validation modes. This keeps the FDE mental model simple: after each BMM stage, sync it to ADP.
- L0 handling is scoped as `adp-l0-reference-sync`. L0 remains a BMM-managed workstream; ADP only indexes and extracts cross-line implications.
- `adp-meeting-sync` is added as a separate workflow rather than being hidden inside `adp-status-sync`, because real X-Large delivery depends on meeting/offline communication closure.
- Shopify/migration specificity is adopted as a migration/cutover profile in L0 and readiness, not as a Shopify-only module fork.

Overlap review:

- `adp-agent-program-lead` synthesizes and coaches; it should not duplicate workflow execution logic.
- `adp-status-sync` handles lightweight volatile updates; `adp-bmm-checkpoint-sync` handles stage-level BMM artifact synchronization.
- `adp-meeting-sync` classifies meeting/offline communication inputs and routes them to WDR, decisions, action lists, daily logs, or business packets; `adp-status-sync` may consume those outputs for lightweight state refresh.
- `adp-risk-dependency-change-review` owns risk/dependency/change normalization; Program Lead uses its outputs for readouts and escalation.
- `adp-acceptance-readiness-review` owns scoring and evidence coverage; Program Lead uses its outputs for acceptance views.
- `adp-l0-reference-sync` owns L0 reference indexing, implication extraction, gap checks, and routing questions back to L0/business decision processes; readiness review consumes its outputs.

Structured output candidates:

- HTML management summary from `adp-agent-program-lead`.
- HTML/Markdown FDE action list from `adp-agent-program-lead` or `adp-status-sync`.
- Markdown meeting closure report from `adp-meeting-sync`.
- Markdown/HTML Business Decision Packet from `adp-risk-dependency-change-review`.
- HTML acceptance readiness report from `adp-acceptance-readiness-review`.
- HTML cutover readiness report from `adp-acceptance-readiness-review`.
- HTML risk matrix and dependency map from `adp-risk-dependency-change-review`.
- Markdown/HTML L0 reference impact scan from `adp-l0-reference-sync`.

## Configuration

默认策略：setup 不要求用户先回答复杂问卷。模块应以默认模板直接可用，优先降低启动成本。项目级差异通过后续编辑 `_bmad/memory/adp/` 下的 schema、cadence、L0 reference summary 和 project charter 来调整。

建议默认配置：

| Variable | Prompt | Default | Result Template | User Setting |
| -------- | ------ | ------- | --------------- | ------------ |
| `adp_memory_root` | ADP shared memory root | `_bmad/memory/adp` | Memory files are created under `{adp_memory_root}`. | No |
| `adp_workstream_root` | Workstream records root | `_bmad/memory/adp/workstreams` | Workstream records are stored under `{adp_workstream_root}/{workstream-id}/`. | No |
| `adp_default_cadence` | Default status cadence | `weekly` | Status sync and weekly reports assume `{adp_default_cadence}` unless overridden. | Yes |
| `adp_readiness_mode` | Readiness scoring mode | `score-and-gaps` | Readiness reports include scores, dimension scores, gaps, owners, and actions. | No |
| `adp_l0_mode` | L0 handling mode | `reference-sync` | L0 is represented as source artifact references plus extracted project-level implications, not as ADP-owned baseline files. | No |
| `adp_project_profile` | Project profile | `generic-delivery` | Optional profiles such as `migration-cutover` add cutover, rollback, data sync, and monitoring readiness dimensions. | Yes |

Setup should not block if these are absent. Skills should use these defaults and create missing files on first run.

## External Dependencies

No required external runtime dependencies for the first version.

Optional future dependencies:

- Git CLI: useful for linking PRs, commits, changed files, and evidence, but not required.
- CI provider CLI/API: useful if later integrating automated gate status or evidence registry.
- Issue tracker API: useful if FDE action lists need to sync to Jira/Linear/GitHub Issues.
- Graph visualization tooling: useful if dependency maps become visual artifacts rather than Markdown/HTML tables.

The initial module should rely on local Markdown state and generated reports so it remains portable and installable without external service setup.

## UI and Visualization

First version should produce Markdown and HTML report artifacts rather than a bespoke web app.

High-value report outputs:

- **Project Lead View**: global status, readiness distribution, top risks, blocked workstreams, dependency concerns, escalation items, next actions.
- **FDE Action List**: grouped by FDE/owner, showing readiness gaps, pending confirmations, evidence gaps, dependency actions, and due/trigger conditions.
- **Acceptance Readiness Report**: per-workstream readiness score, dimension scores, missing evidence, unclosed acceptance criteria, customer/business confirmation status.
- **Cutover Readiness Report**: migration/cutover-specific go/no-go view covering data sync, freeze window, rollback/fallback, monitoring, and evidence gaps.
- **Risk Matrix**: risk severity, likelihood, owner, affected workstreams, mitigation, escalation status.
- **Dependency Map**: cross-line dependencies, impacted lines, L0 dependencies, unresolved blockers. In v1 this can be a Markdown/HTML table; visual graph is optional.
- **Weekly Report**: generated from current shared state and daily logs.
- **Business Decision Packet**: background, open question, options, impacts, recommendation, deadline, and affected workstreams.

HTML reports are strong candidates for readiness reviews, risk matrices, dependency maps, and management summaries because they are easier to scan and share with stakeholders.

## Setup Extensions

The setup skill should scaffold the ADP memory and template structure:

1. Create `_bmad/memory/adp/` and the subfolders defined in Memory Architecture.
2. Create default schema files:
   - `schemas/workstream-delivery-record.md`
   - `schemas/readiness-scorecard.md`
   - `schemas/status-taxonomy.md`
   - `schemas/meeting-sync.md`
   - `schemas/decision-taxonomy.md`
3. Create default L0 reference files:
   - `l0/reference-index.md`
   - `l0/extracted-freeze-model.md`
   - `l0/extracted-contract-inventory.md`
   - `l0/extracted-gates.md`
   - `l0/extracted-nfr.md`
   - `l0/extracted-evidence-rules.md`
   - `l0/extracted-impacts.md`
   - `l0/extracted-decision-gates.md`
   - `l0/exceptions-and-open-questions.md`
4. Create meeting and decision folders:
   - `meetings/`
   - `decisions/decision-log.md`
   - `decisions/business-decision-packets/`
5. Create starter view files under `views/`.
6. Create `project-charter.md`, `cadence.md`, and `index.md`.
7. Optionally create an example workstream record to show expected structure.

Setup should be idempotent: if a file exists, preserve user content and only report missing recommended files.

## Integration

AI Delivery PMO is a standalone module with explicit integration points to BMad core/BMM.

Integration model:

- BMM remains responsible for domain lifecycle artifacts: brainstorming, PRD, architecture, epics/stories, implementation, validation.
- ADP workflows consume BMM artifact paths and checkpoint summaries, then update Workstream Delivery Records.
- ADP views and reports derive from Workstream Delivery Records, L0 reference summaries, readiness scorecards, decisions, evidence, risks, and dependencies.
- ADP should not require BMM internals to be modified. It should work as a coordination layer on top of BMM outputs.

Independent value if BMM is absent: ADP can still manage workstream status, readiness, risks, dependencies, evidence, and project-level views. With BMM present, its artifact index becomes much richer and less manual.

## Creative Use Cases

- Pre-meeting brief: generate a project lead briefing before status sync, showing only what needs attention.
- FDE handoff packet: produce a concise handoff when one FDE transfers a workstream to another.
- Acceptance war room: produce a live checklist of evidence gaps, missing confirmations, and readiness blockers before customer review.
- L0 impact sweep: when L0 reference artifacts change, scan all workstreams and produce affected-line actions.
- Scope drift detector: compare current Record changes against baseline PRD/architecture links and decision logs to surface unapproved drift.
- Evidence audit: verify that every acceptance criterion has linked proof, owner, and confirmation status.
- Meeting closure audit: verify every meeting item became a decision, action, WDR update, business packet, daily log entry, or explicit no-op.
- Cutover command center: generate a go/no-go view for migration windows, rollback readiness, monitoring coverage, and unresolved business confirmations.

## Ideas Captured

- 背景：复杂度较高的交付项目需要高强度人机协作。FDE 工程师是桥梁和驾驶员：一边理解业务、澄清需求、推动客户/业务方对齐，一边指挥 AI 完成分析、设计、实现、验证、文档和交付。
- 项目会拆分成 10 多条工作线，由不同 FDE 分别推进。如果每条线各自理解、各自驱动 AI、各自汇报，会出现状态不一致、验收口径不一致、风险暴露不及时、跨线依赖失控、交付证据不完整等问题。
- 模块目标：提供统一的项目管理逻辑、交付节奏、状态模型和 AI 协作方式。
- 要解决的问题：
  - 让 10 多条 FDE 工作线保持一致推进方式。
  - 让每条线都有清晰的需求、范围、验收标准、风险、依赖和交付证据。
  - 让项目负责人随时看到全局状态、跨线风险和下一步动作。
  - 让 FDE 更稳定地指挥 AI，而不是每个人临场发挥。
  - 让业务澄清、AI 执行、验收交付之间形成闭环。
  - 让状态汇报、风险矩阵、依赖图、周报等产物能从统一状态中派生。
- 预期用户：FDE 工程师、项目负责人、交付负责人，以及需要看项目状态的业务方或管理层。
- 原则：FDE 是核心驾驶员，不希望 AI 取代 FDE。AI 应帮助 FDE 提高澄清、拆解、推进、验证、总结和交付的质量。
- 初步模块设想：倾向于混合型 BMad module，而不是单个 agent。
- 可能包含一个项目级协调 Agent，类似 FDE Program Lead / AI PMO，负责全局状态、风险、依赖、节奏、升级和跨线一致性。
- 可能包含多个 Workflow，用于约束每条线的固定动作，例如项目启动、工作线拆分、需求澄清、状态同步、风险依赖检查、变更控制、验收 readiness review。
- 需要共享项目记忆或状态模型，记录每条工作线的目标、owner、范围、假设、验收标准、风险、阻塞、依赖、决策、交付证据和下一步动作。
- 需要多个派生视图，例如项目周报、风险矩阵、跨线依赖视图、FDE 行动清单、管理层摘要。
- 每条业务/交付工作线会通过 BMad core 的 bmm 模块完成各自 domain 生命周期，包括 bmad-brainstorming、bmad-prd 等能力。
- 其中一条线 L0 作为缝合线，用来制定全局规范和协调。
- 重要边界修正：AI Delivery PMO 不应把“最小状态单元”设计成一套和 BMM 并列的新交付体系。每条工作线仍然使用 BMM 正常推进：brainstorming -> PRD -> architecture -> epic -> story -> code -> validation。
- ADP 模块只要求每个 BMM 阶段把“项目级可同步的信息”补齐，并沉淀为统一状态。核心关系是：BMM 核心交付产物 + 项目级协同所需的管理外壳。
- 最小状态单元候选：Workstream Delivery Record。每条线一个 Record，它不是替代 PRD、架构、story 或代码，而是这些产物的索引、状态摘要和管理补充。
- Workstream Delivery Record 需要回答：“这条线在整个项目里现在是什么状态、影响谁、能不能交付。”BMM 产物回答：“这条线怎么做成。”
- Workstream Delivery Record 可能包含：
  - 工作线身份：编号、名称、owner、业务方、当前阶段。
  - BMM 产物索引：PRD 路径、架构路径、epic/story 路径、代码、PR、部署、测试证据路径。
  - 范围口径：本线做什么、不做什么、关键假设。
  - 验收口径：验收标准、验收人、证据要求、当前 readiness。
  - 项目级状态：进度、阻塞、风险、依赖、变更、下一步动作。
  - 跨线关系：依赖哪些线、影响哪些线、是否依赖 L0 规范。
  - 决策记录：关键取舍、业务确认、范围变更、风险接受。
  - 交付证据：截图、测试结果、演示链接、文档、代码链接、客户确认。
- FDE 正常推进工作线时，不重复写管理报告，而是在关键节点更新 Workstream Delivery Record：
  - 需求澄清后：关联 PRD，补充范围、验收标准、未决问题、依赖和业务确认状态。
  - 架构后：关联架构文档，补充技术依赖、跨线接口、L0 规范影响、风险。
  - epic/story 拆分后：关联 epic/story，补充交付计划、关键里程碑、阻塞点。
  - 代码实现/验证后：关联 PR、测试、部署、截图、验收材料，更新 readiness。
  - 状态同步时：只更新少量字段，例如状态、风险、依赖、变更、下一步动作。
- 项目级 Agent 不应依赖读完所有 PRD、架构、story 和代码才能理解全局状态。它应该优先读取所有 Workstream Delivery Record，再派生全局状态看板、风险矩阵、跨线依赖图、FDE action list、周报、管理层摘要和验收 readiness report。
- 设计原则：避免让 FDE 双写太多内容。BMM 产物是事实来源；Workstream Delivery Record 是项目级摘要和索引。
- Record 不复制完整需求细节，只记录 PRD 在哪里、当前是否 baseline、哪些需求影响其他线、哪些验收标准需要项目级关注、还有什么未确认。
- 模块强制产出不是“再写一份交付文档”，而是每条线必须维护一个轻量但结构化的项目级同步面。这是多 FDE 协作中真正缺失的部分。
- L0 缝合线的定位：为各线并行迁移提供公共契约、门禁、CI/registry/evidence 机制和横向 NFR 基线；这些内容由 L0 工作线自身负责产出，ADP 只同步引用和跨线影响。
- L0 可能沉淀的内容：全局术语、公共契约、接口规范、验收口径、状态字段定义、跨线依赖规则、交付证据标准、质量门禁、横向 NFR 基线、CI/registry/evidence 机制。
- Workstream Delivery Record 不应强制一开始字段齐全。它应支持草稿态、缺口态和 ready 态，用 readiness 评分暴露缺口。
- Readiness 模型不是为了阻止推进，而是为了让缺口可见、可讨论、可升级、可闭环。
- Readiness 需要带分数和缺口清单，而不只是红黄绿状态。
- Readiness 输出应包含：总体分数、维度分数、缺口清单、每个缺口对应的补齐动作、责任人、建议截止时间或触发条件。
- Readiness 候选维度：范围清晰度、验收清晰度、BMM 产物完整度、依赖清晰度、风险暴露度、证据完整度、L0 合规度、下一步可执行度。
- 真实场景评审：Shopify 迁移到自建站 X-Large 项目验证 ADP 主架构适用，但必须增加场景化硬约束，否则容易退化为文档归档工具。
- 评审采纳结论：保留 Program Lead + WDR + checkpoint workflows + shared memory 主架构；新增或强化会议同步、业务决策包、切换兜底 readiness、迁移型 L0 reference sync。
- 必须突出 4 个闭环：工作线闭环、会议闭环、决策闭环、验收闭环。
- 会议闭环要求 1/3/5 FDE 内会、双周业务例会、非常规专项沟通、线下沟通补录都能落到结构化状态。每个会议项应成为 daily log、decision、action、WDR 更新、business decision packet 或 explicit no-op。
- 业务决策包应包含：背景、待决问题、选项、影响、推荐方案、截止时间、关联工作线。适用于 FDE 无法独立拍板、需要业务澄清或业务决策的问题。
- 迁移/切换项目的 L0 不只是公共契约，还要承载数据口径、接口契约、切换门禁、回滚标准、冻结期、灰度策略、监控基线和证据规则；ADP 不维护这些规则本身，只抽取对各工作线的约束和缺口。
- 迁移专项 readiness 维度候选：功能迁移 readiness、数据同步 readiness、业务确认 readiness、切换 readiness、回滚/兜底 readiness、监控与证据 readiness、L0 合规 readiness。
- “验收 ready” 不等于“切换 ready”。对于迁移项目，readiness review 必须显式暴露 cutover readiness 和 go/no-go 风险。
- 实际 L0 PRD 校验：`prd-L0-foundation-platform-2026-06-07/prd.md` 证明 L0 不是普通“基线文档”，而是一个 BMM 管理的 governance / mechanism owner 工作线，包含薄边界、Contract Inventory、G19-A/G19-B/G06 Gate、NFR 承接矩阵、D-series 决策门、E-series 证据依赖和 cutover 口径。
- 对 ADP 的设计修正：`adp-l0-reference-sync` 保持轻量，不恢复为 L0 治理 workflow；但必须 schema-aware，能抽取 freeze model、contract inventory、gate model、NFR matrix、decision/evidence gates，并把影响映射到 WDR/readiness。
- ADP 对 L0 的正确问题不是“L0 是否做得好”，而是“L0 已经建立了哪些可引用约束，哪些工作线受影响，哪些 WDR/readiness 缺失引用或证据，哪些问题要回到 L0 工作线或业务决策包”。
- 派生视图优先级：
  - 项目负责人视角：全局状态、跨线风险、依赖、升级项、下一步推进动作。
  - FDE 视角：我负责的工作线、readiness 缺口、下一步动作、待业务确认项、需要补证据的项。
  - 验收视角：哪些线 ready，哪些线缺证据，哪些验收标准未闭环，哪些需要客户/业务方确认。
- L0 视图暂不确定是否需要独立成一个视图。它可能与项目负责人视角和验收视角交叉，更适合作为契约/门禁/合规/NFR 过滤维度，而不是单独 dashboard。

## Build Roadmap

Recommended build order:

1. **`adp-project-kickoff`** - build first because it creates the shared memory structure, schemas, L0 reference placeholders, starter views, and idempotent setup behavior that every other skill depends on.
2. **`adp-workstream-register`** - build second because Workstream Delivery Record is the core project-level state unit. This validates the schema and gives the module a real operating surface.
3. **`adp-bmm-checkpoint-sync`** - build third so BMM lifecycle outputs can be connected to Records at PRD, architecture, epic/story, and implementation/validation checkpoints.
4. **`adp-meeting-sync`** - build fourth because meetings/offline communications are the main high-frequency input source in real X-Large delivery. This creates the meeting -> decision/action/WDR/business packet closure loop.
5. **`adp-status-sync`** - build fifth to support the recurring operating rhythm and keep Records current between major BMM checkpoints, consuming meeting-sync outputs when available.
6. **`adp-risk-dependency-change-review`** - build sixth because it owns risk/dependency/change normalization and must produce Business Decision Packets for issues FDE cannot decide alone.
7. **`adp-l0-reference-sync`** - build seventh to index L0 artifacts, extract cross-line implications, and route gaps/questions back to L0 or business decision processes.
8. **`adp-acceptance-readiness-review`** - build eighth because readiness scoring relies on WDR fields, evidence indexes, status taxonomy, L0 rules, and migration/cutover baselines. This should produce Markdown/HTML acceptance and cutover readiness reports.
9. **`adp-agent-program-lead`** - build after the core workflows are stable so the Agent can synthesize real workflow outputs rather than inventing behavior. It should orchestrate readouts, coaching, weekly reports, closure audits, and stakeholder views.

Rationale:

- Build the state substrate before reports.
- Build low-level Record update workflows before high-level synthesis.
- Keep the first usable loop small: kickoff -> register workstream -> sync BMM checkpoint -> close meeting/action updates -> produce readiness gaps.
- Defer the main Agent until the module has concrete artifacts and stable conventions.

**Next steps:**

1. Start with **Build a Workflow (BW)** for `adp-project-kickoff`, passing this plan document as context.
2. Build each remaining skill in roadmap order using **Build an Agent (BA)** or **Build a Workflow (BW)**.
3. When all skills are built, return to **Create Module (CM)** to scaffold the module infrastructure.
