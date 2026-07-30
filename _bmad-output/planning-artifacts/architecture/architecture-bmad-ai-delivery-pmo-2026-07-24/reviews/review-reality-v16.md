# ARCHITECTURE-SPINE Brownfield Reality / Currentness Review v16

## Verdict

**FAIL. Critical: 0. High: 1. Medium: 1. Low: 2.** The zero-Critical/zero-High gate is not met. The package now correctly separates production and design trust domains, restricts Node production evidence to supported 22/24 lines, carries an exact current registry inventory, reproduces both 486-vector results byte-for-byte, preserves the Panel v1 corpus, and accurately diagnoses all five current Management Panel failures. It still has one implementation-blocking wire contradiction: `status-sync` is declared the sole owner of the WDR Roadmap, but the registered command schema/capability pair gives it no legal Roadmap mutation.

## Frozen Review Target

- Review date: 2026-07-25 (Asia/Shanghai).
- Repository: `HEAD=a5d873e0ad3e7d60e7157f76096c6ac65085bee3` on `master`.
- Architecture spine raw SHA-256: `30dd97d371a9378077d1e3416fc0ad8ec874100ba10d4b1070d93e171ef59fdc`.
- Analysis plan raw SHA-256: `740c75ee80ecdae3366f5c67dd5a27fcff96b04b8965c99c5f44874dbc522d5e`.
- Pre-existing unrelated untracked paths were `skills/reports/adp-operational-artifacts-panel-refresh-plan.md` and `skills/reports/eval-runs/`; they were not modified.
- This reviewer created only this review file. No spine, contract, fixture, runner, source, result, or production artifact was edited.

## High Findings

### H1 - The sole WDR Roadmap owner has no schema-valid, capability-authorized Roadmap patch

AD-1 assigns `adp-status-sync` exclusive ownership of all WDR current fields **and the entire Roadmap** (`ARCHITECTURE-SPINE.md:79-83`). This preserves current brownfield behavior: production `sync_status.py` treats milestone updates as a WDR delta, validates them against the baseline, and rewrites the WDR Roadmap table (`skills/adp-status-sync/scripts/sync_status.py:1244-1282`, `:1285-1338`, `:1476-1481`, `:1506-1519`). The analysis plan repeats the exclusive ownership and explicitly names `status Roadmap` in the writer-fence completion gate (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:28-30`, `:208-214`, `:438`).

The normative wire contracts cannot express that operation:

- `wdrPatchV1.set` permits status/phase/current collections, `refresh_actions`, meeting history, and `owned_sections`, but has no typed milestone or Roadmap field (`contracts/panel-sync-contracts.schema.json:450-480`).
- Roadmap is reachable only through `owned_sections[].section="roadmap"` (`panel-sync-contracts.schema.json:440-447`).
- The raw registry gives `adp-status-sync` Roadmap in `allowed_sections` but omits `owned_sections` from its `allowed_fields` (`contracts/CONTRACT-REGISTRY.json:53`). Capability validation requires both the actual field set and section set to be subsets, so a status-sync Roadmap patch is rejected (`contracts/conformance/python_runner.py:2571-2588`, `:3275-3280`; Node has the equivalent check).
- The positive owned-section attribution fixture uses `adp-bmm-checkpoint-sync` for `checkpoint-sync-log`; there is no status-sync Roadmap mutation fixture (`python_runner.py:2738-2741`, `:5875-5879`).

This is not a documentation-only discrepancy. An implementation conforming to the raw registry cannot retain the existing status-sync milestone/Roadmap mutation that the architecture promises to preserve. Granting unrestricted `owned_sections` to status-sync would also be too broad because that field can carry several unrelated sections.

**Required fix:** add a typed, structure-preserving Roadmap/milestone patch to `wdrPatchV1`, map it to `roadmap`, grant exactly that field to `adp-status-sync`, and add byte-exact positive and negative attribution/mutation vectors. Alternatively, explicitly transfer Roadmap ownership to a different writer and specify a status-sync intent contract, but then update AD-1, brownfield compatibility, ordering, transaction, and retry rules. Regenerate every affected schema/registry/protocol/suite/runner/result hash.

## Medium Findings

### M1 - The committed refresh-status path disagrees with the registry's only runtime path

AD-8 says live inspect and refresh runtime status is written only to `views/management-panel/refresh-status.json` (`ARCHITECTURE-SPINE.md:121-125`). The analysis plan repeats that exact path twice (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:368`, `:482`). The raw registry instead fixes `panel_refresh_status` at `state/panel-refresh-status.json` (`contracts/CONTRACT-REGISTRY.json:74`), and both harnesses derive the only permitted inspect write path from that registry entry (`contracts/conformance/python_runner.py:5745-5753`, `:5791-5795`; Node equivalent).

