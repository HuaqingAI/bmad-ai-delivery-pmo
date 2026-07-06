---
title: "ADP Action Ledger Implementation Plan"
status: "executable-plan"
module_code: "adp"
created: "2026-07-03"
updated: "2026-07-03"
owner: "bmad-module-builder"
---

# ADP Action Ledger Implementation Plan

## 1. 结论

需要把 Action 升级为 ADP 的一等事实对象。当前 `views/fde-actions.md` 是运营视图，不应继续承担事实源职责；WDR 的 `Project Status -> Next actions` 是工作线状态摘要，也不适合作为全局 action source of truth。

推荐新增事实源：

```text
_bmad-output/adp/memory/actions/action-ledger.md
```

同时保留：

```text
_bmad-output/adp/memory/views/fde-actions.md
```

作为由 Program Lead 生成的当前运营面板。

核心闭环应调整为：

```text
meeting / FDE sync / checkpoint / readiness / risk / L0 / kickoff / register / staleness
  -> action intake
  -> adp-status-sync upsert
  -> actions/action-ledger.md
  -> WDR Next actions merged summary
  -> views/fde-actions.md derived view
  -> adp-agent-program-lead readout and routing
```

## 2. 当前实现验证

本次检查了以下当前实现：

- `skills/adp-meeting-sync/SKILL.md`
- `skills/adp-meeting-sync/scripts/sync_meeting.py`
- `skills/adp-status-sync/SKILL.md`
- `skills/adp-status-sync/scripts/sync_status.py`
- `skills/adp-agent-program-lead/SKILL.md`
- `skills/adp-agent-program-lead/scripts/adp-state-prepass.py`
- `skills/adp-project-kickoff/assets/adp-memory-templates/views/fde-actions.md`
- `skills/adp-project-kickoff/assets/adp-memory-templates/schemas/meeting-sync.md`
- `skills/adp-project-kickoff/assets/adp-memory-templates/schemas/workstream-delivery-record.md`

验证结论：

| 判断 | 当前状态 | 影响 |
| --- | --- | --- |
| meeting-sync 能识别 `classification=action` | 已支持 | 会议 action 会进入会议归档、daily log，并在有 workstream 时追加 WDR block。 |
| meeting-sync 生成 status-sync updates-file | 未支持 | 输出只提示运行 `adp-status-sync`，没有生成可消费 action delta 文件。 |
| status-sync 支持 `--updates-file` | 已支持 | 但只支持轻量字段和 `next_actions` 字符串，不支持 action object。 |
| status-sync 写 action ledger | 未支持 | action 没有稳定事实源。 |
| status-sync 合并 WDR Next actions | 未支持 | 当前 `set_section_bullet` 会替换整行 `Next actions`，有覆盖旧 action 的风险。 |
| program-lead 读取 action ledger | 未支持 | prepass 只从 WDR `Next actions` 抽取 action。 |
| kickoff 创建 actions 目录和 ledger 模板 | 未支持 | 新项目没有 action ledger 基础结构。 |
| views/fde-actions.md 是派生面板 | 设计上接近，但实现未闭环 | 当前模板存在，但没有稳定 ledger 输入。 |

所以“status-sync 输出无行动项”不是单次执行错误，而是当前 action delta 没有进入 status-sync 和 Program Lead 的事实源链路。

## 3. Action 的边界定义

Action Ledger 收录所有项目级可执行缺口，不只来自会议。

应该进入 Action Ledger：

- FDE 能推动或协调关闭的事项。
- 某条 workstream 必须补齐的产物、证据、决策、状态。
- 明确 owner 的跨线依赖处理。
- 超过同步周期的 workstream 状态刷新。
- readiness、risk、dependency、change、L0 gate 产生的可执行补齐项。
- kickoff/register 后暴露的项目级初始化缺口。

不应直接进入普通 FDE action list：

- 需要业务拍板的问题：进入 Business Decision Packet，ledger 可记录 follow-up action，但事实源仍是 decision packet。
- 纯事实记录。
- 已关闭且没有后续动作的历史事项。
- 没有 owner、没有 workstream 或项目对象、没有关闭条件的模糊提醒。
- 纯 BMM 内部实现细节，除非影响项目级状态、验收、风险、依赖或交付节奏。

## 4. Action 数据契约

### 4.1 Ledger 文件

路径：

```text
_bmad-output/adp/memory/actions/action-ledger.md
```

推荐结构：

