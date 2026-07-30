# Architecture Spine Rubric Review v3

## Gate Verdict

**FAIL。** 机械 lint 为 0 finding；上一轮缺失的 Panel publication drift gate 已进入 AD-5 和 publication fence，但 pinned wire contract 尚不能承载该 gate，registry 也未把 dependency/profile/binding 规则固定成唯一可执行数据。另有两个 operational envelope High：跨 workflow 协调状态 identity 未固定，以及 pinned POSIX 文件系统协议与 brownfield Windows 支持边界冲突。

本轮共 **4 个 High，0 个 Critical**。

评审快照：

- `ARCHITECTURE-SPINE.md` 当前 pin：registry `2813d476...f9b6c4`、schema `80bb3b91...d2a53e`、protocol `ff754d59...51dc`。
- 三个附件的实测 raw-byte SHA-256 与上述 pin 完全一致。
- Registry 与 schema 均可解析为 JSON；8 个 `schema_pointer` 均能解析到 schema bundle 中的对象。
- 环境没有 `jsonschema` 包，因此未执行 Draft 2020-12 meta-schema validator；这不影响以下业务合约 finding。

## High Findings

### H1 - Required drift gate 已写入 AD，但唯一 pinned audit contract 无法表达该 verdict

**证据：** AD-5（Spine 第 97-101 行）已明确要求 state-audit 输出 canonical drift verdict，Panel input audit 验证 same-generation live fingerprints，且 selection scope 内每个 physical WDR 都为 `in-sync` 才可发布。Protocol 第 37 行也把 drift verdict 纳入 final publication fence。

但 registry 第 65-70 行提供给 Management Panel/refresh 的唯一 audit wire contract 是 `audit-finding-repair/2.0.0`；其 schema（第 274-314 行）只有 `audit_id`、`findings`、`repair_batches`，没有：

- overall drift verdict；
- selection scope 与逐 physical WDR 的 `in-sync|drift|blocked` 状态；
- `generation_id` 或 dependency manifest ID；
- 生成 verdict 所绑定的 live ledger/WDR/sidecar fingerprints。

AD-11 又规定 unknown field/version fail closed，所以实现不能在 v2 payload 中自行添加这些字段。Panel 无法通过唯一 wire truth 验证“same-generation canonical verdict”，required gate 因而不可执行。

**处置：Fix。** 发布并 pin typed drift verdict contract，或升级 audit contract；至少固定 generation/manifest binding、selection scope、per-WDR status、overall verdict、source fingerprints 和 unknown/missing scope 的 fail-closed 结果。Registry 必须列出 state-audit writer 与 panel-refresh/management-panel readers，并更新版本与 hashes。

### H2 - Registry 的 projection profile 与 Panel binding map 仍不是可机器唯一执行的 contract

**证据：** AD-4（Spine 第 91-95 行）要求 dependency set 由 registry profile + frozen selection policy 派生，并通过 DAG 与 panel binding map 传播 `affects`。但 registry 第 91-127 行的 profile 只有 `required_roles` 和自然语言 `derivation`；没有结构化 source enumerator、DAG node/edge、selection contract reference、Panel view/section binding 或 unknown-impact rule。Schema 第 162-174 行又允许 `affects` 为任意以 `/` 开头的字符串数组，没有约束它必须来自哪个 binding registry。

因此两个实现可对同一 source 分别生成不同 leaf set、`affects` 或 blocked/degraded outcome，同时都满足文字规则。该分叉会直接重现 stale-but-green：漏掉的 leaf 无法被 fingerprint gate 发现，错误的 binding 又会把应 blocked 的 mismatch 降为 degraded。

**处置：Fix。** 把 projection profiles、DAG 和 Panel binding map 变成 pinned structured artifact：固定 node/owner/input/output、source enumeration rule、selection policy/catalog schema reference、source-to-panel pointer map、排序/唯一性及 unknown-impact fail-closed。Registry 应引用其 ID/hash，而不是只保存解释性字符串。

### H3 - Shared lock/journal/generation 的 runtime identity 与存储位置未固定

**证据：** AD-6、AD-10（Spine 第 103-107、127-131 行）和 Protocol 第 28-43 行依赖同一个 shared mutation lock、root registry、transaction journal、`fact_generation`、`panel_generation` 和 current pointer。但 spine、registry、schema、protocol 均未固定这些对象的 root-scoped identity、canonical path/namespace、文件 shape、创建/权限规则或 lock acquisition order；Structural Seed 也没有唯一 transaction manager ownership。

