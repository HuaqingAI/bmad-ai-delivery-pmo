# BMad Architecture Rubric Review v9

**Artifact:** `ARCHITECTURE-SPINE.md` and normative companions
**Review lens:** complete good-spine checklist plus closure of the v8 High findings
**Verdict:** **FAIL**
**Severity:** 0 Critical, 6 High, 1 Medium

The spine is mechanically sound and materially stronger than v8, but it still contains six enforceability gaps that permit divergent implementations or false success. Production conformance remains explicitly `pending`; this review treats that as the intended implementation gate, not as an architecture defect.

## Gate Evidence

- Deterministic spine lint: **PASS**, 0 findings (run directly with Python because `uv` is unavailable in this environment).
- Python design harness: **169/169 passed**, result bytes equal the checked-in result (`010d659d...`).
- Node design harness: **169/169 passed**, result bytes equal the checked-in result (`d0c4ff6c...`).
- Registry, schema, protocol, vector, compatibility-fixture, and runner raw hashes equal the pins shown in the spine/registry.
- Registry correctly keeps `implementation_conformance_status: pending`; the documents do not falsely claim native POSIX/Windows production evidence.
- Targeted diagnostic probes nevertheless showed that the Python validator accepts a patch under a capability whose `allowed_operations` contains only `create`, and that the Panel publication validator accepts a journal whose pointer target is redirected from the canonical current-pointer path when identities are recomputed.
- Python canonical JCS serializes numeric value `1.0` as `1.0`; ECMAScript/Node serializes it as `1`.

## Critical Findings

None.

## High Findings

### H1 - `include_workstreams: "all"` is still self-certified by the projected output

**Evidence:** `publication_eligibility_semantics` derives the universe for `all` from `sync.canonical.status.workstream_current` itself (`python_runner.py:335-352`, mirrored in `node_runner.mjs:268-282`). The representative source materializer also receives that already-derived list rather than enumerating schema-valid workstream directories (`python_runner.py:689-731`). The registry says `selected-workstreams-v1` means “schema-valid-workstream-directories-then-selection-include-exclude”, but neither harness executes that algorithm. The current valid fixture uses `include_workstreams: "all"` and one status row; omitting another real workstream from status, audit, drift, generation leaves, manifests, and receipts is therefore indistinguishable from a valid one-workstream universe.

**Impact:** a mutually consistent subset can publish as `eligible`, preserving the v8 false-green failure for the most common selection mode.

**Required fix:** bind a concrete, non-empty workstream universe (or its content-addressed catalog plus resolved IDs) into the selection policy/generation. Derive `selected = universe - excludes` before any projection exists, and require exact equality across status, audit, drift, generation leaves, manifests, and receipts. Add a two-workstream `all` fixture where one ID is omitted everywhere downstream and must fail.

### H2 - Fact attribution binds a command fingerprint but does not validate that the capability authorizes the command

**Evidence:** `fact_attribution_semantics` recomputes capability and command identities but never checks `command.operation` against `allowed_operations`, nor validates the complete capability registry's unique producer/capability constraints (`python_runner.py:533-565`; `node_runner.mjs:390-409`). A targeted probe changed the status-sync capability to `allowed_operations: ["create"]`, recomputed all identities, retained a patch command, and the validator returned `True`. Protocol section 2 explicitly requires operation/field/section authorization and rejects duplicate producer/ID records.

**Impact:** a valid low-privilege or misconfigured active capability can authorize a command outside its matrix while producing a valid fact receipt. This is an authorization failure, not merely a missing test.

**Required fix:** make the registered fact validator validate the entire registry (unique capability IDs; exactly one active record per required producer; epoch/status constraints), then authorize command operation and target fields/sections against the matched record before validating deltas and targets. Add denied-operation, denied-field, denied-section, duplicate-producer, and duplicate-capability-ID substitutions.

### H3 - Panel publication closure does not bind the canonical pointer/state/receipt target locations or prior state

