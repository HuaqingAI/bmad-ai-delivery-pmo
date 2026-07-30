# ARCHITECTURE-SPINE Brownfield Reality / Currentness Review v13

## Verdict

**PASS WITH FOLLOW-UPS. Critical: 0. High: 0. Medium: 2. Low: 0.** The architecture and companion plan accurately describe the current brownfield failure modes, preserve the status writer's full 20-column action ledger before appending `Action Revision` as column 21, and keep the target runtime separate from what is deployed today. The registry inventory, raw hashes, all 13 source pins, the 346-vector fixed-time replay, Panel v1 compatibility regeneration, and 222 brownfield regression tests all reproduce the package's claims.

The remaining findings concern reproducible freezing of the production evidence baseline and explicit enforcement of the new Python 3.12 floor during rollout. Neither makes a current architectural decision false or permits strict publication before production conformance.

## Frozen Review Target

- Review date: 2026-07-25 (Asia/Shanghai).
- Repository: `HEAD=a5d873e0ad3e7d60e7157f76096c6ac65085bee3` on `master`.
- Architecture spine raw SHA-256: `635cf3b69d870e60410ca1c81ce6a3c0861ed3a3e38beb85e05f9ae939e55c4f`.
- Analysis plan raw SHA-256: `c76fd1fe020e1772955af408d9ed178544c1a8a92652dd12e161c6989e2ea019`.
- Protocol raw SHA-256: `4b673c381701cae56a5f8008d1742eb2881f6b40eb25b674f311ee07b7244849`.
- Pre-existing unrelated untracked paths were `skills/reports/adp-operational-artifacts-panel-refresh-plan.md` and `skills/reports/eval-runs/`; they were not modified.
- This reviewer changed no normative artifact, runner, fixture, or production file. It created only this review.

## Critical Findings

None.

## High Findings

None.

## Medium Findings

### M1 - The production behavior baseline is verified but is still not frozen by a review manifest

