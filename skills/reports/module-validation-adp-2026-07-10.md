---
title: ADP Module Validation Report
module: AI Delivery PMO
module_code: adp
status: fail
validated: 2026-07-10
validator: bmad-module-builder
source_plan: skills/reports/adp-state-audit-meeting-pack-roadmap-plan.md
document_language: Chinese
---

# ADP 模块验证报告

## 1. 总体结论

**验证未通过，当前实现不应标记为 ready。**

模块结构、自动化测试和主要正向执行路径均已跑通，但 audit、roadmap 和业务会议包仍存在会产生错误业务结论的高严重度问题。当前状态更准确地描述为：

- Phase 1-5 的主体能力已经落地。
- 结构完整性通过。
- 关键正向保护线通过。
- 质量门与业务状态语义尚不可信。
- 修复高严重度问题并补齐负向测试后，才能重新申请 ready 判定。

## 2. 验证范围

本次对照以下内容进行验证：

- 计划：`skills/reports/adp-state-audit-meeting-pack-roadmap-plan.md`
- 模块目录：`skills/adp-*`
- 模块注册：`skills/adp-setup/assets/module.yaml`、`module-help.csv`
- 新增 workflow：`adp-state-audit`、`adp-meeting-pack`、`adp-roadmap-sync`
- 既有集成：Program Lead、project kickoff、meeting sync、status sync、checkpoint sync、readiness、risk/dependency
- 真实项目 smoke：`D:/ProgramData/git/repository/github/huaqingai/shopify-migration`

验证方式：

1. 运行 BMad Module Builder 确定性结构校验。
2. 审查所有 ADP skill 与 `module-help.csv` 的能力注册和描述质量。
3. 运行全部 ADP `unittest` 测试。
4. 运行模块验证器自身回归测试。
5. 在真实项目上运行 6 种 audit、2 种 meeting pack、roadmap 和 Program Lead 视图 smoke。
6. 使用 fresh kickoff 项目补充负向和边界验证。

## 3. 验证结果摘要

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| 模块结构 | PASS | 13 条 CSV entry 与 13 个 ADP skill 完整对应，0 structural finding |
| ADP 自动化测试 | PASS | 19 个测试文件，111 项测试通过 |
| 验证器回归 | PASS | 21/21 通过 |
| 真实项目执行 | PASS | 6 种 audit、2 种 meeting pack、roadmap、project-lead、weekly-report 均成功执行 |
| 计划语义验收 | FAIL | 4 个 High、5 个 Medium、2 个 Low finding |
| Ready 判定 | FAIL | 修复 High finding 前不可发布为可信质量门 |

真实项目本次 smoke 快照：

| 指标 | 当前值 |
| --- | ---: |
| Sources read | 54 |
| Missing sources | 1 |
| Workstreams | 14 |
| Actions | 56 |
| Prepass gaps | 19 |
| Cross-reference gaps | 15 |
| Action cross-check | 14 |
| Audit blocking findings | 158 |
| Audit warnings | 46 |

说明：live 项目数字会继续变化。当前 audit blocking/warning 数量受本报告中的假阳性问题影响，不应直接作为项目质量结论。

## 4. High Findings

### H1. `accepted` 决策被误判为待决或阻塞

**位置：**

- `skills/adp-meeting-pack/scripts/render_meeting_pack.py:37,429`
- `skills/adp-state-audit/scripts/audit_state.py:466`
- `skills/adp-roadmap-sync/scripts/render_roadmap.py:416`

**问题：**

三个消费者只把 `closed`、`done`、`cancelled` 识别为闭合状态，没有识别真实 ADP 数据广泛使用的 `accepted`。

**真实影响：**

- Business Decision Board 共 20 项，其中 11 项已是 `accepted`。
- Roadmap `Blocked By Decisions` 共 21 项，其中 3 项已是 `accepted`。
- Audit `open_business_packets` 共 12 项，其中 3 项已是 `accepted`。

已接受的决策会重新进入业务待拍板板、audit closure 风险和 roadmap blocker，直接污染业务会议结论。

**建议修复：**

1. 在共享模块中定义统一的 decision status taxonomy 和 normalizer。
2. 明确 `accepted` 是否等价于闭合；若是，纳入所有 closed-status 集合。
3. Audit、meeting pack、roadmap 和 decision log parser 统一复用该 normalizer。
4. 增加 `accepted`、`closed`、`done`、`cancelled`、`rejected`、`superseded` 的参数化测试。

**退出标准：**

- 已接受决策不进入 Decision Board 的待决区。
- 已接受决策不进入 roadmap blocker。
- 已接受决策不计入 audit open packet。

