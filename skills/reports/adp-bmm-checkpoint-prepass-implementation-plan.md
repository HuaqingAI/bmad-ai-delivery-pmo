---
title: "ADP BMM Checkpoint Prepass Implementation Plan"
status: "executable-plan"
module_code: "adp"
created: "2026-07-07"
updated: "2026-07-07"
owner: "bmad-module-builder"
---

# ADP BMM Checkpoint Prepass Implementation Plan

## 1. 结论

`adp-bmm-checkpoint-sync` 需要从“人工组织 checkpoint packet 再写入 ADP”升级为“先自动发现候选事实，再确认，再幂等写入”的模式。

推荐把当前单段式同步改为三段式：

```text
discover
  -> 从 BMM / TEA 产物抽取候选 checkpoint facts
  -> 生成有稳定主键的 candidate
confirm
  -> 由 owner 只确认范围、影响、缺口、确认人、下一步
sync
  -> 仅消费 confirmed 且未 applied 的 candidate
  -> 幂等写入 WDR / evidence / decisions / readiness / daily
```

核心变化不是“多一个命令”，而是建立一层稳定的 intake 事实面，解决以下问题：

- BMM 不同阶段没有统一的 decision log / change log。
- 同一份 PRD / architecture / story / test evidence 反复 discover 时，不能重复受理。
- 单条线 owner 的局部理解不能直接升级为多线已确认结论。
- ADP 需要继续保持“BMM artifact 是事实源，WDR 是项目级协调面”的边界。

## 2. 已验证事实

本次调研基于本仓库现有 skill、模板、脚本和配置，未假设仓库外实现。

### 2.1 产物根目录

- BMM planning 产物根：`{project-root}/_bmad-output/planning-artifacts`
- BMM implementation 产物根：`{project-root}/_bmad-output/implementation-artifacts`
- TEA test 产物根：`{project-root}/_bmad-output/test-artifacts`

来源：
- [BMM config](/D:/Documents/bmad-builder-taste/_bmad/bmm/config.yaml:1)
- [TEA config](/D:/Documents/bmad-builder-taste/_bmad/tea/config.yaml:1)

### 2.2 BMM 主线不存在统一变更日志

不同阶段的事实源强度不同：

| 阶段 | 强事实源 | 次强事实源 | 备注 |
| --- | --- | --- | --- |
| ideation / brief | `.memlog.md` | `brief.md`, `prfaq-*.md`, `distillate.md` | brief / PRFAQ 本身没有统一 change log |
| prd | `.memlog.md` | `prd.md`, `validation-report.*`, `review-*.md` | `prd.md` 有 `status`, `Open Questions`, `Assumptions`, `FR`, `SM` |
| spec | `.memlog.md` | `SPEC.md`, companions | `SPEC.md` 是派生产物，不是原始轨迹 |
| architecture | `.memlog.md` | `ARCHITECTURE-SPINE.md`, `review-*.md` | `AD-n`, `Deferred`, `Inherited Invariants` 很稳定 |
| epic-story planning | `epics.md` 本体 | `sprint-status.yaml`, readiness report | 无独立 decision log |
| implementation | story/spec file | `Review Findings`, `deferred-work.md`, `sprint-status.yaml` | 关键字段有 `baseline_commit`, `Status`, `Dev Agent Record` |
| validation | `gate-decision.json`, `e2e-trace-summary.json` | `traceability-matrix.md`, `nfr-assessment.md`, `test-review.md`, CI artifacts | 这是最强机器可读 validation 证据 |

