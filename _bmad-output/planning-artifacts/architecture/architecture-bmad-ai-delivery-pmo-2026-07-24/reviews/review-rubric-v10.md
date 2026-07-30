# BMad Architecture Good-Spine Rubric Review v10

**Artifact:** `ARCHITECTURE-SPINE.md` and its normative companions
**Review lens:** complete BMad good-spine gate plus closure of the v9 findings
**Verdict:** **FAIL**
**Severity:** **0 Critical, 3 High, 0 Medium**

The v10 package is substantially stronger than v9. Catalog-first selection, exact Panel binding/cardinality, capability authorization, byte-bound CAS state, journal namespaces, repair terminal branches, and dynamic Panel v1 composition are now explicit and executable. Three remaining gaps still permit a conforming implementation or a green design receipt to show stale current state or compute divergent identities.

Production conformance remains explicitly `pending`. This review treats native POSIX/Windows evidence and production-adapter receipts as an intentional release gate, not as architecture defects.

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

## Gate Evidence

- Architecture spine lint: **PASS**, 0 findings. `uv` is unavailable, so the pinned Python lint script was run directly.
- Draft 2020-12 schema compilation: **PASS** for the main bundle and all five pinned external schemas (Panel model, Panel manifest, flow graph, progress v3, and flow-state v1).
- Independent registry closure: **PASS** for 40 contracts/anchors, 12 source pins, 7 profiles/bindings/envelope kinds, 15 derived DAG edges, 8 runtime paths, 8 semantic validators, 8 identity-set rules, 25 array-ordering rules, 6 Panel bindings, and 4 nested bindings.
- Python reference adapter: **210 passed / 0 failed**, byte-for-byte equal to the checked-in result.
- Node reference adapter: **210 passed / 0 failed**, byte-for-byte equal to the checked-in result.
- Both receipts have valid result identities, use `evidence_kind=design-fixture-check`, and correctly state `native_durability_exercised=false`.

## Critical Findings

None.

## High Findings

### H1 - The declared current-field UI path is not consumed by the pinned Panel renderer

AD-4 and the protocol choose a clear target: `model_v1` is aggregate-only and current workstream values must be read from `/sync/canonical/status/workstream_current` (`ARCHITECTURE-SPINE.md:101`; `WDR-AND-TRANSACTION-PROTOCOL.md:59`; registry `current_workstream_ui_pointer` at `CONTRACT-REGISTRY.json:568`). The executable evidence does not prove that consumer binding. `panel_v1_composition_valid()` only recomposes and compares `model_v1` (`python_runner.py:1595-1624`; Node equivalent `node_runner.mjs:1039-1063`). The compatibility check merely requires current rows to contain four keys (`python_runner.py:1590-1592`). The current-field corpus changes `sync.canonical` and accepts an unchanged legacy model (`python_runner.py:2341-2350`).

The pinned brownfield consumer demonstrates the missing edge. `skills/adp-management-panel/assets/panel.js` parses only `adp-panel-model` and `adp-panel-manifest` (`panel.js:4-6`), populates workstream controls from `model.data.status.progress.by_scope` (`panel.js:125-138`), and renders current progress/status from `model.data.status` (`panel.js:203-221`). It has no read of the v2 `sync` object. A payload can therefore pass all 210 design vectors, carry new progress/blockers/risks in `sync.canonical`, and still render the old v1 values. That is the original Management Panel lag symptom at the actual consumer boundary.

**Required fix:** choose and pin one executable consumer strategy. For the selected v2-current strategy, define a versioned HTML/input binding that embeds the complete v2 payload, update and hash-pin the browser consumer to read current workstream fields only from the registry pointer, and add a browser/DOM known-answer vector that holds `model_v1` constant while changing progress, blockers, and risks and asserts the rendered values change. The alternative is to define a deterministic canonical-current-to-v1 transform and require those values in the recomposed v1 model. A payload-only equality assertion is not sufficient.

### H2 - The safe-integer profile is value- and runtime-inconsistent

The spine and protocol require safe integers and rejection of integer magnitudes above `2^53-1` (`ARCHITECTURE-SPINE.md:143,201`; `WDR-AND-TRANSACTION-PROTOCOL.md:7`). The schema does not enforce that rule: `revision` has only `minimum: 0` (`panel-sync-contracts.schema.json:69-71`), and none of the bundle's integer declarations has a safe maximum. A real Draft 2020-12 validator accepts a complete `factGenerationStateV1` with `fact_generation: 1e21`.

