# ARCHITECTURE-SPINE Brownfield Reality / Currentness Review v15

## Verdict

**FAIL. Critical: 1. High: 2. Medium: 0. Low: 1.** The architecture gate requires zero Critical and zero High findings. The package accurately preserves the distinction between design fixtures and production conformance, reproduces 466/466 vectors in Python and Node, preserves Panel v1 compatibility, keeps strict publication pending, and still diagnoses all five reported Management Panel synchronization failures. It nevertheless cannot pass: the normative production evidence trust roots have their private signing seeds published in both reference runners, the production release policy accepts an end-of-life Node runtime, and all three architecture companions state an obsolete exact registry inventory.

## Frozen Review Target

- Review date: 2026-07-25 (Asia/Shanghai).
- Repository: `HEAD=a5d873e0ad3e7d60e7157f76096c6ac65085bee3` on `master`.
- Architecture spine raw SHA-256: `4e957d906894f7de10c2e814f8b5ac583fe94b68641742cc6e86b1d3d3a34cbb`.
- Analysis plan raw SHA-256: `3d81bb0e35441001c8568691eb4bc21a913c3cd543bd5ec59fc04b3b91b1c5ca`.
- Pre-existing unrelated untracked paths were `skills/reports/adp-operational-artifacts-panel-refresh-plan.md` and `skills/reports/eval-runs/`; they were not modified.
- This reviewer changed no spine, contract, fixture, runner, source, or production artifact. It created only this review.

## Critical Findings

### C1 - The production evidence trust roots are forgeable with private keys published in the reference runners

The normative registry installs two concrete Ed25519 trust roots, `adp-posix-ci-2026` and `adp-windows-ci-2026` (`contracts/CONTRACT-REGISTRY.json:9-17`). The production release validator loads those roots and accepts a receipt when its signature and caller-supplied evidence-blob hashes verify (`contracts/conformance/python_runner.py:1579-1585`, `:1611-1634`; the independent Node implementation does the same at `contracts/conformance/node_runner.mjs:780-817`).

Both reference runners publish the matching private seeds in source. Python creates native-looking `implementation-conformance` receipts with the seeds at `contracts/conformance/python_runner.py:1653-1685` and exposes them again at `:6875-6877`. Node contains the same seeds and signs the same receipts at `contracts/conformance/node_runner.mjs:820-838`, with the keys repeated at `:4122-4123`. The accepted vector explicitly proves that these synthetic receipts pass the authoritative release validator (`contracts/fixtures/CONFORMANCE-VECTORS.json:830-845`). The generated logs are strings such as `native-fault-matrix:passed`, and native durability and lock outcomes are booleans set by the fixture (`python_runner.py:1657-1681`; `node_runner.mjs:826-837`).

This is not mitigated by the checked-in results being design fixtures. Those results are correctly non-production, but the trust roots and verification algorithm they exercise are the normative production gate named by AD-11/AD-12 (`ARCHITECTURE-SPINE.md:143-149`) and protocol section 9 (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:97-101`). Anyone with this repository can construct new receipts and evidence blobs, sign them as either trusted CI identity, and satisfy the contract's authentication checks. The current `pending` state prevents immediate strict publication (`CONTRACT-REGISTRY.json:117-132`), but the gate becomes unsafe as soon as status is promoted.

**Risk:** fabricated native Windows, POSIX fault-injection, lock, and production-adapter evidence can be authenticated as trusted, defeating the only gate that authorizes strict publication.

**Required fix:** use a separate fixture trust domain that can never be accepted by the production release validator. Provision production public roots from secret-backed CI or an equivalent controlled signer; no matching private key may exist in the repository. Add key rotation/revocation rules and a negative vector proving fixture-root signatures are rejected by the production gate. Rotate both exposed roots and regenerate every dependent registry, suite, runner, result, and document hash before any implementation status can become `passed`.

## High Findings

### H1 - The production runtime policy accepts end-of-life Node 18

The normative policy accepts every Node version from `18.0.0` through `<100.0.0` (`contracts/CONTRACT-REGISTRY.json:5-8`). The release validator directly applies that interval (`contracts/conformance/python_runner.py:1598-1603`; `contracts/conformance/node_runner.mjs:793-805`). Its accepted production pair is constructed with Node `18.0.0` (`python_runner.py:1653-1656`; `node_runner.mjs:820-825`) and is expected to pass (`contracts/fixtures/CONFORMANCE-VECTORS.json:830-831`).

The authoritative [Node.js Release Working Group schedule](https://github.com/nodejs/Release/blob/main/schedule.json) records Node 18 end-of-life on 2025-04-30 and Node 20 end-of-life on 2026-04-30. On the review date, the supported LTS lines are Node 22 and Node 24. The current policy therefore authorizes a production receipt on a runtime that stopped receiving security fixes more than a year ago.

**Risk:** a fully signed release pair can pass the architecture's production gate while one implementation executes on an unsupported runtime, after which AD-12 permits strict publication.

**Required fix:** bind Node to a currently supported deployment line, at least `>=22` on 2026-07-25, with a lifecycle-based revisit condition. Add explicit Node 18 and Node 20 rejection vectors plus a supported boundary acceptance vector. Refresh the registry, protocol, suite, runners, results, and all raw hash pins.

### H2 - The exact registry inventory in the spine, protocol, and implementation gate is stale

AD-11 claims the registry fixes `47` contracts, `14` source pins, `43` canonical array-ordering rules, `14` identity-set rules, `17` runtime paths, and `13` semantic validators (`ARCHITECTURE-SPINE.md:139-143`). Protocol section 9 repeats the same exact inventory (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:100`), and P0-A makes those old counts part of its completion gate (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:414-423`). The frozen-evidence paragraph still requires only 14 source pins (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:533`).

