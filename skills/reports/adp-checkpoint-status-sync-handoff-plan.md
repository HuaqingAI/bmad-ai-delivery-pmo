---
title: "ADP Checkpoint to Status Sync Action Handoff Plan"
status: "executable-plan"
module_code: "adp"
created: "2026-07-07"
updated: "2026-07-07"
reviewed: "2026-07-07"
owner: "bmad-module-builder"
---

# ADP Checkpoint to Status Sync Action Handoff Plan

## 1. 结论

评审结论成立：handoff 方向是对的，`adp-bmm-checkpoint-sync` 只产出 action intake，`adp-status-sync` 继续拥有 `actions/action-ledger.md`。

本修订版补齐四个实现前必须明确的洞：

- 主路径是 `discover -> confirm -> sync candidate`，不是 legacy packet。
- readiness gap 默认不能直接变 ledger action，因为现有 row schema 没有 `closure_criteria`。
- 跨 workstream action 使用 JSON carrier 表达 `affected_workstreams`，不靠脆弱的 pipe 字符串扩展。
- 输出字段统一为 `action_handoff_audit`，并补齐幂等测试。

保留当前边界：

- `adp-bmm-checkpoint-sync` 继续原子写入 checkpoint 阶段事实：WDR、artifact index、evidence、decisions、readiness、checkpoint daily log。
- `adp-status-sync` 继续作为轻量状态刷新器和 action ledger 写入器。
- `adp-bmm-checkpoint-sync` 只把 ledger-ready checkpoint follow-up 写成 `status-sync` 可消费的 intake JSON，并在输出里提示 runner alias 或 direct script fallback 命令。

目标效果：

```text
discover/confirm/sync candidate
  -> checkpoint writer
     -> WDR / evidence / decisions / readiness / daily
  -> action handoff writer
     -> intake/status-sync/{stable-key}-actions.json
  -> adp-status-sync
     -> actions/action-ledger.md
     -> local workstream WDR Next actions merged summary
     -> program action ledger/daily only; affected WDRs need later targeted status refresh
```

## 2. 为什么只移交 action

`checkpoint-sync` 和 `status-sync` 的文件级交叉主要在：

- `workstreams/{id}/delivery-record.md`
  - `Identity -> Current ADP status`
  - `Identity -> Current BMM phase`
  - `Project Status -> Progress / Blockers / Risks / Dependencies / Scope or change notes / Next actions`
- `daily/YYYY-MM-DD.md`

但这些字段在 checkpoint 场景里属于同一个阶段事件的摘要，必须和 evidence、decision、readiness gap、checkpoint log 同步落地。拆给当前 `status-sync` 会引入半写入、重复 daily、`Last status sync` 语义污染和事件一致性问题。

Action 不同。Action 是全局可执行事实，现有设计已经把 `actions/action-ledger.md` 定义为 source of truth，且 `status-sync` 已经具备 `updates-file`、action upsert、WDR `Next actions` 合并能力。因此 checkpoint 只应把 action 类输出移交给 `status-sync`。

## 3. 范围

### In Scope

- 为 `adp-bmm-checkpoint-sync` 增加 action handoff 输出。
- 在 candidate contract 中新增 `claims.actions`。
- 支持 confirm 阶段通过 override 补齐 `claims.actions`。
- 在 candidate sync 阶段把 `claims.actions` 映射为 `status-sync` intake。
- 为 legacy packet 模式增加兼容 carrier：`--action-file` 和有限 `--action`。
- 生成 `intake/status-sync/*.json`。
- 在 checkpoint sync 输出中报告 intake 文件、handoff audit 和下一步命令。
- 增加单元测试覆盖 candidate 主路径、跳过、跨线 action、幂等和 `status-sync` 消费路径。

### Out of Scope

- 不改 `status-sync` 核心 schema 或 upsert 行为。
- 不让 `status-sync` 写 evidence、decision、readiness。
- 不把 checkpoint 的 WDR stage summary 改由 `status-sync` 写。
- 不重构通用 `adp-memory-writer`。
- 不改变 BMM artifact source-of-truth 规则。
- 不把模糊 `next_action` 或普通 readiness gap 强行登记为 ledger action。

## 4. 行为规则