```markdown
# Action Ledger

This is the ADP action source of truth. Do not use views/fde-actions.md as a source file.

| Action ID | Status | Owner | Workstream | Action | Source | Reason | Due / Trigger | Closure Criteria | Last Updated | Owning Workflow |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ACT-20260703-001 | open | FDE-A | l3 | 补齐接口验证证据 | meetings/2026-07-03-sync.md#M-001 | readiness evidence gap | 周五 | evidence.md includes linked validation proof and readiness gap is closed | 2026-07-03T16:47:00+08:00 | adp-status-sync |
```

状态枚举：

```text
open / in-progress / blocked / done / cancelled
```

`gap` 不是 action 状态。缺 owner、due、closure criteria 的 action 仍可登记，但应保持 `open` 或 `blocked`，并在 `Reason` 或 `Closure Criteria` 中显式标出 gap。Program Lead 视图再把它归入 `Stale / Missing Sync Follow-ups` 或 `Evidence / Readiness Gaps`。

### 4.2 Intake JSON

所有 workflow 产生 action 时，优先生成 status-sync 可消费的 intake JSON，而不是直接手写 ledger。

路径建议：

```text
_bmad-output/adp/memory/intake/status-sync/YYYY-MM-DD-{source}-actions.json
```

格式：

```json
{
  "updates": [
    {
      "id": "l3",
      "status": "in-progress",
      "next_actions": ["FDE-A 在周五前补齐接口验证证据"],
      "actions": [
        {
          "owner": "FDE-A",
          "workstream": "l3",
          "action": "补齐接口验证证据",
          "source": "meetings/2026-07-03-sync.md#M-001",
          "reason": "会议确认接口验证证据缺口仍未关闭",
          "due": "周五",
          "status": "open",
          "closure_criteria": "validation evidence is linked in evidence.md and readiness gap is closed",
          "owning_workflow": "adp-status-sync"
        }
      ],
      "source": "adp-meeting-sync"
    }
  ]
}
```

兼容规则：

- `next_actions` 继续支持，作为 WDR 摘要输入。
- `actions` 是新增结构化事实输入。
- 老的 updates-file 没有 `actions` 时行为不变。
- `workstream` 可缺省为 update `id`。
- `due`、`trigger`、`due_or_trigger` 归一到 `Due / Trigger`。
- `closure_criteria` 缺失时写入 `TBD`，并产生 unresolved gap。

### 4.3 Action ID 规则

默认稳定编号：

```text
ACT-YYYYMMDD-NNN
```

生成规则：

1. 读取 ledger 中当天已有最大序号。
2. 新建 action 按顺序递增。
3. Upsert 时如果 intake 提供 `action_id`，优先用 `action_id`。
4. 如果没有 `action_id`，用去重键匹配既有 action。

默认去重键：

```text
normalized(owner) + normalized(workstream) + normalized(action) + normalized(source)
```

如果 `source` 变化但语义相同，可用弱去重：

```text
normalized(owner) + normalized(workstream) + normalized(action)
```

弱去重只更新 open/in-progress/blocked，不复活 done/cancelled。

## 5. 来源矩阵

| 来源 workflow | Action 类型 | 写入方式 | 备注 |
| --- | --- | --- | --- |
| `adp-meeting-sync` | 会议行动项、线下沟通 follow-up、业务反馈待处理 | 生成 status-sync intake JSON | 不直接写 ledger。输出明确下一步命令。 |
| `adp-status-sync` | FDE 主动上报 next action、阻塞后的跟进行动、action close/update | 直接 upsert ledger | Action 的主要登记器。 |
| `adp-bmm-checkpoint-sync` | PRD/架构/story/实现/验证 checkpoint 暴露的项目级缺口 | 生成或建议 action intake | 只收项目级缺口，不复制 BMM 细节。 |
| `adp-acceptance-readiness-review` | 证据缺口、确认人缺失、readiness 不达标项 | 生成 action intake 或 report action section | 和 readiness gap 保持双向 source link。 |
| `adp-risk-dependency-change-review` | 风险缓解、依赖解除、范围变更处理、业务决策跟进 | 生成 action intake；业务拍板另进 packet | 不把 business decision 本体伪装成 FDE action。 |
| `adp-l0-reference-sync` | L0 gate/NFR/contract/evidence rule 对工作线产生的补齐动作 | 生成 action intake | Source 指向 L0 summary 或 affected gap。 |
| `adp-workstream-register` | 新线初始化必填缺口 | 可生成 registration action intake | 如 owner、artifact、L0 reference、依赖未补。 |
| `adp-project-kickoff` | 项目级初始化缺口 | 可生成 kickoff action intake | 如 cadence、stakeholder、acceptance owner、L0 baseline 未确认。 |
| staleness check | 过期未同步 | `adp-status-sync stale` 可输出 action candidates 或 intake | 建议先做 follow-up action，不直接改 delivery status。 |
| `adp-agent-program-lead` | 综合判断派生行动 | 默认只读；若用户要求登记，生成 intake 并提示 status-sync 命令 | Program Lead 不直接成为事实写入器。 |

