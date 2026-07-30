# Architecture Spine Rubric Review v5

## Gate Verdict

**FAIL。** v4 的三个 High 已关闭：refresh leaf/output 已分离，repair 已收敛为 per-batch token，meeting-pack profile 已改绑现有 brownfield cadence/meeting/decision/readiness sources。当前冻结包的 raw hashes、registry pins、schema pointers、lint 和两份 32/32 result receipts 都可复现，但 good-spine gate 仍有 **3 个 High**：会议产生的 WDR current-field command 会被自己的 capability matrix 拒绝；pinned conformance runners 没有验证它们声称覆盖的 registry/schema/runtime semantics；refresh receipt/status wire 无法表示 spine 规定的 planned/blocked/pending/inspect 状态。

另有 **2 个 Medium**：repair read set 允许同一实体携带冲突 revision/fingerprint；fresh WDR 的缺失 label 插入规则仍未固定。评审未修改 spine 或任何 normative contract。

## Pin And Evidence Verification

| Artifact | Expected prefix | Actual SHA-256 | Result |
| --- | --- | --- | --- |
| `CONTRACT-REGISTRY.json` | `7b6403d9` | `7b6403d9c9e8734e32556dc3555de5c8fb43f4411a7e379b6cb31d2fc5861d9e` | PASS |
| `panel-sync-contracts.schema.json` | `3b11b8c8` | `3b11b8c86fcb5b7272dd86576afdce10c50745489e8224e78acc89ff1e430bf8` | PASS |
| `WDR-AND-TRANSACTION-PROTOCOL.md` | `b86bba20` | `b86bba208688eeeb5c70b04202437c23ecfced8e3143f6a4bf95dcbc5623c434` | PASS |
| `CONFORMANCE-VECTORS.json` | `fd2f6bfb` | `fd2f6bfbdcc4710851fc22211cfcca000e40391bc103f46ef3978ea53b8fe4a9` | PASS |

- Architecture lint: **PASS, 0 findings**.
- Registry/schema: all 29 `schema_pointer` targets resolve.
- Pinned runner/result hashes match the registry.
- Fresh rerun: Python/POSIX **32 passed, 0 failed**; Node/Windows-model **32 passed, 0 failed**; regenerated result bytes match the pinned result files exactly.
- The semantic strength of those 32 checks is addressed in H2; reproducibility is not the same as adequate conformance coverage.

## High Findings

### H1 - The proposed meeting WDR current-field path is forbidden by the pinned writer capability matrix

**Evidence:** The optimization plan's normative example declares `issuer.producer_id: adp-meeting-sync` and sets `status`, `progress`, `blockers`, `risks`, and `refresh_actions` (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:180-196`). It then says meeting-sync sends that WDR command directly to the shared engine (`:200`).

The protocol requires the host capability, protected registry, serialized issuer, operation, field, and section to match (`WDR-AND-TRANSACTION-PROTOCOL.md:17`). Its field matrix authorizes `status`/`progress` only to status-sync and checkpoint-sync, `blockers`/`risks` only to status-sync, checkpoint-sync, and risk review, and `refresh_actions` only to status-sync (`:19-30`). Meeting-sync owns only `meeting_history_append`. AD-1 makes this matrix mandatory and AD-3 binds meeting-sync `wdr_update` to it (`ARCHITECTURE-SPINE.md:76-92`).

Therefore a conforming engine must reject the documented core command with `WDR_WRITER_UNAUTHORIZED` or a field-ownership error. The original requirement, "meeting `wdr_update` updates the Panel's current fields," remains non-executable despite the typed payload.

**Disposition: Fix.** Keep status-sync as the current-field semantic owner: meeting-sync should emit a typed status-sync intent/evidence envelope, and status-sync should validate it and issue the WDR command under its own host capability. Meeting-sync may issue a separate history command under its capability. Update the example, producer/reader declarations, capability registry bootstrap, and add paired vectors proving (a) the routed status-sync command succeeds and (b) a direct meeting-sync current-field command is rejected. Alternatively granting meeting-sync current-field capability would change the ownership decision and requires an explicit conflict/precedence rule; it should not happen implicitly.

### H2 - The pinned 32/32 receipts do not test the registry/schema contracts they are used to gate

**Evidence:** The suite defines `$SCHEMA_SHA256` and `$REGISTRY_SHA256` substitutions (`CONFORMANCE-VECTORS.json:10-13`), and positive fixtures contain those literal tokens in `contract` (`:27`, `:108`). Those literals are not valid `sha256:` values under `contractRef`. Neither runner accepts a registry path, performs substitution, resolves registry profiles, or validates fixture/result objects against the schema. The Python runner only hashes the schema/protocol at result construction (`python_runner.py:160-184`); the Node runner does the same (`node_runner.mjs:149-162`).