关键来源：
- [bmad-prd](/D:/Documents/bmad-builder-taste/.agents/skills/bmad-prd/SKILL.md:14)
- [PRD template](/D:/Documents/bmad-builder-taste/.agents/skills/bmad-prd/assets/prd-template.md:1)
- [bmad-spec](/D:/Documents/bmad-builder-taste/.agents/skills/bmad-spec/SKILL.md:46)
- [bmad-architecture](/D:/Documents/bmad-builder-taste/.agents/skills/bmad-architecture/SKILL.md:35)
- [spine template](/D:/Documents/bmad-builder-taste/.agents/skills/bmad-architecture/assets/spine-template.md:1)
- [epics template](/D:/Documents/bmad-builder-taste/.agents/skills/bmad-create-epics-and-stories/templates/epics-template.md:1)
- [create-story template](/D:/Documents/bmad-builder-taste/.agents/skills/bmad-create-story/template.md:1)
- [bmad-dev-story](/D:/Documents/bmad-builder-taste/.agents/skills/bmad-dev-story/SKILL.md:13)
- [code-review present step](/D:/Documents/bmad-builder-taste/.agents/skills/bmad-code-review/steps/step-04-present.md:1)
- [trace template](/D:/Documents/bmad-builder-taste/.agents/skills/bmad-testarch-trace/trace-template.md:1)
- [NFR report template](/D:/Documents/bmad-builder-taste/.agents/skills/bmad-testarch-nfr/nfr-report-template.md:1)

### 2.3 ADP 当前写入逻辑是文本级幂等，不是事件级幂等

当前 `adp-bmm-checkpoint-sync` 脚本能避免部分重复文本：

- artifact index 按 artifact label upsert
- table row 仅在整行文本不存在时追加
- checkpoint log / daily log 仅在整段 entry 不存在时追加

但它还没有“稳定 candidate 主键”和“已发现 / 已确认 / 已应用”状态机，所以不能解决重复 discover 的问题。

来源：
- [sync_bmm_checkpoint.py](/D:/Documents/bmad-builder-taste/skills/adp-bmm-checkpoint-sync/scripts/sync_bmm_checkpoint.py:267)
- [sync_bmm_checkpoint.py](/D:/Documents/bmad-builder-taste/skills/adp-bmm-checkpoint-sync/scripts/sync_bmm_checkpoint.py:374)
- [sync_bmm_checkpoint.py](/D:/Documents/bmad-builder-taste/skills/adp-bmm-checkpoint-sync/scripts/sync_bmm_checkpoint.py:440)

## 3. 问题定义

当前 `adp-bmm-checkpoint-sync` 的主要缺口：

1. 它要求 owner 或上游 workflow 手工组织最小可靠 packet。
2. 它没有统一 discover 层，所以无法稳定利用 BMM/TEA 真实产物。
3. 它没有 intake registry，所以无法判断“这次 discover 到的是不是已受理事实”。
4. 它对单线 owner 的 authority scope 没有显式建模，跨线结论容易被误写成项目级已确认。
5. 它没有把 implementation / validation 阶段最强的机器可读证据接进来。

## 4. 目标与非目标

### 4.1 目标

- 让 owner 不再手工拼完整 checkpoint packet，只确认项目级事实。
- 让 BMM / TEA 产物成为 discover 输入，减少口头同步噪声。
- 保证 discover 和 sync 幂等，可重复运行，不重复受理。
- 保证单线确认、跨线待确认、业务确认、证据缺口有明确状态。
- 保持现有 `adp-bmm-checkpoint-sync` 写 WDR 的边界，不把 BMM 正文复制进 ADP。

### 4.2 非目标

- 不改造 BMM 各 skill 的主产物格式。
- 不要求所有主线都新增统一 decision log。
- 不让 ADP 替代 PRD / architecture / story / TEA 报告。
- 不在第一版就自动解决所有跨线冲突，只要求能显式暴露并阻止误写 ready。

## 5. 设计原则

### 5.1 事实源优先级

同一 checkpoint 内，按以下优先级抽取：

1. 机器可读结构化输出
2. append-only `.memlog.md`
3. 主文档的稳定章节和 frontmatter
4. review / validation 报告
5. owner 补充说明

### 5.2 发现与写入分离

- `discover` 只发现和归一化事实，不修改 WDR。
- `confirm` 只做边界确认和 authority 限定。
- `sync` 才修改 ADP memory。

### 5.3 事件级幂等

不能靠时间戳或文本段落去重，必须靠稳定 candidate 主键。

### 5.4 authority scope 显式化

单条线 owner 只能确认自己 authority scope 内的事实。
只要影响其他 workstream，就必须进入 `cross-line-pending`，直到被所需确认人补全。

## 6. 新的能力边界

