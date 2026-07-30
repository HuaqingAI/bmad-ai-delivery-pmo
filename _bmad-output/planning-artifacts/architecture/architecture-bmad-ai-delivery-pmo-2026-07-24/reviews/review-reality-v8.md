# ARCHITECTURE-SPINE Brownfield Reality / Currentness Review v8

## Verdict

**PASS.** 未发现 Critical 或 High finding。冻结目标准确描述了当前 brownfield 的五个断点，并把修复限定为尚待实施的 target architecture；registry 仍为 `implementation_conformance_status: pending`，两份 reference receipt 也明确是 `design-fixture-check` 且 `native_durability_exercised=false`，没有把设计 fixture 冒充 production release evidence。v7 的 Panel 1.0 -> 2.0 兼容 blocker 已关闭：v2 是加法 wrapper，完整 v1 model/manifest 由真实 composer fixture 固定并通过 nested bindings 校验，所有 7 个 outer bindings、4 个 nested bindings及新 current-field path 都进入 conformance gate。

## Frozen Review Target

本评审写入前复验以下 raw-byte hashes：

| Artifact | SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `f9ebcf3aabc2ecf3b67d736585b4188d3199fa2a3419c96dba3332b8106c830a` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `1bad52f7bfb28754c71e888928f01367a105cdfb0771d0919bc27071a2976818` |
| `contracts/CONTRACT-REGISTRY.json` | `222e7bc0b01f86ff6396ef630452170b28073c6c6f9bf8ee0da9909ab88c0e50` |
| `contracts/panel-sync-contracts.schema.json` | `d11b05146d1a8f88a5209c9e93591032d0453083f4ba6923ac3d3fe63b9c37a6` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `0545c52d42aa7e58d714457b6054b53994e7f76ae665f50b71454141e7b722b2` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `4ccfe6388bbbdcffac6250c90b99924a6b28d36fe598a31acf829cbc4c243a30` |
| `contracts/fixtures/PANEL-V1-COMPATIBILITY.json` | `74b4787a48955862622e0a5616a539cef73b44b15d703c7fa4febfaee49dfbb9` |
| `contracts/conformance/python_runner.py` | `906c155562306f8d3c228ac1339040c6e91baa40a66ecf0e79771f02975f87c8` |
| `contracts/conformance/node_runner.mjs` | `83e74c4adf3b958f6a1f12f1e9b90977db0eb2128a5ca202090572fd064f31a5` |
| `contracts/conformance/python-result.json` | `4757356132ce20b2cb4061aa18e015d74e85389ec592b513c2ab66fee6f41958` |
| `contracts/conformance/node-result.json` | `20b4a2294ff407d6c9d21bf10ddc26e5cf154f1bd0f98f04d3b21dadaf1486c7` |

## Critical Findings

None.

## High Findings

None.

## Brownfield Reality Check

### 1. Existing-action mutation

The diagnosis matches the deployed boundary. Meeting v1 normalizes action text/owner/status but has no action identity or operation (`skills/adp-meeting-sync/scripts/sync_meeting.py:274-304`), and its status-sync handoff emits create-shaped action payloads without `action_id` (`:1371-1403`). The downstream status writer can locate an exact ID (`skills/adp-status-sync/scripts/sync_status.py:840-846`), but its current `ActionUpdate.status="open"` default and unconditional status assignment make owner-only reuse unsafe (`:102-123`, `:907-920`). AD-2 therefore fixes a real seam with exact-ID `create|patch`, expected revision and presence-preserving `set`, rather than inventing a replacement ledger model (`ARCHITECTURE-SPINE.md:85-89`; `ANALYSIS-AND-OPTIMIZATION-PLAN.md:73-79,130-168`).

### 2. WDR current fields

The current meeting writer appends a `Meeting Sync Update` block (`skills/adp-meeting-sync/scripts/sync_meeting.py:812-821,1244-1270`); the current-field writer lives in status-sync (`skills/adp-status-sync/scripts/sync_status.py:1458-1474,1523-1545`). The design correctly refuses to infer field/mode from legacy free text, preserves it as history/evidence, and requires an additive typed status payload before routing an intent through status-sync (`WDR-AND-TRANSACTION-PROTOCOL.md:15,19`; `ARCHITECTURE-SPINE.md:91-95,127-131`). This closes the reported gap without granting meeting-sync conflicting semantic ownership.

### 3. Live freshness

