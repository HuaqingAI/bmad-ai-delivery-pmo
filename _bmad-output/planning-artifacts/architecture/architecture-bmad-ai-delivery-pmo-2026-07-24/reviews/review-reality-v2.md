# ARCHITECTURE-SPINE Reality Re-review v2

## Verdict

**FAIL。** 修订已闭合 checkpoint ownership、action-flow ownership 和 runtime sidecar 的主要缺口，也明确了独立 action/WDR revision、shared lock、CAS 与 typed transitive dependency 的方向；但以下 High finding 仍会让不同实现得到不兼容或不安全的行为。

## Remaining High Findings

- **High：legacy action inference 仍没有消除 destructive ambiguity。** AD-9 与 Contract Seed 规定“带 `action_id` 为 legacy update、无 ID 为 create”（`ARCHITECTURE-SPINE.md:118-122, 144-147`），但没有规定 legacy update 只把输入中实际出现的 key 转为 partial `set`，也没有规定 ID 不存在时是 conflict、migration-required 还是 create-with-explicit-ID。现有 v1 reader 会给缺失 status/owner/due 注入 `open`/`TBD` 默认值，且当前 contract 允许显式 action ID 的新建；因此“带 ID 即 update”既可能继续把 owner-only 更新重置为 open，也可能拒绝合法的 explicit-ID create。必须明确 presence-preserving decoder：仅 supplied keys 进入 `set`，锁内读取现存 action revision；ID 不存在时返回确定错误，除非 command 显式 `operation:create`。

- **High：首次 WDR revision 的 CAS 语义自相矛盾。** AD-3 要求 patch 携带 `expected_wdr_revision`，成功后递增 `WDR State Revision`（`:82-86`）；Contract Seed 却只说首次 v2 写入“补 `WDR State Revision: 1`”（`:148`），没有说明 legacy WDR 缺 revision 时逻辑 revision 是 `0` 还是 `1`，首次 command 应期待哪个值，也没有说明补列本身是否算 mutation。必须像 action ledger 一样定义 legacy read revision、first-write CAS 和 resulting revision，否则 producer 无法生成可通过的首次 patch，两个实现还可能分别采用 `0 -> 1` 和 `1 -> 2`。

- **High：“多文件原子发布”仍缺少可实现的 commit point。** AD-10 要求 action ledger、action-flow、多个 WDR/sidecar、daily log、receipt 在共享锁内“原子发布”（`:124-128`），但普通文件系统只能逐文件 rename；现有 status-sync 正是逐个 `os.replace` 并做 best-effort rollback。架构必须定义读者认可的单一 commit marker/transaction manifest、staged generation ID、publish 顺序和 crash recovery；或者把承诺降为锁内串行、receipt 提交后可见。否则进程在第 N 个 rename 后崩溃时仍会暴露半批状态，CAS/lock 无法解决 crash atomicity。

- **High：typed transitive dependency manifest 仍不足以产生确定的 freshness gate。** AD-4 定义了 entry 的 `{root, path, role, fingerprint}`（`:88-92`），但没有定义承载 entries 的字段名/数组 schema、`role` 枚举、`(root,path)` 唯一性与重复 digest 冲突规则，也未列出各 strict producer schema 的升级版本。更关键的是，“影响当前展示则 blocked”仍没有从 leaf dependency 到 Panel selection/view/section 的机器映射，`role` 语义也未承担该映射（`:92, 138`）。必须给出 dependency manifest JSON schema，并定义 selected lineage closure 或 dependency-to-section references；否则相同 mismatch 仍可能被不同 consumer 分别判为 blocked 与 degraded。