推荐保留 skill 名称 `adp-bmm-checkpoint-sync`，但扩展为三类能力：

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| `discover` | 从 BMM/TEA 产物生成 checkpoint candidate | `project-root`, `workstream-id`, `checkpoint`, `artifact path(s)` | `candidate.json`, `candidate-preview.md`, intake registry 更新 |
| `confirm` | 让 owner 或调用方补齐 authority、确认状态、缺口和 next action | `candidate-id`, overrides | candidate 状态更新为 `confirmed` / `dismissed` / `superseded` |
| `sync` | 把 confirmed candidate 幂等写入 ADP WDR 及周边文件 | `candidate-id` 或已有兼容参数 | WDR / evidence / decisions / readiness / daily 更新，candidate 标记 `applied` |

这仍然是一个 workflow，不需要拆出独立 agent。

## 7. Candidate Intake 设计

### 7.1 存储结构

新增：

```text
_bmad-output/adp/memory/intake/bmm-checkpoints/
  index.jsonl
  candidates/
    {candidate-id}.json
    {candidate-id}.preview.md
```

### 7.2 Candidate 状态机

```text
discovered
confirmed
applied
superseded
dismissed
```

规则：

- 相同 `candidate_id` 再 discover：直接返回已有 candidate，状态不变。
- 相同 `source_scope_key` 但 `source_revision` 改变：新 candidate `discovered`，旧 candidate `superseded`。
- 仅 `confirmed` 且未 `applied` 的 candidate 允许 sync。
- 已 `applied` candidate 再 sync：返回 `no_op=true`。

### 7.3 Candidate 主键

```text
candidate_id = sha256(
  workstream_id,
  checkpoint,
  source_scope_key,
  source_revision,
  normalized_claims
)
```

字段说明：

- `source_scope_key`
  同一来源对象的稳定标识。例如 `prd:<path>`、`architecture:<path>`、`story:<path>`、`trace:<path>`
- `source_revision`
  当前来源对象的版本标识，不是 discover 时间
- `normalized_claims`
  discover 抽出的项目级事实，经排序、去空格、去时间噪声后的稳定表示

### 7.4 source_revision 规则

| 来源类型 | source_revision 建议 |
| --- | --- |
| `prd.md` / `SPEC.md` / `ARCHITECTURE-SPINE.md` | `sha256(artifact_file + memlog_file)` |
| `brief.md` | `sha256(brief_file + memlog_file)` |
| `prfaq` | `sha256(prfaq_file + distillate_file)` |
| `epics.md` | `sha256(epics_file)` |
| story file | `sha256(story_file) + baseline_commit + status` |
| `sprint-status.yaml` | `sha256(file) + last_updated` |
| `gate-decision.json` | `sha256(file)`，有 CI run id 则一并带入 |
| `e2e-trace-summary.json` | `sha256(file)` |
| `nfr-assessment.md` / `test-review.md` | `sha256(file)` |

## 8. Candidate 数据契约

### 8.1 discover 输出

```json
{
  "candidate_id": "CHK-...",
  "status": "discovered",
  "workstream_id": "l1",
  "checkpoint": "prd",
  "artifact": {
    "kind": "prd",
    "path": "D:/.../prd.md",
    "status": "draft",
    "source_scope_key": "prd:D:/.../prd.md",
    "source_revision": "sha256:..."
  },
  "claims": {
    "summary": "L1 PRD updated after Q1 confirmation.",
    "scope": {
      "in": [],
      "out": [],
      "assumptions": [],
      "non_goals": []
    },
    "acceptance": {
      "criteria": [],
      "owner": "",
      "evidence_required": [],
      "success_metrics": []
    },
    "decisions": [],
    "open_questions": [],
    "dependencies": [],
    "impacts": [],
    "risks": [],
    "business_confirmation": [],
    "readiness_gaps": [],
    "next_actions": []
  },
  "authority": {
    "asserted_by": "L1 owner",
    "authority_scope": ["l1"],
    "affected_workstreams": ["l1", "l2"],
    "required_confirmers": ["L2 owner"],
    "confirmation_state": "cross-line-pending"
  },
  "source_refs": [
    "D:/.../prd.md#Open Questions",
    "D:/.../.memlog.md"
  ]
}
```

### 8.2 confirm 输入

推荐支持：