status-sync、checkpoint-sync、meeting append、repair engine、Panel refresh 和 readers 若各自选择不同 lock/journal/counter 文件，仍可声称使用“shared lock”，但事实上无法互斥或恢复同一事务。随后 CAS、ABA fence 与 crash recovery 的安全性全部失效。

**处置：Fix。** Pin runtime state layout/identity contract：每个 root pair 的 lock key/path、root registry、journal directory、generation records、Panel pointer、nonce index、权限与 owner；规定初始化、锁顺序、stale/corrupt state 行为，并要求所有 bound workflows 通过同一 transaction-manager API 与 conformance fixture。

### H4 - Pinned protocol 的 POSIX-only 原语与现有 Windows 支持边界冲突

**证据：** Protocol 第 28、42 行逐字要求 `lstat/openat`、POSIX relative path、same-directory replace 和 parent-directory `fsync`。当前 brownfield CI 的 management-panel 与 meeting-sync workflows 明确覆盖 `ubuntu-latest`、`macos-latest`、`windows-latest`。Python/Win32 不提供与上述逐字要求相同的 `openat`/directory-fsync 行为，文件锁与 replace durability 语义也不同。Spine 的 Stack 只写 Python `>=3.10`，没有声明支持 OS/filesystem、local-vs-network volume 或 durability assumptions。

这违反 good-spine 的 brownfield ratification 与 operational/environmental envelope：团队 A 可放弃 Windows，团队 B 可自行模拟 Win32 行为，两者都会产生不同的安全保证。

**处置：Discuss + fix。** 二选一固定：明确本功能只支持具备所需语义的 POSIX/local filesystems，并同步收窄支持矩阵；或 pin POSIX/Windows platform adapter contract，分别定义 secure path walk、cross-process shared/exclusive lock、atomic replace/directory durability 的等价保证及 crash/concurrency conformance tests。网络盘/同步盘支持也应明确拒绝或列出已验证能力。

## Checklist Result

| Good-spine checklist | 结论 | 说明 |
| --- | --- | --- |
| 固定一级下钻的真实 divergence points | **部分通过** | mutation、revision、snapshot、repair 已明显收敛；drift wire、binding map、runtime state identity 仍会分叉。 |
| 每个 AD Rule 可执行并真正阻止 stated divergence | **未通过** | AD-5 的 required gate 没有可传输 verdict；AD-4/6/10 的关键依赖缺少 pinned machine contract。 |
| Deferred 不会泄漏核心决定 | **通过** | Action Center、watcher/queue、DB migration、fuzzy action matching、offline archive freshness 均边界清楚；显式 refresh 足以承载本轮目标。 |
| Named technology/version verified-current | **部分通过** | JSON Schema Draft 2020-12、RFC 8785、Panel schema 1.0.0 已 pin；Python 只给最低版本，OS/filesystem 能力未 pin。 |
| Ratify brownfield rather than contradict it | **未通过** | 业务 ownership 基本 ratify；POSIX-only protocol 与现有 Windows CI 支持冲突。 |
| 覆盖五项原始能力 | **部分通过** | typed mutation、WDR current fields、live freshness、drift、exact-ID repair 均有架构位置；drift publication wire contract仍缺。 |
| Parent spine 不被弱化 | **不适用** | 未声明 parent spine。 |
| 本 altitude 的 operational/environmental envelope 有决定或 Deferred | **未通过** | crash protocol 已深入，但 runtime coordination identity、supported platform/filesystem 仍静默。 |

## Passed Areas

- Panel publication required drift gate 的**架构意图已关闭**：scope 内每个 physical WDR 必须 `in-sync`；scope 外才可 degraded/repair queue。
- Exact-action legacy decoder、action/WDR revisions、batch atomicity、repair exact IDs/token binding、immutable refresh generation 与 final publish fence 已形成较强的 AD。
- 三项 normative artifact hash、registry 内 schema/protocol hash 和所有 schema pointers 在当前快照一致，没有 pin drift。
- Deferred 项目克制且有边界，没有把本轮问题扩大到数据库、后台 daemon 或模糊 entity resolution。

## Gate Exit

关闭 H1-H4 后再执行一次 hash/pointer/meta-schema 校验，并至少用两套独立 fixture runner 验证：同一 drift snapshot 得到同一 verdict/publication outcome；同一 registry/selection 得到同一 leaf set/binding；所有 workflow 命中同一 lock/journal/generation；Windows 或明确支持的 POSIX 环境满足同一 crash/concurrency safety contract。