### 4.1 Checkpoint 仍然直接写

`adp-bmm-checkpoint-sync` 继续写：

- `workstreams/{id}/delivery-record.md`
- `workstreams/{id}/evidence.md`
- `workstreams/{id}/decisions.md`
- `workstreams/{id}/readiness.md`
- `daily/YYYY-MM-DD.md`

这些写入保持现有 dry-run、ready guardrail、candidate applied no-op 和 append-if-missing 行为。

### 4.2 Candidate 是主路径

新能力必须优先接入当前推荐路径：

```text
discover -> confirm -> sync candidate
```

Candidate schema 新增：

```json
{
  "claims": {
    "next_actions": ["legacy WDR/daily summary only"],
    "actions": [
      {
        "owner": "FDE-A",
        "workstream": "l1-checkout",
        "affected_workstreams": ["l1-checkout"],
        "action": "Link checkout smoke test evidence",
        "source": "workstreams/l1-checkout/readiness.md#validation-gap",
        "reason": "validation checkpoint readiness gap",
        "due_or_trigger": "before acceptance readiness review",
        "status": "open",
        "closure_criteria": "Evidence row links the smoke test report and the readiness gap is closed or superseded",
        "owning_workflow": "adp-bmm-checkpoint-sync"
      }
    ]
  }
}
```

Rules:

- `claims.next_actions` remains a free-form WDR/daily summary input. It must not generate ledger intake.
- `claims.actions` is the only candidate-level ledger action carrier.
- `candidate_to_sync_args()` must map `claims.actions` into the checkpoint action handoff writer.
- If a confirmed candidate has no `claims.actions`, sync must not emit action intake.
- If a candidate is already `applied`, rerunning `sync` must return no-op and must not emit a new action intake.

### 4.3 Confirm Override

Confirm must support adding or replacing actions with existing override mechanics:

```bash
uv run "{skill-root}/scripts/sync_bmm_checkpoint.py" confirm "{project-root}" \
  --candidate-id CHK-... \
  --decision confirm \
  --confirmed-by "FDE-A" \
  --override 'claims.actions=[{"owner":"FDE-A","workstream":"l1-checkout","action":"Link checkout smoke test evidence","due_or_trigger":"before acceptance readiness review","closure_criteria":"Evidence row links the smoke test report and readiness gap is closed","source":"workstreams/l1-checkout/readiness.md#validation-gap","reason":"validation checkpoint readiness gap","status":"open","owning_workflow":"adp-bmm-checkpoint-sync"}]'
```

For larger payloads, prefer:

```bash
uv run "{skill-root}/scripts/sync_bmm_checkpoint.py" confirm "{project-root}" \
  --candidate-id CHK-... \
  --decision confirm \
  --confirmed-by "FDE-A" \
  --overrides-file checkpoint-action-overrides.json
```

where:

```json
{
  "claims.actions": [
    {
      "owner": "FDE-A",
      "workstream": "l1-checkout",
      "affected_workstreams": ["l1-checkout"],
      "action": "Link checkout smoke test evidence",
      "source": "workstreams/l1-checkout/readiness.md#validation-gap",
      "reason": "validation checkpoint readiness gap",
      "due_or_trigger": "before acceptance readiness review",
      "status": "open",
      "closure_criteria": "Evidence row links the smoke test report and readiness gap is closed",
      "owning_workflow": "adp-bmm-checkpoint-sync"
    }
  ]
}
```

This uses whole-field replacement for `claims.actions`; do not require list-index patching.

### 4.4 Legacy Packet Compatibility

Compatibility mode may accept actions, but it is not the primary path.

Add:

```text
--action-file <path-to-json>
```

`--action-file` uses the same action object schema as `claims.actions`, wrapped or unwrapped:

```json
{
  "actions": [
    {
      "owner": "FDE-A",
      "workstream": "l1-checkout",
      "affected_workstreams": ["l1-checkout"],
      "action": "Link checkout smoke test evidence",
      "source": "workstreams/l1-checkout/readiness.md#validation-gap",
      "reason": "validation checkpoint readiness gap",
      "due_or_trigger": "before acceptance readiness review",
      "status": "open",
      "closure_criteria": "Evidence row links the smoke test report and readiness gap is closed",
      "owning_workflow": "adp-bmm-checkpoint-sync"
    }
  ]
}
```

