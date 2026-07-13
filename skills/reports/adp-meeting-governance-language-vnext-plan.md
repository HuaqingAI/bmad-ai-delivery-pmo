---
title: 'ADP 会议治理与语言一致性 vNext 规划'
status: 'complete'
module_name: 'AI Delivery PMO'
module_code: 'adp'
module_description: '让多工作流 AI 交付项目的总体进度、计划偏差、风险和会议闭环对项目负责人清晰可见。'
architecture: 'Hybrid: one Program Lead agent over deterministic planning, status, audit, roadmap, meeting-pack, and sync workflows'
standalone: true
expands_module: ''
skills_planned:
  - adp-plan-baseline
  - adp-program-status
  - adp-status-sync
  - adp-state-audit
  - adp-roadmap-sync
  - adp-meeting-pack
  - adp-meeting-sync
  - adp-project-kickoff
  - adp-agent-program-lead
  - adp-setup
  - adp-workstream-register
  - adp-bmm-checkpoint-sync
  - adp-risk-dependency-change-review
  - adp-l0-reference-sync
  - adp-acceptance-readiness-review
config_variables:
  - default_reporting_cadence
  - status_stale_after_days
  - schedule_variance_tolerance_days
  - meeting_pack_item_limit
created: '2026-07-12'
updated: '2026-07-13'
---

# ADP 会议治理与语言一致性 vNext 规划

## Vision

ADP 应让项目负责人在一次短读中判断项目是否按计划推进、偏差在哪里、判断有多可靠，以及下一步需要谁行动或决策。它既保留底层事实和证据的可追溯性，又针对 FDE 晨会和业务双周会提供不同的信息编排，并让所有生成产物严格遵循项目配置的输出语言。

## Architecture

### 决策

继续采用混合架构：一个 `adp-agent-program-lead` 作为主要对话入口，多个确定性 workflow 分别拥有事实写入、计划基线、状态计算、质量审计、会议编排和会后闭环。此次不新增第二个 agent。

推荐的责任链：

```text
项目目标与门禁
  -> adp-plan-baseline（计划事实）
  -> adp-status-sync / adp-bmm-checkpoint-sync（实际进展事实）
  -> adp-state-audit（完整性与一致性）
  -> adp-program-status（总体状态、偏差、置信度、周期快照）
  -> adp-roadmap-sync（基线感知的时间线）
  -> adp-meeting-pack（按受众编排）
  -> adp-meeting-sync / adp-status-sync（会后回写）
```

`adp-agent-program-lead` 负责理解用户意图、选择 workflow、解释已生成结果和推动闭环，不直接拥有 baseline 解析、偏差算法或会议 Markdown 拼装逻辑。这样交互体验保持统一，关键计算仍可测试、可审计、可 headless 运行。

### 新增的两个能力边界

1. `adp-plan-baseline`：创建、更新和校验项目计划基线。它是项目目标日期、阶段门禁、workstream 里程碑、关键路径和可选权重的唯一 ADP 写入口；不从 action due date 或会议措辞猜测计划。
2. `adp-program-status`：读取 baseline、WDR、checkpoint、readiness、risk、decision 和 audit，生成项目级状态与报告周期快照。它拥有 on-plan / at-risk / off-plan / indeterminate 判定、计划偏差、本周期变化和置信度计算；不修改底层事实。

### 现有能力的调整

- `adp-project-kickoff`：新增 baseline/schema/snapshot 目录和模板；项目无基线时给出显式 onboarding gap。
- `adp-roadmap-sync`：从“仅汇总 source-backed milestone”升级为消费正式 baseline 和实际状态，输出 planned / forecast / actual / variance；仍不把普通 action due date 升级为 milestone。
- `adp-meeting-pack`：变为薄编排层。业务双周会消费 `program-status` 和 roadmap；FDE 晨会消费周期 delta、今日 action 和 escalation。禁止把完整历史字段直接铺进主会场表格。
- `adp-agent-program-lead`：消费统一项目状态产物生成解释和 routing，不再自行拼一套可能不同的总体判断。
- `adp-setup`：安装新 workflow、注册帮助信息，并确保语言/配置解析能力可供生成器使用。

### 为什么不是其他方案

- 不把总体进度直接加进 `adp-meeting-pack`：那会让双周会、weekly report、project-lead view 各自计算一次，最终出现不同结论。
- 不把所有逻辑放进 Program Lead agent：计划偏差和状态判定需要确定性、回归测试和 headless 能力，不应依赖每次对话临场推理。
- 不新增“会议 agent”：两个会议只是同一项目事实的不同消费场景，不需要独立人格或个人记忆。
- 暂不先做 Web dashboard：先稳定 baseline、program-status 和 language contract；HTML/dashboard 可在结构化 JSON 契约稳定后自然增加。

### 状态判定原则

- `status` 与 `report_confidence` 正交：前者回答已有事实是否表明项目偏离计划，后者回答证据覆盖是否充分。低置信度不得覆盖已经证实的延期；例如已确认关键里程碑逾期时，结果仍为 `off-plan + low confidence`。
- `on-plan`：所有适用的关键里程碑/门禁未逾期，forecast 不晚于允许阈值，关键路径无未处理阻塞，并且没有足以推翻该结论的未知关键约束。低置信度可与 `on-plan` 并存，但界面不得渲染为无条件绿色。
- `at-risk`：尚未确认延期，但 forecast、关键依赖、readiness 或即将到期门禁显示偏离风险。
- `off-plan`：已有 source-backed milestone/gate 逾期，或 forecast 超出批准基线/容差。
- `indeterminate`：不存在足够事实对关键约束作出任何可靠状态判断。它不得覆盖已被证实的 `off-plan`，不得渲染为绿色，也不得自动视为延期。
- 总体状态由最关键的门禁、关键路径和项目目标约束决定，不对 14 条 workstream 状态做简单平均。
- 确定性优先级为：已证实的关键约束 `off-plan` > 已证实的 `at-risk` > 无法判断关键约束的 `indeterminate` > `on-plan`。实现必须提供适用性规则和真值表，明确未来 milestone 无 actual、forecast 不适用、部分关键路径缺证据以及多个状态并存时的结果。

### 语言架构

