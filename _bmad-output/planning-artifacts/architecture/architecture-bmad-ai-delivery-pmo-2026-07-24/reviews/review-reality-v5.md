# ARCHITECTURE-SPINE Brownfield Reality Review v5

## Verdict

**FAIL。** 当前包已经把 single-writer、typed mutation、same-generation publication、per-batch repair 和跨平台 journal 的目标边界写得很清楚，且所有 target-state 改造都放在显式 migration gate 后；但五个 High 仍会让严格模式在现有 brownfield 数据上不可执行，或继续漏掉真实源变化。尤其是当前 32/32 conformance receipts 可重复，却没有执行 schema validation、真实 WDR engine 或 Windows filesystem adapter，不能作为 AD-11 所要求的独立实现门禁。

## Frozen Review Target

本轮评审固定在以下 raw-byte hashes；评审结束前复验未变化：

| Artifact | SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `976904b3702c932a9e79f6e0d54721c7590cc2a9341c44fb42e171645dba96db` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `e7cdd79ad95b7f6f7690840193eced97a3f10f894422e3afeacce1d1ee87a813` |
| `contracts/CONTRACT-REGISTRY.json` | `7b6403d9c9e8734e32556dc3555de5c8fb43f4411a7e379b6cb31d2fc5861d9e` |
| `contracts/panel-sync-contracts.schema.json` | `3b11b8c86fcb5b7272dd86576afdce10c50745489e8224e78acc89ff1e430bf8` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `b86bba208688eeeb5c70b04202437c23ecfced8e3143f6a4bf95dcbc5623c434` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `fd2f6bfbdcc4710851fc22211cfcca000e40391bc103f46ef3978ea53b8fe4a9` |

## High Findings

### High 1 - WDR strict grammar has no migration for the section order produced by current writers

The pinned WDR template is correctly hashed, but it contains neither `## Roadmap` nor `## Checkpoint Sync Log` (`skills/adp-workstream-register/assets/workstream-templates/delivery-record.md:1-67`). Current status-sync inserts a missing Roadmap immediately before `## Record Rule` (`skills/adp-status-sync/scripts/sync_status.py:1360-1365`), while current checkpoint-sync appends a missing Checkpoint Sync Log at the end of the file, therefore after Record Rule (`skills/adp-bmm-checkpoint-sync/scripts/sync_bmm_checkpoint.py:945-968`). This is a real deployed grammar, not a hypothetical malformed file.

The normative protocol instead orders `Roadmap -> Cross-Workstream Links -> Decisions and Evidence -> Checkpoint Sync Log -> Record Rule`, with Meeting Sync History between Checkpoint Sync Log and Record Rule (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:34-36`). It defines a legacy migration only for top-level Meeting Sync Update sections, not for a tail-positioned Checkpoint Sync Log. At the same time, the byte-exact create vector reproduces the current template without Roadmap or Checkpoint Sync Log (`contracts/fixtures/CONFORMANCE-VECTORS.json:23-54`) and passes because both runners only check its hash/newline, not the protocol grammar.

**Impact:** the first target-state patch can either reject a valid current WDR as `WDR_SCHEMA_AMBIGUOUS`, silently retain an order the protocol forbids, or relocate bytes differently across implementations. The sidecar revision-0 migration gate does not resolve the physical grammar.

**Required fix:** declare which sections are optional on create, define one byte-preserving migration from every current order (including Record Rule before Checkpoint Sync Log), and pin the insertion/relocation result. Add a vector starting from an exact current template after current status-sync and checkpoint-sync have both written it; apply the shared engine and compare whole-file bytes.

### High 2 - Action v2 cannot represent one supported brownfield action scope, and legacy time normalization is not closed

The pinned status-sync v1 contract explicitly requires a shared action to use `workstream: "program"` plus `affected_workstreams`, and says action-only program updates remain valid (`skills/adp-status-sync/references/batch-status-updates.md:7-11`). The current decoder also automatically chooses `program` for a multi-workstream action (`skills/adp-status-sync/scripts/sync_status.py:551-564`). However, v2 create requires `workstream_id`, and that field references `workstreamId`, whose negative lookahead rejects exactly `program` (`contracts/panel-sync-contracts.schema.json:34-36,130-160`). A pinned v1 payload can therefore be valid in production but impossible to adapt into a schema-valid v2 create.

There is a second deterministic-adapter gap: the pinned meeting grammar shows `started_at` with a timezone offset (`skills/adp-meeting-sync/references/sync-plan-schema.md:9-13`), while v2 evidence accepts only second-precision UTC `Z` (`contracts/panel-sync-contracts.schema.json:42-44,119-127`). The protocol says to derive evidence time from `meeting.started_at`, but does not say whether to copy, parse-and-normalize, round, or reject an offset/fractional timestamp (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:9-11`). Since normalized `observed_at` also enters legacy command identity, implementations can produce different command IDs.