Keep a limited convenience CLI only for single-workstream local actions:

```text
--action "owner|action|due_or_trigger|closure_criteria"
```

This form always scopes to the current `--workstream-id`. It must not support cross-workstream fanout. Use `--action-file` or `claims.actions` for `program` and `affected_workstreams`.

### 4.5 Readiness Gap Boundary

Existing `--readiness-gap` shape is:

```text
GAP|DIMENSION|OWNER|ACTION|DUE|ESCALATION
```

It lacks `closure_criteria`, so it is not ledger-ready by itself.

Short-term rule:

- Do not convert ordinary `--readiness-gap` rows into action intake.
- Report them under `action_handoff_audit.handoff_gaps` when they look action-like but lack closure criteria.
- Generate an action only when the same checkpoint supplies a structured `claims.actions`, `--action-file`, or `--action` entry with closure criteria.

Future option:

- Extend readiness row schema/template to include `Closure Criteria`.
- Only then allow deterministic readiness-gap-to-action conversion.

### 4.6 Cross-Workstream Actions

For one source action affecting multiple workstreams, use one canonical action:

```json
{
  "owner": "Project Lead",
  "workstream": "program",
  "affected_workstreams": ["l1-checkout", "l2-search"],
  "action": "Confirm checkout-search dependency owner",
  "source": "intake/bmm-checkpoints/candidates/CHK-123.preview.md#cross-line-impact",
  "reason": "architecture checkpoint cross-workstream dependency",
  "due_or_trigger": "before epic/story planning",
  "status": "open",
  "closure_criteria": "Both affected workstream owners confirm the dependency route or mark it not applicable",
  "owning_workflow": "adp-bmm-checkpoint-sync"
}
```

Do not duplicate the same action into every affected workstream unless owner, due trigger, or deliverable differs. This matches `status-sync` support for `workstream: "program"` plus `affected_workstreams`.

### 4.7 Ledger-Ready 判定

An action enters `actions[]` only when:

- `owner` is specific, not empty, `TBD`, `FDE owner`, or generic group text.
- `workstream` is the current workstream id, `program`, or another explicit valid route.
- `affected_workstreams` is present for `program` / cross-workstream actions.
- `action` is an observable deliverable or decision-follow-up.
- `due_or_trigger`, `due`, or `trigger` is explicit.
- `closure_criteria` is explicit.
- `source` points to a candidate, checkpoint output, or ADP memory file.

Blocked or incomplete candidates go to `action_handoff_audit.blocked_actions`; they do not enter the intake file.

## 5. Intake Contract

Generated path:

```text
_bmad-output/adp/memory/intake/status-sync/{stable-key}-actions.json
```

Stable key:

- Candidate sync: `{YYYY-MM-DD}-bmm-checkpoint-{workstream-id}-{checkpoint}-{candidate-id}`
- Legacy packet: `{YYYY-MM-DD}-bmm-checkpoint-{workstream-id}-{checkpoint}-{payload-hash}`

Write behavior:

- Dry-run reports the planned path but does not write.
- If the path exists with identical canonical JSON, report no-op.
- If the path exists with different content, write a hash-suffixed path rather than overwriting silently.
- Candidate `applied` no-op must not create or rewrite intake.

Intake JSON:

```json
{
  "updates": [
    {
      "id": "program",
      "source": "adp-bmm-checkpoint-sync",
      "actions": [
        {
          "owner": "Project Lead",
          "workstream": "program",
          "affected_workstreams": ["l1-checkout", "l2-search"],
          "action": "Confirm checkout-search dependency owner",
          "source": "intake/bmm-checkpoints/candidates/CHK-123.preview.md#cross-line-impact",
          "reason": "architecture checkpoint cross-workstream dependency",
          "due": "before epic/story planning",
          "status": "open",
          "closure_criteria": "Both affected workstream owners confirm the dependency route or mark it not applicable",
          "owning_workflow": "adp-bmm-checkpoint-sync"
        }
      ]
    }
  ]
}
```