- 所有生成型 workflow 在进入业务逻辑前解析同一份 effective config，并把标准化 locale 放入 render context。
- `document_output_language` 控制 Markdown/HTML/JSON 中的系统文案；`communication_language` 只控制交互说明。
- 标题、表头、枚举显示值、空状态、警告、恢复指引和日期格式由 locale catalog 提供，禁止散落硬编码。
- JSON 保留稳定的英文 machine keys 和 canonical enum values，同时提供本地化 display label；避免下游因切换语言而破坏解析。
- 源事实默认保真；vNext 首版不持久化 AI 生成的源事实翻译。需要翻译时只在派生视图中生成显式显示字段并携带 source lineage，不覆盖原文，也不写回事实层。
- 首版正式支持 `Chinese` 与 `English`；未知语言明确警告并回退 English。

### Memory Architecture

沿用 ADP 的单一共享 memory。Program Lead 和所有 workflow 读取同一项目状态，但严格区分事实、派生快照和会议视图。

```text
_bmad-output/adp/memory/
  plans/
    program-baseline.md
    baseline-history/
  schemas/
    program-baseline.md
    program-status.md
  snapshots/
    program-status/
      <snapshot-id>.json
      latest.json
  views/
    program-status.md
    program-status.json
    roadmap.md
    roadmap.json
    weekly-report.md
    project-lead.md
    meeting-packs/
  workstreams/
  actions/
  decisions/
  meetings/
  audits/
```

`plans/program-baseline.md` 是计划事实源；WDR/checkpoint/action/decision 等仍按现有职责保存实际事实；`snapshots/` 是带 source fingerprint 和 baseline revision 的不可变周期派生产物；`views/` 与 meeting packs 都不是事实源。

### Memory Contract

| 文件 | 用途 | 主要读取者 | 唯一写入者/规则 |
| --- | --- | --- | --- |
| `plans/program-baseline.md` | 项目目标、阶段门禁、里程碑、关键路径、容差、可选权重 | baseline/status/roadmap/audit/Program Lead | `adp-plan-baseline`；更新必须递增 revision 并归档旧版 |
| `schemas/program-baseline.md` | baseline 字段、枚举、日期、source、revision 规则 | kickoff/baseline/audit | 模块模板；setup/update 管理 |
| `schemas/program-status.md` | 总体状态、偏差、置信度、delta、lineage 契约 | status/meeting-pack/Program Lead | 模块模板；setup/update 管理 |
| `snapshots/program-status/<snapshot-id>.json` | 一个报告周期的不可变项目状态，用于与上期比较 | status/meeting-pack/weekly report | `adp-program-status`；历史快照不可 replace，latest 仅为可替换指针 |
| `views/program-status.json` | 最新机器可读管理视图 | roadmap/meeting-pack/Program Lead | `adp-program-status`，派生且可再生 |
| `views/program-status.md` | 最新人类可读管理摘要 | 项目负责人/Program Lead | `adp-program-status`，遵循输出语言 |
| `views/roadmap.*` | baseline-aware timeline、forecast 和 variance | 双周会/Program Lead | `adp-roadmap-sync`，派生且可再生 |
| `views/meeting-packs/*` | 特定会议的短时消费视图 | 参会者/meeting-sync | `adp-meeting-pack`；不得作为后续状态事实 |

每个派生产物必须记录：`generated_at`、`as_of`、`reporting_period`、`baseline_revision`、`source_inventory/fingerprints`、`input_audit_id`、`report_confidence`、`locale` 和 `generator_version`。周期快照使用由 reporting period、as-of、baseline revision 和内容 fingerprint 构成的稳定 `snapshot_id`；同一输入幂等命中同一快照，不同输入永不覆盖历史快照。

### Cross-Agent Patterns

模块只有一个对话 agent，因此不存在 agent 间记忆竞争。跨能力协作采用 artifact handoff：

- Program Lead 是用户入口和 router，但 workflow 也可被用户直接调用。
- baseline -> status -> roadmap/meeting-pack 通过稳定 JSON/Markdown 契约衔接，不通过复制对话摘要衔接。
- `adp-state-audit` 分为两阶段：生成前 input audit 检查事实完整性与一致性，生成后 artifact validation 检查新鲜度、lineage 和渲染契约。派生产物只嵌入本次 `input_audit_id`；artifact validation 另写结果，不反向改写不可变快照。finding severity 与 execution disposition 分开，只有 disposition=`blocked` 才阻止生成；允许降级生成时必须降低置信度并改变视觉状态。
- 会议前由 meeting-pack 消费最新快照；会议后由 meeting-sync 分类，再交 status-sync 写入实际状态。meeting-pack 自身永不回写 baseline 或 WDR。会后 lineage 必须保留 meeting instance ID、scenario、program-status snapshot ID、baseline revision、source fingerprints、input audit ID 和 generator version，并以 meeting instance ID 防止重复回写。
- Program Lead 发现 baseline 缺失时路由 `adp-plan-baseline`；发现实际状态陈旧时路由 status-sync；发现决策或 readiness 问题时继续使用现有专项 workflow。

## Skills

### Shared Rendering Contract（内部组件，不是用户入口）

**Core Outcome:** 所有 ADP 生成型 skill 使用同一 effective config、locale catalog、canonical enum 和 provenance metadata，杜绝“配置是中文但脚本仍硬编码英文”。

**The Non-Negotiable:** `document_output_language` 必须实际控制用户可见文档；缺失或不支持时必须在产物和运行结果中披露 fallback。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Resolve effective config | 按统一优先级解析 core/adp 设置并记录值来源 | project root、可选 CLI overrides | effective config JSON/context、warnings |
| Localize system text | 本地化标题、表头、状态标签、空状态、警告和恢复指引 | locale、message key、参数 | Chinese/English display text |
| Preserve machine contract | 语言切换不破坏下游解析 | canonical keys/enums、locale | stable JSON + localized labels |
| Preserve source facts | 原文与翻译可追溯，含义不被静默覆盖 | source text、可选 translated text、source anchor | original/translated/locale/lineage fields |

**Tool Dependencies:** Python 3.10+；不依赖翻译 API。

**Design Notes:** 从现有 `adp-bmm-checkpoint-sync/scripts/resolve_bmad_config.py` 演进，不再让每个 renderer 自己实现配置优先级。模块内所有用户可见字面量必须通过 catalog 或明确标记为 canonical technical token。

---

### `adp-plan-baseline`

**Type:** workflow（新增）

**Purpose:** 创建、更新、检查和版本化项目计划基线。

**Core Outcome:** 项目目标、阶段门禁、workstream 里程碑和关键路径成为可审计的一等事实，使 ADP 能可靠比较 planned / forecast / actual。