**Impact:** program-scoped shared actions fail migration, and ordinary offset meeting timestamps can yield schema failure or replay-identity divergence precisely on the meeting-to-existing-action path this architecture is meant to repair.

**Required fix:** separate an action routing/scope ID from a physical WDR workstream ID, allow the pinned `program` routing value only for action facts, keep WDR commands physical-only, and pin ISO-8601 parsing plus canonical UTC-second rendering before command-ID derivation. Add program-action and `+08:00`/fractional timestamp golden vectors.

### High 3 - Registry profiles still omit material inputs consumed by canonical readers

The current profiles are structured and no longer self-enumerate current JSON outputs, but they still are not complete enough to drive AD-4/AD-6 freshness:

- `state-audit/1.0.0` lists ledger, selected WDRs/sidecars, daily/decision/L0/intake/status receipts, but not the program baseline, effective config, the three core files, workstream `evidence.md`/`decisions.md`/`readiness.md`, or the Markdown views that current state-audit evaluates (`contracts/CONTRACT-REGISTRY.json:379-398`). The actual prepass reads core/views/checkpoint sources and scans all three workstream sidecars (`skills/adp-agent-program-lead/scripts/adp-state-prepass.py:32-84,545-603,813-844`); state-audit also always validates `plans/program-baseline.md` (`skills/adp-state-audit/scripts/audit_state.py:1554-1678`).
- `roadmap/1.0.0` omits the action ledger, `l0/extracted-gates.md`, `l0/extracted-decision-gates.md`, previous baseline history, and the state-audit projection it gates on (`contracts/CONTRACT-REGISTRY.json:418-434`). All are current renderer inputs (`skills/adp-roadmap-sync/scripts/render_roadmap.py:58-76,371-422,1167-1191`).
- `meeting-pack/1.0.0` has useful meeting/action/decision/readiness/cadence leaves and program-status/roadmap/flow upstreams, but no selected WDR/workstream sidecars, daily logs, or state-audit upstream (`contracts/CONTRACT-REGISTRY.json:451-473`). Current meeting-pack builds its boards from a scenario prepass and audit, including workstream state (`skills/adp-meeting-pack/scripts/render_meeting_pack.py:331-446`).
- `management-panel/1.0.0` has only renderer assets as direct leaves (`contracts/CONTRACT-REGISTRY.json:475-491`), while the internal-full Panel embeds raw previews from `actions/action-ledger.md`, `views/risk-matrix.md`, and arbitrary Markdown paths referenced by meeting packs (`skills/adp-management-panel/scripts/management_panel.py:183-228,773-807`). Those bytes affect the HTML and manifest but are absent from its profile.

**Impact:** a baseline-history, workstream readiness/evidence, L0 gate, risk-matrix, or other preview-source mutation can leave producer receipts and the final Panel apparently fresh. This recreates the original “Panel checks artifacts but not source updates” defect inside the new registry-driven design.

**Required fix:** freeze actual read inventories with instrumentation, then either (a) add every direct leaf with a deterministic enumerator, or (b) remove the direct read and bind the corresponding same-generation upstream. State-audit, roadmap, meeting-pack, and management-panel each need an executable read-set test asserting `actual reads == registry-derived allowed reads`; undeclared reads must fail. Add change-one-source invalidation vectors for every source kind above.

### High 4 - The repair contract cannot encode an orphaned WDR action whose ledger row is absent