```json
{
  "candidate_id": "CHK-...",
  "decision": "confirm",
  "overrides": {
    "authority.confirmation_state": "confirmed-local",
    "claims.business_confirmation": ["Biz-A confirmed Q1 conclusion."],
    "claims.impacts": ["l2 pending confirmation"],
    "claims.next_actions": ["L2 owner confirm impact before architecture checkpoint"]
  }
}
```

### 8.3 sync 输入

推荐优先支持：

```bash
uv run "{skill-root}/scripts/sync_bmm_checkpoint.py" sync "{project-root}" --candidate-id CHK-...
```

兼容保留旧模式：

```bash
uv run "{skill-root}/scripts/sync_bmm_checkpoint.py" "{project-root}" --workstream-id ... --checkpoint ...
```

旧模式内部先转 discover candidate，再进入 confirm/sync 兼容流。

## 9. 各 checkpoint 的 extractor 规则

### 9.1 `prd`

优先级：

1. `SPEC.md + .memlog.md`
2. `prd.md + .memlog.md`
3. `brief.md + .memlog.md`
4. `prfaq-*.md + distillate`
5. `brainstorm .memlog.md`

可抽取字段：

- `summary`
- `scope.in / out / assumptions / non_goals`
- `acceptance.criteria`
- `acceptance.success_metrics`
- `open_questions`
- `decisions`
- `changes`
- `business_confirmation`
- `readiness_gaps`

明确规则：

- `status: final` 仅表示可作为 baseline 候选，不表示业务确认完成。
- `Open Questions` 必须优先转为 `--open-question` 或 `readiness gap`。
- `.memlog.md` 中的 `decision/change/override/question` 比主文档 prose 优先级更高。

### 9.2 `architecture`

优先级：

1. `ARCHITECTURE-SPINE.md + .memlog.md`
2. reviewer outputs
3. owner 补充说明

可抽取字段：

- `AD-n`
- `Binds / Prevents / Rule`
- `Inherited Invariants`
- `Stack`
- `Capability -> Architecture Map`
- `Deferred`
- `question / constraint / version`

映射建议：

- `AD-n` -> `decision`
- `Deferred` -> `readiness-gap` 或 `open-question`
- 外部接口/L0/contract 约束 -> `dependency`, `impact`, `l0-reference`

### 9.3 `epic-story`

优先级：

1. `epics.md`
2. story file
3. `sprint-status.yaml`
4. implementation readiness report

可抽取字段：

- epic goal
- FR/NFR coverage
- story AC
- milestones / sequence
- blockers
- action items
- status transitions

映射建议：

- story 顺序 / milestone -> `--milestone`
- 明确阻塞 -> `--blocker`
- 跨 story / 跨 line 依赖 -> `--dependency`
- `sprint-status.yaml` 中 open action items -> `next actions` 或交给 `adp-status-sync`

### 9.4 `implementation`

优先级：

1. story/spec file
2. `Review Findings`
3. `deferred-work.md`
4. `sprint-status.yaml`
5. test summary / automation summary

可抽取字段：

- `baseline_commit`
- story/spec `Status`
- tasks completion
- `Dev Agent Record`
- `File List`
- `Change Log`
- `Verification`
- unresolved review findings

明确规则：

- story `review` 表示“待 review”，不表示 implementation checkpoint ready。
- `Status: done` 也不能替代 validation evidence。
- 未解决的 `decision-needed` / `patch` / `defer` 必须转为 gap 或 risk。

### 9.5 `validation`

优先级：

1. `gate-decision.json`
2. `e2e-trace-summary.json`
3. `traceability-matrix.md`
4. `nfr-assessment.md`
5. `test-review.md`
6. `automation-summary.md` / `test-summary.md`
7. CI artifacts

可抽取字段：

- gate status
- coverage stats
- blockers
- recommendations
- NFR PASS / CONCERNS / FAIL
- evidence sources
- evidence gaps
- flakiness
- quality score

明确规则：

- validation checkpoint 应优先消费机器可读 gate 结果，不优先消费 owner 口头“测试通过”。
- 没有实际 test run 证据时，不允许 discover 自动给出 `record-status=ready`。

## 10. 去重与重复受理控制

这是本方案的硬要求。

### 10.1 discover 幂等

同一 `candidate_id` 再 discover：

- 不创建新 candidate
- 不新写 preview
- 返回已有 candidate 路径和状态