**Evidence:** the publication graph binds projection paths, target roles, and after hashes, but for pointer, panel-state, and receipt it checks only role count, document hash, and equality with receipt copies (`python_runner.py:1466-1485`; `node_runner.mjs:894-905`). It never requires `views/management-panel/current-pointer.json`, the registered panel-state location, a deterministic receipt location, or the expected root instance. It also does not bind `before_pointer_id` to a validated before-image/current state. A targeted probe redirected the pointer target to `wrong/current-pointer.json`, updated the receipt copy and identities, and `panel_publication_semantics` still returned `True`.

**Impact:** the journal can commit a complete-looking generation without advancing the location readers actually use, directly recreating a stale Management Panel while the receipt says publication succeeded.

**Required fix:** register and validate exact root/path/operation rules for pointer, panel state, and publication receipt; validate the pointer/state before-images and bind `before_pointer_id` and `before_panel_generation` to them. Add redirected-pointer, redirected-state, redirected-receipt, wrong-root, and substituted-before-image vectors.

### H4 - The repair graph cannot represent its promised failure/orphan branches and models `refresh_actions` as an action mutation

**Evidence:** the registered validator hardcodes nonce states to `unused,reserved,consumed` (`python_runner.py:1111-1129`) and hardcodes a committed repair receipt (`python_runner.py:1166-1175`), although AD-7 and protocol section 8 require `consumed|invalidated`. `repairRunReceiptV1.fact_receipt_id` is mandatory even for `blocked|rolled-back`. The complete graph has no valid invalidated vector; the simple two-batch vector bypasses `repair_graph_semantics`. For orphan reads, the validator computes `revision + 1` even when the schema-supported value is `null` (`python_runner.py:1148-1152`), which raises in Python and diverges from JavaScript. More fundamentally, the fixture turns every `refresh_actions` read record into `changed_fields: ["status"]` and increments action revision, while the pinned brownfield `adp-status-sync` contract says `refresh_actions` rebuilds WDR `Next actions` and preserves the ledger. Its repair journal fixture targets `actions/action-ledger.md` instead of the exact WDR, WDR state, and action-projection sidecar.

**Impact:** failed repairs and orphan cleanup cannot produce a valid audit graph, while a nominally valid repair receipt attests to fact mutations that the operation must not perform. Batch repair is therefore not safely implementable from the contract.

**Required fix:** define separate committed and invalidated graph outcomes. The committed `refresh_actions` graph must treat ledger/action revisions as read-set evidence, leave `action_deltas` empty, and bind exact WDR, WDR-state, sidecar, and fact-generation targets. The invalidated branch must bind rollback/recovery evidence and permit no committed fact receipt (or use an explicitly typed nullable/non-committed reference). Add full-graph valid vectors for `expected_present=false,revision=null` and `reserved -> invalidated`, in both runtimes.

### H5 - The Python canonicalizer is not RFC 8785/ECMAScript number serialization

**Evidence:** `_canonical_number` is a custom renderer (`python_runner.py:31-58`). Python parses JSON `1.0` as a float and the canonicalizer emits `1.0`; RFC 8785's ECMAScript serialization and the Node runner emit `1`. Existing vectors cover only `1.5`, unsafe integer, and JSON-parsed `-0`, so both harnesses pass while this cross-runtime identity divergence remains. Pinned v1 payloads explicitly permit finite IEEE-754 fractions, so the issue is within the declared contract surface.

**Impact:** otherwise identical Panel/projection/receipt content can receive different IDs across the two supported implementations.

**Required fix:** use a verified RFC 8785 binary64 serializer or a proven ECMAScript-equivalent algorithm in Python. Add known-answer vectors for `1.0`, threshold notation (`1e-6`, `1e21`), rounding boundaries, smallest/subnormal values, and representative RFC 8785 number cases, and require exact Python/Node bytes.

### H6 - The registry is not yet the executable semantic-validator authority claimed by AD-11

