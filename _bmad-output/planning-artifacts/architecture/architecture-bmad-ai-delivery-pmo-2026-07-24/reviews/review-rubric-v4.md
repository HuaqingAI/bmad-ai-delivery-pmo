# Architecture Spine Rubric Review v4

## Gate Verdict

**FAIL。** v3 的四个 High 已在结构层关闭：typed same-generation drift gate、结构化 profile/DAG/binding、固定 runtime paths/identities、POSIX/Windows adapter 均已进入 pinned artifacts。但对这些 contract 做端到端可执行性检查后，仍有 **3 个 High**：projection read set 与同轮输出重叠导致 refresh 自失效、single-use repair token 与 multi-batch run 合约冲突、meeting-pack 必需 policy source 在当前 brownfield 中零匹配。

机械 lint 为 0 finding。评审未修改 spine 或 contract 附件。

## Pin Verification

| Artifact | Expected prefix | Actual SHA-256 | 结果 |
| --- | --- | --- | --- |
| `CONTRACT-REGISTRY.json` | `84700ee0` | `84700ee0e9cb93155e53a8ba51ec21e959e0d4ab1fb5c770d6ef96baddbee5ba` | PASS |
| `panel-sync-contracts.schema.json` | `2c358067` | `2c358067ec23a535de59e6d663cc55e653747eee999a899ce46bd5508fdc990e` | PASS |
| `WDR-AND-TRANSACTION-PROTOCOL.md` | `db31b737` | `db31b7374a1fbd7500130090ad946fdc7978e73b2d885fb70e924447a6bcc86b` | PASS |
| `CONFORMANCE-VECTORS.json` | `79c3b1b2` | `79c3b1b251e916cc0277e7609f915444fbae9dd5dfe743b74d6027f5fecb9dda` | PASS |

Registry 与 schema/vectors 均可解析为 JSON；20 个 registry `schema_pointer` 均存在，且其 fragment 与目标 `$anchor` 一致。

## High Findings

### H1 - Mutable projection 被列为同轮 leaf source，refresh 会自失效或发布后立即 stale

**证据：** Registry 第 283 行把 `views/**/*.json` 全部列为 `state-audit` 的 required leaf；第 301 行又把 `views/program-status.json` 列为 `program-status` 的 `previous-program-status` leaf。Brownfield producer 明确写回这些固定路径：`program_status.py:1507,1538` 写 `views/program-status.json`，flow/status/risk producer 同样写 `views/flow-graph.json`、`views/action-flow.json`、`views/risk-flow.json`。

AD-4/AD-6 与 Protocol 第 35-42 行同时要求：generation 冻结全部 leaf bytes，producer 只消费 staged handles，publication 前重算全部 live leaves并要求 blob map不变。但 contract 没有定义 intermediate canonical projections 的 publication/pointer：

- 若 producer 按 brownfield 行为在 refresh 中写回固定 `views/*.json`，state-audit generation 的 leaf 在 final fence 前已变化，必然 `SOURCE_CHANGED_DURING_REFRESH`。
- 若 producer只生成 staged output而不更新固定路径，现有 canonical readers继续看到旧 projection。
- 若最终 Panel journal顺带发布这些 projection，commit后 leaf fingerprint立即不同于已发布 generation，下一次 inspect会把刚发布的 Panel判 stale。

因此 unchanged/full refresh 都没有一个同时满足 brownfield canonical paths 与 live-leaf invariant 的实现。

**处置：Fix。** 从 leaf profiles 中移除本轮会被覆盖的 mutable projection paths；需要 previous/history 时引用 immutable content-addressed snapshot/upstream ID。另行固定每个 canonical projection 的 staged output、current pointer及与 Panel publication的事务关系，保证 producer output不会同时作为本 generation 的可变 live leaf。

### H2 - Repair wire contract 的 single-use token 与 multi-batch run 语义互相冲突

**证据：** Protocol 第 52-55 行要求 repair run 按多个 `batch_id` 顺序执行、不同 batch允许 partial success，同时 nonce 在一个 batch commit 后从 `reserved` 变为终态 `consumed`。Schema 第 622-635 行允许一个 dry-run request携带多个 batches，但第 637-650 行只返回一个 token；apply request（第 653-665 行）只携带一个 `batch_id`；run receipt（第 667-681 行）却又包含复数 `applied_batch_ids`/`transaction_ids` 和单一 `nonce_status`。

若 token绑定整个 batch list，apply request无法表达有序 run，且首个 batch commit后 token已 consumed；若 token仅绑定一个 batch，dry-run result无法为请求中的其余 batch返回各自 token。两个实现会分别选择“每批一个 token”或“一次 run 一个 token”，但都无法同时满足当前 schema和状态机。原始需求的批量 repair因此不可互操作。

**处置：Fix。** 明确一种模型并同步 schema/protocol：