Current `inspect_current()` validates embedded/bundle/resource integrity but does not reload WDR or ledger (`skills/adp-management-panel/scripts/management_panel.py:1120-1173`). Current input audit checks lineage hash shape and age, while same-run source sealing is only available when source inputs are present (`skills/adp-state-audit/scripts/panel_audit.py:344-395,626-690`). AD-4/6/8 replace that weak signal with registry-derived live leaves, a fact-generation fence, final compare and separate integrity/freshness/publication verdicts (`ARCHITECTURE-SPINE.md:97-101,109-125`). The static `file://` limitation is stated explicitly rather than hidden (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:460-469`).

### 4. Ledger/WDR drift

The existing prepass computes missing-ID sets, but only when active ledger actions exist (`skills/adp-agent-program-lead/scripts/adp-state-prepass.py:911-955,1154`); state-audit converts those IDs into disagreements (`skills/adp-state-audit/scripts/audit_state.py:2282-2303`). AD-5 closes empty-set, content, coverage and selected-scope false-green cases with a durable status-sync-owned sidecar and a schema/semantic-gated drift verdict (`ARCHITECTURE-SPINE.md:103-107`). The profile reads the ledger, exact selected WDRs, WDR state and action sidecars; it is not relying on a projection's self-report (`contracts/CONTRACT-REGISTRY.json:569-628`).

### 5. Exact repair IDs

Current raw disagreements carry `action_id`, and that ID contributes to finding identity, but `canonical_finding()` does not copy it into the public finding (`skills/adp-state-audit/scripts/audit_state.py:2282-2303,2951-3014`). AD-7 and the v2 repair schemas preserve exact IDs and bind finding/batch/read-set/WDR revision/token/receipt identities bidirectionally (`ARCHITECTURE-SPINE.md:115-119`). Negative vectors cover dangling/reversed graph links, action union mismatch, duplicate source/WDR reads, revision mismatch, cross-batch token and binding mismatch; both runners recompute those identities rather than trusting labels.

## Panel v1 -> v2 Compatibility and Binding Closure

The v7 High is closed in the frozen target:

- The deployed v1 schema still requires status, roadmap, three scenario flows, two meeting packs, history, exactly three view IDs, selection/catalog/recovery and manifest (`skills/adp-management-panel/assets/adp-management-panel-v1.schema.json:7-22,42-99,135-174`).
- The target v2 does not replace that model. It requires `model_v1` alongside `sync`; canonical status/roadmap/flow/meeting payloads live under `sync.canonical`, and current fields have the single path `/sync/canonical/status/workstream_current` (`contracts/panel-sync-contracts.schema.json:728-763`; `ARCHITECTURE-SPINE.md:97-101`).
- Registry closure is complete: 7 projection profiles correspond exactly to 7 outer schema bindings and envelope kinds. Four nested bindings pin full brownfield progress-v3, flow-state-v1, Panel model v1 and Panel manifest v1 schemas (`contracts/CONTRACT-REGISTRY.json:534-547`).
- `PANEL-V1-COMPATIBILITY.json` is not a hand-written reduced model. Its generator imports the production `panel_model`, loads the representative source fixture, calls `compose_panel()`, resolves every deployed view binding and records target hashes (`contracts/conformance/generate_panel_compat_fixture.py:20-67`). Independent recomputation produced exact model and source-fixture equality, 19 consumer-binding checks, all three view IDs, history, and every keyed FDE/business board.
- Both harnesses validate the v1 model and manifest against the pinned schemas, check required views/data/flows/meetings/boards and consumer target hashes, and reject omitted history/required boards (`contracts/conformance/python_runner.py:630-656,951-1017`; `contracts/conformance/node_runner.mjs:390-402,478-488,661-700`). The integrated valid case also requires the new workstream current fields and full outer lineage. Therefore the prior reduced-payload false positive is no longer available.

## Technology and Currentness

- Python `>=3.10` ratifies the module's declared runtime and CI matrix; it is not a speculative platform change (`skills/adp-management-panel/scripts/management_panel.py:3`; `.github/workflows/adp-management-panel.yml:29-41`; `.github/workflows/adp-meeting-sync.yml:17-29`).
- JSON Schema Draft 2020-12 is the dialect already used by the pinned Panel, flow and progress schemas. The target bundle uses the same published dialect.
- RFC 8785 JCS, RFC 6901 pointers, SHA-256 and SemVer are stable standards/contracts. The architecture narrows JCS input to schema integers and adds explicit array ordering because RFC 8785 does not sort arrays (`WDR-AND-TRANSACTION-PROTOCOL.md:7-9`).
- POSIX/Windows APIs are target adapter contracts, not claims about existing durability. Native POSIX fault injection and native Windows CI remain mandatory future evidence (`WDR-AND-TRANSACTION-PROTOCOL.md:75,87`).

No unsupported framework/runtime migration or unverified third-party library version is bound by the spine.

## Verification Evidence

- Architecture lint via direct Python fallback: **0 findings** (`uv` was unavailable).
- Brownfield regression scope: **199/199 passed**: meeting-sync 25, status-sync 29, state-audit 63, management-panel 28, panel-audit 12, state-prepass 10, panel-model 6, Panel contract 26.
- Python reference harness: **132/132 passed**, 0 failed.
- Node reference harness: **132/132 passed**, 0 failed.
- Checked-in result receipts bind the current registry/schema/protocol/suite hashes, but both remain `evidence_kind: design-fixture-check`, design-model platforms, and `native_durability_exercised: false` (`contracts/CONTRACT-REGISTRY.json:20-50`). They are design evidence only.

## Residual Delivery Boundary

This is an architecture PASS, not an implementation release. The production `skills/adp-*` code still exhibits the five diagnosed limitations. Strict publication must remain disabled until the phased implementation is complete and the registry release gate accepts two distinct production implementations/builds plus real POSIX fault injection and native Windows CI (`ARCHITECTURE-SPINE.md:139-143,169-176`; `ANALYSIS-AND-OPTIMIZATION-PLAN.md:396-447,507`). The pinned v2 consumer rule also requires future Panel code to read current fields only from `sync.canonical.status.workstream_current`; merely wrapping an unchanged v1 HTML/model would not satisfy AD-4 or production conformance.