**The Non-Negotiable:** 不从 action due date、会议语气或模型推断生成已批准计划；所有 baseline 项必须有 source、owner、revision 和确认状态。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Create baseline | 从用户确认的目标和现有候选建立 revision 1 | project root、目标日期、gates、milestones、dependencies、sources | `plans/program-baseline.md`、validation report |
| Propose candidates | 从 charter、WDR roadmap、checkpoint 和已有 roadmap 提取待确认候选 | project state、可选 workstream scope | dry-run candidate JSON；不写 baseline |
| Update baseline | 受控调整日期、范围、owner、依赖、容差或权重 | expected revision、change reason、decision/source | 新 revision、`baseline-history/` 旧版、diff |
| Validate baseline | 检查 ID、日期、依赖、循环、source、owner、关键路径和权重 | baseline path | pass/warning/blocked JSON/Markdown |
| Inspect baseline | 面向用户说明当前基线、缺口和历史 | project root、可选 as-of | 本地化摘要、revision lineage |

**Memory:** 读取 charter、WDR roadmap、checkpoint candidates、decisions；唯一写入 `plans/program-baseline.md` 和 `plans/baseline-history/`。

**Init Responsibility:** 首次运行发现无 baseline 时进入 create/propose 流程；已有 baseline 时绝不覆盖，必须使用 update 和 expected revision。

**Activation Modes:** interactive 与 headless；headless 只有在输入完整且明确 `--execute` 时才写入。

**Tool Dependencies:** Python 3.10+；shared rendering/config contract。

**Design Notes:** baseline Markdown 必须同时对人可读、对 parser 稳定；每个 milestone 使用稳定 ID。可选权重只在总和、来源和完成口径可审计时生效。

**Relationships:** kickoff 之后、program-status 之前；baseline change 可引用 risk/change review 的批准 decision。

---

### `adp-program-status`

**Type:** workflow（新增）

**Purpose:** 形成唯一的项目级管理状态、计划偏差、报告置信度和周期变化。

**Core Outcome:** weekly report、project lead view、roadmap 和会议包对“总体进度如何、是否符合计划”给出一致且可追溯的答案。

**The Non-Negotiable:** 将 `indeterminate` 与 `off-plan` 分开；信息不足绝不渲染为绿色，也不伪造精确百分比。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Compute current status | 计算 overall、phase/gate、milestone、critical-path、variance 和 confidence | baseline、WDR/checkpoint actuals、readiness、risk、decision、audit | program-status model JSON |
| Compare reporting period | 识别自上一快照以来的新增、改善、恶化、完成和漂移 | current model、previous snapshot、period | delta/trend model |
| Render management views | 生成结论先行的管理摘要 | model、locale、audience | `views/program-status.md/json`、`weekly-report.md`、`project-lead.md`；HTML 为后续候选 |
| Persist snapshot | 保存可复现周期状态用于后续比较 | model、source fingerprints、baseline revision | `snapshots/program-status/<snapshot-id>.json` + `latest.json` 指针 |
| Explain judgment | 逐项说明总体状态与置信度为何如此 | status model、可选 question | source-backed explanation 和 routing |

**Memory:** 读取所有项目级事实和 audit；只写 program-status views、weekly/project-lead 派生视图和 snapshots。

**Init Responsibility:** baseline 缺失时仍生成 `indeterminate` readout，列出最小恢复动作；不替用户创建 baseline。

**Activation Modes:** interactive/headless，支持 `--dry-run`、`--as-of`、`--period-start`、`--workstream`。

**Tool Dependencies:** Python 3.10+；shared rendering/config contract；`adp-state-audit`。

**Design Notes:** overall status 采用关键门禁/关键路径优先规则；status 与 confidence 正交；加权百分比默认禁用。所有 derived judgment 带算法版本与输入 fingerprint。状态实现必须由版本化真值表驱动，并输出命中的规则 ID。

**Relationships:** 消费 baseline/status/audit，供 roadmap、meeting-pack 和 Program Lead 使用。

---

### `adp-status-sync`

**Type:** workflow（扩展）

**Purpose:** 在现有 workstream 状态与 action 同步之外，写入可与 program baseline 对齐的 milestone actual/forecast 状态。

**Core Outcome:** owner 的轻量更新可以刷新实际进展与预测，而不修改批准计划。

**The Non-Negotiable:** status-sync 只能更新 actual/forecast/evidence，不能改 baseline planned date、容差或关键路径。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Sync volatile status | 保持现有 progress/blocker/risk/action 能力 | owner update、meeting intake | WDR、ledger、daily log |
| Sync milestone actual | 将 milestone 状态、forecast、actual、evidence 与 baseline ID 对齐 | milestone ID、status、forecast/actual、source | WDR milestone-status row、daily log |
| Validate baseline mapping | 暴露 unknown/duplicate milestone ID | update、current baseline | unresolved gaps；不创建隐式 milestone |
| Find stale status | 同时检查 workstream 与关键 milestone 的更新时效 | max age、scope | stale report/candidates |

**Memory:** 读取 baseline ID 和目标 WDR；写 WDR volatile/milestone actual、action ledger、daily。

**Activation Modes:** interactive/headless；保持 JSON batch intake。

**Tool Dependencies:** Python 3.10+；shared rendering/config contract。

**Relationships:** meeting/checkpoint 的 actual updates 进入此 workflow；program-status 消费其结果。

---

### `adp-state-audit`

**Type:** workflow（扩展）

**Purpose:** 将 baseline 和 program-status 质量纳入现有 freshness/completeness/consistency/closure/merge audit。

**Core Outcome:** 在管理读出前识别“计划不存在、计划无来源、实际无法映射、快照陈旧、视图语言契约失效”等问题。

**The Non-Negotiable:** audit 只报告和分级，不自动修复 baseline 或重写项目事实。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Audit baseline integrity | 检查 revision、sources、owners、dates、dependency cycles、weights | baseline/schema | findings + recommended workflow |
| Audit plan/actual mapping | 找出 baseline milestone 无 actual、actual 无 baseline、状态冲突 | baseline、WDR/checkpoints | mapping gaps/conflicts |
| Audit inputs | 在生成管理读出前检查事实完整性、一致性和 mapping | baseline、WDR/checkpoints、effective config | immutable input audit + execution disposition |
| Validate artifact freshness | 生成后检查 program status、roadmap、weekly、meeting pack 是否基于声明的事实 | fingerprints、views、facts | artifact validation + refresh requirements |
| Validate render contract | 生成后检查 locale metadata、catalog coverage 和 fallback disclosure | generated artifacts、effective config | language validation findings |

