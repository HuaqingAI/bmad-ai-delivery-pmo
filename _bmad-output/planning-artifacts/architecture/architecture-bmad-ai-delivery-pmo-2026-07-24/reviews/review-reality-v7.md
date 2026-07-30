# ARCHITECTURE-SPINE Brownfield Reality Review v7

## Verdict

**FAIL。** v6 High 1 与 High 2 已按真实 brownfield 约束关闭，progress-v3/flow-state-v1 也已有可复验的 nested schema pin；但 v6 High 3 仍有一个 High blocker。当前 target Panel wire shape 不是现有 Management Panel 1.0 消费模型的无损、可消费升级，71/71 design fixture 因使用人工简化 payload 而没有暴露该不兼容。

## Frozen Review Target

写入本评审前复验以下 raw-byte hashes未变化：

| Artifact | SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `9a36f98d377a2d4cdc6b1748cb220148b6a675f62e6941236fcede1dcf740e70` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `518277988c606fca82664f3bca70ea33b84f6137b6580554b449e188165be769` |
| `contracts/CONTRACT-REGISTRY.json` | `fe4ce0bc88ce9bc1da4a213e54ea0521726f09a71c23f4aa31e14b4748363c5a` |
| `contracts/panel-sync-contracts.schema.json` | `db06ba082306fdac6c739a71e6e13acf60567737fb3c15a9474d744f2d33164c` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `071a5ced3da7825875a4d13054775a2606a9bf67afc77f566f3bc7c13aab1afb` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `da259f8a5c8988bcb8eb89d72f6c7f3ee9db998b0826f1c9dc09734bf5b6c983` |
| `contracts/conformance/python-result.json` | `b89b71ef1125f7be34d5074ad19a7c9051050e6d966e6097bc7ff74c6bdd6cf2` |
| `contracts/conformance/node-result.json` | `278e7641159be49f08e3205dd2995aceb9050b1e77f4c0962a68c689097bf66e` |

## High Finding

### High 1 - Target Panel payload cannot losslessly feed the deployed Panel information architecture

The deployed Panel contract requires `data.status`, `data.roadmap`, three scenario-specific `data.flows`, two meetings and `data.history`; its three view IDs are `project-lead`, `fde-morning` and `business-biweekly` (`skills/adp-management-panel/assets/adp-management-panel-v1.schema.json:71-98,135-149`). The composer builds those exact views and binds concrete fields such as progress series, source fingerprints, keyed meeting boards and per-scenario flow selections (`skills/adp-management-panel/scripts/panel_model.py:44-70,565-600,604-666,874-908`). Representative brownfield inputs confirm that meeting `boards` is a heterogeneous object keyed by `fde_period_delta`, `fde_blockers`, `business_decisions`, and related board names, while status, roadmap and meeting payloads carry reporting period, baseline/audit identities, selection IDs, source fingerprints and exception collections (`skills/adp-management-panel/assets/fixtures/panel-contract-v1/panel-source-fixture.json:1-99`).

The target schema instead requires a singular `data.flow`, omits `data.history`, and replaces the three deployed views with `executive`, `workstreams`, and `actions` carrying only label/count (`contracts/panel-sync-contracts.schema.json:687-744`). `meetingPackPayloadV2` turns keyed boards into an array of generic items and rejects all undeclared producer fields; `programStatusPayloadV2` and `roadmapPayloadV2` likewise omit fields the current composer reads (`contracts/panel-sync-contracts.schema.json:600-684,1338-1392`). The unconstrained `extensions` member does not close this gap because the protocol only permits consumers to rely on schema-declared fields, and no extension field bindings are registered.

