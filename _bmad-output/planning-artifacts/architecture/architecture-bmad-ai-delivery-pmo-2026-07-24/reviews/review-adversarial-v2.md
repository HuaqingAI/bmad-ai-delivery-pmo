# Architecture Spine 对抗性复审 v2

审查对象：修订后的 `ARCHITECTURE-SPINE.md`

审查范围仅限上轮五组缺口：schema/version negotiation、WDR collection/revision/marker ownership、fingerprint/path resolution、refresh snapshot consistency、repair dry-run/apply binding。

## 结论

**FAIL**。

修订版已经关闭了“完全没有 revision、root identity、active status、refresh snapshot、dry-run token”这一层问题，但仍未把这些规则固化为唯一可执行协议。两支独立团队仍可在完全遵守当前文字的情况下，生成不同的 WDR bytes、不同的 marker ownership、不同的 dependency manifest、不同的 refresh generation，以及安全属性不同的 repair token。剩余问题会直接导致事实丢写、旧 Panel 被发布或未授权 repair 被应用，仍属于冻结前必须关闭的缺口。

## 复测结果

| 原缺口 | 修订已关闭的部分 | 仍未关闭的部分 |
| --- | --- | --- |
| Schema/version negotiation | 增加 Contract Seed、固定主要版本 | 没有 normative schema artifact、schema hash、reader/writer negotiation 和 unknown-version 行为 |
| WDR collection/revision/marker | 增加 per-workstream revision、CAS、active status、sidecar、add/remove identity | replace/serialization、全文件并发、marker grammar/multiplicity、summary contract 仍不唯一 |
| Fingerprint/path | 固定 raw bytes SHA-256、显式 root、transitive leaves | manifest schema/role/duplicate policy、root canonicalization/symlink policy、依赖完整性验证仍缺失 |
| Refresh snapshot | 增加开始快照、producer 回报和发布前复验 | hash 与实际读取 bytes 未绑定、新旧 upstream ID 模型冲突、复验与 publish 间仍有 TOCTOU |
| Repair dry-run/apply | token 绑定 batch bytes 与 revisions | token authenticity、authorization、scope/expiry、batch grouping 和 replay/rollback 防护未定义 |

## 仍可成立的双实现反例

实现 A 和实现 B 都逐字遵守修订版：

- A 把 WDR collection trim 后去重并按字典序写回；B 只用 trim 后值做 membership 判断，但保留首次出现顺序和原始展示空白。二者都采用“trim 后、大小写敏感的 exact value set”，revision 都 CAS 后加一，但同一 patch 产生不同 durable bytes 和不同 downstream fingerprints。
- A 遇到一个 entry 中两个 `[action_id:*]` marker 时阻断；B 取第一个 marker，并把第二个 marker 当普通正文的一部分。二者都把“带 marker 的 entry”交给 sidecar 投影并比较 exact summary，但 ownership 和 repair result 不同。
- A 接受解析后仍位于 root 内的 symlink；B 拒绝所有 symlink。二者都拒绝 root escape，但同一合法 manifest 在一个实现中 fresh、另一个实现中 unverifiable。
- A 在 refresh 开始时把 leaf bytes 复制到不可变 staging，并让所有 producer 只读 staging；B 先 hash live path，再重新打开同一路径读取内容，最后按规则重算 fingerprint。若文件在 hash 与读取间 A→B→A，B 可以报告原 snapshot 且发布从中间 bytes 构建的 projection；当前文字没有要求 hash 与 consumed bytes 来自同一个 file descriptor/content-addressed blob。
- A 的 repair token 是服务端持久化的随机 nonce，绑定 project identity、principal、expiry 和 batch digest；B 的 token 是公开字段的普通 SHA-256。二者都可声称 token “绑定完整 batch bytes 与当前 revisions”，但 B 的 token可伪造且可跨 project/revision rollback 重放。

## Findings