Several checks are assertions about fixture labels rather than adapter behavior:

- `refresh-output-not-a-leaf` intersects two arrays supplied by the vector, never the registry's live `projection_input_profiles` (`python_runner.py:84-97`, `node_runner.mjs:77-90`). A registry regression can pass unchanged.
- The two-batch repair check verifies token uniqueness and the final committed list, but not dry-run/apply ordering, nonce invalidation, failed-batch stop, or fresh retry binding (`python_runner.py:129-145`, `node_runner.mjs:126-140`).
- Platform vectors pass when `expected_error` is one of two known strings; no path resolver or durability adapter is exercised (`python_runner.py:147-148`, `node_runner.mjs:144-145`).

The rerun genuinely reproduces 32/32, but it proves only that both small runners accept the fixture's self-described outcomes. It does not justify AD-11's contract-change gate or the suite's `evidence_status: passed`. A production adapter that does not implement profile derivation, schema negotiation, repair state transitions, or filesystem rejection could still satisfy the same checks.

**Disposition: Fix.** Make the harness load the pinned registry and raw bytes, verify every hash/pointer, substitute tokens, and schema-validate every positive and negative envelope. Derive leaf/output sets from the actual registry. Execute WDR, journal/recovery, repair, and path vectors against isolated adapter state, perturb inputs for negative cases, and assert observed outputs/errors rather than an `expected_error` label. The result receipt should identify the registry under test without introducing a hash cycle (for example, bind a separately hashed release manifest). Do not mark release evidence passed until two implementations pass that executable suite.

### H3 - Refresh runtime wire cannot encode the planned, blocked, dirty, and live-inspect states required by the spine