**Memory:** 只读事实与派生视图；写 `audits/`。

**Activation Modes:** interactive/headless；新增 `program-status` 与 `baseline` scenario。

**Tool Dependencies:** Python 3.10+；shared config resolver。

**Relationships:** baseline/status/roadmap/meeting-pack 的质量门。

---

### `adp-roadmap-sync`

**Type:** workflow（扩展）

**Purpose:** 将正式 baseline 与实际状态合成为可信 timeline 和 variance view。

**Core Outcome:** 双周会能够看见计划、预测、实际、偏差、变更和未排期事项，而不是一长串无日期 decision。

**The Non-Negotiable:** baseline planned date 与 actual/forecast 必须保持来源区分；普通 action due date 仍不得升级为 milestone。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Render baseline timeline | 展示 approved planned milestones/gates | baseline、scope、locale | roadmap timeline Markdown/JSON |
| Overlay actual/forecast | 计算 variance、at-risk dates、completed/late | program-status model | source-backed overlay |
| Show baseline changes | 显示 revision diff 与批准原因 | baseline history、decisions | changed-since section |
| Expose gaps | 分开呈现 unscheduled、unmapped、indeterminate 和 excluded actions | audit/status | recovery-oriented sections |

**Memory:** 读取 baseline/program-status/audit；只写 roadmap views。

**Activation Modes:** interactive/headless/dry-run。

**Tool Dependencies:** Python 3.10+；shared rendering contract。

**Relationships:** program-status 之后、business meeting-pack 之前。

---

### `adp-meeting-pack`

**Type:** workflow（重构）

**Purpose:** 从同一 program-status 模型生成适合不同会议决策节奏的短会议包。

**Core Outcome:** 业务双周会能在首屏判断项目是否按计划；FDE 晨会能立即聚焦今天需要闭环的变化和阻塞。

**The Non-Negotiable:** meeting pack 是有信息预算的派生视图，不复制完整历史，也不自行计算第二套总体状态。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Render business biweekly | 结论、baseline/forecast、阶段门禁、Top 偏差、决策、confidence | program-status、roadmap、audit、period | localized Markdown/JSON；HTML 候选 |
| Render FDE morning | delta、今日 blocker、承诺、到期、cross-line escalation | current program-status、confirmed meeting window、窗口内事实/ledger、audit | localized Markdown/JSON |
| Resolve FDE meeting window | 以上次成功归档的实际 FDE 会议为增量锚点，识别常规与异常窗口 | meeting archive、cadence、project timezone、as-of、可选 runtime override | confirmed window 或 `needs_confirmation` 候选范围 |
| Enforce information budget | 按不可裁剪类别、严重度、关键路径、到期时间和稳定 tie-breaker 排序，每节最多 N 项 | item limit、versioned rank rules | truncated counts、appendix/JSON lineage、命中的 rank rule ID |
| Produce meeting distillate | 为 meeting-sync 提供稳定上下文与 source lineage | rendered boards | scenario-agnostic schema、scenario-preserving distillate JSON |
| Validate freshness/collision | 防止陈旧输入与无意覆盖 | fingerprints、planned paths | blocked/warning run result |

**Memory:** 读取 program-status/roadmap/audit/action/decision、`cadence.md` 和实际 meeting archive/cursor；只写 `views/meeting-packs/`。

**Activation Modes:** interactive/headless；支持 scenario、period、scope、dry-run/replace。

**Tool Dependencies:** Python 3.10+；shared rendering contract。

**Design Notes:** `business-biweekly-zh` 等语言后缀目录废止；同一 scenario 目录中的 artifact metadata 记录 locale。状态 canonical value 保持英文，display label 本地化。

FDE 默认例会节奏为项目时区内的周一、周三、周五，并由 kickoff 写入 `cadence.md`。常规运行要求当前日期为例会日，且上一次成功 `adp-meeting-sync` 归档的实际 FDE 会议正好对应前一个预期例会日；增量窗口采用 `(last_archived_meeting_ended_at, as_of]`，避免重复摄入上次会议已经处理的事项。

以下任一情况属于异常窗口：当前日期不是例会日；上一条实际归档会议不是前一个预期例会日；找不到历史会议；或 runtime 明确标记节假日/临时改期。交互模式必须展示上次实际会议、预期锚点、建议开始/结束时间和缺口原因，由用户确认或修改。headless 模式返回 `needs_confirmation`，只有显式提供 `--period-start` 与 `--period-end` 才继续。一次异常确认只属于当前 meeting instance，不修改长期 cadence。

**Relationships:** meeting 前消费状态；meeting 后把 distillate/lineage 交给 meeting-sync。

---

### `adp-meeting-sync`

**Type:** workflow（扩展）

**Purpose:** 在现有会议分类和会后回写基础上，建立可幂等的 meeting instance、vNext lineage 和下一次 FDE 增量锚点。

**Core Outcome:** 实际发生并成功同步的会议成为唯一会议时间锚点；会后事实可以追溯到会议包所消费的 program-status、baseline、audit 和源 fingerprints。

**The Non-Negotiable:** 生成过 meeting pack 不代表会议已经发生；只有成功归档的实际会议才能推进 FDE meeting cursor。同一 meeting instance 重放不得重复追加 WDR、daily、decision 或 action。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Preserve vNext lineage | 原样携带会议包的计算与来源身份 | distillate lineage | meeting instance ID、scenario、snapshot ID、baseline revision、source fingerprints、input audit ID、generator version |
| Archive actual meeting | 记录实际开始/结束时间并推进对应 scenario cursor | confirmed meeting plan、raw evidence、actual timestamps | immutable meeting archive + latest successful cursor |
| Enforce replay safety | 同一 meeting instance 重试时检测已落地 destinations | meeting instance ID、plan fingerprint、write receipts | idempotent no-op、resume report 或显式 conflict |
| Emit status handoff | 只把合格 action/actual/forecast 交给 status-sync | classified meeting items、lineage | source-backed status-sync intake |

**Memory:** 读取 meeting-pack distillate、raw evidence 和既有 meeting instance；写 meeting archive、write receipts、scenario cursor 和现有最小事实目的地。

**Activation Modes:** interactive/headless；headless 写入仍要求完整 plan 与显式 execute。

**Tool Dependencies:** Python 3.10+；shared rendering/config contract。

**Relationships:** meeting-pack 之后、status-sync 之前；其成功归档时间是下一次 FDE meeting-pack 的增量锚点。

