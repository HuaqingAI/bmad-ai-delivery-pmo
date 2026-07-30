# ARCHITECTURE-SPINE Brownfield Reality / Currentness Review v10

## Verdict

**FAIL. Critical: 0. High: 2.** v10 materially closes v9's catalog-scope, fact-receipt byte binding, and journal-image locality gaps, and both reference harnesses reproducibly pass all 210 declared design vectors. Two false-green boundaries remain. First, the contract declares that current workstream UI values come from the v2 `sync` surface, but no executed renderer or consumer reads that surface; the pinned production Panel still reads only the v1 model. Second, the registered general fact-attribution validator accepts only `actionCommandV2`, so ordinary WDR current-field and meeting-history transactions cannot be validated under the protocol's claimed universal coordinator semantics. These are contract/evidence gaps, separate from the correctly deferred production implementation.

## Frozen Review Target

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `afe920412c07e2e86c63b2d3eafe2616a82cca2291cf5ac0df7cdc06f6a5d67f` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `bc259b4502caf956b40b3ff34111108bc79df7b693df1878f087c81945906edc` |
| `contracts/CONTRACT-REGISTRY.json` | `44933af3193aadbd507e5291c49fe298a13fb93cfbc889b6a81b5710bc207e61` |
| `contracts/panel-sync-contracts.schema.json` | `09fdae139aa006176fa303d19fb63e16214e1bac94f18c766d21d7397c2814be` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `9a6e2ceeee30cae36941a2eeb4bb9c00b86c4863debe67a16eab0513cc3abd27` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `bb2727c73c07c2f10934a4ee35c56697beea120440ad26cf85e64a059c659b75` |
| `contracts/fixtures/PANEL-V1-COMPATIBILITY.json` | `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` |
| `contracts/conformance/python_runner.py` | `99e2cdc63677982199cf87cb54f184f6a1dcf483322204b16c6915d42fad479f` |
| `contracts/conformance/node_runner.mjs` | `9ffbbbd862427e2e916f065158c222092e46de2ddf8bb7e7b3a9abf33ec39c9f` |
| `contracts/conformance/python-result.json` | `badac9e15c44c0a852e20f086384b387860705833028ad6eb6526b56394abb4c` |
| `contracts/conformance/node-result.json` | `8606efe2e1aae6dfb02ae8ea865ce55e7fcb4ab4cbb2b79afded7f4e776c51f6` |

The normative package did not change during this review. This reviewer created only this review file.

## Critical Findings

None.

## High Findings

### H1 - The claimed v2 current-field UI path is not an executable consumer contract

The registry now makes a clean semantic distinction: v1 is `aggregate-only`, while current workstream UI data is declared at `/sync/canonical/status/workstream_current` (`CONTRACT-REGISTRY.json:563-575`). The same-generation binding checks also prove that the v2 current rows equal the current Program Status payload. That closes v9's ambiguity about which payload is authoritative.

The registered composition validator does not, however, execute any consumer of the declared pointer. `panel_v1_compatibility_valid()` merely checks that current rows contain `workstream_id`, `progress`, `blockers`, and `risks`; `panel_v1_composition_valid()` recomposes only `model_v1` (`python_runner.py:1566-1624`). The positive vector `panel-v1-compose-current-fields-independent` deliberately changes v2 progress/blockers/risks, leaves `model_v1` byte-identical, and passes (`python_runner.py:2335-2350`; `CONFORMANCE-VECTORS.json:526`). That is valid only if an independently verified renderer consumes the v2 surface.

No such renderer is present in the pinned current project. `panel-template.html` embeds only `adp-panel-model`, manifest, and previews (`panel-template.html:45-47`). `panel.js` parses `adp-panel-model` and renders current status from `model.data.status`, including filters, header, FDE metrics, and business metrics (`panel.js:4-10,127,203-221,809-840`). It contains no `sync` or `workstream_current` consumer. Consequently, all 210 vectors can pass while the actually displayed Progress, Blockers, and Risks remain v1/stale.

**Required fix:** define, pin, and execute a v2 Panel renderer/consumer contract. The fixture must embed the complete `managementPanelPayloadV2` (or a separately hashed v2 sync payload), render workstream Progress/Blockers/Risks from the registry pointer, and assert observable output changes when only those fields change. Add a negative vector for a renderer that continues to read `model_v1`, and make this consumer test part of release acceptance. Until then, keep strict current publication disabled and describe the v2 renderer as unimplemented target state, not validated behavior.

### H2 - General fact attribution cannot validate ordinary WDR commands