### H2. Roadmap 没有真正执行或消费 audit gate

**位置：**

- `skills/adp-roadmap-sync/scripts/render_roadmap.py:91,119-151,209-215`

**问题：**

`--audit` 和 `--prepass-json` 只被加入 source inventory，没有读取或验证 JSON 内容，也没有检查 `audit_status`。Roadmap 在 blocked audit 下仍无条件返回 `status: complete`。

这违反计划规定的链路：

```text
prepass -> state audit -> scenario filter -> render -> source inventory
```

**真实影响：**

向 roadmap 传入 blocked audit 后，仍生成 2 个 timeline item、4 个 unscheduled item 和 21 个 decision blocker，输出中没有明确的风险读出标记。

**建议修复：**

1. 未提供 audit 时，由 roadmap 调用 `adp-state-audit --scenario roadmap`。
2. 提供 audit 时，读取并校验 schema、scenario、memory root 和生成时间。
3. 在 roadmap JSON/Markdown 中持久化 `audit_path`、`audit_status`、`report_confidence`。
4. `blocked` 时允许生成风险读出，但必须显式标红，并禁止呈现为 green/normal timeline。
5. 对 invalid/stale/wrong-memory-root audit 返回 blocked 或明确降级结果。

**退出标准：**

- 无 audit 不能静默生成 roadmap。
- blocked audit 的 roadmap 明确标记风险状态。
- roadmap 测试覆盖 pass、warning、blocked、invalid、stale audit。

### H3. Audit 将全部 status-sync JSON 视为未消费

**位置：**

- `skills/adp-state-audit/scripts/audit_state.py:211-216,452-461`

**问题：**

Audit 无条件扫描 `intake/status-sync/*.json`，将每个文件都认定为未被 `adp-status-sync` 消费，并全部计入 blocking finding。

**真实影响：**

- 本次 live smoke 标记 24 个 unconsumed intake。
- 其中至少 9 个是 `*-report.json` 输出报告，不是待消费 intake。
- 多个原始 intake 已有对应成功执行 report，仍被再次判为未消费。

**建议修复：**

1. 为 status-sync intake 定义生命周期字段，例如 `status: pending|applied|superseded|failed`。
2. 成功执行后写入 durable receipt，包含 input hash、report path、applied_at 和 affected action/WDR ids。
3. Audit 只将 `pending` 且没有成功 receipt 的 canonical intake 视为未消费。
4. 明确排除 `*-report.json`、dry-run report、plan、preview、migration report。
5. 对 legacy 文件使用 input/report 配对和内容字段做兼容识别。

**退出标准：**

- 输出报告不再计入 unconsumed intake。
- 已有成功 receipt 的 intake 不再阻断 audit。
- 真正 pending 的 canonical intake 仍能稳定被识别。

### H4. Fresh kickoff 会生成虚假 Roadmap milestone

**位置：**

- `skills/adp-roadmap-sync/scripts/render_roadmap.py:421-489,896-909`
- `skills/adp-project-kickoff/assets/adp-memory-templates/views/acceptance-readiness.md:3`
- `skills/adp-project-kickoff/assets/adp-memory-templates/l0/extracted-gates.md:3`
- `skills/adp-project-kickoff/assets/adp-memory-templates/l0/extracted-decision-gates.md:3`

**问题：**

`has_substantive_content()` 将 kickoff 模板的说明文字和 TBD 表格视为真实 gate 来源。

**独立复现：**

全新 kickoff 后直接运行 roadmap，得到 3 个虚假 unscheduled milestone：

- L0 decision gates
- L0 delivery gates
- Acceptance readiness gate

**建议修复：**

1. 给模板增加稳定的 `template_status: placeholder` 或机器可读 marker。
2. 解析真实数据行，而不是用“文件存在且含非标题文字”判断有效性。
3. 只有至少一条非 placeholder gate/readiness row 或明确 generated metadata 时才创建 milestone。
4. Fresh kickoff fixture 必须断言 timeline 和 unscheduled milestones 均为空。

**退出标准：**

- Fresh kickoff roadmap 为 0 timeline、0 unscheduled milestone。
- 填入真实 gate 后才生成对应 source-backed milestone。

## 5. Medium Findings

### M1. Audit freshness 和 placeholder 判定产生系统性假阳性

**位置：** `skills/adp-state-audit/scripts/audit_state.py:305-329,819-830`

任意出现一次 `TBD` 就将整个视图判断为 placeholder；所有派生视图又与全部 durable source 的全局最新时间比较，而不是按视图自己的 source lineage 比较。真实 smoke 中，刚生成的 risk/dependency 视图仍因局部合法 TBD 被判需刷新。