## 6. 分阶段落地方案

### Phase 1: Memory scaffold and schema

目标：新项目有 action ledger 的标准位置和说明。

改动：

- 更新 `adp-project-kickoff/scripts/bootstrap_adp.py`
  - 创建 `actions/` 目录。
  - 创建 `actions/action-ledger.md` 模板。
  - 可选新增 `schemas/action-ledger.md`，定义字段、状态、来源、关闭规则。
- 更新 `adp-project-kickoff/SKILL.md`
  - Memory output 增加 `actions/action-ledger.md`。
  - 明确 `views/fde-actions.md` 是派生视图。
- 更新 `assets/adp-memory-templates/views/fde-actions.md`
  - 顶部增加说明：source is `actions/action-ledger.md` plus readiness/decision/risk outputs。

验收：

- `adp-project-kickoff` 后存在 `actions/action-ledger.md`。
- 既有项目重复 kickoff 不覆盖已有 ledger。
- 单测覆盖 scaffold idempotency。

### Phase 2: meeting-sync produces action intake

目标：会议 action 不再停留在会议归档。

改动：

- 更新 `adp-meeting-sync/scripts/sync_meeting.py`
  - 对 `classification == "action"` 的 item 生成 intake JSON。
  - 默认写入 `intake/status-sync/{date}-{slugified-meeting-title}-actions.json`。
  - touched 输出新增 `status_sync_intake_files`。
  - `next_actions()` 中输出精确命令：

```bash
uv run "{status-sync-skill-root}/scripts/sync_status.py" update "{project-root}" --updates-file "<generated-file>"
```

由于 meeting-sync 脚本不知道目标安装环境中 status-sync skill root，脚本 JSON 可以输出通用命令片段：

```text
adp-status-sync update --updates-file <generated-file>
```

由 SKILL.md 用当前 workflow 语境转成实际命令。

- 更新 `adp-meeting-sync/SKILL.md`
  - Write 部分说明 action intake 文件。
  - Output Contract 增加 generated updates-file。

验收：

- meeting plan 含 action 时，生成 intake JSON。
- action 缺 owner/due 时仍生成 intake，但 unresolved gaps 包含缺口。
- meeting plan 无 action 时不生成空 intake。
- 单测覆盖 source link、workstream normalization、missing due gap。

### Phase 3: status-sync owns action upsert and WDR merge

目标：status-sync 既能处理 owner 状态，也能登记 action。

改动：

- 扩展 `StatusUpdate`
  - 新增 `actions: list[ActionUpdate]`。
  - `ActionUpdate` 字段与 ledger 一致，支持 `action_id`、`status`、`closure_criteria`。
- 扩展 `update_from_mapping()`
  - 读取 `actions` list。
  - 保持老格式 `next_actions` 兼容。
- 新增 ledger helper
  - `ensure_action_ledger(memory_root)`
  - `parse_action_ledger(path)`
  - `upsert_actions(path, actions, timestamp)`
  - `next_action_id(existing, date)`
  - `normalize_action_key(action)`
- 修改 WDR Next actions 更新逻辑
  - 当前 `Next actions` 不能覆盖旧值。
  - 将现有 `Next actions` 拆分为列表，和 update `next_actions`、open/in-progress/block action 摘要合并去重。
  - 写回为单行 `; ` 分隔，保持当前 schema 兼容。
- daily log 追加
  - 增加 Action registered/updated/closed 摘要。
- 输出 JSON
  - `action_ledger`
  - `actions_registered`
  - `actions_updated`
  - `actions_closed`
  - `unresolved_gaps`

关闭规则：

- `status` 为 `done`、`cancelled` 时更新 ledger，不删除记录。
- 如果 action close 没有 `closure_criteria` 或 source，仍允许关闭，但 unresolved gap 标出原因。
- WDR `Next actions` 只展示未关闭 action；关闭后从合并摘要中移除对应 action 文案。

验收：

