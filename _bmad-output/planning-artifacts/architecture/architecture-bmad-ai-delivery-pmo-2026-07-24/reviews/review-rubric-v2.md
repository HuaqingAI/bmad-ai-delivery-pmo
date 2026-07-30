# Architecture Spine Rubric Re-review

## Verdict

**未通过：剩余 1 个 High。** 上一轮 5 个 High 中 4 个已关闭，2 个 Medium 中 1 个已关闭、1 个仍为 Medium。机械 lint 继续为 `ok: true`、0 finding。

## Remaining High

### H4 - WDR/ledger drift 仍未被明确纳入 Panel publication required gate

- AD-5（第 94-98 行）定义 ledger、sidecar、WDR 的三方审计，但 `Binds` 仍只有 ledger、WDR、prepass、state-audit，没有 Management Panel 或 refresh orchestrator。
- Capability Map（第 196 行）仍把 drift alert 的归属限定为 `prepass + state-audit`。AD-4 只阻断 live fingerprint mismatch；fingerprint 全部匹配并不能证明 WDR action projection 在生成时就与 ledger 一致。
- AD-6 的 snapshot/CAS（第 100-104 行）解决 refresh 期间源变化，却没有列出 publication 的 required gates；AD-10（第 124-128 行）只说明失败后保留旧 Panel，也没有要求发布前消费 canonical drift verdict。
- 因此一个实现可以完成 fingerprint freshness gate、跳过 AD-5 drift result，并仍自称满足 spine。`missing_in_wdr`、`orphaned_in_wdr`、重复 marker 或 summary drift 仍可能随“fresh”Panel 发布。

**需要的修订：** 在 AD-5 或 AD-6 中明确：Panel refresh/publish 必须消费 state-audit 的 canonical WDR-action drift verdict，且该 verdict 是 required gate；同时固定 blocked/degraded 的机器判据。完成后把 Capability Map 的该能力绑定到 state-audit + panel-refresh/management-panel。

## Prior Findings Closure

| 上轮 finding | 状态 | 复核依据 |
| --- | --- | --- |
| H1 legacy exact-action compatibility | **关闭** | AD-9 第 118-122 行明确“有 `action_id` 为 legacy update，无 ID 才 create”；Contract Seed 第 146 行重复固化该兼容路由。 |
| H2 status/checkpoint atomic boundaries | **关闭** | AD-10 第 124-128 行分别固定 status-sync batch 与 checkpoint batch 的共享 lock、CAS、原子发布边界，并分离 meeting archive、fact mutation、refresh 三个事务。 |
| H3 exact IDs in repair batches | **关闭** | AD-7 第 106-110 行强制 batch 携带排序后的 exact `action_ids`、`finding_ids`、audit ID 与 revisions，并以绑定完整 batch bytes 的 token 执行 apply。 |
| H4 drift in Panel publication gate | **未关闭，High** | 见上。 |
| H5 version compatibility matrix | **关闭** | Contract Seed 第 142-153 行固定 8 个 contract 的 version、producer 和 compatibility rule；AD-2/4/5/7/9 与表中版本一致。 |
| M1 canonical drift taxonomy | **仍为 Medium** | AD-5 已增加 sidecar、active status、manual marker ownership 和 exact summary，但仍未固定 canonical kinds（如 `missing_in_wdr`、`orphaned_in_wdr`、`duplicate_marker`）及默认等级。可与 H4 一并修订。 |
| M2 concurrency/operations boundary | **关闭** | AD-6 的 frozen input snapshot + publish-before recheck 关闭 TOCTOU；AD-10 固定 shared lock/CAS 和 dirty receipt；实时 watcher/SLO 已在 Deferred 第 204 行显式给出 revisit condition。 |

## Gate Exit

将 canonical drift verdict 明确接入 Panel publication required gate，并固化 drift kinds/等级后，可重新判定通过；无需重开其他上一轮 finding。