**建议：** 使用模板签名和必填区段判断 placeholder；为每个派生视图记录 `generated_at`、`source_paths`、`source_hashes`，按 lineage 判断 freshness。

### M2. Audit JSON 未实现计划中的稳定输出契约

**位置：** `skills/adp-state-audit/scripts/audit_state.py:230-266,694-704`

当前使用 `schema_version`、`audit_status` 和嵌套 `findings`，未提供计划要求的：

- `audit_schema_version`
- `safe_to_generate`
- `safe_to_generate_green_report`
- `report_confidence`
- finding 统一字段 `id`、`severity`、`kind`、`source_type`、`sources`、`workstreams`、`owner`、`summary`

**建议：** 先确定 canonical audit schema v1；若保留当前结构，应更新计划并提供兼容层，而不是让内部消费者各自猜测字段。

### M3. Roadmap Markdown 缺少 `source_type`

**位置：** `skills/adp-roadmap-sync/scripts/render_roadmap.py:676-700`

Roadmap JSON item 已包含 `source_type`，但 Markdown timeline/unscheduled 表没有 `Source Type` 列，不满足计划要求的人读可追溯契约。

**建议：** 为所有 roadmap item 表格增加 `Source Type`，并测试 Markdown 与 JSON 字段一致性。

### M4. Program Lead 尚未真正消费 roadmap 和 meeting pack

**位置：**

- `skills/adp-agent-program-lead/scripts/adp-state-prepass.py:62-70`
- `skills/adp-agent-program-lead/scripts/render_program_views.py:238-365`

Program Lead 已真正消费 audit，但只 inventory `roadmap.md`，没有解析 `roadmap.json`；也没有扫描 `views/meeting-packs/*`。Project Lead 和 Weekly Report 中没有 roadmap 或 meeting closure 的消费内容。

**建议：** prepass 增加 typed roadmap/meeting-pack scan；Program Lead 只消费最新且 audit-compatible 的派生产物，并在视图中加入 timeline exceptions、unscheduled milestones 和 last meeting closure。

### M5. Meeting-pack 回写链路缺少机器可追踪 lineage

**位置：**

- `skills/adp-meeting-sync/references/sync-plan-schema.md:9-19`
- `skills/adp-meeting-sync/scripts/sync_meeting.py:200-211`

Meeting pack 包含人读 checklist，但 meeting-sync schema 没有 pack id/path/scenario/audit lineage，无法稳定追踪 pack 到会后 outcome。

**建议：** 增加 `meeting_pack_id`、`meeting_pack_path`、`scenario`、`audit_path`、`roadmap_version`，并在 meeting archive、daily、status-sync intake 中保留该 lineage。

## 6. Low Findings

### L1. `module-help.csv` 能力与 CLI 契约不完整

**位置：** `skills/adp-setup/assets/module-help.csv`

- Program Lead 多个 capability 被压缩成单一 `readout`，args 缺 state-audit gate 和 roadmap timeline readout。
- BMM checkpoint 的 discover、confirm、sync 没有独立能力注册。
- Meeting Pack outputs 只写 Markdown，遗漏 JSON distillate。
- State Audit、Meeting Pack、Roadmap、Kickoff 部分 CLI 参数未注册。

**建议：** 按 distinct capability 拆行或建立清晰的 action/mode 注册；通过脚本从 CLI parser 和 SKILL contract 生成或校验 help rows。

### L2. Agent roster 描述已陈旧

**位置：**

- `skills/adp-setup/assets/module.yaml:10`
- `skills/adp-agent-program-lead/customize.toml:12-16`

两处字段彼此一致，但描述未包含新增 state audit gate、meeting pack、roadmap、project-lead/weekly generation 能力。

**建议：** 同步更新两处 description，并增加 roster/skill capability drift 检查。

## 7. 已通过的关键能力

以下能力已验证，可以保留并作为修复后的回归基线：

- `adp-state-audit` 能运行 prepass 并输出 JSON/Markdown。
- Audit 已覆盖 freshness、completeness、consistency、closure、duplicate、overlap、conflict 类别。
- FDE meeting pack 的 42 个 action 全部来自 action ledger，均带 source 和 closure criteria。
- FDE pack 没有把 `views/fde-actions.md` 当作 action 真源。
- Dependency map 被裁剪，没有整份复制进会议包。
- Business pack 在 roadmap 缺失或 audit 非 pass 时显示 TBD/unscheduled。
- Roadmap 不将普通 action due date 自动升级为 milestone；live smoke 中 42 项普通 action 进入 Excluded Items。
- Roadmap JSON item 带 source、confidence 和 source_type。
- Program Lead 在生成 project-lead/weekly 前运行或消费 audit。
- Blocking audit 下 weekly report 明确标 RED，不输出“全局正常”。
- Kickoff 已初始化 audits、meeting-packs、roadmap 模板和 WDR Roadmap section。
- Checkpoint candidate 和 canonical status-sync action intake 集成可用。
- Agent roster code/title/icon/name/customize.toml 无机械 drift。