- `--updates-file` 包含 `actions` 时创建 ledger 并登记 action。
- 重复运行同一 intake 不创建重复 action。
- 新 action 合并到 WDR `Next actions`，不覆盖旧 action。
- `done/cancelled` action 不出现在 WDR `Next actions` 摘要。
- 老的 `--next-action` 单命令行为保持兼容。

### Phase 4: Program Lead reads ledger and refreshes fde-actions view

目标：Program Lead 的 FDE action list 从事实源生成，而不是只读 WDR 摘要。

改动：

- 更新 `adp-agent-program-lead/scripts/adp-state-prepass.py`
  - Capability `fde action list` 增加 `actions` group。
  - 读取 `actions/action-ledger.md`。
  - 输出 `ledger_actions`，并把 open/in-progress/blocked 合并进 `actions`。
  - 保留 WDR `Next actions` 作为 cross-check，而不是唯一 action 源。
  - 输出 ledger/WDR mismatch gaps：
    - ledger open action 未出现在 WDR Next actions。
    - WDR Next actions 找不到 ledger 对应记录。
- 更新 `adp-agent-program-lead/SKILL.md`
  - 明确 Program Lead 不登记 action，除非用户要求生成 intake。
  - `FDE Action List` 读取源排序：
    1. action ledger
    2. WDR Next actions
    3. readiness gaps
    4. business decision packets
    5. risk/dependency outputs
- 可选新增 view writer
  - `adp-state-prepass.py --write-fde-actions-view`
  - 或新脚本 `render_fde_actions_view.py`
  - 建议先只做 prepass 输出，Program Lead 负责生成 Markdown；后续再脚本化视图刷新。

`views/fde-actions.md` 推荐结构：

```markdown
# FDE Action List

Generated from `actions/action-ledger.md` and ADP review outputs.

## Open Actions By FDE

## Blocked Actions

## Evidence / Readiness Gaps

## Business Decisions Needed

## Stale / Missing Sync Follow-ups
```

验收：

- ledger 有 open action 时，prepass `actions` 非空。
- WDR 没有 `Next actions` 但 ledger 有 action 时，Program Lead 仍能输出 FDE action list。
- done/cancelled 不展示在 open action view。
- 单测覆盖 ledger parsing、status filtering、WDR mismatch gap。

### Phase 5: Close/update rules across workflows

目标：action 生命周期闭合，不只会创建。

支持来源：

- FDE status-sync 说明已完成。
- meeting-sync 会议确认完成。
- readiness review 发现证据已补齐。
- business decision packet 已确认。
- risk/change review 标记风险或依赖已关闭。

统一行为：

- workflow 产生 close/update intake。
- status-sync upsert ledger。
- status-sync 合并刷新 WDR Next actions。
- Program Lead view 只展示 open/in-progress/blocked。

Intake close 示例：

```json
{
  "updates": [
    {
      "id": "l3",
      "actions": [
        {
          "action_id": "ACT-20260703-001",
          "status": "done",
          "source": "workstreams/l3/evidence.md#validation-proof",
          "reason": "readiness review confirmed evidence gap closed",
          "closure_criteria": "validation evidence linked and accepted"
        }
      ],
      "source": "adp-acceptance-readiness-review"
    }
  ]
}
```

验收：

- close intake 更新 ledger 状态和 Last Updated。
- close intake 不删除历史记录。
- close intake 后 view 不再展示该 action。
- 如果找不到 `action_id`，通过弱去重尝试匹配；仍找不到则返回 unresolved gap，不新建 done action。

### Phase 6: Historical meeting migration

目标：历史会议不会污染当前待办面板。

迁移规则：

- 已完成事项：写入 meeting archive；ledger 标记 done 或不进入 ledger。
- 当前仍有效事项：进入 ledger，状态 open/in-progress/blocked。
- 无法确认 owner/due/closure criteria：进入 ledger 但标出 gap，由 Program Lead 归类为 missing sync follow-up。
- 业务待拍板：进入 Business Decision Packet，不作为普通 FDE action；如需要，可创建 follow-up action 指向 packet。
- 已有 WDR `Next actions` 可作为 seed，但必须人工或规则确认是否仍 open。

建议新增一次性脚本：

```text
skills/adp-status-sync/scripts/migrate_actions.py
```

输入：

```bash
uv run ".../migrate_actions.py" "{project-root}" --from-meetings --since 2026-07-01 --dry-run
```

输出：

- migration candidate report
- proposed intake JSON
- unresolved migration gaps

