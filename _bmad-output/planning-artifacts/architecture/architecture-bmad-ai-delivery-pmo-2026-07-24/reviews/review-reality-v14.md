# ARCHITECTURE-SPINE Brownfield Reality / Currentness Review v14

## Verdict

**PASS WITH FOLLOW-UPS. Critical: 0. High: 0. Medium: 2. Low: 0.** The current architecture package still describes a target state rather than claiming deployment, accurately diagnoses all five brownfield failures, preserves the production 20-column action ledger before adding `Action Revision` as column 21, and keeps strict publication disabled while implementation evidence is incomplete. Raw hashes, registry inventory, all 14 source pins, the fixed-time 413-vector replay, Panel v1 compatibility regeneration, and the stated 205 + 17 brownfield regressions reproduced independently.

The remaining findings are evidence-baseline reproducibility and enforcement of the Python 3.12 production floor. Neither allows strict publication under the current `pending` registry, but both should be closed before implementation conformance can be promoted to `passed`.

## Frozen Review Target

- Review date: 2026-07-25 (Asia/Shanghai).
- Repository: `HEAD=a5d873e0ad3e7d60e7157f76096c6ac65085bee3` on `master`.
- Architecture spine raw SHA-256: `321f04a7d39b5ef4197295c7ce52890baadc836754b771527b29a1e08f604a5e`.
- Analysis plan raw SHA-256: `6d8a024c02378478b6372b73a2514eac1c011ac263bc85c3b52687a6a30aef14`.
- Pre-existing unrelated untracked paths were `skills/reports/adp-operational-artifacts-panel-refresh-plan.md` and `skills/reports/eval-runs/`; they were not modified.
- This reviewer changed no normative artifact, fixture, runner, or production file. It created only this review.

## Critical Findings

None.

## High Findings

None.

## Medium Findings

### M1 - The brownfield diagnosis is reproducible at this HEAD but has no frozen evidence manifest

The spine lists nine production scripts as diagnosis sources (`ARCHITECTURE-SPINE.md:21-30`), and the plan makes exact source-line claims against those files (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:71-124`). The completion gate nevertheless freezes only test counts and artifact checks in prose (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:533`). The runtime registry's source-pin list begins at `contracts/CONTRACT-REGISTRY.json:100`; it correctly pins contract inputs, but it does not pin eight of these nine diagnostic production scripts. The separately registered Panel v1 composer is the exception (`contracts/CONTRACT-REGISTRY.json:162`).

Current diagnosis-source hashes are:

| Source | Raw SHA-256 |
| --- | --- |
| `skills/adp-meeting-sync/scripts/sync_meeting.py` | `435c0ca4e9262bbce7d753e0a295a74fa03e15a9840f855e0e5926cb5ba77c0d` |
| `skills/adp-status-sync/scripts/sync_status.py` | `372b0b9160d9c7771d519273549d2ace247f8c17012d27fb7e86ba2fcff171ad` |
| `skills/adp-bmm-checkpoint-sync/scripts/sync_bmm_checkpoint.py` | `f9d5c471043c1657a76f9fabc6f0e86f7253fd11fc5d142471691c5408ab6804` |
| `skills/adp-risk-dependency-change-review/scripts/review_risk_dependency_change.py` | `596a7012947c985551138a352cc9a889870c48075f5d988d2ea87248dded1393` |
| `skills/adp-workstream-register/scripts/register_workstream.py` | `67e07457138157e5417d53365ad8bcd543aa3d33d81a16e386f64477fdec1d68` |
| `skills/adp-state-audit/scripts/audit_state.py` | `70df59e42ca3094585dd79574d519c4c09e181c312bd1fe1861c9a94f4b77b74` |
| `skills/adp-state-audit/scripts/panel_audit.py` | `7fc3e6e3e460e1d5eb34fd0180639abfb2ba57ca4f51e8db6eb4bc677d200f20` |
| `skills/adp-agent-program-lead/scripts/adp-state-prepass.py` | `28701301eec0330c81d3c1f30bf36e15dc05b1987a3f2dc58a7d79246dd5ede5` |
| `skills/adp-management-panel/scripts/management_panel.py` | `f70657ffa15dc833ac2ab2195ecc3ea7042da14bfa1842aa268ffc114c6d8171` |