---

### `adp-project-kickoff`

**Type:** workflow（扩展）

**Purpose:** 幂等初始化 baseline、program-status 和报告周期所需结构，并识别尚未建立计划基线的项目。

**Core Outcome:** 新项目从一开始就知道“哪些是计划事实、哪些仍待确认”，旧项目升级时不丢内容。

**The Non-Negotiable:** kickoff 只创建结构和 intake，不猜测或批准 baseline。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Scaffold vNext memory | 创建 plans/snapshots/schemas/views | profile、config、project root | idempotent scaffold report |
| Discover baseline candidates | 从现有计划产物提取待确认候选 | BMM artifacts、charter、WDR | baseline intake JSON/Markdown |
| Capture cadence facts | 记录 reporting period 与两个会议节奏 | user-confirmed cadence | localized `cadence.md` |
| Upgrade existing memory | 只补缺失目录/模板并报告 legacy gaps | existing ADP memory | migration report/recovery |

**Memory:** 创建模板和 intake；不写正式 baseline/WDR。

**Activation Modes:** interactive/headless/dry-run。

**Tool Dependencies:** Python 3.10+；shared config resolver。

**Relationships:** setup 后首个项目 workflow；输出交给 baseline/workstream-register。

---

### `adp-agent-program-lead`

**Type:** agent（扩展现有 agent）

**Persona:** 冷静、证据导向的 AI Delivery PMO Program Lead；先给管理判断，再解释来源与下一步，不用大量底层表格替代结论。

**Core Outcome:** 用户通过一个入口理解总体状态、偏差、置信度并路由到正确闭环 workflow。

**The Non-Negotiable:** 只解释 canonical program-status，不在对话中另算一套总体状态或覆盖 audit/baseline 结论。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Global project readout | 回答总体进度、计划符合度和关键原因 | latest program-status/roadmap/audit | localized concise readout |
| Period review | 解释本周期变化、趋势和 Top 偏差 | current/previous snapshots | management narrative |
| Meeting preparation | 根据受众运行正确 meeting-pack | meeting intent、period/scope | pack paths + pre-meeting gaps |
| Recovery routing | 将 baseline/status/decision/readiness/language gap 路由到 owner workflow | findings、user intent | explicit workflow recommendation |
| Drill-down explanation | 从总体结论下钻到 workstream/source | question、lineage | source-backed explanation |

**Memory:** 激活时先读 `views/program-status.json` metadata，再选择性读取 roadmap/audit/source；不维护个人 memory。

**Init Responsibility:** program-status 缺失时路由生成；baseline 缺失时路由 baseline workflow。

**Activation Modes:** interactive 为主；headless 使用底层 workflow，不依赖 agent prose。

**Tool Dependencies:** 现有本地脚本和 ADP workflows。

**Relationships:** 用户主入口与 router；不是事实写入者。

---

### `adp-setup`

**Type:** workflow（扩展）

**Purpose:** 安装/升级 vNext skill、团队配置、帮助注册和共享渲染契约。

**Core Outcome:** fresh install 与 update 都得到一致配置、完整能力注册和非破坏式迁移提示。

**The Non-Negotiable:** update 不删除或覆盖用户的 ADP memory/baseline；配置迁移使用 anti-zombie 规则并明确报告默认值来源。

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Collect team defaults | 收集 cadence/stale/tolerance/item limit | existing config、user answers/defaults | validated `adp` config section |
| Register capabilities | 注册新旧 skills 与推荐顺序 | module metadata/help source | merged module-help.csv |
| Install rendering contract | 让所有 renderer 可访问统一 config/locale 资源 | installed skills root | resolver/catalog availability report |
| Inspect upgrade | 识别旧配置、缺失 skills 和 memory migration needs | project root/module version | dry-run install state |
| Preserve project state | 安装只管理 config/help/skill resources | existing memory | explicit untouched paths |

**Activation Modes:** interactive/headless。

**Tool Dependencies:** Python 3.10+、现有 setup scripts。

**Relationships:** module 安装入口；完成后推荐 kickoff/upgrade，再 baseline。

---

### Module-Wide Language Retrofit

以下现有 skills 不增加新业务能力，但其所有用户可见输出必须接入 Shared Rendering Contract，并补 Chinese/English golden tests：

| Skill | 必须本地化的输出 |
| --- | --- |
| `adp-workstream-register` | WDR 初始结构、readiness/evidence/decision 模板和运行摘要 |
| `adp-bmm-checkpoint-sync` | WDR checkpoint append、gap rows、daily log、候选/执行报告 |
| `adp-meeting-sync` | meeting archive、daily/decision/WDR append、business packet、运行报告 |
| `adp-risk-dependency-change-review` | risk/dependency views、business packet、review 报告 |
| `adp-l0-reference-sync` | L0 summaries、gap suggestions、运行报告 |
| `adp-acceptance-readiness-review` | readiness Markdown/HTML、scorecard 和运行报告 |

本地化测试必须区分系统文案与源事实：golden fixture 中故意放入另一语言的 source text，验证它保持原文且 lineage 不丢失。

## Configuration

ADP 使用 BMad core 的 `communication_language`、`document_output_language` 和 `output_folder`，不重复创建模块级语言变量。以下变量均为项目团队设置，写入共享 `config.yaml` 的 `adp` section，不属于 `config.user.yaml`。

| Variable | Prompt | Default | Result Template | User Setting |
| --- | --- | --- | --- | --- |
| `default_reporting_cadence` | ADP 默认以什么节奏形成项目状态周期？ | `weekly`；可选 `weekly` / `biweekly` / `custom` | 原值；`custom` 时由 kickoff/runtime 收集具体规则 | false |
| `status_stale_after_days` | Workstream 状态多少天未更新后标记为陈旧？ | `7` | 校验为 1-90 的整数 | false |
| `schedule_variance_tolerance_days` | Forecast 晚于 baseline 多少天后判定 off-plan？ | `0` | 校验为 0-90 的整数；单个 milestone 可在 baseline 覆盖 | false |
| `meeting_pack_item_limit` | 每个会议主板块最多展示多少条事项？ | `10` | 校验为 3-30 的整数 | false |

运行时覆盖规则：显式 CLI 参数 > baseline 中针对单项的设置 > `adp` 模块配置 > workflow 内置默认值。任何使用了 fallback 的生成产物都要在 metadata 中记录 effective value 和来源。

不作为 setup 变量的内容：