The spine names nine production scripts as brownfield sources (`ARCHITECTURE-SPINE.md:21-30`), and the analysis makes exact claims against them (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:73-124`). Those claims are correct at the reviewed HEAD, but the registry's 13 `pinned_source_artifacts` intentionally pin runtime contract inputs rather than these nine diagnosis sources. Only the separate Panel v1 composer is a pinned production Python source.

Current raw SHA-256 values are:

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

**Risk:** a later production edit can invalidate the diagnosis while every normative pin and all 346 design vectors remain green.

**Recommended fix:** add a non-normative `brownfield-evidence.json` (or equivalent) containing the reviewed commit, these source hashes, and the regression command/count ledger. Regenerate it whenever the production baseline changes; do not add these diagnosis-only pins to runtime contract negotiation.

### M2 - Python 3.12 is the correct target floor, but rollout does not explicitly enforce it in script metadata or CI

The spine now binds Python `>=3.12` and requires an exact interpreter/build in production conformance (`ARCHITECTURE-SPINE.md:202-209`). That policy is current and executable: the local baseline is Python 3.12.13, both reference and brownfield suites pass under it, and the Python Developer's Guide lists 3.12 in security support through October 2028.

The deployed brownfield scripts still declare PEP 723 `requires-python = ">=3.10"`, and both cross-platform workflows test only Python 3.10 (`.github/workflows/adp-meeting-sync.yml:17-29`; `.github/workflows/adp-management-panel.yml:29-41`). This is not a contradiction because the target runtime is explicitly not deployed, and `>=3.10` admits Python 3.12. However, P0 rollout does not specifically name the metadata/CI transition that will make the target floor enforceable.

**Risk:** an implementation can satisfy the architecture fixtures on 3.12 while leaving package metadata and normal CI able to bless unsupported 3.10 execution after the target is released.

**Recommended fix:** add a production-conformance prerequisite that updates affected PEP 723 declarations and normal CI to `>=3.12`, retains Windows/macOS/Linux coverage, and rejects a strict-mode release whose runtime metadata still admits a lower interpreter.

## Five Brownfield Limitations

All five reported limitations remain directly observable in current production code:

| Reported limitation | Current-code evidence | Verdict |
| --- | --- | --- |
| `meeting-sync` cannot mutate an existing action owner/status | Meeting plan action items have no operation, exact `action_id`, or revision (`sync-plan-schema.md:42-59`). `build_status_sync_intake()` emits only create-shaped action fields and no ID (`sync_meeting.py:1371-1381`). Status sync separately supports exact-ID mutation, so the break is at the meeting contract. | Confirmed |
| `wdr_update` appends history but does not update Panel current fields | Meeting sync calls `append_file()` with `render_wdr_block()` (`sync_meeting.py:812-821`), producing a `## Meeting Sync Update` block (`:1244-1264`). It does not issue a Project Status field mutation. | Confirmed |
| Panel inspection does not prove live facts were updated | `inspect_current()` verifies embedded model/manifest, immutable bundle, resources, and artifact audit (`management_panel.py:1120-1156`), but does not reread live WDR/ledger leaves or compare them with generation fingerprints. | Confirmed |
| WDR/ledger projection drift alert is incomplete | Prepass compares active action ID sets only (`adp-state-prepass.py:911-958`) and runs the cross-check only when the global active ledger set is non-empty (`:1154`). State audit turns only those ID differences into findings (`audit_state.py:2282-2303`); owner/text/due/content and empty-ledger closure are not proven. | Confirmed |
| Canonical audit results do not carry exact action IDs | `finding_identity_details()` hashes `action_id`/`action_ids` (`audit_state.py:3001-3014`), but `canonical_finding()` omits them from the public object (`:2951-2998`), while downstream formatting still tries to read them (`:3341-3362`). | Confirmed |

The target contracts close the corresponding boundaries without claiming that production is already fixed: AD-2 introduces exact action patch identity, AD-3 typed WDR mutation, AD-4 live-source lineage, AD-5 complete drift reconstruction, and AD-7 typed finding/action repair identity.

## Action Ledger Preservation

The ledger migration decision matches the actual brownfield shapes:

- The active status writer's `ACTION_FIELDS` contains exactly 20 columns (`sync_status.py:69-90`): the original 12 plus `Closure Criteria Verifiable`, lifecycle timestamps, baseline revision, and plan/flow relations.
- The kickoff asset remains the pinned 12-column legacy template. The architecture accurately treats 12 and 20 columns as ingress formats rather than silently relabeling the template as current canonical output.
- Protocol section 4 fixes all 20 status-writer columns in the same order and appends only `Action Revision` as column 21 (`WDR-AND-TRANSACTION-PROTOCOL.md:49-54`). It explicitly preserves lifecycle, baseline, and relation values during migration.
- Python and Node runners independently implement the same 21 columns, exact escaping, framing, parsing, sorting, create/patch lifecycle derivation, and state/flow rebound checks (`python_runner.py:143-240`; `node_runner.mjs:74-119`).
- Executable negatives cover owner/status/text/due/closure/routing/affected-workstream rebound, omitted target closure, terminal reopen, stale ledger fingerprint/revision, missing state/flow target, and extra target. Both runners passed the same vectors.

No evidence was found that the design truncates the 20-column brownfield row or overloads `Baseline Revision` as the new action revision.

## Contract and Raw-Artifact Reality

| Artifact | Observed raw SHA-256 | Binding result |
| --- | --- | --- |
| `contracts/CONTRACT-REGISTRY.json` | `85fb125836a77542a15ac04f8f21b281c22178f0c01ada69fbfcca6ed3b4e5aa` | Matches spine and runner results |
| `contracts/panel-sync-contracts.schema.json` | `7d09235d5c338b6874b588de6b9f4b00fa9c1bc74f4b7ad11c890e92ad9067ec` | Matches registry and spine |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `4b673c381701cae56a5f8008d1742eb2881f6b40eb25b674f311ee07b7244849` | Matches registry release gate and spine |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `4d4cde15226ad27a84c733e2187b59ea5c93c4b4d7121bbe8ec8d067dabc4e9b` | Matches registry and spine |
| `contracts/fixtures/PANEL-V1-COMPATIBILITY.json` | `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` | Matches source pin, spine, and regeneration |
| `contracts/conformance/python_runner.py` | `93b021fe731f1881aaa75bf43324102648bb97024266bfd05114ecc86b81378b` | Matches registry and spine |
| `contracts/conformance/node_runner.mjs` | `f927e59c1e97d742d37ce55b24c56df1b66f9df93c04704612b703f937f1f77a` | Matches registry and spine |
| `contracts/conformance/panel_v2_consumer.mjs` | `eaf497fbf79eaedface1408b1ea1679aec7d7e85709fd9e3a386550855bbc423` | Matches source pin and spine |
| `contracts/conformance/python-result.json` | `8edb013ac6c6515437068473fbb2418af99d0eb63b7c5dc51b152d6123187ea7` | Matches fixed-time replay byte for byte |
| `contracts/conformance/node-result.json` | `579785739cacf7ffc3698b3e40431b08c1979a016bc903d50640243d9ce4abdd` | Matches fixed-time replay byte for byte |

All 13 registered source artifact hashes match their current raw files. Independently observed registry counts are 44 contracts, 13 pins, 9 dependency enumerators, 7 input profiles, 7 outer payload bindings, 4 nested payload bindings, 6 Panel bindings, 15 DAG edges, 37 canonical array-ordering rules, 12 identity-set rules, 13 runtime paths, and 11 semantic validators. These match AD-11 and the analysis plan.

## Runtime and Deployment Boundary

- `ARCHITECTURE-SPINE.md` and `ANALYSIS-AND-OPTIMIZATION-PLAN.md` both remain `status: draft`.
- `skills/adp-fact-transaction`, `skills/adp-wdr-mutation`, and `skills/adp-panel-refresh` do not exist. They appear only in the target Structural Seed.
- The production Panel remains the v1 implementation. The v2 current consumer is located under the architecture conformance package and is pinned as a design artifact, not installed as deployed Panel code.
- Registry `implementation_conformance_status` remains `pending`.
- Both checked-in results declare `evidence_kind=design-fixture-check` and `native_durability_exercised=false`.
- AD-12 and protocol section 9 therefore correctly force strict open/inspect/publication to `migration-required`; the package does not claim native POSIX fault injection, native Windows CI, writer-fence migration, or production strict publication already exists.

## Named Technology Currentness

| Technology / standard | Currentness check on 2026-07-25 | Result |
| --- | --- | --- |
| Python `>=3.12` | The official Python Developer's Guide lists 3.12 in security support through October 2028. Local Python is 3.12.13 and all reviewed Python checks pass. | Current and fit; rollout enforcement is M2 |
| JSON Schema Draft 2020-12 | The JSON Schema specification site still identifies 2020-12 as the current version. All six package/production schemas parse and declare the 2020-12 meta-schema URI. | Current and fit |
| RFC 8785 JCS | RFC Editor identifies RFC 8785 as JSON Canonicalization Scheme; its deterministic ECMAScript serialization model fits cross-language content identity. | Exists and fit |
| RFC 6901 JSON Pointer | RFC Editor identifies RFC 6901 as the Standards Track JSON Pointer syntax. | Exists and fit |
| RFC 3339 timestamps | RFC Editor identifies RFC 3339 as the Internet timestamp profile; the protocol intentionally narrows output to UTC whole seconds. | Exists and fit |
| BCP 14 | The protocol now cites both RFC 2119 and RFC 8174 and limits normative meaning to uppercase keywords. | Current citation |
| POSIX/Windows durability profiles | These are target platform contracts, not versioned dependencies. Native evidence is explicitly deferred to the release gate. | Fit and correctly evidence-gated |
| CQRS-style materialized projections | This is an architectural paradigm rather than a dependency. It matches the target separation of fact mutation from immutable read models and explicitly rejects event sourcing. | Fit |

Official sources checked: `https://devguide.python.org/versions/`, `https://json-schema.org/specification`, and RFC Editor pages for RFC 3339, 6901, 8174, and 8785.

## Verification Evidence

- Architecture lint: **PASS, 0 findings** using Python 3.12.13.
- Python/Node syntax checks: **PASS** for both runners and the pinned v2 consumer.
- Fixed-time Python replay: **346 passed / 0 failed**, byte-for-byte equal to checked-in `python-result.json`.
- Fixed-time Node replay: **346 passed / 0 failed**, byte-for-byte equal to checked-in `node-result.json`.
- Python/Node passed-vector order and set: **exactly equal**, with 346 unique IDs each.
- Panel v1 compatibility fixture: regenerated byte-for-byte equal, SHA-256 `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7`.
- Brownfield baseline: **205/205 passed**: meeting-sync 31, status-sync 29, state-audit 63, management-panel 28, panel-audit 12, state-prepass 10, panel-model 6, Panel contract 26.
- Additional Program Lead regressions: **17/17 passed**: consume-program-status 12 and render-program-views 5.
- Six schema files parse as JSON and declare Draft 2020-12; their production contract suites and the 346-vector master suite pass.
- Source pin verification: **13/13 raw hashes match**.

## Gate Decision

**Reality/currentness gate passes with zero Critical and zero High findings.** The architecture may proceed through the remaining independent v13 reviewer lenses. M1 should be closed before relying on this diagnosis after production-source changes; M2 should be made an explicit production-conformance prerequisite.
