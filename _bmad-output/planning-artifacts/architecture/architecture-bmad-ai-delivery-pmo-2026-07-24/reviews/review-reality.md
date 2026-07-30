# ARCHITECTURE-SPINE Reality Check Review

## 结论

方向成立，但当前版本尚不能作为 build-ready architecture spine。核心的单向状态传播、单 writer、live-source freshness、typed repair 都针对了真实缺陷；问题在于文档把目标态不变量写成了已落定合约，却没有把现有 writer、schema、身份模型和兼容输入迁移到目标态的路径闭合。若按现文直接实现，最可能出现三类二次问题：把 `baseline_revision` 误当 action revision、把运行态 freshness 元数据写进不可变 Panel identity、以及在并发 `status-sync` 中继续发生 lost update。

审查基于仓库现有实现逐项核对，重点读取了 `adp-meeting-sync`、`adp-status-sync`、`adp-state-audit`、`adp-agent-program-lead`、`adp-management-panel`，并补查了 `adp-bmm-checkpoint-sync`、`adp-program-status`、`adp-roadmap-sync`、`adp-flow-graph`、`adp-meeting-pack` 的实际调用和 schema。

## Findings

- **AD-1 的 writer 清单不完整，当前仓库中至少还有一个未纳入设计的 `Project Status` writer。** `adp-bmm-checkpoint-sync/scripts/sync_bmm_checkpoint.py:971-1002` 直接替换 WDR 的 `Project Status` 中 `Progress`、`Blockers`、`Risks`、`Dependencies`、`Scope or change notes`、`Next actions`，并在 `:1315-1320` 写回更新后的 record。与此同时，`adp-meeting-sync/scripts/sync_meeting.py:812-821` 仍直接向 WDR 文件追加会议区块。Spine 的 `binds`、`sources` 和 structural seed 均未包含 `adp-bmm-checkpoint-sync`。必须把所有现存 writer 做成显式 inventory，并规定每个入口是迁移为 status-sync command producer、保留为初始化 writer，还是退役；否则“独占 writer”只在文档范围内成立。

- **“meeting-sync 只写 evidence/archive/receipt”与会议证据的实际落点没有闭合。** 当前 meeting-sync 把 `Meeting Sync Update` 证据区块写进 `delivery-record.md`（`sync_meeting.py:1244-1269, 812-821`）。目标态若禁止 meeting-sync 写 WDR，需要明确这些区块是迁移到 meeting archive/daily log，还是由 status-sync 在同一次 command 中代写 evidence append；后者又意味着 WDR 不只是 current-field patch。现文同时说自由文本“仅作为 evidence”却没有指定 durable evidence owner/path，实施者会得到两个合理但互不兼容的实现。

- **AD-2 所需的 action revision 在现有数据模型中不存在，且不能复用 `Baseline Revision`。** `ActionUpdate` 只有 `baseline_revision`（`adp-status-sync/scripts/sync_status.py:102-123`），ledger 列也只有 `Baseline Revision`，没有 action revision（`:69-90`）；action-flow v1 的 `baseline_revision` 同样代表计划基线（`assets/action-flow-relation-v1.schema.json:27-38`）。Spine 必须明确新增独立的 `Action Revision`/`revision`，定义旧行初始值、create 初始值、patch 递增规则、投影字段和 schema 版本升级；否则 `expected_revision` 会被错误绑定到项目基线修订号。

- **版本化 patch 还缺少真正的并发边界。** status-sync 目前复制整个 memory root 到 staging，最后对变更文件逐个 `os.replace` 并在失败时回滚（`sync_status.py:1963-2037, 1736-1786`），但没有像 meeting-sync 那样的文件锁。两个 status-sync 同时从相同 ledger/WDR 快照执行时，均可通过本地检查，后发布者覆盖先发布者。仅在 command 内校验 `expected_revision` 不足以防止该竞态；架构需要规定锁文件、锁粒度、锁内重新读取与 CAS 校验，以及 action ledger、WDR、action-flow、receipt 的事务边界。

- **文档的 idempotency 约定没有可落地的 command identity/receipt 索引。** AD-2 没有把 `command_id` 写入必填字段，Consistency Conventions 却要求按 command ID、target revision、payload fingerprint 去重。当前 status-sync receipt 每次使用随机 `ssr-{uuid}`，只记录 input path/hash/update count（`sync_status.py:1802-1832`），执行前不查重；同一 intake 可重复执行并生成新 receipt。需定义 command ID 的生成方、唯一作用域、canonical payload hash、receipt lookup/index、相同 ID 不同 payload 的 conflict，以及批次内每条 command 与 root receipt 的映射。