An independent structured recount of the raw registry gives:

| Registry category | Claimed | Current raw registry |
| --- | ---: | ---: |
| Schema contracts | 47 | 50 |
| Pinned source artifacts | 14 | 23 |
| Dependency enumerators | 9 | 9 |
| Projection profiles / outer payload bindings | 7 / 7 | 7 / 7 |
| Nested payload bindings | 4 | 4 |
| Panel source bindings | 6 | 6 |
| DAG edges | 15 | 15 |
| Canonical array ordering | 43 | 46 |
| Identity-set ordering | 14 | 15 |
| Semantic sequence ordering | 3 | 3 |
| Runtime paths | 17 | 33 |
| Semantic validators | 13 | 14 |

The affected raw arrays begin at `contracts/CONTRACT-REGISTRY.json:59`, `:154`, `:271`, `:708`, `:719`, `:735`, `:752`, `:800`, and `:805`. The registry visibly contains the newly added 50th contracts at `:657-670` and nine brownfield evidence pins at `:225-269`. All 23 current pin hashes match their raw files, so this is not pin corruption; it is stale committed architecture truth after the registry expanded.

**Risk:** implementers following the stated completion gate either reject the canonical registry for having too many records or omit newer contracts, writer evidence, paths, ordering rules, and validators to satisfy the document. The claim that the registry inventory was current and exactly reality-checked is false.

**Required fix:** replace the six stale counts in all three companions with the current derived inventory, or remove prose counts and generate a machine-readable inventory summary from the registry. Add a gate that compares any published inventory summary to structured `length` values so future registry additions cannot leave the architecture currentness claim behind.

## Medium Findings

None.

## Low Findings

### L1 - The checked-in Python design receipt is generated on an end-of-life runtime below the production floor

The checked-in Python result records CPython `3.9.6` (`contracts/conformance/python-result.json:8-12`) while the registry production floor is CPython `3.10.0` (`contracts/CONTRACT-REGISTRY.json:5-7`). Python 3.9 is end-of-life. A fixed-time replay under the system 3.9.6 interpreter reproduced the checked-in raw hash byte-for-byte; a second replay under supported CPython 3.12.13 also passed the same 466 vectors but necessarily produced a different runtime-bound result ID and raw hash.

This is Low because the receipt is honestly marked `design-fixture-check` and `native_durability_exercised=false` (`python-result.json:3-14`), while the release validator rejects non-production evidence and out-of-policy runtime versions (`python_runner.py:1592-1603`). AD-11 and the evidence table also say these results are design-only (`ARCHITECTURE-SPINE.md:178-185`).

**Recommended fix:** regenerate the checked-in Python design receipt under a supported interpreter, preferably the same supported floor or CI baseline used for the production adapter, and update its result hash in the spine. Keep the 3.9 rejection as a negative release vector rather than the runtime of the positive design receipt.

## Five Brownfield Management Panel Failures

All five reported failures remain observable in the current pinned production sources. The new brownfield pins at `contracts/CONTRACT-REGISTRY.json:225-269` match the current raw files, so these are current deployed limitations rather than stale line citations.

