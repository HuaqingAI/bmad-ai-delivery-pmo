---
title: 'Management Panel 事实同步闭环'
type: 'feature'
created: '2026-07-30'
status: 'done'
review_loop_iteration: 1
baseline_commit: 'a5d873e0ad3e7d60e7157f76096c6ac65085bee3'
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-bmad-ai-delivery-pmo-2026-07-24/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Management Panel 更新链路存在断点：meeting-sync 不能修改已有 action，WDR 更新不进入 Panel 字段，审计不验证 source freshness 或 WDR/ledger drift，finding 也缺少可修复的 action ID。旧 producer 缺少稳定 ID 时，status-sync 重放还会重复创建 action。

**Approach:** 以 typed mutation、CAS/command replay、精确 drift finding 和可恢复 refresh 串起 `meeting-sync -> status-sync -> panel-refresh`。producer 生成或保留稳定 ID；Panel 发布前验证 freshness、pending intent 和 projection convergence。

## Boundaries & Constraints

**Always:** action 身份只由显式 ID 决定，不能使用 owner、workstream、文本或 source。事实写入与视图生成分离；失败时保留 current Panel。Python/Node 对 669 个向量必须一致且固定时间重放字节相同。

**Ask First:** 扩大 Panel 信息架构、增加 Action Center、改变 audience view、实时/定时刷新或破坏 memory 兼容性。

**Never:** 通过语义文本去重掩盖缺失 ID；让 Panel 直接推断或回写事实；提交 `.analysis/`、`.memlog.md`、eval reports、cache 或 Python bytecode。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 新 action | 缺失 action ID | producer 派生稳定 action/command ID | 必填事实缺失则停写 |
| mutation | 精确 ID 与 owner/status patch | CAS 更新同一 action 和投影 | revision/fingerprint 冲突停写 |
| BMM 重放 | 同 stable key 与 ordinal | 同 command 幂等 no-op | 不得重复创建 |
| identity 冲突 | 同 ID、不同 payload | fingerprint conflict | fail closed |
| Panel refresh | source 收敛 | 原子发布并返回 receipt | 阻断时保留 current 与恢复计划 |

</frozen-after-approval>

## Code Map

- `skills/adp-meeting-sync/scripts/sync_meeting.py` -- typed create/patch producer。
- `skills/adp-status-sync/scripts/sync_status.py` -- ledger/WDR、CAS、replay 和 projection owner。
- `skills/adp-state-audit/scripts/audit_state.py` -- freshness、drift 与 repair batch。
- `skills/adp-panel-refresh/` -- 可恢复刷新和原子发布。
- `skills/adp-bmm-checkpoint-sync/scripts/sync_bmm_checkpoint.py` -- BMM intake 稳定身份。
- `_bmad-output/planning-artifacts/architecture/architecture-bmad-ai-delivery-pmo-2026-07-24/` -- 协议与 receipts。

## Tasks & Acceptance

**Execution:**
- [x] meeting/status/audit/panel-refresh -- typed mutation、replay、drift 门禁与 durable refresh。
- [x] Program Lead、management-panel、setup、marketplace -- 路由、帮助和安装验证。
- [x] BMM producer -- 以 stable key + ordinal 派生缺失 ID。
- [x] 架构 receipts 与全仓验证 -- 更新 pin、双运行时结果并单次提交。

**Acceptance Criteria:**
- Given meeting/BMM action，when 同一输入重放，then ledger 只有一个相同 ID 的实体。
- Given owner/status patch，when apply，then ledger、WDR 与 sidecar 收敛。
- Given stale source、pending intent 或 drift，when refresh，then publication 阻断并返回 action ID 与 repair batch。
- Given source 收敛，when `policy -> detect -> plan -> apply -> inspect`，then Panel 原子发布并生成 receipt。
- Given 固定时间，when 双运行时执行 669 个向量两次，then结果一致且重放字节相同。

## Spec Change Log

## Design Notes

BMM 用 checkpoint `stable_key` 与分组前的 action ordinal 派生缺失 ID；显式 ID 原样保留。

## Verification

**Commands:**
- 18 个 `unittest` 目录、565 个测试 -- 全部通过，0 个失败目录。
- Module Builder 结构与质量验收 -- 18 个 skill、40 条 capability 注册、0 个结构问题；质量偏差已修复。
- Python/Node 669-vector 双运行时与 fixed-time replay -- 两端均 669/669、0 failed，各运行时两次输出逐字节一致，向量集合一致。
- source-pin、Draft 2020-12 schema、架构 lint、compileall、JSON、资产字节一致、Ruff changed-line delta、`git diff --check` -- 全部通过。

### Review Findings

