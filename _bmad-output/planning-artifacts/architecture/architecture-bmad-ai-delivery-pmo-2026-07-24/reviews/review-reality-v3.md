# ARCHITECTURE-SPINE Final Brownfield Reality Review v3

## Verdict

**FAIL。** 三个 normative artifact 的实际 raw SHA-256 与 Spine 固定值逐字一致，两个 JSON 文件语法有效；最新版也已经明确 presence-preserving legacy intent、`ACTION_NOT_FOUND`、WDR revision 0 -> 1、shared physical writer、journal、typed dependency manifest 和 mutable refresh sidecar。但以下五个 High finding 仍会阻断现有仓库向 pinned contracts 的确定性迁移。

## Remaining High Findings

- **High：pinned WDR grammar 不能读取现有 physical WDR，且 `file_generation` 没有权威存储位置。** Protocol `2.15` 要求 `### Project Status` 恰好一次，但实际 workstream template 使用 `## Project Status`（`skills/adp-workstream-register/assets/workstream-templates/delivery-record.md:35`），现有 status-sync/risk writer也只识别 `##`（`sync_status.py:1577-1587`、`review_risk_dependency_change.py:800-805`）；Protocol 的 legacy canonical migration 只覆盖换行/BOM，没有 heading migration，因此所有现有 WDR 首次进入 engine 都会 `WDR_SCHEMA_AMBIGUOUS`。同时 Protocol `2.18` 只声明每个 WDR 有 `file_generation`，却未规定它存于 WDR、独立 metadata sidecar 还是 transaction root registry，也未规定 legacy file 的初始 generation；`wdrPatchV1.expected_file_generation` 因而没有可读取、可 CAS、可恢复的 source of truth。必须固定现有 `##` grammar或提供 versioned heading migration，并 pin file-generation record/schema、legacy 初值、创建/删除/重建规则。

- **High：physical WDR writer inventory 仍不完整，唯一 WDR command 又无法表达已声明的 checkpoint ownership。** 除 meeting/status/checkpoint 外，`adp-risk-dependency-change-review` 仍直接改写 `Project Status` bytes（`review_risk_dependency_change.py:792-817`），`adp-workstream-register` 直接创建 physical `delivery-record.md`（`register_workstream.py:218-235`）；二者均未出现在 Spine AD-1 binds、registry `wdr-mutation.writers` 或 Structural Seed 的迁移责任中。更根本的是，pinned `wdrPatchV1.set` 只有 current fields、`refresh_actions` 与 `meeting_history_append`（schema `:104-133`），不能表达 Spine AD-1 要求 checkpoint 通过 engine 写入的 BMM Artifact Index、Scope、Acceptance、Checkpoint Sync Log、Decisions/Evidence 等 owned sections，也没有 WDR create operation。必须补齐 writer inventory，并增加 versioned create/owned-section/risk-relation commands，或收窄 AD-1 的 ownership 承诺。

- **High：legacy adapter 的规则仍不能构造一个 schema-valid、幂等且可 CAS 的 v2 command。** Registry 只说显式 `action_id` 转 patch、记录 raw presence、target 不存在返回 `ACTION_NOT_FOUND`；但 v2 patch 强制要求 `command_id`、`expected_revision`、非空 evidence 及 `ACT-YYYYMMDD-NNN` ID（schema `:57-93`），legacy payload 不保证提供这些字段。附件未规定 command ID/evidence 的确定性 derivation，也未规定 adapter 必须在 shared lock 内读取 revision。现有 ledger/schema允许通用 stable ID，测试和实际兼容路径使用过 `A-FLOW-1` 等非 `ACT-*` ID，这些已存在 target 也无法通过 v2 `actionId`。必须 pin legacy adapter output contract：ID migration/alias、deterministic command ID、evidence binding、锁内 revision capture及 replay identity。

- **High：journal 把 `committed` marker 写在 fact generation 与 applied receipt 之前，恢复协议无法唯一完成已提交事务。** Protocol `6.42` 的顺序是 targets 验证后先写 `committed`，再递增 fact generation并写 receipt；进程可在 marker 后、generation/receipt 前崩溃。Protocol `6.43` 对“已有 marker且 targets 等于 after hash”没有规定补写 generation/receipt，reader 可能把 mutation 当成已恢复但仍保留旧 `fact_generation`，从而破坏 refresh ABA/TOCTOU fence。应把 fact-generation record和applied receipt纳入 journal target manifest，并让 committed marker成为最后一个持久化 commit point；或明确 marker 内容绑定 intended generation/receipt并规定 marker-present roll-forward。还需为 target 出现 neither-before-nor-after hash 定义 corruption，而不是用笼统“否则恢复 before-image”覆盖未知写入。

- **High：dependency manifest schema存在，但 registry profile 无法完整、机器化地派生现有 producer 的真实依赖。** Registry 的 `required_roles` 使用 `fact:action-ledger` 等细分 token，schema `dependencySource.role` 却只有 `fact|config|audit|evidence`，没有 subtype 字段，validator无法证明 profile被满足；profiles 还把 program-status/roadmap/flow-graph 等 same-generation projection写成 `fact:*`，与 schema 的 `upstreams` 分层不一致。Brownfield inventory也明显不完整：现有 state-audit还读取 daily、decisions、L0、business packets、pending intake/receipts和已有 views；program-status读取config source、locale catalog、可选 signals/evidence/previous snapshot；roadmap与meeting-pack读取更多 decisions、readiness、cadence、meeting archives/receipts，而 registry未枚举这些 derivation。最后，schema只定义 standalone manifest，没有为现有 `additionalProperties:false` projection schema定义嵌入字段/升级版本，`affects` 也未绑定一个 pinned panel binding-map artifact/hash。按 Protocol 的 sandbox规则，现有 producer会大量返回 `UNDECLARED_DEPENDENCY`。必须先生成并冻结真实 read inventory，区分 leaf role subtype 与 direct upstream，pin binding map，并逐个升级 canonical projection schema。

## Verified Closures

- Spine 中的 registry、schema bundle、protocol 三个 SHA-256 均与当前文件 raw bytes匹配。
- Legacy raw presence 与 missing target 的目标行为已经写明；剩余问题是 adapter 必填字段与 legacy ID 可构造性。
- WDR business revision 的 legacy `0 -> 1` 已闭合；剩余问题是实际 heading 和 file-generation persistence。
- Runtime refresh status 已从不可变 Panel manifest 中拆出。
- Action-flow ownership 已明确归 `adp-status-sync`，并被纳入 mutation transaction。