`next_actions` may be omitted when `actions[]` is present. For local workstream actions, `status-sync` merges active ledger actions back into that WDR `Next actions`. For `program` actions, `status-sync` updates ledger and daily log only; affected WDR summaries need later targeted status refresh or Program Lead readout.

## 6. Output Contract

Checkpoint sync result adds:

```json
{
  "status_sync_intake_files": [".../intake/status-sync/...-actions.json"],
  "action_handoff_audit": {
    "actions_seen": 2,
    "ledger_ready_actions": 1,
    "blocked_actions": [
      {
        "action": "Resolve before record-status ready",
        "reason": "missing closure_criteria",
        "source": "readiness gap"
      }
    ],
    "handoff_gaps": [
      "readiness gap 'Validation evidence rows need confirmation' has action/due but no closure_criteria"
    ],
    "fanout_suppressed": 1,
    "no_op": false
  },
  "next_actions": [
    "If runner alias exists: adp-status-sync update \"D:/project\" --updates-file \".../intake/status-sync/...-actions.json\"",
    "Otherwise resolve adp-status-sync skill root and run: uv run \"{status-sync-skill-root}/scripts/sync_status.py\" update \"D:/project\" --updates-file \".../intake/status-sync/...-actions.json\""
  ]
}
```

Use `action_handoff_audit` consistently. Do not use `action_handoff_registered_candidates` or `action_handoff_actions`.

## 7. File-Level Changes

### 7.1 `skills/adp-bmm-checkpoint-sync/SKILL.md`

Update `Candidate Intake`:

- Define `claims.actions` as the structured action carrier.
- State that `claims.next_actions` remains WDR/daily-only.

Update `Confirm`:

- Explain adding actions via `--override claims.actions=<json-array>` or `--overrides-file`.
- State that whole-field replacement is the supported action override shape.

Update `Sync`:

- After successful candidate sync, generate status-sync action intake for ledger-ready `claims.actions`.
- Compatibility mode may use `--action-file`; `--action` is local-only convenience.
- Do not route WDR/evidence/decision/readiness writes through status-sync.
- Do not treat free-form `--next-action` or `claims.next_actions` as ledger-ready.
- Do not convert ordinary `--readiness-gap` rows into actions without closure criteria.

Update `Output Contract`:

- `status_sync_intake_files`
- `action_handoff_audit`
- next command, with runner alias and direct script fallback:

```bash
adp-status-sync update "{project-root}" --updates-file "<generated-intake-file>"
```

```bash
uv run "{status-sync-skill-root}/scripts/sync_status.py" update "{project-root}" --updates-file "<generated-intake-file>"
```

`adp-status-sync update ...` is a runner alias. If the runner does not expose that alias, resolve the installed `adp-status-sync` skill root and use the direct script form.

Update `Guardrails`:

- Action ledger writes belong to `adp-status-sync`; checkpoint only creates intake.
- Checkpoint daily entry remains checkpoint-owned; status-sync daily entry is created only when the intake is consumed.

### 7.2 `skills/adp-bmm-checkpoint-sync/scripts/sync_bmm_checkpoint.py`

Parser additions:

```text
--action-file <path>
--action "owner|action|due_or_trigger|closure_criteria"
```

Namespace additions:

- `action_file`
- `action`
- internal `handoff_actions`

Candidate mapping:

- `candidate_to_sync_args()` must map `claims.actions` into `handoff_actions`.
- `candidate_to_sync_args()` must keep `claims.next_actions` mapped only to existing `next_action`.
- `candidate_to_sync_args()` must not derive actions from `readiness_rows_for_sync`.

Helpers:

- `claim_actions(candidate) -> list[dict]`
- `load_action_file(path) -> list[dict]`
- `parse_local_action_specs(raw_items, workstream_id, source) -> list[dict]`
- `normalize_handoff_action(raw, default_workstream, default_source) -> dict`
- `audit_handoff_actions(actions, readiness_rows, checkpoint_context) -> dict`
- `status_sync_intake_path(memory_root, workstream_id, checkpoint, stable_key, dry_run) -> Path`
- `write_status_sync_intake(memory_root, workstream_id, checkpoint, actions, stable_key, dry_run) -> tuple[path, no_op]`

### 7.3 `skills/adp-bmm-checkpoint-sync/scripts/checkpoint_extractors.py`