### 10.2 source 变更后的 supersede

当 `source_scope_key` 相同但 `source_revision` 变化：

- 旧 candidate -> `superseded`
- 新 candidate -> `discovered`
- preview 明确标注“source revision changed”

### 10.3 sync 幂等

当 candidate 已 `applied`：

- `sync` 直接返回 `no_op=true`
- 不重复写 WDR / evidence / decisions / readiness / daily

### 10.4 文本幂等仍然保留

现有脚本中的：

- artifact index upsert
- table row append-if-missing
- checkpoint log append-if-missing
- daily log append-if-missing

仍然保留，作为第二道防线。

### 10.5 confirmation scope 去重

重复 confirm 同一 candidate：

- 如果 overrides 相同，返回 no-op
- 如果 overrides 不同，追加 confirmation event，并视为新的 candidate revision 或本 candidate 的新 confirmed state

不能让第二次 confirm 静默覆盖第一次。

## 11. authority 与跨线确认

### 11.1 authority 模型

每个 candidate 必须带：

- `asserted_by`
- `authority_scope`
- `affected_workstreams`
- `required_confirmers`
- `confirmation_state`

### 11.2 confirmation_state 枚举

推荐：

```text
discovered
confirmed-local
cross-line-pending
cross-line-confirmed
business-pending
business-confirmed
dismissed
```

### 11.3 写入 ready 的前提

以下情况一律不能自动写 `record-status=ready`：

- 影响其他 workstream 但未拿到 required confirmer
- 验收 owner 未知
- 业务 confirmation 缺失
- evidence gap 未关闭
- TEA gate 不是 `PASS` 或等价明确放行

## 12. 对现有 skill 的具体修改点

### 12.1 `skills/adp-bmm-checkpoint-sync/SKILL.md`

需要更新：

- 增加 `discover / confirm / sync` 三段式说明
- 增加 candidate intake 目录契约
- 增加 idempotency / authority guardrails
- 增加不同 checkpoint 的事实源说明

### 12.2 `skills/adp-bmm-checkpoint-sync/scripts/sync_bmm_checkpoint.py`

需要扩展：

- 新增子命令 `discover`
- 新增子命令 `confirm`
- 现有主命令重命名或兼容为 `sync`
- 新增 intake registry 读写
- 新增 candidate id / source revision 计算
- 新增 extractor dispatch
- 新增 `--candidate-id`

### 12.3 新增脚本

建议新增：

```text
skills/adp-bmm-checkpoint-sync/scripts/checkpoint_discovery.py
skills/adp-bmm-checkpoint-sync/scripts/checkpoint_extractors.py
skills/adp-bmm-checkpoint-sync/scripts/checkpoint_registry.py
```

职责：

- `checkpoint_discovery.py`
  CLI orchestrator
- `checkpoint_extractors.py`
  按 checkpoint 类型和 artifact 类型提取事实
- `checkpoint_registry.py`
  candidate 存储、去重、状态迁移

### 12.4 测试

建议新增：

```text
skills/adp-bmm-checkpoint-sync/scripts/tests/test_checkpoint_discovery.py
skills/adp-bmm-checkpoint-sync/scripts/tests/test_checkpoint_registry.py
```

必须覆盖：

- 同源同 revision discover 幂等
- source revision 变化触发 superseded
- applied candidate 再 sync no-op
- cross-line pending 阻止 ready
- PRD / architecture / story / trace 各自 extractor 的最小 happy path

## 13. 交付文件建议

workflow build 实现后，推荐最小交付：

```text
skills/adp-bmm-checkpoint-sync/SKILL.md
skills/adp-bmm-checkpoint-sync/scripts/sync_bmm_checkpoint.py
skills/adp-bmm-checkpoint-sync/scripts/checkpoint_discovery.py
skills/adp-bmm-checkpoint-sync/scripts/checkpoint_extractors.py
skills/adp-bmm-checkpoint-sync/scripts/checkpoint_registry.py
skills/adp-bmm-checkpoint-sync/scripts/tests/test_checkpoint_discovery.py
skills/adp-bmm-checkpoint-sync/scripts/tests/test_checkpoint_registry.py
```

第一阶段不要求修改 BMM 各主线 skill。

## 14. 分阶段实施顺序