**Evidence:** neither runner references `registry.semantic_validators`; the seven validators and their algorithms are invoked through hardcoded control flow. Consequently, removing, adding, renaming, or changing a registered semantic validator does not itself fail validator dispatch/coverage. The same gap appears in the same-generation compatibility path: Python reads `registry.panel_v1_composition.source_bindings` and executes pinned `panel_model.py` (`python_runner.py:1223-1252`), while Node ignores the registry composition map and composer and only compares the Panel to frozen compatibility-fixture values (`node_runner.mjs:760-772`). This contradicts AD-11 and protocol section 9's claim that both harnesses execute all registered semantic validators from the actual registry.

**Impact:** registry and implementation can drift while the suite remains green; changes to the composition mapping or validator inventory need not affect one or both harnesses.

**Required fix:** implement an exact dispatcher keyed by registered validator ID, reject unknown/missing/duplicate IDs, and assert the executed ID set equals the registry set. Likewise execute dependency-enumerator definitions and Panel composition bindings from the registry in both runtimes; the Node implementation must independently run an equivalent pinned composer, not merely compare one frozen fixture. Add registry-substitution negatives for validator omission/addition/algorithm mismatch and composition binding changes.

## Medium Finding

### M1 - Nullable non-repairable findings are schema-valid but graph-invalid

`auditFindingRepairV2` allows `repair_batch_id: null`, and AD-7 limits mandatory batch membership to repairable findings. The graph validator nevertheless requires every finding's `repair_batch_id` to resolve to a batch (`python_runner.py:1059-1064`, mirrored in Node). A mixed audit containing informational/non-repairable findings therefore cannot pass. Either separate repairable findings into a dedicated graph input or explicitly skip/null-validate non-repairable findings while still enforcing global finding identity.

## Good-Spine Checklist

| Checklist item | Result | Notes |
| --- | --- | --- |
| Fixes the real divergence points | **Fail** | The intended mutation/projection/freshness/repair boundaries are present, but H1-H6 leave executable divergence. |
| Every AD Rule is enforceable and prevents its stated divergence | **Fail** | AD-4, AD-7, AD-10, and AD-11 overclaim current validator closure. |
| Deferred items cannot cause unacknowledged divergence | **Pass** | Native production conformance is explicitly pending and strict publication is gated on it. |
| Named technology is verified/current enough for the binding | **Pass** | RFC 8785, RFC 6901/2119, Draft 2020-12, and OS durability APIs are appropriate; H5 is an implementation mismatch, not a technology choice problem. |
| Ratifies brownfield reality | **Partial** | Pins and compatibility fixtures are strong, but H4 contradicts the pinned `refresh_actions` behavior. |
| Covers the driving capabilities | **Pass conceptually** | Existing-action mutation, WDR current fields, live freshness, drift, and machine-repairable audit IDs are all represented. |
| Inherited spine constraints | **N/A** | No parent spine is declared. |
| All owned dimensions decided/deferred/open | **Pass** | Ownership, state, mutation, projection, security, migration, durability/recovery, cross-platform behavior, and operations are addressed. |

## v8 Closure Audit

| Prior v8 High | v9 status |
| --- | --- |
| Same-generation `model_v1` composition | **Partially closed**: Python recomposes; Node only fixture-compares and ignores registry composition dispatch (H6). |
| Exact non-empty selection closure | **Not closed for `all`** (H1). |
| Real per-pointer ordering/read validation | **Substantially closed**: 24 pointers use schema-valid representative documents and complete lineage is non-empty; actual enumerator dispatch remains covered by H1/H6. |
| Exact fact authorization | **Partially closed**: action deltas are command-derived, but the command is not checked against capability authority (H2). |
| Transaction-kind journal closure | **Partially closed**: role/marker closure is present, but canonical pointer/state/receipt location and pre-state binding remain open (H3). |
| Reconstructed repair graph | **Partially closed**: binding reconstruction is real, but invalidated/orphan semantics and actual repair effects are not (H4). |
| Cross-runtime RFC 8785 behavior | **Not closed** (H5). |

## Gate Decision

Do not finalize the spine or treat the design-fixture pass as architectural closure. Resolve H1-H6, regenerate pins/results, rerun the full gate, and preserve the existing explicit production-conformance deferral until native implementation receipts satisfy the registered release gate.