Update extractor normalization so the candidate path recognizes action claims before sync:

- `default_claims()` includes `actions: []`.
- `normalize_claims()` preserves and canonicalizes `claims.actions`.
- Extractors do not infer ledger actions from generic prose or readiness gaps.
- Extractors may emit `claims.actions` only when owner, route, due/trigger, source, and closure criteria are deterministic from structured source data.
- Cross-workstream actions emitted by extractors use `workstream: "program"` plus `affected_workstreams`.

Execution order:

1. Validate checkpoint writer inputs.
2. Run existing checkpoint writer or dry-run planning.
3. If writer failed, do not create action intake.
4. Build handoff actions from `claims.actions`, `--action-file`, and local `--action`.
5. Audit ledger readiness.
6. Write intake only when at least one action is ledger-ready.
7. Return `status_sync_intake_files` and `action_handoff_audit`.

Important:

- `sync` on an already `applied` candidate returns before intake generation.
- Legacy packet reruns use stable payload hash so they do not emit duplicate equivalent intake files.
- `status-sync` may still protect the ledger with upsert; checkpoint should still avoid producing duplicate intake for the same event.

### 7.4 `skills/adp-bmm-checkpoint-sync/scripts/tests/test_sync_bmm_checkpoint.py`

Add tests:

1. `test_candidate_claims_actions_flow_generates_status_sync_intake`
   - Discover candidate.
   - Confirm with `claims.actions` override.
   - Sync candidate.
   - Assert intake JSON exists.
   - Assert `actions/action-ledger.md` is not created by checkpoint.

2. `test_candidate_next_actions_do_not_generate_intake`
   - Confirm candidate with only `claims.next_actions`.
   - Sync candidate.
   - Assert WDR/daily include next action.
   - Assert no status-sync intake is generated.

3. `test_candidate_to_sync_args_preserves_claim_actions`
   - Load a candidate with `claims.actions`.
   - Assert `candidate_to_sync_args()` returns internal handoff action data.
   - Assert `readiness_gap` conversion does not fabricate actions.

4. `test_candidate_applied_rerun_does_not_generate_new_handoff`
   - Sync confirmed candidate once.
   - Sync same candidate again.
   - Assert second result is no-op.
   - Assert no new or rewritten intake file.

5. `test_action_file_cross_workstream_program_action`
   - Run legacy packet with `--action-file` containing `workstream: "program"` and `affected_workstreams`.
   - Assert intake preserves one canonical program action.
   - Assert no fanout rows are generated.

6. `test_freeform_action_cli_is_local_only`
   - Run legacy packet with `--action`.
   - Assert generated action is scoped to current workstream.
   - Assert no `affected_workstreams` fanout is inferred.

7. `test_readiness_gap_without_closure_is_reported_not_registered`
   - Provide `--readiness-gap` row with owner/action/due but no closure criteria.
   - Assert no intake action is generated from it.
   - Assert `action_handoff_audit.handoff_gaps` names the missing closure criteria.

8. `test_checkpoint_action_intake_can_be_consumed_by_status_sync`
   - Generate intake.
   - Run `skills/adp-status-sync/scripts/sync_status.py update <project-root> --updates-file <intake>`.
   - Assert `actions/action-ledger.md` contains the action.
   - For local workstream actions, assert the matching WDR `Next actions` includes the active action summary.
   - For `workstream: "program"` actions, assert ledger and daily log are updated, and assert affected WDR files are not fanout-written by status-sync.

9. `test_repeated_status_sync_consumption_does_not_duplicate_ledger_action`
   - Run status-sync twice with the same generated intake.
   - Assert one ledger row remains.

10. `test_dry_run_reports_planned_intake_without_writing`
   - Run candidate sync or legacy packet with dry-run.
   - Assert result lists planned intake path.
   - Assert file is absent.

## 8. Output Example

After successful candidate sync:

```json
{
  "ok": true,
  "workstream_id": "l1-checkout",
  "checkpoint": "validation",
  "candidate_id": "CHK-1234567890ABCDEF",
  "files_updated": [
    ".../workstreams/l1-checkout/delivery-record.md",
    ".../workstreams/l1-checkout/readiness.md",
    ".../daily/2026-07-07.md"
  ],
  "status_sync_intake_files": [
    ".../intake/status-sync/2026-07-07-bmm-checkpoint-l1-checkout-validation-CHK-1234567890ABCDEF-actions.json"
  ],
  "action_handoff_audit": {
    "actions_seen": 1,
    "ledger_ready_actions": 1,
    "blocked_actions": [],
    "handoff_gaps": [],
    "fanout_suppressed": 0,
    "no_op": false
  },
  "next_actions": [
    "If runner alias exists: adp-status-sync update \"D:/project\" --updates-file \".../2026-07-07-bmm-checkpoint-l1-checkout-validation-CHK-1234567890ABCDEF-actions.json\"",
    "Otherwise resolve adp-status-sync skill root and run: uv run \"{status-sync-skill-root}/scripts/sync_status.py\" update \"D:/project\" --updates-file \".../2026-07-07-bmm-checkpoint-l1-checkout-validation-CHK-1234567890ABCDEF-actions.json\""
  ]
}
```

Direct fallback command shape:

```bash
uv run "{status-sync-skill-root}/scripts/sync_status.py" update "D:/project" --updates-file ".../2026-07-07-bmm-checkpoint-l1-checkout-validation-CHK-1234567890ABCDEF-actions.json"
```

## 9. Rollout Plan

### Phase 1: Contract and docs

- Update `adp-bmm-checkpoint-sync/SKILL.md`.
- Document `claims.actions`, `--action-file`, and local-only `--action`.
- Document free-form `claims.next_actions` / `--next-action` boundary.
- Add output contract fields.

### Phase 2: Candidate path

- Add `claims.actions` normalization.
- Add confirm override examples.
- Map `candidate_to_sync_args()` to handoff actions.
- Ensure applied candidate no-op does not generate handoff.

### Phase 3: Legacy compatibility

- Add `--action-file`.
- Add limited local `--action`.
- Add stable payload hash path for packet-mode intake.

### Phase 4: Audit and writer

- Add action readiness audit.
- Add deterministic intake writer.
- Report readiness gaps that lack closure criteria instead of registering them.

### Phase 5: Consumption verification

- Add cross-script test where generated intake is consumed by `status-sync`.
- Confirm checkpoint never writes `actions/action-ledger.md` directly.
- Confirm repeated status-sync consumption does not duplicate ledger rows.

## 10. Acceptance Criteria

This short-term fix is complete when:

- Checkpoint sync still writes WDR/evidence/decisions/readiness/daily directly.
- Candidate `claims.actions` is the primary action handoff carrier.
- Confirm can add actions through `claims.actions` override or `--overrides-file`.
- Candidate sync emits status-sync intake for ledger-ready `claims.actions`.
- Candidate `claims.next_actions` and legacy `--next-action` do not silently become ledger actions.
- Legacy packet mode supports `--action-file` for program/cross-workstream actions.
- Local `--action` cannot express cross-workstream fanout.
- Ordinary readiness gaps without closure criteria are reported, not registered.
- Output uses `status_sync_intake_files` and `action_handoff_audit` consistently.
- Candidate applied rerun does not emit new intake.
- Repeated equivalent packet sync does not create duplicate equivalent intake.
- Status-sync can consume checkpoint-generated intake without schema changes.
- Repeated status-sync consumption does not duplicate ledger rows.
- Local workstream action consumption refreshes that workstream's WDR `Next actions`.
- Program / cross-workstream action consumption updates ledger and daily log only; affected WDR summaries require later targeted status refresh or Program Lead readout.

## 11. Risk Notes

- If the implementation only adds legacy `--action`, the primary candidate path remains broken. Do not stop there.
- If ordinary readiness gaps become actions without closure criteria, action ledger quality drops immediately.
- If cross-workstream action uses pipe-string syntax, affected-workstream semantics will be fragile. Use JSON.
- If checkpoint also writes ledger directly, action source-of-truth splits again.
- If checkpoint calls status-sync automatically, command resolution and double daily logging become more complex. For now, generate intake and report the command.
- If equivalent reruns create new intake files, status-sync upsert may protect the ledger but the intake directory will still become noisy. Use stable keys and no-op detection.