## 8. 推荐优化顺序

### Stage 1：先修错误业务结论

目标：让 audit、业务会议包和 roadmap 不再输出错误状态。

工作项：

1. 修复 H1 decision status taxonomy。
2. 修复 H3 intake consumption lifecycle。
3. 修复 H4 placeholder milestone。
4. 修复 H2 roadmap audit gate。

完成标准：4 个 High finding 都有负向测试，真实项目 smoke 不再出现 accepted decision blocker、report-as-intake 或 placeholder milestone。

### Stage 2：稳定跨 workflow 数据契约

目标：让下游消费者不依赖 Markdown 猜测。

工作项：

1. 固化 audit schema 和 finding schema。
2. 为 derived views 增加 source lineage/hash/freshness metadata。
3. 补 risk/dependency 可裁剪 rows 和 readiness gate metadata。
4. 补 meeting-pack 到 meeting-sync 的 lineage。
5. 补 roadmap Markdown `Source Type`。

完成标准：audit、meeting pack、roadmap、Program Lead 之间使用版本化 JSON 契约；Markdown 仅作为人读渲染。

### Stage 3：完成 Program Lead 消费闭环

目标：兑现 Program Lead 作为综合消费方的计划边界。

工作项：

1. Prepass typed scan `roadmap.json` 和最新 meeting pack distillate。
2. Project Lead view 增加 roadmap exception、unscheduled milestone、meeting closure。
3. Weekly report 增加本周 milestone change、decision closure、未闭合会议事项。
4. 保留 audit gate，并拒绝消费 stale/incompatible derived artifact。

完成标准：Program Lead 不自行重算 roadmap/meeting 事实，只消费通过契约检查的派生产物。

### Stage 4：修正模块注册与文档

目标：保证用户和 LLM 能正确发现所有能力。

工作项：

1. 更新 `module-help.csv` distinct capabilities、args 和 outputs。
2. 同步 module roster/customize description。
3. 增加 help/CLI/SKILL contract tests。
4. 根据兼容性决定 module version 是否升级。

完成标准：Module Builder 结构校验通过，且人工质量审查不再发现 capability 漏注册或参数失真。

### Stage 5：完整复验与发布门

建议按以下顺序复验：

```powershell
python .agents/skills/bmad-module-builder/scripts/validate-module.py skills
python .agents/skills/bmad-module-builder/scripts/tests/test-validate-module.py
```

然后运行全部 ADP `unittest` 测试文件，并执行以下 smoke：

1. Fresh kickoff：roadmap 不产生虚假 milestone。
2. Decision fixture：accepted 不进入 open/blocker board。
3. Intake fixture：applied/report 不进入 unconsumed；pending 仍被识别。
4. Audit fixture：pass/warning/blocked/invalid/stale 均有稳定行为。
5. Live project：6 种 audit、2 种 meeting pack、roadmap、Program Lead 全链路运行。

最终发布门：

- 结构校验 0 finding。
- 所有自动化测试通过。
- 4 个 High 和 5 个 Medium finding 关闭。
- Live smoke 中没有已知假阳性或已决事项误报。
- Audit 和 roadmap schema 有版本与兼容策略。

## 9. 建议补充的测试

优先新增：

- `test_accepted_business_decision_is_closed_everywhere`
- `test_applied_intake_and_report_are_not_unconsumed`
- `test_pending_canonical_intake_is_blocking`
- `test_fresh_kickoff_has_no_roadmap_milestones`
- `test_roadmap_requires_or_runs_audit`
- `test_blocked_audit_marks_roadmap_risk_bearing`
- `test_legitimate_tbd_does_not_mark_entire_view_placeholder`
- `test_audit_finding_contract_has_source_type`
- `test_roadmap_markdown_contains_source_type`
- `test_program_lead_consumes_latest_compatible_roadmap_and_meeting_pack`
- `test_module_help_matches_cli_and_skill_contracts`

## 10. 最终判定

当前判定：**Needs fixes / Not ready**。

建议先完成 Stage 1，再运行一次聚焦复验；Stage 1 通过后再推进契约、Program Lead 消费和注册质量。不要先做 HTML 控制台，因为当前需要先稳定底层状态语义和数据契约。

Validation complete.