AD-11 makes raw registry bytes the wire authority, so the harness behavior is deterministic, but the spine still tells an implementer to write a different file. That can split operator-visible runtime state and cause an inspect result to appear missing or stale without changing immutable Panel identity.

**Required fix:** change AD-8 and both analysis-plan occurrences to the registry path, or change the registry and regenerate all dependent hashes. Add a documentation/registry path lint so literal normative paths cannot diverge again.

## Low Findings

### L1 - The advertised Node allowlist lacks the decisive in-range Node 23 rejection vector

The registry and both release-gate implementations correctly require Node major 22 or 24 (`CONTRACT-REGISTRY.json:7`; `python_runner.py:1624-1630`; `node_runner.mjs:807-813`). The accepted base pair exercises Node 22 and a separate vector exercises Node 24. The suite rejects 18, 20, 21, and 25, but has no Node 23 case (`CONFORMANCE-VECTORS.json:861-866`). Node 23 is the important value that satisfies the numeric `>=22,<25` range while violating `allowed_major_versions`; without it, the exact allowlist branch is statically present but not proven by the claimed 486-vector evidence.

**Recommended fix:** add `release-runtime-node-23-rejected` and regenerate the suite, runners, results, registry, protocol, and spine hashes.

### L2 - The Python 3.10 floor is current for only about two more months and has no lifecycle revisit trigger