- [x] [Review][Patch] Validate durable refresh IDs before resolving or deleting staging paths [skills/adp-panel-refresh/scripts/panel_refresh.py:753]
- [x] [Review][Patch] Reject absolute, parent-relative, or symlink-escaping recovery journal targets [skills/adp-panel-refresh/scripts/panel_refresh.py:1102]
- [x] [Review][Patch] Preflight meeting status-intent conflicts before writing meeting facts [skills/adp-meeting-sync/scripts/sync_meeting.py:846]
- [x] [Review][Patch] Derive overdue action checks from a durable meeting date instead of wall-clock date [skills/adp-meeting-sync/scripts/sync_meeting.py:1391]
- [x] [Review][Patch] Reject unsupported meeting action operations instead of treating them as creates [skills/adp-meeting-sync/scripts/sync_meeting.py:443]
- [x] [Review][Patch] Make completed status-sync input replay a validated durable no-op [skills/adp-status-sync/scripts/sync_status.py:2928]
- [x] [Review][Patch] Validate status-intent field allowlists and exact mutation binding before consumption [skills/adp-status-sync/scripts/sync_status.py:531]
- [x] [Review][Patch] Require typed action evidence and bind full evidence bytes into command replay identity [skills/adp-status-sync/scripts/sync_status.py:845]
- [x] [Review][Patch] Reject malformed existing action-ledger state rather than treating it as absent [skills/adp-status-sync/scripts/sync_status.py:1485]
- [x] [Review][Patch] Revalidate repair source records and fact generation before issuing a token [skills/adp-status-sync/scripts/sync_status.py:3221]
- [x] [Review][Patch] Verify repair token-state content identity before apply or recovery [skills/adp-status-sync/scripts/sync_status.py:3480]
- [x] [Review][Patch] Block audit drift evaluation on duplicate active ledger action IDs [skills/adp-state-audit/scripts/audit_state.py:1416]
- [x] [Review][Patch] Return exact drift action IDs and repair batches from Panel detect and inspect [skills/adp-panel-refresh/scripts/panel_refresh.py:558]
- [x] [Review][Patch] Fail closed on malformed durable status-intent outbox content [skills/adp-panel-refresh/scripts/panel_refresh.py:210]
- [x] [Review][Patch] Validate staged publication eligibility before switching the current Panel [skills/adp-panel-refresh/scripts/panel_refresh.py:1289]
- [x] [Review][Patch] Require ready audit disposition and symmetric source binding for publication eligibility [skills/adp-panel-refresh/scripts/panel_refresh.py:1437]
- [x] [Review][Patch] Serialize live inspect with refresh and fact locks [skills/adp-panel-refresh/scripts/panel_refresh.py:1381]
- [x] [Review][Patch] Mark reuse plans terminal and recompute refresh-status identity [skills/adp-panel-refresh/scripts/panel_refresh.py:1268]
- [x] [Review][Patch] Reject applying a plan older than the latest successful publication [skills/adp-panel-refresh/scripts/panel_refresh.py:1166]
- [x] [Review][Patch] Suppress aggregate WDR projection rows when all embedded action IDs exist in the ledger [skills/adp-agent-program-lead/scripts/adp-state-prepass.py:1004]
- [x] [Review][Patch] Pin installed status-sync registry and schema checksums [skills/adp-setup/assets/module.yaml:66]
- [x] [Review][Patch] Reject colliding inspection and validated-answer output paths [skills/adp-setup/scripts/inspect-install-state.py:826]

## Suggested Review Order

**刷新编排与发布门禁**

- 从 durable plan 恢复 DAG，并仅发布严格收敛的 staged generation。
  [`panel_refresh.py:1416`](../../skills/adp-panel-refresh/scripts/panel_refresh.py#L1416)

- 发布前统一验证 audit、pending intent、drift 和 Panel 完整性。
  [`panel_refresh.py:1113`](../../skills/adp-panel-refresh/scripts/panel_refresh.py#L1113)

- detect 返回精确 action ID、repair batch 和中断恢复位置。
  [`panel_refresh.py:623`](../../skills/adp-panel-refresh/scripts/panel_refresh.py#L623)

**事实 mutation 与幂等**

- Meeting 先完成冲突预检，再生成 typed create/patch intake。
  [`sync_meeting.py:781`](../../skills/adp-meeting-sync/scripts/sync_meeting.py#L781)

- Status Sync 统一处理 command replay、WDR 投影和 refresh handoff。
  [`sync_status.py:3122`](../../skills/adp-status-sync/scripts/sync_status.py#L3122)

- BMM 为缺失身份的 action 生成稳定 command/action ID 和 evidence。
  [`sync_bmm_checkpoint.py:570`](../../skills/adp-bmm-checkpoint-sync/scripts/sync_bmm_checkpoint.py#L570)

**漂移审计与可恢复修复**

- Audit 对 ledger/WDR/sidecar 做逐 action 对账并阻断重复身份。
  [`audit_state.py:1409`](../../skills/adp-state-audit/scripts/audit_state.py#L1409)

- Repair 重新绑定 source read-set、fact generation 和 token state。
  [`sync_status.py:3480`](../../skills/adp-status-sync/scripts/sync_status.py#L3480)

**操作路由与安装契约**

- Program Lead 的 Panel refresh 直达编排器，不依赖旧 canonical view。
  [`consume_program_status.py:102`](../../skills/adp-agent-program-lead/scripts/consume_program_status.py#L102)

- Setup 固定 schema/registry 字节，防止安装态悄然漂移。
  [`module.yaml:67`](../../skills/adp-setup/assets/module.yaml#L67)

**回归证据**

- 端到端覆盖 crash resume、失败前不切换 current Panel。
  [`test_panel_refresh.py:151`](../../skills/adp-panel-refresh/scripts/tests/test_panel_refresh.py#L151)

- 验证 updates-file 成功重放复用 receipt 且零写入。
  [`test_sync_status_v2.py:289`](../../skills/adp-status-sync/scripts/tests/test_sync_status_v2.py#L289)

- 验证 meeting owner-only patch 精确命中已有 action identity。
  [`test_sync_meeting.py:1343`](../../skills/adp-meeting-sync/scripts/tests/test_sync_meeting.py#L1343)