- 项目目标日期、阶段门禁、milestone、关键路径、权重和 owner：由 `adp-plan-baseline` 收集并版本化。
- FDE 晨会与业务双周会具体星期/时间、参会人和接收人：由 kickoff 写入 `cadence.md`，属于项目运营事实。FDE 默认星期为周一/周三/周五；项目可显式调整长期 cadence。
- 源事实是否翻译：首版固定为“事实层原文保真、派生视图可选临时翻译且不持久化”，避免个人偏好破坏团队产物一致性。

## External Dependencies

不新增外部 SaaS、MCP 或网络依赖。

- Python 3.10+：所有确定性 renderer、validator 和 writer 的运行时；沿用现有 ADP 要求。
- `uv`：首选脚本启动器，但不是硬依赖；不可用时允许直接使用 Python 3.10+。
- BMad config 文件：语言、输出目录和 ADP 模块默认值的配置来源；缺失时必须报告并使用文档化 fallback。
- 不接入机器翻译 API。交互 agent 可在单次派生视图中显式生成带 lineage 的源事实翻译，但不得持久化或写回事实层；无翻译时保留原文。

## UI and Visualization

vNext 首先交付稳定的 Markdown + JSON 管理界面，不建设独立 Web 应用。

业务双周会 Markdown 的首屏固定为：

1. 总体状态与置信度。
2. baseline 目标、当前 forecast、偏差与趋势。
3. 阶段/门禁达成和关键路径。
4. 本周期变化与 Top 偏差。
5. 需要业务决策的事项。

FDE 晨会 Markdown 的首屏固定为：

1. 已确认的增量时间窗口，以及自上次实际归档会议以来的变化。
2. 今日 blocker / escalation。
3. 今日承诺与即将到期。
4. 需要跨线协同的事项。

主板块应用 `meeting_pack_item_limit` 信息预算；待业务决策、关键路径 blocker 和已逾期关键承诺属于不可裁剪类别，其余事项按严重度、关键路径影响、到期时间和稳定 ID 依次排序。被裁剪事项必须在 JSON 中保留，并在 Markdown 中显示“另有 N 项”及下钻位置。详细 source inventory、完整 workstream 状态、历史 action 和长风险说明进入附录或 JSON，不进入首屏。

待 JSON 契约稳定后，可增加自包含 HTML executive view，展示状态色、里程碑趋势、关键路径和周期变化；HTML 只能消费同一 `program-status.json`，不能建立第二套计算逻辑。独立 dashboard/web app 不进入本轮首批交付。

## Setup Extensions

- `adp-setup/assets/module.yaml` 增加 4 个团队配置问题、版本升级和默认值校验。
- `adp-setup/assets/module-help.csv` 注册 `adp-plan-baseline` 与 `adp-program-status`，更新 kickoff -> baseline -> workstream/status -> program-status -> meeting-pack 的推荐顺序。
- 将现有 `adp-bmm-checkpoint-sync/scripts/resolve_bmad_config.py` 提升为模块共享的 effective-config resolver，扩展读取 `adp.*` 变量、value source、fallback warning 和规范化 locale。所有生成型 workflow 必须通过同一契约调用它。
- `adp-project-kickoff` 创建 `plans/`、`plans/baseline-history/`、`snapshots/program-status/`、两个 schema 和初始 view placeholder，保持幂等且绝不覆盖已有项目内容。
- fresh kickoff 在发现没有 baseline 时生成 baseline intake/template，并引导 `adp-plan-baseline create`；不在没有用户确认的情况下猜测目标日期或计划。
- update 安装检测旧项目是否缺少 baseline/schema/snapshot 目录，只补缺失结构；现有 roadmap、weekly report、meeting pack 和 WDR 保留。
- 升级后首次运行 `adp-program-status` 时，如果没有 baseline，返回 `indeterminate` 管理视图和明确 recovery，不将历史 action due date 自动迁移为 milestone。
- locale catalog 至少包含 `en` 与 `zh`，并由测试扫描生成器中的用户可见硬编码，防止新英文文案绕过 catalog。

## Integration

ADP 继续作为可独立使用的项目协调模块，同时对 BMM 产物提供增强集成。

- 没有 BMM 时：baseline、WDR、owner update、meeting、decision 和 evidence link 足以形成项目级状态。
- 使用 BMM 时：`adp-bmm-checkpoint-sync` 将 PRD/architecture/epic/story/validation checkpoint 作为 actual/gate evidence 输入，但 BMM artifact 仍是交付事实源。
- `adp-plan-baseline` 不修改 BMM artifact；它只引用批准的目标、门禁和里程碑来源。
- BMM baseline checkpoint 与 ADP program baseline 是不同概念：前者描述某个交付 artifact 的基线状态，后者描述项目计划。命名、schema 和帮助文案必须避免混淆。
- 所有 workflow 仍可直接调用；Program Lead 只提供统一入口与解释，不成为运行新 workflow 的前置依赖。

## Creative Use Cases

- **管理层异步预读：** 双周会前自动生成一页 program-status 摘要，参会者先看总体状态和待决策项，会议只处理分歧与承诺。
- **计划变更影响预览：** `adp-plan-baseline propose/update --dry-run` 展示目标日期或关键路径调整会影响哪些 milestone、meeting conclusion 和 forecast，但不直接批准变更。
- **状态取证回放：** 使用 baseline history + immutable snapshots 回答“某次双周会为什么判断 at-risk”“之后哪些事实发生变化”。
- **跨语言利益相关方包：** 同一 canonical JSON 分别生成中文和英文会议包；原始业务事实保持不变，系统文案和派生摘要按受众语言呈现。
- **项目组合输入：** 多个项目未来可汇总各自 `program-status.json`，形成 portfolio view，而不读取每个项目的 WDR 细节。
- **质量驱动的会议降级：** audit 发现 baseline 或 actual 覆盖不足时，会议包自动从“进度评审”降级为“状态补齐/计划确认会”，避免用不可靠数据做进度承诺。
- **回顾与校准：** 对比多个周期的 forecast variance 与最终 actual，识别长期低估、状态更新滞后或某类 gate 经常失准，为后续计划改进提供证据。

## Ideas Captured