**Risk:** after a production edit, every normative pin and design vector can stay green while the document's current-state diagnosis silently becomes stale.

**Recommended fix:** add a non-normative brownfield evidence manifest containing the reviewed commit, these source hashes, exact regression commands, interpreter version, and observed counts. Regenerate it whenever the production baseline changes; keep it out of runtime contract negotiation.

### M2 - The Python 3.12 floor is current and testable, but release evidence cannot prove it

The stack binds Python `>=3.12` and says a production receipt must record the exact interpreter/build (`ARCHITECTURE-SPINE.md:202-209`). The local review ran on Python 3.12.13, and Python 3.12 remains in security support through October 2028 according to the official Python Developer's Guide.

The production receipt schema does not carry an interpreter field: `conformanceResultV1` allows only generic `host_platform`, implementation/version, and `adapter_build_id` (`contracts/panel-sync-contracts.schema.json:1829-1851`), while the implementation-conformance branch requires only build ID and evidence classes (`contracts/panel-sync-contracts.schema.json:1853-1863`). Because `additionalProperties` is false, a conforming receipt cannot add a typed interpreter/version record. The release gate therefore cannot reject a native receipt produced on Python 3.10. That gap is visible in deployed metadata: affected scripts still declare `requires-python = ">=3.10"` (for example `skills/adp-meeting-sync/scripts/sync_meeting.py:3` and `skills/adp-management-panel/scripts/management_panel.py:3`), and normal cross-platform CI still selects Python 3.10 (`.github/workflows/adp-meeting-sync.yml:17-29`; `.github/workflows/adp-management-panel.yml:29-41`). P0-A does not name the metadata/CI transition (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:414-423`).

**Risk:** implementation conformance could be accepted and strict mode enabled without evidence that the runtime satisfies the architecture's declared Python floor.

**Recommended fix:** add a typed runtime/interpreter record to `conformanceResultV1`, bind it into `result_id`, and require Python `>=3.12` in the release validator. Make production metadata and normal Windows/macOS/Linux CI use the same floor before changing registry status to `passed`.

## Low Findings

None.

## Five Brownfield Limitations

All five reported limitations remain observable at the reviewed HEAD, and the analysis cites the right ownership boundary in each case.

| Reported limitation | Current-code evidence | Reality verdict |
| --- | --- | --- |
| Meeting sync cannot mutate an existing action owner/status | The meeting plan action item has no operation, exact `action_id`, or revision (`skills/adp-meeting-sync/references/sync-plan-schema.md:42-59`). `build_status_sync_intake()` emits create-shaped fields without an action ID (`skills/adp-meeting-sync/scripts/sync_meeting.py:1371-1381`). Status sync can look up and merge exact IDs (`skills/adp-status-sync/scripts/sync_status.py:840-846`, `:907-920`), so the break is correctly located at the meeting contract. | Confirmed |
| `wdr_update` does not update Panel current fields | Meeting sync appends to each WDR (`skills/adp-meeting-sync/scripts/sync_meeting.py:812-821`) and renders a `Meeting Sync Update` block (`:1244-1264`). It does not issue a Project Status field mutation. | Confirmed |
| Panel inspection does not prove current live facts | `inspect_current()` checks embedded model/manifest, immutable bundle, resources, previews, and artifact audit (`skills/adp-management-panel/scripts/management_panel.py:1120-1156`) but does not reread WDR/ledger leaves or compare them to generation fingerprints. | Confirmed |
| WDR/ledger drift detection is incomplete | Prepass compares action ID sets only (`skills/adp-agent-program-lead/scripts/adp-state-prepass.py:911-958`) and skips the check when the global active ledger list is empty (`:1154`). State audit only converts those ID-set differences into findings (`skills/adp-state-audit/scripts/audit_state.py:2282-2303`); owner/text/due/content drift is not proven. | Confirmed |
| Canonical audit output loses exact action IDs | Action IDs contribute to identity (`skills/adp-state-audit/scripts/audit_state.py:3001-3014`), but `canonical_finding()` does not copy `action_id`/`action_ids` into the public object (`:2951-2998`), even though formatting later tries to read them (`:3341-3362`). | Confirmed |

The target architecture closes these gaps without claiming they are deployed: AD-2 defines exact action patch identity, AD-3 owns typed WDR current mutations, AD-4 binds freshness to live source bytes, AD-5 reconstructs full drift, and AD-7 retains typed action IDs through repair.

## Action Ledger Preservation

The production status writer defines exactly 20 action columns, including lifecycle, baseline, and relation fields (`skills/adp-status-sync/scripts/sync_status.py:69-90`). The target protocol retains those 20 columns and appends only `Action Revision` as column 21. Both reference runners exercise absent, legacy 12-column, and brownfield 20-column bootstrap; `bootstrap-legacy20-all-fields-preserved` passed in both fixed-time runs. Action mutation vectors also cover stale evidence, lifecycle chronology, terminal reopen, field rebound, missing ledger state/action-flow targets, and extra targets.

No evidence was found that the design truncates a brownfield 20-column row or overloads `Baseline Revision` as the action revision.

## Contract and Raw-Artifact Reality

| Artifact | Observed raw SHA-256 | Binding result |
| --- | --- | --- |
| `contracts/CONTRACT-REGISTRY.json` | `55764209eb0d5806299607caed280bf6009c22040897c8790ce5203663fd3824` | Matches spine and both results |
| `contracts/panel-sync-contracts.schema.json` | `dd8a1af940c04c7044bebbfd07822fb7b0656c3dad112fcc40aa8f29e5860a94` | Matches registry and spine |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `b81bd3327626eb4ecae79b11b175b40e77c45f60827f082fd3decd9a473990e3` | Matches registry release-gate pin and spine |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `1cd33eb6f3b24fd5ac6af8f14116ce81b287ce564cbe366bed3cc667595134cc` | Matches registry and spine |
| `contracts/fixtures/PANEL-V1-COMPATIBILITY.json` | `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` | Matches pin, spine, and regeneration |
| `contracts/conformance/python_runner.py` | `f4418808a245ed5723775d867f0ddaf724ab304ea743fa2cfb24a98ab8ec1281` | Matches registry and spine |
| `contracts/conformance/node_runner.mjs` | `1ba4d5d3cda04365543e08710993641e8d5da766e0dce191dfc81cdbfe2c5ca0` | Matches registry and spine |
| `contracts/conformance/panel_v2_consumer.mjs` | `eaf497fbf79eaedface1408b1ea1679aec7d7e85709fd9e3a386550855bbc423` | Matches source pin and spine |
| `contracts/conformance/python-result.json` | `6559e87c6fa7ebedbda0661c8dc49995b3ab3a48dd983b109cfda60ed739e13a` | Fixed-time replay byte-for-byte equal |
| `contracts/conformance/node-result.json` | `8ab196d8e4ce95146ad2592f89464530780417351b9501c24acfc95330c26d9f` | Fixed-time replay byte-for-byte equal |

All 14 registered source hashes match current raw files. Independently observed registry counts are 47 contracts, 14 pins, 9 enumerators, 7 input profiles, 7 outer payload bindings, 4 nested bindings, 6 Panel bindings, 15 DAG edges, 43 canonical array-ordering rules, 14 identity-set rules, 3 semantic sequence rules, 17 runtime paths, and 13 semantic validators. These equal `ARCHITECTURE-SPINE.md:139-143`, `WDR-AND-TRANSACTION-PROTOCOL.md:100`, and `ANALYSIS-AND-OPTIMIZATION-PLAN.md:423`.

## Target Architecture Versus Deployed State

- Both deliverables remain `status: draft` (`ARCHITECTURE-SPINE.md:8`; `ANALYSIS-AND-OPTIMIZATION-PLAN.md:3`).
- Target modules `skills/adp-fact-transaction`, `skills/adp-wdr-mutation`, and `skills/adp-panel-refresh` do not exist. Their appearance under Structural Seed (`ARCHITECTURE-SPINE.md:213-227`) is therefore correctly read as implementation shape, not current deployment.
- The v2 current consumer exists only under the architecture conformance package. Production Management Panel remains v1.
- Registry implementation status is exactly `pending` (`contracts/CONTRACT-REGISTRY.json:68`; `contracts/fixtures/CONFORMANCE-VECTORS.json:8`).
- Checked-in results are explicitly `design-fixture-check`, run design platforms, and set native durability false (`contracts/conformance/python-result.json:3-8`; `contracts/conformance/node-result.json:3-8`).
- AD-12 explicitly prevents strict production publication before implementation conformance and writer-fence activation (`ARCHITECTURE-SPINE.md:145-149`). Protocol section 9 repeats that the package itself does not authorize strict production (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:99-101`).