- **legacy action “缺 operation 一律按 create”会破坏现有合法更新流。** 当前 contract 允许带 exact `action_id` 的 owner/status 更新，终态更新甚至强制要求 `action_id`（`sync_status.py:529-606`）；仓库测试也以无 `operation` 的 `{action_id, status: done}` 更新既有 action。若全部解释为 create，将与现有 action ID 冲突，或把更新错误注册为新 action。兼容 decoder 至少应区分“无 action_id 的 legacy create”和“有 action_id 的 legacy upsert”，后者应 fail-visible 为 migration-required，或在锁内确认 ID 存在后生成带当前 revision 的显式 patch；不能无条件降级为 create。

- **现有 partial update 默认值正是 AD-2 要消除的问题，但 spine 未定义完整字段语义和状态机。** `ActionUpdate` 默认 `status="open"`、`owner/workstream/due/closure="TBD"`（`sync_status.py:103-120`），`merge_action_row` 总会覆盖 status，再条件覆盖其他字段（`:907-946`）。目标 `set` 必须列出允许字段、值类型、clear/unset 表达、不可变字段、status 枚举和终态转换规则，并说明 derived timestamps 如何更新。否则“omitted 与 empty 不同”仍无法指导实现，尤其无法区分清空 owner、拒绝空 owner和保持 owner。

- **`ACT-*` identity convention 与当前 schema/测试不一致。** 当前 action-flow schema 的 stable ID 只要求通用 `[A-Za-z0-9][A-Za-z0-9._-]*`，status-sync 接受显式的 `A-FLOW-1`、`ACT-WRONG` 等 ID；只有自动生成路径使用 `ACT-YYYYMMDD-NNN`。Spine 若把“Action 使用 `ACT-*`”设为 invariant，需要给出 legacy ID 迁移或 alias 策略，并升级所有生产者/消费者 schema；否则应把 convention 改成“新建 ID 使用 ACT-*，既有稳定 ID 保留”。

- **AD-3 的 WDR patch 形状不足以表达当前字段的 omitted/empty/replace 行为。** 现有 `StatusUpdate` 对 `blockers/risks/dependencies/change_notes` 使用空 list 默认值，`update_values` 仅在 list 非空时写入，因此无法用空 list 清空字段；只有 `next_actions_provided` 额外保存了 presence（`sync_status.py:136-168, 393-429, 1523-1545`）。架构应给出 versioned JSON schema 示例，至少定义 scalar `replace/clear`、collection `replace/add/remove`、空数组语义、去重/排序、冲突组合以及 field allowlist。还应明确 `next_actions` 与 `refresh_actions` 同时出现时是拒绝还是固定优先级；当前实现是显式 `next_actions` 优先（`:1458-1465`）。

- **action-flow 的 ownership 与“事实 mutation / projection refresh 两个 receipt”相冲突。** status-sync 当前在一次 staged transaction 中改 ledger、立即生成 `views/action-flow.json`，再和 WDR/receipt 一起发布（`sync_status.py:1970-2037`）。Spine 图中把 action ledger 到 action-flow 画成投影关系，但 structural seed 没有 action-flow producer，AD-6/Publication 又要求事实 mutation 与 projection refresh 分开。必须选定一种模型：action-flow 是 status-sync 事务内的强一致派生物，还是独立 producer/DAG 节点；若独立，需新增 owner、CLI、schema gate、receipt 和失败后的 dirty 行为。

- **AD-4 的 fingerprint 规范与现有 producer 并不兼容，迁移范围被低估。** state-audit 的 `file_sha256` 返回裸 64 位 hex（`adp-state-audit/scripts/audit_state.py:2018-2032`）；roadmap timeline gate 使用绝对路径 key 和裸 hash，并直接 `Path(raw_path)` 复验（`adp-roadmap-sync/scripts/render_roadmap.py:948-960, 1294-1305`）；Spine 则要求相对路径、`sha256:` 前缀和 semantic fingerprint。必须定义统一 fingerprint record（建议包含 root kind、relative path、hash mode、digest），以及 raw-byte、UTF-8/LF-normalized、canonical JSON、Markdown semantic hash 分别何时使用。否则“semantic”只是形容词，跨平台换行或非语义格式变化会得到不同结论。

- **新增 `projection_contract_version` 会直接撞上现有严格 schema。** Management Panel model/manifest、flow-graph、program-status progress 等 schema 普遍 `additionalProperties: false`，例如 `adp-management-panel-v1.schema.json:6-9`、manifest schema `:6-38`、`adp-flow-graph-v1.schema.json:6-9`。当前 top-level producer 又各自使用不同版本字段，如 `flow_graph_schema_version`、`progress_schema_version`、`schema_version`。Spine 需要列出每个 projection 的新 schema version、字段落点和兼容 reader policy，而不是只增加一个跨产品字段名；否则升级第一步就会被已有验证器阻断。