**Evidence:** AD-8 requires mutable `views/management-panel/refresh-status.json` to carry `last_successful_refresh`, `pending_invalidations`, and latest inspect (`ARCHITECTURE-SPINE.md:118-122`). The plan requires dry-run node states `reused|refresh|blocked`, a dirty receipt that lists completed/pending/blocked work, and the same mutable fields (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:325-350`, `:451-460`).

The only registered wire is `refreshReceiptStatusV1`. It permits top-level `planned|refreshing|dirty|published|blocked`, but `nodes` are plain `dependencyUpstream` records, which only carry an already-existing output ID, manifest ID, and generation ID (`panel-sync-contracts.schema.json:872-888`, `:379-390`). There is no node status, invalidation reason, pending/blocked classification, retry cursor, or live-inspect verdict. Planned or blocked nodes often have no output identity at all.

The neighboring `producerReceiptV1` has the same contradiction: `status` permits `blocked`, while `output` is always required and non-null (`panel-sync-contracts.schema.json:614-630`). A conforming producer must invent output/manifest IDs, omit the blocked node, or write an ad hoc extension that `additionalProperties: false` rejects. None can satisfy the stated retry and UX contract.

**Disposition: Fix.** Separate immutable run receipt from mutable status state, or define a discriminated union:

- `producerReceipt`: produced/reused requires output; blocked forbids output and requires a typed error.
- immutable refresh run receipt: per-node instance key, disposition, reason, consumed inputs, optional output, completed/pending/blocked sets, and retry cursor.
- mutable refresh status: last successful generation/time, pending invalidations, latest live-inspect result/time, and current dirty/blocked run reference.

Register all wires with exact ownership/identity rules and add vectors for planned-before-output, blocked producer, dirty retry, successful publication, and subsequent live inspect.

## Medium Findings

### M1 - Repair read-set identity multiplicity is ambiguous

`repairCommand.action_ids` is unique, but `repairReadSet.action_revisions`, `wdr_revisions`, and `source_records` have no identity uniqueness constraint (`panel-sync-contracts.schema.json:452-503`). Protocol section 6 requires set equality and exactly one WDR record, but does not reject two entries for the same action with different revisions or two records for the same `(root_instance_id,path)` with different fingerprints (`WDR-AND-TRANSACTION-PROTOCOL.md:62-65`). First-wins, last-wins, require-all, and reject are all schema-compatible interpretations of a destructive repair CAS.

**Disposition: Fix.** Normatively require exactly one action-revision entry per command action ID, exactly one WDR entry, and one source record per root/path; conflicting or duplicate identities return `REPAIR_BATCH_INVALID`. Add duplicate-same and duplicate-conflicting negative vectors.

### M2 - A first patch against the pinned fresh WDR has an undefined missing-label behavior

The pinned create template and `create-byte-exact` vector do not contain `Last status sync`, while the protocol maps that field and says scalar mutation replaces the target label line (`WDR-AND-TRANSACTION-PROTOCOL.md:15-16`, `:19-35`; pinned template lines 35-42). The brownfield status writer inserts a missing label, but the new shared-engine protocol does not fix insertion position/bytes or an error. Two engines may reject the first status update or insert the label in different positions while both follow the current wording.

**Disposition: Fix.** Pin missing-label insertion semantics and exact order within `Project Status`, or include the label in the pinned create template and migration grammar. Add a whole-file vector that creates a WDR and immediately applies the first current-field/status-sync patch.

## v4 Finding Closure

| v4 finding | v5 result | Evidence |
| --- | --- | --- |
| Mutable projection self-invalidation | **Closed** | Profiles no longer enumerate canonical refresh outputs; previous program status resolves through immutable content ID. Registry lines 380-491 and Protocol 4 explicitly separate leaves, staged outputs, current views, lineage, and runtime. `action-flow`/`risk-flow` are deliberately fact-generation-bound indexes. |
| Multi-batch/single-token mismatch | **Closed** | Dry-run/apply/result/receipt are one batch each; protocol fixes sorted client iteration and retry. The schema now has singular `batch`/`batch_id`, and a two-batch retry vector exists. M1 is a narrower read-set multiplicity issue, not a reopening of the batch model. |
| Nonexistent meeting policy source | **Closed** | Meeting-pack profile now binds meeting archives/cursors/receipts, ledger, decisions, readiness, `memory/cadence.md`, and the locale catalog. Kickoff bootstraps `cadence.md`; there is no mandatory nonexistent `*policy*.json` glob. |

## Good-Spine Checklist

| Checklist | Result | Notes |
| --- | --- | --- |
| Fixes real divergence points for the level below | **Partial** | The five original gaps map to explicit ADs, but H1 makes the meeting-to-current-state path fail closed and H3 leaves refresh retry/status consumers without a shared wire. |
| Every AD enforceable and prevents stated divergence | **Fail** | AD-1/3 conflict on the core meeting path; AD-6/8 cannot be represented by the registered schema; AD-11's evidence gate does not execute the pinned contract. |
| Nothing in Deferred leaks required decisions | **Pass** | Action Center, watcher/queue, DB migration, fuzzy matching, and offline live verification are safely outside this explicit-refresh slice. |
| Named technology/current versions | **Pass** | Draft 2020-12, RFC 8785, Python >=3.10, POSIX/Windows primitives, and raw artifact versions are explicit; no unverified vendor service is bound. |
| Ratifies brownfield | **Partial** | Existing single-writer and WDR structure are largely preserved. H1 contradicts the proposed example and H3 does not cover the stated brownfield open/inspect UX; M2 misses an existing insertion behavior. |
| Covers source capabilities | **Partial** | Exact-ID action patch, live freshness, projection drift, immutable publication, and per-batch repair are represented. Direct meeting WDR mutation and deterministic refresh recovery/status remain incomplete. |
| Parent spine inheritance | **N/A** | No parent spine is declared. |
| Operational/environmental envelope | **Partial** | Root identities, generations, locks, journal recovery, path safety, and POSIX/Windows durability are strong. The run/status persistence layer is not wire-closed (H3), and the claimed adapter evidence does not exercise those operations (H2). |

## Positive Findings

- All four requested raw hashes exactly match the frozen package and both main documents.
- Registry profile/output separation now prevents the v4 self-invalidation failure; canonical projection publication is explicitly journaled with the Panel pointer/state.
- Per-batch repair ownership, token lifetime, commit/rollback nonce states, and second-batch retry are materially clearer than v4.
- Meeting-pack no longer depends on a nonexistent policy file and is tied to actual ADP memory surfaces.
- Exact action IDs, WDR revisions/generations, same-generation drift verdict, immutable snapshots, filesystem-safe IDs, and crash recovery are all real divergence controls rather than generic guidance.
- Deferred is disciplined and the architecture does not expand into a database, daemon, or new Panel information architecture.

## Gate Exit

Resolve H1-H3, then update all dependent raw hashes and regenerate evidence. The next frozen suite must prove the authorized meeting-to-status-to-WDR route, validate profiles directly from the registry, and represent blocked/planned/dirty/live-inspect refresh states without fabricated output IDs. Add the M1/M2 negative and first-patch vectors before rerunning lint, schema/pointer checks, both independent adapters, and the full reviewer gate.