Current audit explicitly reports every WDR action ID without an open ledger reference (`skills/adp-state-audit/scripts/audit_state.py:2282-2303`); this includes both terminal rows and IDs no longer present in the ledger. The target repair command requires at least one action ID, and its read set requires at least one positive action revision (`contracts/panel-sync-contracts.schema.json:452-477`). Protocol equality then requires the read-set action ID set to exactly equal the command/finding action ID set (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:62-64`).

A truly absent ledger ID has no revision >= 1, so the most important orphan-removal case cannot produce a schema-valid batch. The current vectors cover matching and mismatching extant ID sets, but no empty-ledger/orphaned-WDR case (`contracts/fixtures/CONFORMANCE-VECTORS.json:145-178`).

**Impact:** audit can now preserve the exact ID, but the advertised batch repair path still cannot execute for one real drift class; operators fall back to manual editing.

**Required fix:** model expected presence explicitly, for example `{action_id, expected_present, revision}` with `revision: null` only when absence is asserted under the same ledger fingerprint, or decouple `refresh_actions` scope from per-action revision checks while retaining the ledger fingerprint and WDR CAS. Add vectors for empty ledger + orphan marker, terminal ledger row + stale marker, and active ledger row missing from WDR.

### High 5 - The two conformance receipts are reproducible but are not independent contract implementations

Both checked-in result files were regenerated with the pinned timestamp and reproduced byte-for-byte: Python `4dc1b920...5802`, Node `82f27c143...cd6`, each reporting 32 passed and 0 failed. The source/result hashes in the registry are also correct.

However, the vectors embed literal `$SCHEMA_SHA256` and `$REGISTRY_SHA256` placeholders in contract references (`contracts/fixtures/CONFORMANCE-VECTORS.json:23-27,106-108`), which violate the schema's `sha256:` pattern. Neither runner validates them. The Python create test checks only rendered hash/LF (`contracts/conformance/python_runner.py:50-69`); generation-envelope “conformance” checks only an ID prefix, two roots, and a nonempty leaf list (`:84-97`); platform tests merely recognize an expected error string (`:99-108,147-148`); journal tests consume pre-labeled before/after states rather than performing I/O (`:110-121`). The Node implementation follows the same ID-specific predicates (`contracts/conformance/node_runner.mjs:45-60,77-116,144-145`). Its `windows-model` label does not execute `CreateFileW`, `ReplaceFileW`, reparse-point checks, directory flushes, or crash recovery.

**Impact:** schema-invalid commands and the WDR/profile/runtime gaps above all pass 32/32. AD-11's “two independent implementations” gate is therefore not satisfied even as design evidence.

**Required fix:** resolve placeholders before validation; validate every embedded contract at its registered schema pointer; run the same vectors through two adapters that implement the normative renderer, legacy adapter, profile enumerator, journal, and repair state machine. POSIX durability cases must use real temporary filesystem operations, and Windows cases must run on native Windows CI. Result receipts should distinguish `design-fixture-check` from `implementation-conformance` until then.

## Medium Findings

### Medium - Journal manifest does not identify the staged and before-image bytes it promises

Protocol says the prepared manifest fixes staged bytes and before images (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:54-58`), but `mutationTarget` carries only target path and before/after hashes, and `transactionJournalManifestV1` adds no blob/path locator for either image (`contracts/panel-sync-contracts.schema.json:633-642,720-735`). No deterministic per-journal derivation is specified. A recovery implementation that did not create the journal cannot locate the bytes it must roll forward/back, and target ordering is described as sorted without a normative sort key.

Pin per-target staged/before blob locations or content IDs, the absent-target representation, and exact target ordering; exercise cross-process recovery rather than a pre-labeled state table.

### Medium - Risk writer ownership differs across the plan, protocol, and current code

The optimization plan says risk review has exact mappings for `Dependencies` and `Cross-Workstream Links` (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:204-209`). The protocol permits its Project Status `dependencies` patch but reserves all non-Roadmap owned sections, including Cross-Workstream Links, to checkpoint/register (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:19-34`). Current risk relation apply only mutates Blockers, Risks, Scope/change notes, or a decision row (`skills/adp-risk-dependency-change-review/scripts/review_risk_dependency_change.py:48-53,792-904`).

Choose the target owner and state the migration explicitly. If risk review is to gain dependency/cross-link mutation, add its current-to-target command mapping and capability vectors; otherwise remove those claims from the plan and capability matrix.

## Verified Closures

- The three pinned brownfield source hashes match actual bytes: WDR template `ae36419b...ccf4`, meeting v1 grammar `99f7e526...6226`, status v1 grammar `ef1a00ae...3c4b`.
- Registry, schema, protocol, vectors, runner, and result hashes are internally consistent with the frozen package.
- Mutable current projections are no longer enumerated as same-round leaves; program-status history is bound to an immutable selected snapshot.
- Meeting-pack uses actual brownfield sources (`cadence.md`, archives/cursors/receipts, decisions, readiness) rather than a nonexistent mandatory policy file.
- Repair dry-run/apply/result/receipt semantics are now per-batch, and the two-batch retry vector is present.
- Runtime schemas, filesystem-safe ID tokens, first-create versus replace primitives, and POSIX/Windows target behavior are materially specified. The remaining problem is evidence strength and staged-image discoverability, not absence of a platform design.
- P0/P1 rollout gates explicitly keep strict publication at `migration-required` until fact writers, WDR sidecars, profiles, and publication are migrated. Current direct-write code is therefore acceptable as brownfield input, but the Highs above must be fixed before that gate can close.

## Pass Conditions

1. Resolve all five High findings in schema/protocol/registry/vectors and regenerate the entire raw-hash chain.
2. Add executable read-set equality tests for every canonical producer and change-one-source invalidation coverage.
3. Replace design-only receipts with schema-valid engine-backed Python and Node evidence, plus native Windows filesystem evidence.
4. Re-run the reality gate against one frozen hash set; only then mark the spine and optimization plan `final`.