The design evidence does not test a brownfield transform. `panel-binding-catalog-to-schema` composes hand-built reduced payloads, and the nested vectors only validate a standalone progress fixture / flow-state instance plus canonical JSON round-trip (`contracts/fixtures/CONFORMANCE-VECTORS.json:91-166,411-416`; `contracts/conformance/python_runner.py:266-292,456-474`; `contracts/conformance/node_runner.mjs:165-189,361-378`). It never transforms the representative Panel source fixture and compares every Panel-consumed pointer. Therefore a production adapter can satisfy the frozen outer/nested schemas while dropping history, scenario flow selections, existing view sections, meeting-board columns, audit lineage, or roadmap exceptions.

**Impact:** implementing the frozen contract either breaks the current Panel renderer or silently removes management information while still allowing a schema-valid producer receipt and atomic publication. This directly undermines the requested current-field/freshness fix: fresh WDR values may exist in `workstream_current`, yet the deployed views have no pinned lossless route to render them.

**Required fix:** make `managementPanelPayloadV2` an additive, lossless evolution of the pinned Panel 1.0 model, or explicitly version and migrate the renderer in the same architecture. Preserve the current three view IDs/sections, scenario flow selections, history, keyed board semantics, and every status/roadmap/meeting identity or lineage field the renderer reads. Add a compatibility vector that transforms the representative brownfield producer/Panel fixture, validates the target outer and nested schemas, and checks equality at every existing consumer pointer plus the new `workstream_current` pointers. Add negative vectors proving omission of those required consumer fields cannot produce a receipt.

## Verified v6 Closures

- **Legacy `wdr_update`: closed.** Protocol and AD-9 now make v1 free text history/evidence-only, require typed status input for current mutation, and return `LEGACY_STATUS_INTENT_REQUIRED`; the free-text vector produces zero current mutations (`WDR-AND-TRANSACTION-PROTOCOL.md:15`; `ARCHITECTURE-SPINE.md:127-131`; `CONFORMANCE-VECTORS.json:402-404`).
- **Readiness derived leaves: closed.** Registry profiles read selected WDR/evidence/decision/workstream readiness and same-generation audit; neither `views/acceptance-readiness.md` nor `views/cutover-readiness.md` is a leaf. The target protocol explicitly forbids both paths (`WDR-AND-TRANSACTION-PROTOCOL.md:55-57`; `CONTRACT-REGISTRY.json:535-693`). Existing roadmap/meeting-pack code still reads the old views, but replacing those reads is correctly treated as implementation work before strict publication, not a new architecture blocker.
- **Nested progress/flow binding: closed in isolation.** Registry pins complete brownfield progress-v3 and flow-state-v1 schemas, both runners verify their raw hashes/IDs and parent pointers at startup, and the full progress fixture reaches the composed Panel fixture (`CONTRACT-REGISTRY.json:531-534`; `python_runner.py:582-611`; `node_runner.mjs:209-233`). The remaining High is the surrounding consumer payload, not these two nested objects.
- The other five requested loops are implementable in the target state: exact-ID action create/patch, typed status intents through status-sync, live leaf fingerprint inspection, complete ledger/WDR drift coverage, and per-batch exact-ID repair with CAS/single-use tokens.

## Reverified Evidence

- Brownfield regression scope rerun: **199/199 passed** - meeting-sync 25, status-sync 29, state-audit 63, management-panel 28, panel-audit 12, state-prepass 10, panel-model 6, panel-contract 26.
- Python reference runner rerun: **71/71 passed**, 0 failed; temporary result raw hash exactly matches checked-in `python-result.json`.
- Node reference runner rerun: **71/71 passed**, 0 failed; temporary result raw hash exactly matches checked-in `node-result.json`.
- Both receipts correctly remain `evidence_kind: design-fixture-check` and `native_durability_exercised: false`. Production adapters, real POSIX fault injection and native Windows CI remain release prerequisites, not this gate's High finding.

## Pass Condition

Close the Panel 1.0-to-2.0 lossless consumer mapping above, regenerate the raw-hash chain, rerun 199 regressions and both complete design suites, then run the independent gate again on one frozen target.