- `ARCHITECTURE-SPINE.md:142-153` 只给出 contract 名称和版本表，没有给出 normative JSON Schema 路径、`$id`、schema content hash 或必需字段的完整类型。两个团队可发布不同字段 shape 却都标成 `2.0.0`。应把每个 contract artifact 的路径和 SHA-256 固定在 spine，并要求 receipt 记录实际 schema ID/hash。

- `ARCHITECTURE-SPINE.md:144-153` 的 compatibility rule 没有定义 capability negotiation。`v1 reader 保留；v2 writer 强制` 无法回答 v2 producer 能否向 v1 consumer 发命令、未知 minor 字段是忽略还是阻断、未知 major 是 migration-required 还是 contract violation。应发布 reader/writer compatibility matrix、negotiation algorithm 和 fail-closed error code。

- `ARCHITECTURE-SPINE.md:80,86,110` 同时使用 action command v2、WDR mutation v1、audit v2，但 mutation envelope 仍只有概念字段，没有 canonical serialization。`command_id + fingerprint`、repair batch bytes 和 content identity 会因 JSON key order、Unicode、number/string version 表示不同而分裂。应指定 canonical JSON profile及 payload fingerprint 的 included/excluded fields。

- `ARCHITECTURE-SPINE.md:86` 只把 `add/remove` 定义为 trim 后 exact value set，没有定义 `replace` 是否 trim、去重、排序，也没有定义写回时保留原始空白还是保存 canonical value。两个合规 writer 会为同一逻辑集合生成不同 WDR raw bytes，随后 AD-4 会把序列化差异当业务失效。应为三种 mode 统一规定 canonical stored value、dedupe、ordering、absent remove 和 empty replace 行为。

- `ARCHITECTURE-SPINE.md:74,86,128` 的 WDR State Revision/CAS 没有覆盖所有物理 WDR writer。meeting-sync 被允许直接追加同一 WDR 的 history/evidence，而 shared WDR mutation engine 在 shared memory lock 下改写 current fields；若 meeting-sync 不使用同一文件锁或 full-file CAS，append 与 rewrite 仍会互相覆盖。应让所有物理 WDR 写入共享同一 file generation/CAS，或把 owner sections 拆成独立 durable files 后再 materialize。

- `ARCHITECTURE-SPINE.md:98` 固定了 sidecar 和 active status，但仍没有 marker grammar。entry 边界、marker 位置、每 entry marker 数量、大小写、escaping、非法 ID、同 ID 多 entry 的 multiplicity 都没有统一处理。set 比较可以静默吞掉 duplicate，而 multiset 比较会报 drift；两者都可称为“比较 ID 与 exact summary”。应发布 parser grammar、canonical renderer 和 malformed/duplicate 真值表。

- `ARCHITECTURE-SPINE.md:98,150` 引用了 `wdr-action-projection/1.0.0` 和 deterministic rendered summaries，却没有绑定该 contract 的 schema/renderer version/hash。owner、text、due 的分隔符、空值、escaping 和 Unicode normalization 仍可不同。应把 summary 作为结构化字段保存，固定 renderer bytes，并提供 ledger→sidecar→WDR 的 golden vectors。

- `ARCHITECTURE-SPINE.md:92,137` 描述了 dependency manifest 的四个字段，但没有定义 `role` enum、同一 root/path 多 role 是否允许、duplicate leaf 如何归并、排序 key 和 manifest identity。不同 producer 会得到不同 snapshot bytes/identity，进而产生不同 invalidation set。应发布完整 manifest schema与 canonical ordering/deduplication 规则。

- `ARCHITECTURE-SPINE.md:92,137` 要求拒绝 root escape，但没有定义 project/memory root 的 canonical identity、symlink policy、case sensitivity 和 Unicode-normalized path comparison。接受安全的 in-root symlink与拒绝所有 symlink都合规，却产生 fresh/unverifiable 分歧。应规定 `realpath`/`openat` 策略、root instance ID，并把 root identity 绑定到 receipt/token，不能只绑定逻辑枚举值。