### Phase 1

先做基础设施：

- candidate registry
- `candidate_id`
- `source_revision`
- `discover` 命令骨架

目标：先把“不会重复受理”做对。

### Phase 2

先接高价值 checkpoint：

- `prd`
- `architecture`

目标：把 planning 阶段最常见、最强事实源接入。

### Phase 3

再接：

- `epic-story`
- `implementation`

目标：把 story / sprint / review 进入项目级事实面。

### Phase 4

最后接：

- `validation`
- TEA / CI 证据源

目标：让 ready / gap / evidence judgment 真正依赖验证证据，而不是 owner 口头同步。

## 15. workflow build Brief

以下内容可直接作为 Workflow Builder 输入。

### adp-bmm-checkpoint-sync enhancement

**Type:** workflow enhancement

**Purpose:** Upgrade `adp-bmm-checkpoint-sync` from direct write workflow into a discover-confirm-sync workflow with idempotent candidate intake.

**Core Outcome:** BMM and TEA artifacts become reusable project-level checkpoint fact sources without duplicating source content, and repeated discover runs do not create duplicate accepted events.

**The Non-Negotiable:** The workflow must never treat repeated discovery of the same source revision as a new accepted checkpoint event, and it must never upgrade single-line owner understanding into cross-line confirmed project truth without explicit confirmation state.

**Capabilities:**

| Capability | Outcome | Inputs | Outputs |
| --- | --- | --- | --- |
| Discover candidate | Extract project-level checkpoint facts from BMM/TEA artifacts | `project-root`, `workstream-id`, `checkpoint`, artifact path(s) | candidate JSON, preview markdown, intake registry entry |
| Confirm candidate | Attach authority scope, confirmation state, owner corrections, and missing business/project facts | `candidate-id`, overrides | updated candidate state |
| Sync candidate | Idempotently write confirmed candidate facts into ADP WDR/evidence/decisions/readiness/daily | `candidate-id` | WDR updates, evidence rows, decisions rows, readiness gaps, daily log, applied marker |
| Supersede stale candidate | Detect new source revision and retire old candidate | source scope + new revision | old candidate `superseded`, new candidate `discovered` |

**Inputs:**

- BMM planning artifacts under `_bmad-output/planning-artifacts`
- BMM implementation artifacts under `_bmad-output/implementation-artifacts`
- TEA artifacts under `_bmad-output/test-artifacts`
- Existing ADP memory under `_bmad-output/adp/memory`

**Outputs:**

- `_bmad-output/adp/memory/intake/bmm-checkpoints/index.jsonl`
- `_bmad-output/adp/memory/intake/bmm-checkpoints/candidates/{candidate-id}.json`
- `_bmad-output/adp/memory/intake/bmm-checkpoints/candidates/{candidate-id}.preview.md`
- Existing ADP WDR / evidence / decisions / readiness / daily updates after sync

**Design Notes:**

- Prefer machine-readable evidence over prose.
- Prefer `.memlog.md` over raw prose when available.
- Preserve existing `sync_bmm_checkpoint.py` write semantics as secondary protection, but add event-level idempotency through candidate registry.
- Explicitly model `authority_scope`, `affected_workstreams`, `required_confirmers`, and `confirmation_state`.

**Relationships:**

- Upstream: BMM `prd`, `spec`, `architecture`, `epics`, `story`, `dev-story`, `code-review`, TEA `trace`, `nfr`, `test-review`
- Downstream: `adp-status-sync`, `adp-risk-dependency-change-review`, `adp-acceptance-readiness-review`, `adp-agent-program-lead`

## 16. Build Roadmap

1. Implement intake registry and candidate id model.
2. Add `discover` command with PRD and architecture extractors.
3. Add `confirm` command and authority-state transitions.
4. Rewire `sync` to consume candidate ids idempotently.
5. Add epic-story and implementation extractors.
6. Add validation / TEA / CI extractors.
7. Update skill documentation and test suite.

**Next steps:**

1. Hand this document to Workflow Builder as the source brief for the `adp-bmm-checkpoint-sync` enhancement.
2. Build the registry and `discover` capability first, before any new extractor breadth.
3. Use PRD and architecture as first integration checkpoints because they have the strongest planning-stage fact sources.