第一版不自动写 ledger，只生成 candidate intake，由用户确认后交给 `adp-status-sync update --updates-file`。

## 7. 具体文件改造清单

优先修改：

- `skills/adp-project-kickoff/scripts/bootstrap_adp.py`
- `skills/adp-project-kickoff/scripts/tests/test_bootstrap_adp.py`
- `skills/adp-project-kickoff/assets/adp-memory-templates/views/fde-actions.md`
- `skills/adp-project-kickoff/SKILL.md`
- `skills/adp-meeting-sync/scripts/sync_meeting.py`
- `skills/adp-meeting-sync/scripts/tests/test_sync_meeting.py`
- `skills/adp-meeting-sync/SKILL.md`
- `skills/adp-status-sync/scripts/sync_status.py`
- `skills/adp-status-sync/scripts/tests/test_sync_status.py`
- `skills/adp-status-sync/SKILL.md`
- `skills/adp-agent-program-lead/scripts/adp-state-prepass.py`
- `skills/adp-agent-program-lead/scripts/tests/test_adp_state_prepass.py`
- `skills/adp-agent-program-lead/SKILL.md`

可选新增：

- `skills/adp-project-kickoff/assets/adp-memory-templates/actions/action-ledger.md`
- `skills/adp-project-kickoff/assets/adp-memory-templates/schemas/action-ledger.md`
- `skills/adp-status-sync/scripts/migrate_actions.py`
- `skills/adp-agent-program-lead/scripts/render_fde_actions_view.py`

## 8. 实施优先级

1. 先补 kickoff scaffold 和 action ledger 模板。
2. 再补 meeting-sync -> status-sync updates-file 交接。
3. 再让 status-sync 支持 action ledger upsert 和 WDR Next actions 合并去重。
4. 再让 program-lead prepass 读取 action ledger，并生成/刷新 `views/fde-actions.md`。
5. 最后补 action close/update 规则和历史会议迁移脚本。

## 9. 回归测试命令

每个阶段至少运行对应单测：

```bash
python -m unittest skills.adp_project_kickoff.scripts.tests.test_bootstrap_adp
python -m unittest skills.adp_meeting_sync.scripts.tests.test_sync_meeting
python -m unittest skills.adp_status_sync.scripts.tests.test_sync_status
python -m unittest skills.adp_agent_program_lead.scripts.tests.test_adp_state_prepass
```

当前目录名包含连字符，直接按 module path 运行可能不适用。更稳的本仓库运行方式是：

```bash
python skills/adp-project-kickoff/scripts/tests/test_bootstrap_adp.py
python skills/adp-meeting-sync/scripts/tests/test_sync_meeting.py
python skills/adp-status-sync/scripts/tests/test_sync_status.py
python skills/adp-agent-program-lead/scripts/tests/test_adp_state_prepass.py
```

最终验收建议运行：

```bash
python skills/adp-project-kickoff/scripts/tests/test_bootstrap_adp.py
python skills/adp-meeting-sync/scripts/tests/test_sync_meeting.py
python skills/adp-status-sync/scripts/tests/test_sync_status.py
python skills/adp-agent-program-lead/scripts/tests/test_adp_state_prepass.py
```

## 10. 完成定义

该优化完成时应满足：

- 新 ADP 项目默认包含 `actions/action-ledger.md`。
- 会议 action 会生成 status-sync intake。
- status-sync 可以把 intake action upsert 到 ledger。
- WDR `Next actions` 是 open action 的合并摘要，不会被单次同步覆盖。
- Program Lead 从 ledger 输出完整 FDE action list。
- `views/fde-actions.md` 是可再生成运营面板，不是事实源。
- action 可以被更新、阻塞、完成、取消。
- 历史迁移只把仍有效事项带入当前 open list，业务拍板事项进入 decision packet。

## 11. 风险和约束

- Markdown table 作为 ledger 简单、可读、低依赖，但字段含 `|` 时必须转义。实现需要统一 `cell()` 处理。
- WDR `Next actions` 当前是单行 bullet。第一阶段保持兼容，用 `; ` 合并；后续可把 WDR schema 升级为多行列表。
- Program Lead 不能绕过 status-sync 直接写 ledger，否则事实源写入路径会分叉。
- Business Decision Packet 和 Action Ledger 需要保持边界：业务要拍板的问题是 decision，FDE 跟进拍板动作才是 action。
- 历史迁移必须默认保守，不能把所有历史会议 action 都变成 open。