- **当前 Panel audit 只能证明“装载的投影文件未变”，不能证明底层 WDR/ledger 仍是 live，AD-4 尚缺 transitive resolver。** Management Panel 的 `_panel_source_paths` 只包含 program-status、roadmap、flow-graph、meeting-pack、history 及 artifact audit 文件（`adp-management-panel/scripts/management_panel.py:570-631`）。panel input audit 对这些文件做 byte hash，并检查投影自报 fingerprint 的格式和 generated_at 年龄（`adp-state-audit/scripts/panel_audit.py:372-395, 489-556`），没有从 manifest 路径解析并重算 WDR/ledger leaf hash。必须定义由哪一层携带完整 transitive leaf lineage、Panel 如何在 project root/memory root 间解析、路径消歧和 root-escape 拒绝，以及缺 leaf 时的 migration-required。

- **现有 Panel 合并 fingerprint 使用无命名空间的 `dict.update`，会静默覆盖同名 source。** `panel_model.py:938-956` 依次合并 status、roadmap、两个 meeting pack 的 `source_fingerprints`；相同路径 key 后写覆盖前写，审计无法知道两个 producer 是否声明了不同 digest。新 lineage contract 应按 producer 保存 source set，或在合并时要求同 key digest 完全一致并对冲突 fail closed；这也是 invalidation DAG 正确计算依赖边的前提。

- **AD-5 要比较 owner/text/due，但 WDR 当前投影不是可靠的结构化载体。** WDR `Next actions` 是分号分隔的单行字符串，managed entry 形如 `[action_id:id] owner: text (due: value)`（`sync_status.py:1131-1172`）。解析器目前只从行首 marker 提取 ID（`:1210-1212`）；prepass 也只比较 ID 集，虽然 ledger evidence 携带 text/due，却不比较 owner/text/due（`adp-state-prepass.py:911-957`）。action text、owner 或 due 本身含 `;`、`:`、括号时还会破坏 round-trip。应采用可转义的结构化 block/JSON projection，或冻结严格 grammar 与 escaping；仅靠展示字符串无法满足可验证 split ownership。

- **AD-7 指出了 canonicalization 丢 ID，但目标 repair contract 仍不足以安全批修。** 当前 `canonical_finding` 把 `action_id` 只放进 finding identity 计算，不复制到输出（`audit_state.py:2951-2998, 3001-3014`），这与问题描述一致。新增 `entity_refs`/`repair_batches` 时还必须定义 JSON schema、稳定排序和 batch ID、每条 repair 的 expected action revision/source fingerprint、允许的 command type、重复 finding 合并规则，以及 source line 变化后的行为。`source_line` 只能作诊断提示，不能作为 repair selector；否则审计后文件发生插入就可能修错行。

- **AD-6 的 orchestrator 只有目录名和概念，没有足够的信息调用现有 workflow。** 仓库不存在 `skills/adp-panel-refresh/`。现有 producer 参数并不统一：flow-graph需要 baseline/program-status/action-flow/risk 输入，meeting-pack 需要 scenario/window/confirmation，Panel 还要求 explicit selection policy。Spine 应补充 orchestrator input/output schema、quick/full 的精确定义、producer adapter/command mapping、selection policy 获取规则、dry-run/apply authorization、resume/retry、receipt identity、超时和部分失败处置。仅凭 fingerprint 和 identity 不能推导所有业务参数。

- **invalidation DAG 没有明确 artifact-audit 节点和 selection-policy 失效传播。** Panel 当前要求 program-status、roadmap、meeting-pack 的 artifact audit，并把相关 audit ID 纳入 `panel_id`（`panel_model.py:957-975`）；flow graph、history 和 selection policy 也参与 Panel compose。图中只画 producer，没有表示每层 input/artifact audit 何时重建，也没有说明 topology/state 变化后旧 selection policy 是自动迁移还是阻断。DAG contract 应将 audit gate、selection policy validation和 action-flow producer列为显式节点/边，否则 orchestrator 可能生成内容更新但 audit ID 或选择策略仍旧的半新快照。