| Reported failure | Current production evidence | Verdict |
| --- | --- | --- |
| Meeting sync cannot mutate an existing action owner/status | The meeting item has no `operation`, exact `action_id`, or revision (`skills/adp-meeting-sync/references/sync-plan-schema.md:42-59`). `build_status_sync_intake()` emits create-shaped fields without identity (`skills/adp-meeting-sync/scripts/sync_meeting.py:1371-1381`). Status sync can find exact IDs and merge rows (`skills/adp-status-sync/scripts/sync_status.py:840-846`, `:907-920`), confirming the break is at the meeting contract. | Confirmed |
| `wdr_update` does not update Panel current fields | Meeting sync appends a block to each WDR (`skills/adp-meeting-sync/scripts/sync_meeting.py:812-821`) and renders a `Meeting Sync Update` region (`:1244-1264`). Current `Project Status` fields are instead written by status sync (`skills/adp-status-sync/scripts/sync_status.py:1458-1474`, `:1523-1538`). | Confirmed |
| Panel inspection does not prove current live facts | `inspect_current()` verifies embedded and immutable Panel artifacts but does not reread WDR or ledger leaves (`skills/adp-management-panel/scripts/management_panel.py:1120-1156`). Panel audit checks fingerprint shape and generated age, not current raw fact equality (`skills/adp-state-audit/scripts/panel_audit.py:378-424`, `:489-501`). | Confirmed |
| WDR/ledger drift detection is incomplete | Prepass compares action ID sets (`skills/adp-agent-program-lead/scripts/adp-state-prepass.py:911-958`) and skips the cross-check when the global active ledger list is empty (`:1154`). State audit only converts those ID differences into findings (`skills/adp-state-audit/scripts/audit_state.py:2282-2303`); it does not prove owner/text/due content. | Confirmed |
| Canonical audit output loses exact action IDs | `canonical_finding()` omits action IDs from the public object (`skills/adp-state-audit/scripts/audit_state.py:2951-2998`) even though identity includes them (`:3001-3014`) and later formatting attempts to read them (`:3341-3362`). | Confirmed |

AD-2, AD-3, AD-4, AD-5, and AD-7 target these failures without claiming they are deployed. The planned target modules `skills/adp-fact-transaction`, `skills/adp-wdr-mutation`, and `skills/adp-panel-refresh` do not exist, and production Management Panel remains schema/model v1 (`skills/adp-management-panel/scripts/panel_model.py:25`; `skills/adp-management-panel/assets/adp-management-panel-v1.schema.json:7-9`).

## Contract, Hash, and Compatibility Evidence

| Artifact | Observed raw SHA-256 | Reality result |
| --- | --- | --- |
| `contracts/CONTRACT-REGISTRY.json` | `7a36b2941ebd285d4682f8506bb12467d4525eee0b31a36c9345db47e8b81efa` | Matches spine and both receipts |
| `contracts/panel-sync-contracts.schema.json` | `b2629be5e871d1eb8c43a839a86b8de165101b0d16788e1b8a5b094902ac4e73` | Matches registry and spine |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `81bcfcbc3872cd4fce0ba04899c169462a64d9aa81adb5d0c5b6b8789587de87` | Matches registry and spine |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `f99caeb3721503179ae7e4c70c786cc631141f232b83cfe2de6452f386c1ea8d` | Matches registry and spine |
| `contracts/fixtures/PANEL-V1-COMPATIBILITY.json` | `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` | Matches pin and regenerated fixture |
| `contracts/conformance/python_runner.py` | `12189b3af7d521a76e438c2b88376b42493af5c7fd55ab87b415c6d17c52d24b` | Matches registry and spine |
| `contracts/conformance/node_runner.mjs` | `bc0acadc85bcbbb66e51d934935866498dacb5e11cc7a0bb611e6f5943b006b7` | Matches registry and spine |
| `contracts/conformance/panel_v2_consumer.mjs` | `eaf497fbf79eaedface1408b1ea1679aec7d7e85709fd9e3a386550855bbc423` | Matches pin and spine |
| `contracts/conformance/python-result.json` | `9f3134a9d957c3da588fc2aefda29f330e4572a0a206ec95c4b1bebe13785870` | Exact fixed-time replay under CPython 3.9.6 |
| `contracts/conformance/node-result.json` | `d5fe61bb7badb7b8b7ee7df5dd9613c9349f46a271ad339596b8c69f0fd17943` | Exact fixed-time replay under Node 24.16.0 |