The production policy `>=3.10,<4.0` is valid on the review date, and the accepted design release pair uses the exact 3.10 floor while a negative vector rejects 3.9. The authoritative [Python Developer's Guide version table](https://devguide.python.org/versions/) lists 3.10 in security-only support with end-of-life in 2026-10. The policy therefore becomes stale shortly after this review unless the rollout or registry is revised.

This is Low because every production receipt must still identify the exact interpreter/build and pass native conformance, and strict publication is currently blocked. It is nevertheless a currentness maintenance gap in a package intended to become a production gate.

**Recommended fix:** set a registry-policy review deadline before 2026-10 and plan to raise the minimum to a supported line before provisioning production roots or promoting implementation conformance.

## Five Brownfield Management Panel Failures

All five reported failures remain observable in the current production source bytes. The architecture correctly treats them as existing behavior to replace, not as already implemented fixes.

| Reported failure | Current production evidence | Verdict |
| --- | --- | --- |
| Meeting sync cannot patch an existing action | The meeting item shape has no operation, exact action ID, or revision (`skills/adp-meeting-sync/references/sync-plan-schema.md:42-59`), and `build_status_sync_intake()` emits create-shaped data without identity (`skills/adp-meeting-sync/scripts/sync_meeting.py:1371-1381`). Status sync can find an exact ID and merge a row (`skills/adp-status-sync/scripts/sync_status.py:840-846`, `:907-920`), while `ActionUpdate.status` defaults to `open` and merge writes it unconditionally (`:103-105`, `:907-909`). | Confirmed |
| `wdr_update` does not update Panel current fields | Meeting sync appends to the WDR (`skills/adp-meeting-sync/scripts/sync_meeting.py:812-821`) as a `Meeting Sync Update` block (`:1244-1264`). It does not invoke the status-sync current-field writer; status sync owns those mutations (`skills/adp-status-sync/scripts/sync_status.py:1458-1474`, `:1523-1544`). | Confirmed |
| Panel inspection does not prove current live facts | `inspect_current()` validates Panel/immutable identities but does not reread WDR or ledger leaves (`skills/adp-management-panel/scripts/management_panel.py:1120-1156`). Panel audit validates fingerprint shape and generated age, not live raw fact equality (`skills/adp-state-audit/scripts/panel_audit.py:378-424`, `:489-501`). | Confirmed |
| WDR/ledger action drift detection is incomplete | Prepass compares action ID sets (`skills/adp-agent-program-lead/scripts/adp-state-prepass.py:911-958`) and skips the comparison when the global active ledger list is empty (`:1154`). It does not prove owner/text/due content, and Panel does not consume a complete drift verdict. | Confirmed |
| Canonical audit output loses action IDs | Raw action findings contain `action_id` (`skills/adp-state-audit/scripts/audit_state.py:3530-3541`), and identity includes it (`:3001-3014`), but `canonical_finding()` omits it from the public object (`:2951-2998`) before later formatting tries to read it (`:3341-3362`). | Confirmed |

## Brownfield Compatibility

- The pinned Panel v1 compatibility fixture regenerated byte-for-byte equal at SHA-256 `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7`.
- The frozen brownfield baseline passed **205/205**: meeting-sync 31, status-sync 29, state-audit 63, management-panel 28, panel-audit 12, state-prepass 10, panel-model 6, and Panel contract 26.
- The additional Program Lead baseline passed **17/17**: consume-program-status 12 and render-program-views 5.
- Panel v2 remains additive in the design fixture: the v1 model/manifest and three view/flow/meeting-board families are retained, while the instrumented v2 consumer reads the declared `/sync/canonical/status/workstream_current` and `/panel_id` paths.
- H1 is the exception to the otherwise reproducible brownfield story: current status-sync milestone/Roadmap writes do not have a legal v2 command path.

## Registry, Hash, and Pin Evidence

The spine's exact registry inventory matches current raw JSON:

| Category | Observed |
| --- | ---: |
| Schema contracts | 50 |
| Pinned source artifacts | 23 |
| Dependency enumerators | 9 |
| Projection profiles / payload bindings | 7 / 7 |
| Nested payload bindings | 4 |
| Panel source bindings | 6 |
| DAG edges | 15 |
| Typed array ordering / identity-set ordering / semantic sequences | 46 / 15 / 3 |
| Runtime paths | 33 |
| Semantic validators | 14 |

All 23 pinned source artifact hashes match the current repository bytes, including the nine production sources used for the five-problem diagnosis. All outer/nested schema paths, pointers, IDs, and raw hashes loaded successfully in both harnesses.

| Artifact | Observed raw SHA-256 | Result |
| --- | --- | --- |
| `contracts/CONTRACT-REGISTRY.json` | `68da99c0336f83cf70a2b54c93262355b7d20bf7ffdfdf6f0079be611523f064` | Matches spine and results |
| `contracts/panel-sync-contracts.schema.json` | `ea7f20f5f99f131fede251703b57f0b77e10b57a4e7a6cbf58d2f135b104a5d5` | Matches registry and spine |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `11f0784a667cef959a35dc9f55d0331c0ba83db8fc1947376bc1c6a36c18ec26` | Matches registry and spine |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `b898b48711a86eea5822b0b34099db22423e20596c888dbbf3c851762fc32a11` | Matches registry and spine |
| `contracts/fixtures/PANEL-V1-COMPATIBILITY.json` | `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` | Matches pin and regenerated bytes |
| `contracts/conformance/python_runner.py` | `caf5b522d511c83a9affde0dc1986f99589af0706f86e72a93e5a614b5304ead` | Matches registry and spine |
| `contracts/conformance/node_runner.mjs` | `a23bb74311da2f84930d18db25e7db3dfd41a9d677516670a74aa1bdae934b3f` | Matches registry and spine |
| `contracts/conformance/panel_v2_consumer.mjs` | `eaf497fbf79eaedface1408b1ea1679aec7d7e85709fd9e3a386550855bbc423` | Matches pin and spine |
| `contracts/conformance/python-result.json` | `a6dd2e8871447e746cc720e7ca3977c0afa3eb7f1b1a09aa3a23b3cc19873d87` | Exact fixed-time replay |
| `contracts/conformance/node-result.json` | `70066f1f0088a115bd39472c5d198b37861e0a53ee2e923ea3918858b072fd3e` | Exact fixed-time replay |

## Runtime and Standards Currentness

| Policy / standard | Authoritative current check | Verdict |
| --- | --- | --- |
| Node 22 / 24 only | The [Node.js Release Working Group schedule](https://github.com/nodejs/Release/blob/main/schedule.json) gives v22 end-of-life 2027-04-30 and v24 end-of-life 2028-04-30. On 2026-07-25, v22 is Maintenance LTS and v24 is Active LTS; v26 is Current and does not become LTS until 2026-10-28. | Current and appropriate for production LTS; L1 is a vector gap, not a policy defect |
| CPython `>=3.10,<4.0` | The [Python Developer's Guide](https://devguide.python.org/versions/) lists 3.10 in security support through 2026-10. The suite's accepted base pair uses 3.10 and rejects 3.9. | Current today; time-bounded under L2 |
| JSON Schema Draft 2020-12 | The official [Draft 2020-12 meta-schema](https://json-schema.org/draft/2020-12/schema) remains published at the declared URI. | Current and fit |
| RFC 8785 / RFC 6901 / Ed25519 | Registry ordering, pointers, known-answer canonicalization, and signature paths execute in both design harnesses. | Standards are current; production proof remains intentionally absent |

## Production vs Design Trust Roots

The v15 production-key defect is closed in current bytes:

- Raw production registry `trust_roots` is exactly empty and `minimum_production_trust_roots` is 2 (`CONTRACT-REGISTRY.json:9-18`).
- Raw implementation conformance remains `pending` (`CONTRACT-REGISTRY.json:117-132`).
- Fixture private seeds exist only in the reference runners and are injected into an in-memory design-mock registry. A dedicated negative vector runs the same receipts against the unprovisioned raw production registry and rejects them (`CONFORMANCE-VECTORS.json:846-847`).
- The spine and protocol require a reviewed raw-registry update before provisioning at least two production public roots and state that design-mock roots cannot authorize strict publication (`ARCHITECTURE-SPINE.md:143-149`; `WDR-AND-TRANSACTION-PROTOCOL.md:22`, `:102`).

No production trust root or matching production private key is committed. Current design receipts cannot authorize production.

## Live-Inspect Closure

The registered live-inspect semantic validator and both harness implementations exercise restart-safe loading of activation state, writer attestation, capability registry, release evidence, current pointer, exact lineage index/object closure, fact state, and re-enumerated live leaves. The suite covers fresh restart, fact/source drift, unreadable source, missing/tampered lineage, extra/missing leaves, pending registry, activation rollback/epoch drift, capability drift, attestation replacement, writer-build drift, design-only evidence, and write-surface restriction (`CONFORMANCE-VECTORS.json:998-1020`). Strict-activation vectors additionally cover writer/fence/capability/fact/WDR/sidecar/receipt closure.

This is coherent **design evidence only**. The production modules named in the structural seed (`adp-fact-transaction`, `adp-wdr-mutation`, and `adp-panel-refresh`) do not yet exist, current production `management_panel.py` has no live-fact inspect closure, and no strict activation attestation exists. AD-12 correctly returns `migration-required` while the raw registry is pending and production roots are empty. M1 must be corrected so the eventual runtime writes its inspect result to the same registered sidecar.

## 486-Vector Evidence Boundary

- The suite contains exactly **486 unique vector IDs**.
- Python 3.12.13 replay: **486 passed, 0 failed**, raw result exactly `a6dd2e...73d87`.
- Node 24.16.0 replay: **486 passed, 0 failed**, raw result exactly `70066f...fd3e`.
- Passed-vector ID sets are equal, and both checked-in result files replay byte-for-byte at the fixed execution timestamp.
- Both receipts are honestly marked `design-fixture-check`; Python uses `posix-design-model`, Node uses `windows-design-model` while its host is `darwin-arm64`, and both record `native_durability_exercised=false`.
- The 486 passes prove deterministic design-fixture behavior and cross-language agreement. They do **not** prove a production adapter, native Windows execution, real POSIX crash/fault durability, deployed writer fences, or a live production inspect. The registry's pending state and empty production roots correctly keep those boundaries closed.
- H1 passes unnoticed because the suite contains current-field and checkpoint-owned-section WDR attribution fixtures but no status-sync Roadmap command. L1 identifies the analogous Node 23 allowlist branch gap.

## Gate Decision

**Reality/currentness gate fails with 0 Critical, 1 High, 1 Medium, and 2 Low findings.** Do not finalize the spine as implementation-ready until status-sync has a schema-valid, least-authority Roadmap mutation path and corresponding byte-exact conformance coverage. Reconcile the refresh-status path at the same time, add the missing Node 23 release vector, and set the Python-floor lifecycle review before regenerating all affected pins and results.