Both canonicalizers also condition rejection on the rendered number not containing an exponent (`python_runner.py:38-39`; `node_runner.mjs:28`). The checked-in `jcs-exponent-threshold-high` vector explicitly accepts `1e21`, even though it is mathematically an integer above the declared bound. More seriously, the same raw JSON integer is parsed differently by the two runtimes. A temporary 211th vector containing `1000000000000000000000` with expected `JCS_NUMBER_PROFILE_INVALID` produced **Python 211/211** and **Node 210/211**; Node parsed it as exponent-form binary64 and accepted it. Thus the same wire bytes can be accepted and hashed by one supported runtime and rejected by the other.

**Required fix:** use one coherent number domain. The clean option is full RFC 8785 binary64 canonicalization plus `maximum: 9007199254740991` on every integer-valued contract field (and the symmetric lower bound where negatives are allowed). If the global safe-integer profile is retained instead, reject every mathematically integral unsafe binary64 value regardless of exponent spelling and use a JSON parser/profile that makes the decision identical across runtimes. Add raw-wire vectors for `9007199254740991`, `9007199254740992`, `1e21`, and `1000000000000000000000`, plus complete-contract integer-field cases, and require identical accept/reject results and canonical bytes.

### H3 - NFC-equivalent identity-set values do not have the promised total order

Protocol section 1 requires identity-set strings to be compared after NFC normalization and requires duplicate identity keys, including collisions, to be rejected (`WDR-AND-TRANSACTION-PROTOCOL.md:9`). The registry correctly lists eight identity-set locations (`CONTRACT-REGISTRY.json:501-510`), including unrestricted `authorization_scopes` and `finding_ids`. Their schemas allow arbitrary Unicode and `uniqueItems` compares raw strings (`panel-sync-contracts.schema.json:14-20,854,1266-1268`).

The Python validator sorts by the NFC byte key but rejects duplicates using the original strings (`python_runner.py:1161-1163`); Node does the same (`node_runner.mjs:751-752`). Consequently composed and decomposed spellings such as `"repair:\u00e9"` and `"repair:e\u0301"` have equal ordering keys but are not considered duplicates. Both input orders are accepted, yet JCS preserves the original array order, so authorization binding digests, batch identities, or receipts can differ for the same semantic set. The suite has only canonical and permutation cases for identity sets; its NFC-collision case covers the separate array-ordering validator, not these eight rules.

**Required fix:** either require and validate NFC-normalized stored scalar values before identity calculation, or compute normalized identity keys and reject duplicate normalized keys before sorting/hashing. Add canonical, permutation, exact-duplicate, and NFC-collision vectors for every identity-set rule that admits non-ASCII text, especially `authorization_scopes` and `finding_ids`, in both runtimes.

## Good-Spine Checklist

| Checklist item | Result | Notes |
| --- | --- | --- |
| Fixes the real divergence points | **Fail** | Mutation, freshness, drift, and repair boundaries are strong, but H1 leaves the visible Panel consumer outside the closed path. |
| Every AD rule is enforceable and prevents its stated divergence | **Fail** | AD-4/AD-11 overclaim consumer and canonical-identity closure (H1-H3). |
| Deferred items cannot cause unacknowledged divergence | **Pass** | Production/native conformance is explicitly pending and release-gated. |
| Named technology is current and appropriate | **Pass** | RFC 8785, RFC 6901, Draft 2020-12, and the durability APIs are suitable; H2 is a profile implementation contradiction. |
| Ratifies brownfield reality | **Partial** | Source pins are accurate, but the pinned browser consumer contradicts the chosen v2 current-field read path (H1). |
| Covers the driving capabilities | **Pass conceptually** | Existing-action mutation, WDR current fields, freshness, drift, and typed repair IDs are represented. |
| Inherited spine constraints | **N/A** | No parent spine is declared. |
| All owned dimensions decided/deferred/open | **Pass** | Ownership, data, security, compatibility, recovery, operations, and rollout are covered. |

## v9 Closure Audit

The current package materially closes the v9 catalog/self-selection, capability-scope, pointer/state/receipt location, repair-branch, semantic-validator inventory, physical-leaf identity, journal-locator, and dynamic-composer findings. The tested RFC 8785 rendering cases also now agree across Python and Node. H1 is the still-missing consumer proof from the earlier same-generation/current-surface finding; H2 exposes an untested part of the revised number profile; H3 shows that the newly registered identity-set rules are not yet collision-complete.

## Gate Decision

Do not finalize the spine or use the 210/210 design receipts as architectural closure. Resolve H1-H3, regenerate the complete registry/schema/protocol/vector/runner/result hash chain, and rerun the independent gate. Preserve `implementation_conformance_status: pending` until the existing native and production-adapter release requirements are satisfied.