- 真实试运行项目：`D:\ProgramData\git\repository\github\huaqingai\shopify-migration`。
- 使用者反馈：新版 ADP 可以正常生成产物，但整体体验仍不够好；两个会议场景尤其明显。
- 业务双周会的首要问题不是“缺少更多明细”，而是读者无法快速判断：当前总体进度如何、是否符合原计划、偏差在哪里、还能否按目标推进、需要谁做什么决策。
- 当前业务双周会已经包含 audit、decision、scope change、readiness、roadmap、dependency、meeting closure 等板块，但缺少明确的项目级管理叙事和统一判定。信息存在，不等于结论可见。
- 当前 roadmap 主要列出 source-backed milestone/decision，很多日期为 `TBD`；这保证了不编造计划，但也暴露出“项目计划基线”本身没有被 ADP 作为一等事实维护。没有 baseline，就无法可靠回答 on-plan / at-risk / off-plan。
- “总体进度”不能简单平均 14 条 workstream 的百分比。需要先定义进度的业务含义，可能包括阶段/门禁达成、关键里程碑、范围权重、关键路径、readiness、证据完成度，以及计划偏差。
- 需要区分至少三种状态：事实完整且按计划、事实完整但偏离计划、事实不足所以无法判断。当前大量 `gap`/`TBD` 不应直接等价为项目延期，但必须影响报告置信度。
- FDE 晨会包约 84 KB。Workstream Roundtable 将每条线积累的长 progress/risk/action 历史直接铺开，导致“今天最该处理什么”被历史材料淹没。
- 晨会更需要增量视角：自上次会议以来发生了什么、今日阻塞、今日承诺、即将到期、需要跨线协同的少量事项。历史详情应按需下钻，不应进入主会场阅读路径。
- 业务双周会和 FDE 晨会的受众、时间跨度、决策粒度不同，不能只是对同一份状态数据换几张表。
- `shopify-migration/_bmad/adp/config.yaml` 已配置 `communication_language: Chinese` 和 `document_output_language: Chinese`，配置本身正确。
- `adp-meeting-pack/scripts/render_meeting_pack.py` 当前没有读取任何 BMad 配置，也没有 `--language`/`--locale` 参数；标题、表头、空状态、审计说明、恢复指引等渲染文案均硬编码为英文。
- 同一天出现 `fde-morning`、`fde-morning-zh` 和 `business-biweekly-zh` 目录，说明中文输出目前依赖调用者用自定义输出目录绕行，而不是由正式语言契约保证。
- 源事实中还混有英文状态、英文 action 和英文风险文本。仅翻译模板外壳不能完全解决语言问题，需要定义“系统文案本地化”与“源事实语言规范化/保真展示”的边界。
- 语言要求应是一项可测试的端到端契约：配置解析、脚本参数传递、模板字典、Markdown/JSON 输出、降级行为和测试夹具都需要覆盖。
- 现有 `weekly-report.md` 比业务双周会更接近“管理摘要”，但它不是稳定生成链的一部分，也没有成为 meeting pack 的可追踪上游输入。
- 候选方向（尚未进入架构决策）：建立明确的 program baseline / reporting period / progress model；给两个会议定义严格的信息预算；把 executive summary、plan variance 和 confidence 放在双周会首屏；把 delta、today commitments 和 escalation 放在晨会首屏；统一语言解析器供所有生成型 skill 复用。
- 用户已确认采用建议的计划基线模型：项目目标日期、阶段门禁和各 workstream 关键里程碑共同构成 baseline；若项目尚无正式基线，由 ADP kickoff 引导创建，而不是由会议生成器临时推断。
- 用户已确认业务双周会首屏以管理判断为中心：总体状态、基线与预测、阶段完成情况、本周期变化、Top 偏差、待决策事项、数据置信度。详细 workstream、风险、依赖和来源清单下沉到后续章节。
- 用户已确认语言边界：系统生成的标题、表头、状态、空值、说明和操作指引必须遵循 `document_output_language`；引用的源事实默认保持原文，需要时附目标语言翻译，不静默改写事实含义。
- 用户已确认 vNext 首版不持久化源事实翻译；可选翻译只存在于单次派生视图并携带 lineage。
- 语言契约的默认回退顺序应与 ADP 现有配置加载顺序一致；配置缺失时明确披露并回退英语，不能静默忽略配置。
- 总体进度默认不输出伪精确的平均百分比。优先呈现阶段/门禁达成、里程碑按期率、关键路径偏差和报告置信度；只有项目明确提供可审计的权重与完成口径时才输出加权百分比。
- 用户已确认 status 与 report confidence 正交；已证实的延期不会因为其他证据不足而退回 `indeterminate`。
- 用户已确认 FDE 默认按周一/周三/周五运行，增量窗口以上次成功 `adp-meeting-sync` 归档的实际会议为起点。非例会日、漏掉预期会议、首次运行或节假日/临时改期时，系统展示建议范围并要求确认；生成过 meeting pack 不能推进锚点。
- 业务双周会默认采用“结论先行、证据下钻”；FDE 晨会默认采用“变化先行、今日闭环”。两者共享同一事实层，但不共享同一信息编排。

## Build Roadmap

### 1. Shared Contract Foundation

先从现有 `resolve_bmad_config.py` 建立 shared effective-config/locale contract、Chinese/English catalog、canonical enum/display label 规则和 golden-test harness。

**原因：** 后续所有新 renderer 都依赖这一层；若最后再补语言，会再次产生硬编码和重复迁移。

**完成门：** 配置优先级、缺失 fallback、未知语言、稳定 JSON key、源事实保真和“翻译不持久化”均有自动化测试。

### 2. Build `adp-plan-baseline`

实现 baseline schema、create/propose/update/validate/inspect、revision history 和 dry-run。

**原因：** 没有正式计划事实，任何总体进度和偏差计算都只能继续猜测。

**完成门：** 无来源计划不能 execute；revision 冲突阻止覆盖；依赖循环、重复 ID、非法日期和不可审计权重被发现。

### 3. Extend `adp-project-kickoff`

加入 plans/snapshots/schema/view scaffold、baseline candidate intake、cadence capture 和旧项目幂等升级。

**原因：** 新能力需要在 fresh project 与现有 `shopify-migration` 上都能非破坏落地。

**完成门：** fresh/dry-run/update 测试通过；任何已有 memory 文件不被覆盖；`cadence.md` 明确项目时区、FDE 默认周一/周三/周五和可选长期覆盖。

### 4. Extend `adp-status-sync`

增加 milestone ID、forecast、actual、status、evidence 的结构化更新与 baseline mapping validation。

**原因：** baseline 只有 planned，status-sync 提供可以与之比较的 actual/forecast。

**完成门：** unknown milestone 不隐式创建；更新不改变 baseline；batch intake、daily lineage 和现有 action 行为保持兼容。