- All 23 current `pinned_source_artifacts` hashes match, including all nine brownfield diagnosis sources.
- The suite has exactly 466 IDs and 466 unique IDs. Python 3.12.13 and Node 24.16.0 each passed 466/466 with zero failures, and their passed-ID sets are exactly equal. Node replay was byte-for-byte equal to the checked-in result; Python 3.12 differed only in runtime-bound receipt fields, while Python 3.9.6 reproduced the checked-in bytes.
- The Panel v1 compatibility fixture regenerated byte-for-byte equal at SHA-256 `3b96b780...b6fe7`. The vector suite executed the pinned v1 composer corpus and the instrumented v2 current consumer, but these remain design evidence.
- The registry and suite both state `implementation_conformance_status=pending` (`contracts/CONTRACT-REGISTRY.json:117-132`; `contracts/fixtures/CONFORMANCE-VECTORS.json:4-9`). Checked-in results are `design-fixture-check`, use design platforms, and set native durability false (`python-result.json:3-14`; `node-result.json:3-14`). Protocol section 9 and AD-12 therefore correctly forbid current strict production publication (`WDR-AND-TRANSACTION-PROTOCOL.md:97-101`; `ARCHITECTURE-SPINE.md:145-149`).

## Current Standards Check

| Technology / standard | Authoritative check on 2026-07-25 | Verdict |
| --- | --- | --- |
| CPython `>=3.10,<4.0` | The official [Python Developer's Guide version table](https://devguide.python.org/versions/) lists 3.10 in security support through 2026-10. Production metadata and both normal cross-platform workflows use 3.10 (`skills/adp-meeting-sync/scripts/sync_meeting.py:1-4`; `.github/workflows/adp-meeting-sync.yml:15-34`; `.github/workflows/adp-management-panel.yml:27-46`). | Current today, but time-bounded; L1 concerns only the design receipt |
| Node `>=18,<100` | The official [Node.js release schedule](https://github.com/nodejs/Release/blob/main/schedule.json) ended v18 support in 2025-04 and v20 in 2026-04. | Not current; H1 |
| JSON Schema Draft 2020-12 | [JSON Schema specification](https://json-schema.org/specification) still identifies Draft 2020-12 as the current published draft. All nine in-scope schema files parse and declare its official meta-schema URI. | Current and fit |
| RFC 8785 JCS / RFC 6901 JSON Pointer / RFC 3339 timestamps / BCP 14 | The cited RFC Editor documents remain the authoritative definitions. Cross-language known-answer, pointer, timestamp, and ordering vectors passed. | Current and fit |
| Ed25519 | RFC 8032 remains a current standard and both implementations agree cryptographically. | Algorithm fit; key management is critically invalid under C1 |
| CQRS-style materialized projections | The paradigm matches the target single-writer facts and immutable read model and explicitly excludes event sourcing (`ARCHITECTURE-SPINE.md:41-43`). | Fit |

## Independent Verification Summary

- Architecture lint: **PASS, 0 findings**, run directly with the skill's `lint_spine.py` because `uv` is not installed in this environment.
- Contract runners: **Python 466/466, Node 466/466**, zero failures, equal passed-ID sets.
- Python and Node syntax/executable paths: exercised successfully by the full runners; the pinned v2 consumer was executed by its conformance vectors.
- Panel v1 compatibility regeneration: **byte-for-byte PASS**.
- Brownfield baseline: **205/205 PASS**: meeting-sync 31, status-sync 29, state-audit 63, management-panel 28, panel-audit 12, state-prepass 10, panel-model 6, Panel contract 26.
- Program Lead additions: **17/17 PASS**: consume-program-status 12 and render-program-views 5.
- Schema set: **9/9 parse and declare Draft 2020-12**.
- Source pins: **23/23 raw hashes match**. The architecture prose saying 14 is H2.
- Current production evidence remains incomplete: no accepted real native Windows receipt, no real POSIX fault-injection receipt, no production adapter pair, no writer-fence migration, and no strict activation attestation.

## Gate Decision

**Reality/currentness gate fails with 1 Critical and 2 High findings.** Do not finalize the spine or authorize implementation from this package until the exposed signing roots are replaced with a production-safe trust model, the Node production floor is moved to a supported line, and the exact registry inventory is reconciled across the spine, protocol, and implementation plan. After those changes, regenerate every affected raw hash and rerun this gate from a fresh context.