- `ARCHITECTURE-SPINE.md:92` 要求 producer 自报“全部 transitive leaf dependencies”，但没有外部方式验证其完整性。遗漏一个实际读取源的 manifest 仍会通过 Panel 的 fingerprint 重算，因为 inspect 只能验证已声明项。应由 contract registry规定每种 projection 的 required roles/dependency derivation，或由 orchestrator提供并记录所有 content-addressed inputs，禁止 producer自行缩小依赖集。

- `ARCHITECTURE-SPINE.md:104` 冻结了 leaf fingerprints 和 upstream IDs，却没有区分 refresh 开始时的旧 upstream IDs 与本轮 DAG 新生成的 staged upstream IDs。下游 producer若消费旧 ID 会发布旧链，消费新 ID又无法逐字回报开始时冻结的 snapshot。应定义 generation model：只冻结 leaf fact snapshot和 selection policy；每个 staged node再绑定同 generation 的 predecessor IDs，并验证其 transitive leaf snapshot一致。

- `ARCHITECTURE-SPINE.md:104,139` 的 producer “回报 consumed snapshot” 只是声明，没有把被 hash 的 bytes 与 parser 实际读取的 bytes 绑定。先 hash、后重新打开文件会遭遇 A→B→A 的 ABA，发布前全量 fingerprint仍相等。应让 orchestrator建立 immutable/content-addressed staging，producer只通过 snapshot handle读取，或要求 hash 和 parse来自同一稳定 file descriptor并验证 generation metadata。

- `ARCHITECTURE-SPINE.md:104,139` 只要求“发布前”复验，没有让复验与 current pointer更新处于同一个 fence/CAS。源可以在最后一次比较后、Panel rename 前变化，旧 generation仍被发布；两个并发 refresher也缺少 monotonic generation fence。应在共享 mutation read lock 内执行 final compare + publish CAS，或让每次事实 mutation原子推进 generation counter并以该 counter作为 publish precondition。

- `ARCHITECTURE-SPINE.md:110` 没有定义 repair token 的真实性。普通 digest、HMAC、服务端 opaque nonce 都符合“绑定 batch bytes 与 revisions”，但只有后两者能防伪造。应规定 token issuer、签名/MAC或durable nonce index、key/version、single-use消费和验证失败 error code。

- `ARCHITECTURE-SPINE.md:110` 的 token 没有绑定 project/memory root identity、authorization principal/scope、schema/engine version、expiry 和 dry-run outcome。相同 revisions 在另一个项目或备份回滚后可能再次出现，旧 token仍可重放；当前规则也没有证明 apply 人获准执行该 repair。应把这些字段纳入服务端验证状态，并在任一不一致时强制重新 dry-run/approve。

- `ARCHITECTURE-SPINE.md:110` 只说按 workflow/workstream 输出 deterministic batches，没有规定每组恰好一批、跨 operation 的合并/依赖、全局排序、batch ID 和 partial-apply receipt。A 可按 workstream 原子修复，B 可按 finding 逐项提交；两者 token bytes和失败后的事实状态不同。应固定 batch construction algorithm、transaction boundary、retry cursor和 all-or-nothing/partial-success 语义。

## 通过条件

- Contract Seed 的每个版本都能解析到不可变 schema/renderer artifact，并有跨版本 negotiation conformance tests。
- 同一 WDR patch 与 marker fixture 在独立实现中产生完全相同的 fact bytes、sidecar bytes、revision 和 finding。
- dependency manifest、root resolution 和 snapshot identity 有唯一 canonical bytes；未声明依赖与 symlink/root ambiguity 均 fail closed。
- producer只能消费 orchestrator提供的不可变 generation inputs；final source-generation CAS 与 current Panel publish不可分割。
- repair token不可伪造、单次使用、绑定项目/主体/授权/版本/期限；batch construction和失败恢复有唯一结果。