### 5. Extend `adp-state-audit`

实现生成前 input audit 与生成后 artifact validation，覆盖 baseline integrity、plan/actual mapping、snapshot freshness 和 render-language checks。

**原因：** program-status 必须建立在可解释的质量门上，而不是自己隐藏输入缺陷。

**完成门：** baseline missing、actual missing、unmapped actual、stale snapshot、locale fallback 均得到正确 finding severity、execution disposition 与 recovery workflow；artifact validation 不改写不可变 snapshot。

### 6. Build `adp-program-status`

实现状态模型、confidence、period comparison、snapshot、program-status/weekly/project-lead renderer。

**原因：** 这是所有管理读出的唯一计算核心。

**完成门：**

- baseline/actual 完整且无偏差 -> `on-plan`。
- forecast 已晚于 planned 但未超过批准容差，或关键依赖/readiness 给出 source-backed 风险 -> `at-risk`。
- source-backed variance 超过容差 -> `off-plan`。
- 没有足够事实判断任何关键约束 -> `indeterminate`，绝不输出绿色；已证实的关键 `off-plan` 不得被其他未知项覆盖。
- status 与 confidence 独立输出；实现覆盖状态优先级真值表、适用性规则与命中 rule ID。
- snapshot ID 对同一输入稳定、对不同输入不碰撞；历史 snapshot 不允许 replace。
- 同一输入可重复生成一致 JSON；每个判断可追溯到 source 和算法版本。

### 7. Extend `adp-roadmap-sync`

消费 baseline/program-status，生成 planned/forecast/actual/variance、baseline changes、unscheduled/unmapped sections。

**原因：** 双周会需要可信时间线，但 roadmap 不应重新计算总体判断。

**完成门：** action due date 不会变 milestone；planned 与 forecast 来源严格分开；revision diff 可追溯。

### 8. Refactor `adp-meeting-pack`

重建两个 scenario 的信息架构、信息预算、period delta、distillate 和本地化输出。

**原因：** 只有在统一状态与 roadmap 稳定后，会议编排才不会再次夹带计算逻辑。

**完成门：**

- 业务双周会首屏包含总体状态、置信度、baseline/forecast、门禁、Top 偏差和待决策项。
- FDE 晨会首屏显示已确认窗口、增量变化、今日 blocker/承诺/到期/跨线升级，不再铺完整历史 roundtable。
- 常规周一/周三/周五窗口自动从上一实际归档会议推进；非例会日、漏会、首次运行和临时改期进入确认流。
- headless 异常窗口无显式 `--period-start/--period-end` 时返回 `needs_confirmation`。
- 每节不超过 `meeting_pack_item_limit`；不可裁剪类别、排序和稳定 tie-breaker 有 golden tests，裁剪项在 JSON/appendix 可下钻。
- `Chinese` 配置下系统文案为中文；源事实原文不被改写。
- 不再生成 `*-zh` scenario 目录；locale 进入 artifact metadata。

### 9. Extend `adp-meeting-sync`

扩展 meeting instance、vNext lineage、实际会议归档 cursor 和 replay-safe write receipts。

**原因：** FDE 增量只能由实际发生且成功同步的会议推进；会后闭环还必须保留 program-status、baseline、audit 和 source fingerprint 身份。

**完成门：** 生成 meeting pack 不推进 cursor；成功归档才推进；同一 meeting instance 重放不重复写入；lineage 包含 scenario、snapshot ID、baseline revision、source fingerprints、input audit ID 和 generator version。

### 10. Update `adp-agent-program-lead`

改为消费 canonical program-status，提供总体解释、周期回顾、会议准备和 recovery routing。

**原因：** agent 应在稳定事实之上提供交互价值，而不是拥有另一套算法。

**完成门：** 对相同 program-status 的总体判断与 workflow 一致；缺产物时只路由，不臆测。

### 11. Complete Module-Wide Language Retrofit

**实施状态：已完成（2026-07-13）。** 六个目标技能已统一接入 shared effective config 与 locale catalog；中英文 golden tests、catalog 对称性、显式硬编码豁免扫描、事实字段 / canonical enum / lineage 不变性回归均已通过。

依次迁移 workstream-register、checkpoint-sync、meeting-sync、risk/dependency review、L0 sync、readiness review 的系统文案，并加入中英文 golden tests。

**原因：** 用户反馈指向所有生成文档，不能以 meeting-pack 修复代替模块级完成。

**完成门：** 扫描无未豁免的用户可见英文硬编码；语言切换不改变事实字段、canonical enum 或 source lineage。

### 12. Update `adp-setup` and Validate the Module

更新 module version、4 个配置变量、help entries、skill ordering、共享资源安装检查和升级报告；然后运行完整模块校验。

**原因：** setup 应在能力实际存在后注册最终契约，避免帮助入口先于实现。

**完成门：** fresh install、update、headless、legacy config migration、help anti-zombie 和 installed-skill inspection 测试通过。

### 13. Real-Project Acceptance on `shopify-migration`

先以副本/output override 或 dry-run 运行，不覆盖当前正式 meeting packs 和 baseline。依次执行 baseline proposal、audit、program-status、roadmap、两个 meeting packs。

**验收标准：**

- 用户在业务双周会文档开头即可判断总体状态、是否按计划、主要偏差和需要的决策。
- 用户在 FDE 晨会文档开头即可判断今天要处理的事项，历史明细不再淹没主流程。
- 周三常规运行自动使用周一实际归档会议至当前的窗口；周五仅存在周一记录、非周一/三/五运行和首次运行均要求用户确认范围。
- 当前中文配置被自动解析，不再依赖 `-zh` 输出目录或人工提示。
- `indeterminate`、`at-risk`、`off-plan` 在真实缺口下表现准确，不因大量 `TBD` 直接误报延期；已证实延期与低置信度可以同时成立。
- 所有状态、日期和结论可下钻到 baseline revision、WDR/checkpoint、decision、audit 和 source fingerprint。
- 同一 meeting instance 和同一 program-status 输入重复执行均幂等，不重复回写或覆盖历史 snapshot。

### Recommended Build Handoff

首个用户可调用的 skill 是 **`adp-plan-baseline`**。构建它之前，先在同一实现任务中完成 Shared Contract Foundation；随后按以上顺序逐步接通 status、audit、program view 和会议层。

所有 skills 完成后，返回 **Create Module (CM)** 更新模块基础设施，并运行 **Validate Module (VM)** 做最终结构与注册校验。