- **AD-8 把运行态字段放进 Panel/manifest 会破坏现有不可变 identity，必须分层存储。** 当前 `panel_id` 包含 source fingerprints 和 audit IDs，manifest 是 model 的一部分，bundle/HTML 按 panel ID 不可变发布（`panel_model.py:966-1016`；`management_panel.py:1186-1237`）。`last_successful_refresh`、`pending_invalidations` 会在不改变业务内容时持续变化；若写入 manifest，会改变 panel content/identity或要求改写 immutable bundle。应把 `artifact_integrity`、`business_freshness` 的生成时快照与运行态 refresh status 分开：不可变 manifest 记录生成时 freshness evidence，另设原子更新的 current pointer/status sidecar 记录 last refresh、dirty reason、pending DAG、当前/旧 panel ID。

- **失败时“旧 Panel 保留并返回 dirty state”只有前半句由现有代码保证。** 当前 Panel 在所有 gate 后才 `atomic_replace(index.html)`（`management_panel.py:1204-1237`），因此失败会保留旧文件；但没有 durable dirty state，也没有标准错误 envelope 携带旧 panel identity、failed node、pending invalidations 和 retry receipt。Spine 应指定 sidecar schema及写入时机，避免 CLI 失败后只能通过日志猜测为什么 current 仍旧。

- **AD-8 提到 Program Lead open/refresh 必须先 live inspect，但相关实现和 source binding 未纳入变更范围。** `consume_program_status.py:383-419` 的 open/readiness 只把 embedded panel 的 `program_status_snapshot_id` 与 canonical status 比较，`inspect_panel` 只校验 HTML/model/bundle 一致性（`:439-470`）；refresh 甚至只返回 route-required（`:356-363`）。Spine `sources` 列了 prepass，却没列真正负责 Panel open route 的 `consume_program_status.py`。应把该文件加入 binds/sources，并要求调用统一 live inspect contract，而不是保留第二套较弱 inspection。

- **“影响当前展示则 blocked，否则 degraded”缺少可执行的 relevance 规则。** 当前 Panel 有三种 view、history selection、meeting subgraph和 shareable redaction；一个 leaf source mismatch 是否影响当前展示，不能由路径字符串可靠推断。需要由每个 projection 输出 source-to-field/section dependency，或由 Panel selection 产生所选 lineage closure；orchestrator/audit只能据此判定 blocked/degraded。没有这个 contract，实施者仍会凭经验分类，正好重现现有 stale-but-green 问题。

## 已核实可保留的决定

- Python `>=3.10` 与代码一致：相关脚本使用 PEP 723 `requires-python = ">=3.10"`，Panel 与 meeting-sync CI 明确在 Python 3.10 上运行。
- Self-contained HTML Panel schema `1.0.0` 与实现一致：`panel_model.py` 的 `PANEL_SCHEMA_VERSION` 和两个 Panel JSON schema均固定为 `1.0.0`。
- 现有 Panel 已具备外部 input/artifact audit、immutable bundle 和 current HTML 原子替换；目标方案应复用这些边界，而不是重写 publication 模型。
- ELK 与 Markdown renderer 版本来自仓库固定资源而非推测：当前分别为 ELK `0.9.3` 和 markdown-it `14.1.0`，且已有 hash/license 校验。Spine 未直接承诺这两个版本，无需为本轮 freshness 改造升级它们。
- state-audit 当前确实能发现 WDR/ledger 的 action ID 集漂移，且现有 finding 具有稳定 `finding_id`；本轮应在此基础上扩展 entity refs、字段级漂移和 repair plan，而不是另建第二套 audit identity。

## 建议的 build-ready 修订门槛

- 增加 current-state/target-state/migration 三列表，逐项标注 AD-1 至 AD-9 是已实现、部分实现还是待实现；移除容易被理解为“代码已符合”的单独 `[ADOPTED]` 标记，或解释它只代表架构决策。
- 增加四个机器合约示例及 schema version：`action_command`、`wdr_patch`、`projection_lineage`、`repair_batch`。
- 增加 writer inventory、projection/audit ownership matrix 和完整 invalidation DAG，覆盖 `adp-bmm-checkpoint-sync`、action-flow、artifact audit、selection policy、Program Lead route。
- 增加并发与事务章节：status-sync 锁/CAS、revision 初始化、批次失败、receipt 去重及 fact/projection publication 边界。
- 将不可变 Panel manifest 与可变 current refresh status 拆开，并定义失败后旧 Panel、dirty sidecar、pending invalidations 的原子更新顺序。
- 为每个 strict JSON schema列出升级版本和 reader compatibility，特别处理 relative fingerprint path、`sha256:` 前缀、action revision 和 `projection_contract_version`。

完成以上修订后，该 spine 才能把正确的设计方向转换为可验证、可迁移且不会破坏现有发布身份的实现基线。