The protocol requires every fact transaction to bind one typed command and derive authorization, targets, generations, receipt identity, and deltas from it; it explicitly says WDR-only `refresh_actions` has `action_deltas=[]` (`WDR-AND-TRANSACTION-PROTOCOL.md:51`). The plan repeats that every fact transaction is subject to this rule and that meeting history plus all current-field targets must use the common journal/generation boundary (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:423-426`).

The registered validator is narrower. `fact-receipt-attribution/1.0.0` scopes only `action-command/2.0.0`, not `wdr-command/1.0.0` (`CONTRACT-REGISTRY.json:491-499`). Its implementation validates `command` as `actionCommandV2`, reads `command["set"]|["create"]`, and requires exactly one derived action delta (`python_runner.py:666-732`). The WDR vectors validate rendering in isolation (`python_runner.py:1957`), not a complete WDR command -> authorization -> journal -> fact state -> receipt graph.

The repair-specific validator does correctly cover committed `refresh_actions` with empty action deltas (`python_runner.py:1513-1554`; registry `repair-graph-semantics/1.0.0`). That does not cover ordinary status-sync WDR commands for Progress/Blockers/Risks or meeting-sync history commands. An implementation can therefore bypass capability scope, target attribution, or exact receipt effects for those fact writes and still pass the declared 210-vector suite. This leaves the mutation path central to the reported `wdr_update` problem outside the executable coordinator contract.

**Required fix:** make fact attribution discriminate the registered typed command kinds, at minimum `actionCommandV2` and `wdrCommandV1`. For each WDR operation, derive and verify exact allowed fields/sections, business target paths and before/after hashes, fact-generation transition, command fingerprint, and `action_deltas=[]`; retain the repair graph as an additional repair workflow constraint rather than the only executable WDR receipt path. Add complete positive and substitution/unauthorized-target negatives for status current fields, meeting history, and non-repair `refresh_actions`.

## v9 Finding Closure

- v9 H2 is closed: selection is catalog-first, `all` resolves from the independently hashed nonempty workstream catalog, and full/subset mismatch vectors execute.
- v9 H3 is closed: the fact graph binds the exact registry receipt path and journal target hash to the supplied receipt bytes and before/after fact state.
- v9 H4 is closed: journal directory and image paths are exact, with foreign-journal and parent-alias negatives.
- v9 H1 is partially closed: canonical current rows are same-generation and deliberately separated from aggregate v1 semantics, but H1 above remains until the declared v2 consumer is executable.

## Brownfield Reality Check

The package accurately treats all five reported production limitations as target-state work, not completed deployment:

- Meeting v1 still carries create-shaped action details without a stable mutation operation/exact existing action ID handoff (`skills/adp-meeting-sync/scripts/sync_meeting.py:274-304,1371-1403`). Status-sync can find an exact ID, but its default `ActionUpdate.status="open"` and unconditional status assignment still make owner-only patching unsafe (`skills/adp-status-sync/scripts/sync_status.py:102-123,840-846,907-920`).
- Production `wdr_update` still appends a Meeting Sync Update/history block (`sync_meeting.py:1244-1270`); current Project Status fields remain a separate status-sync write path (`sync_status.py:1458-1474,1523-1545`).
- `inspect_current()` still verifies embedded artifacts and immutable bundle identity without re-reading live WDR/ledger leaves (`skills/adp-management-panel/scripts/management_panel.py:1120-1173`).
- Production still has no same-generation WDR/ledger projection-drift sidecar and strict publication gate.
- Raw audit disagreements carry `action_id`, but `canonical_finding()` still omits it from the public finding and uses it only inside identity details (`skills/adp-state-audit/scripts/audit_state.py:2282-2303,2951-3014`).

The proposed ownership, typed mutation, fact fence, drift verdict, repair batch, and publication graph remain directionally appropriate. The two High findings are the remaining design-proof gaps; the five production bullets are expected implementation work and are already guarded by `pending` conformance status.

## Verification Evidence

- Architecture lint: **PASS, 0 findings**.
- Draft 2020-12 schema compilation: **PASS** for the target bundle, Panel model v1, Panel manifest v1, flow-graph v1, progress v3, and flow-state v1.
- Registry closure: **PASS** for 40 contracts, 12 pins, 7 profiles, 7 outer bindings, 4 nested bindings, 15 DAG edges, 25 array-ordering rules, 8 identity-set rules, 8 runtime paths, and 8 semantic validators. Harness startup revalidated raw pins, anchors, payload bindings, and nested bindings.
- Compatibility regeneration: **PASS**, byte-for-byte identical through the pinned production `panel_model.compose_panel()` path.
- Python design runner: **210 passed / 0 failed**, byte-for-byte identical to the checked-in result; result ID `sha256:0ca5f6a945b201265b2edf2d0558a189a399b448306360ca09020988e1e37343`.
- Node design runner: **210 passed / 0 failed**, byte-for-byte identical to the checked-in result; result ID `sha256:67885cb6068189b731060fb23c1a165798cfe726291ddbd7fc96ad3dc0113bcb`.
- Brownfield regressions: **205/205 passed**: meeting-sync 31, status-sync 29, state-audit 63, management-panel 28, panel-audit 12, state-prepass 10, panel-model 6, and Panel contract 26.

The successful checks are reproducible, but neither runner executes a v2 browser consumer or a complete ordinary WDR fact-attribution graph, so they do not invalidate H1-H2.

## Evidence Boundary

Both main documents remain `status: draft`. The registry remains `implementation_conformance_status: pending`; both checked-in results are `design-fixture-check` with `native_durability_exercised=false`. Native POSIX fault injection, native Windows CI, two distinct production adapters, the v2 renderer, and the production coordinator/adapters remain release work. No production `skills/adp-*` implementation was changed by this architecture package. Strict publication must remain disabled until H1-H2 are closed and the declared production conformance gate is satisfied.