- per-batch：dry-run request恰好一个 batch，result/apply/receipt均单 batch，客户端按 cursor重复；或
- per-run：token绑定有序 batch list，apply request携带完整 run/batches，nonce只在整个 run终止时 consumed/invalidated，并定义 partial-success 后的不可重放 cursor。

Conformance vectors需加入至少一个两 batch、第二批失败、retry 的完整状态序列。

### H3 - Meeting-pack required profile 在当前 brownfield 中必然 cardinality failure

**证据：** Registry 第 345 行将 `meeting-policy` 定为 `one-or-more`，enumerator 固定扫描 `skills/adp-meeting-pack/**/*policy*.json`。当前 `skills/adp-meeting-pack` 中不存在任何匹配 JSON；只有 `SKILL.md`、`customize.toml`、renderer 与 tests。Protocol 第 34-35 行规定 profile 是强制 read set，producer不得自报缩小。Management Panel profile又要求每个 meeting kind 的 meeting-pack upstream，因此这不是可忽略的 optional source。

一个严格 orchestrator必须在 meeting-pack node报告 required cardinality 缺失并阻断 Panel publication；放宽为 zero matches则违反 pinned registry。Spine 的 migration/Deferred 也没有创建该 policy artifact 的 seed或 rollout gate，不符合 brownfield ratification。

**处置：Fix。** Pin 实际存在且由 meeting-pack消费的 policy/config source，或新增具名、schema-valid、hash-covered policy artifact并把创建/迁移列入结构 seed与 legacy rollout；然后用 real-repo conformance fixture证明 required enumerator至少返回一个文件。

## v3 Finding Closure

| v3 finding | v4 结果 | 依据 |
| --- | --- | --- |
| Typed required drift verdict缺失 | **关闭** | Registry 第 79-85 行注册 `action-projection-drift-verdict/1.0.0`；schema 第 361-388 行绑定 generation、selection、ledger与逐 WDR status；AD-5/Protocol 第 41-42 行固定 required publication gate。 |
| Profile/DAG/binding不可机器执行 | **结构层关闭** | Registry 第 207-373 行固定 enumerators、DAG、binding map、profiles/cardinality/affects；schema固定 selection policy、catalog、manifest与producer receipt。H1/H3 是 profile内容的可执行性问题。 |
| Runtime identity/path未固定 | **关闭** | Protocol 第 57-61 行固定 `.adp-runtime/panel-sync/1.0.0/`、locks、journals、generation、nonce、Panel pointer/state及 root mismatch行为。 |
| POSIX-only 与 Windows冲突 | **关闭** | Protocol 第 60-61 行固定 POSIX/Windows primitives、no-follow/reparse规则、capability probe及 `DURABILITY_UNAVAILABLE` fail-closed。 |

## Good-Spine Checklist

| Checklist | 结论 | 说明 |
| --- | --- | --- |
| Fixes real divergence points for level below | **部分通过** | 五项原始问题和 v3 gaps均有明确 AD/contract；projection publication与repair run仍可产生不兼容实现。 |
| Every AD enforceable and prevents stated divergence | **未通过** | AD-4/6 在 H1 下无法同时保持 leaf不变并更新 canonical projections；AD-7 在 H2 下没有一致执行模型。 |
| Nothing in Deferred leaks required decisions | **通过** | Action Center、watcher/queue、DB、fuzzy matching、offline archive freshness均不影响本轮显式 refresh正确性。 |
| Named tech/current versions pinned | **通过** | Contract/hash、Draft 2020-12、RFC 8785、Panel schema与platform adapter均固定。 |
| Ratifies brownfield | **未通过** | Ownership和路径大体继承；H1与固定 `views/*.json` 写入冲突，H3引用不存在的 mandatory policy。 |
| Covers source capabilities | **部分通过** | typed action/WDR mutation、live freshness、drift gate、exact-ID repair均覆盖；batch repair执行与 refresh publication仍有阻断。 |
| Parent spine inheritance | **不适用** | 未声明 parent spine。 |
| Operational/environmental envelope | **通过** | Lock、journal、recovery、generation、runtime paths、POSIX/Windows capability与fail-closed均有决定；实时 daemon/SLO 有明确 Deferred。 |

## Positive Findings

- 四项 artifact hash与 spine pin完全一致，registry的20个 contract pointer/anchor全部可解析。
- Typed drift verdict已经是 same-generation、selection-bound 的 wire contract，Panel publication gate不再只是 prose。
- Profiles、DAG、binding、allowed affects 与 producer receipts已从自然语言提升为 hash-covered结构。
- Runtime root、journal、lock、fact/panel generation和跨平台 durability边界已足以让多个 workflow使用同一协调域。
- Deferred克制且有清楚的 revisit condition，没有把本轮修复扩大到数据库或后台服务。

## Gate Exit

修复 H1-H3 后，新增三组 pinned conformance：一次完整 refresh不因自身 projection输出失效；两 batch repair在第二批失败后只能按唯一 cursor重试；real-repo meeting-pack profile满足所有 required cardinality。随后重跑 hash/pointer/lint 与 independent adapter gate。