The separation is unambiguous: the package is a detailed build substrate and acceptance model, not evidence that meeting/status/WDR/Panel synchronization has already been implemented.

## Named Technology Currentness

| Technology / standard | Official-source check on 2026-07-25 | Verdict |
| --- | --- | --- |
| Python `>=3.12` | [Python Developer's Guide](https://devguide.python.org/versions/) lists Python 3.12 in security support through 2028-10. Local verification used 3.12.13. | Current and fit; enforcement gap is M2 |
| JSON Schema Draft 2020-12 | [JSON Schema specification](https://json-schema.org/specification) continues to identify Draft 2020-12 as the current published specification. All nine in-scope schemas parse and declare its official meta-schema URI. | Current and fit |
| RFC 8785 JCS | [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html) defines the JSON Canonicalization Scheme used by the package. Cross-language known-answer vectors passed. | Fit |
| RFC 6901 JSON Pointer | [RFC 6901](https://www.rfc-editor.org/rfc/rfc6901.html) remains the Standards Track JSON Pointer definition. Root-pointer and binding vectors passed. | Fit |
| RFC 3339 timestamps | [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339.html) remains the Standards Track Internet date/time profile. The protocol intentionally narrows durable output to UTC whole seconds. | Fit |
| BCP 14 | The protocol cites both RFC 2119 and RFC 8174 and limits normative meaning to uppercase terms (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:3`). | Correct |
| CQRS-style materialized projections | This is an architectural paradigm, not a pinned dependency. It matches the target single-writer facts plus immutable read-model direction and explicitly rejects event sourcing (`ARCHITECTURE-SPINE.md:41-43`). | Fit |

## Verification Evidence

- Architecture lint: **PASS, 0 findings**.
- Python/Node syntax checks: **PASS** for both runners and the pinned v2 consumer.
- Fixed-time Python replay: **413 passed / 0 failed**; byte-for-byte equal to checked-in `python-result.json`.
- Fixed-time Node replay: **413 passed / 0 failed**; byte-for-byte equal to checked-in `node-result.json`.
- Python/Node passed-vector sets: **exactly equal**, with 413 unique IDs each.
- Result raw hashes: Python `6559e87c...e13a`; Node `8ab196d8...c26d9f`, matching the spine table.
- Panel v1 compatibility: regenerated byte-for-byte equal; SHA-256 `3b96b780...b6fe7`.
- Brownfield baseline: **205/205 passed**: meeting-sync 31, status-sync 29, state-audit 63, management-panel 28, panel-audit 12, state-prepass 10, panel-model 6, Panel contract 26.
- Additional Program Lead regressions: **17/17 passed**: consume-program-status 12 and render-program-views 5.
- Nine in-scope schema files parse as JSON and declare `https://json-schema.org/draft/2020-12/schema`.
- Registry source-pin verification: **14/14 raw hashes match**.
- Current deployment evidence remains deliberately incomplete: no native Windows conformance, no real POSIX fault-injection receipt, no production adapter receipt, no writer-fence migration, and no strict activation evidence.

## Gate Decision

**Reality/currentness gate passes with zero Critical and zero High findings.** The architecture can proceed through the remaining v14 reviewer lenses. Close M1 before relying on the diagnosis after production-source changes, and close M2 before changing implementation conformance from `pending` to `passed`.
