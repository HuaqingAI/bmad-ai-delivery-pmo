# ARCHITECTURE-SPINE Brownfield Reality / Currentness Review v11

## Verdict

**FAIL. Critical: 0. High: 6. Medium: 5. Low: 2.** The frozen v11 package is reproducible and is honest that its 242/242 receipts are design-fixture evidence, not production implementation. It closes the two v10 gaps at the level of executable examples: the pinned Node v2 current-field consumer is really executed, and the fact-attribution handler now accepts ordinary WDR status, meeting-history, owned-section, refresh-actions, and create commands. The proof remains false-green in six material ways: contract references are not bound to the frozen schema/registry hashes; WDR target bytes and CAS state are not derived from the command; the reference capability graph authorizes risk review to overwrite Roadmap despite exclusive status-sync ownership; physical inventory does not perform its declared content-schema checks; a lone non-NFC ordering identity is accepted; and numeric `apply_order` is ordered lexically, contradicting journals with ten or more targets.

These are contract/harness defects, not evidence that the deferred production design has already been deployed. Strict publication must remain disabled.

## Frozen Review Target

Repository state observed for the brownfield check: `HEAD=a5d873e0ad3e7d60e7157f76096c6ac65085bee3` (`master`). The only pre-existing worktree changes were unrelated untracked files under `skills/reports/`; this reviewer did not modify them.

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `65e19ae9ceab8e3301154363db01064be01b15203c7e39e61b06eed6b3196e2d` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `17f250f0a783ce77e9da7162b911b3f1b33ec9c483f67e9895cbb63f05f8a73d` |
| `contracts/CONTRACT-REGISTRY.json` | `51bb9f1d283f738cbb6a930d1947ad0066087252994cd3e76d58a2405fa4cf6f` |
| `contracts/panel-sync-contracts.schema.json` | `293caf12342777ae1d44c62bc50a9dff407e833f9a0a1bfaf13cda4d86881055` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `4941fc1b78a7962b6299d367903cfd206bcba178edbaca7dd2904cb62c44b15d` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `b8eed03e2c052ef995bb6db1d76fff7e059080a7f51df5630d3ab5eae4cd0ce1` |
| `contracts/fixtures/PANEL-V1-COMPATIBILITY.json` | `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` |
| `contracts/conformance/python_runner.py` | `ac5f3c32ef56b20ce7ff595858fde08b18a973c1eb321602aa18afcaddad47f9` |
| `contracts/conformance/node_runner.mjs` | `477368005c6e9ef9f4326c4ee06a94145cdeb5bde007b722ae209f81aa560581` |
| `contracts/conformance/panel_v2_consumer.mjs` | `56cdab9ea6e03289fa238419c6c1220fc921cdd37b69543e056632263d87afce` |
| `contracts/conformance/python-result.json` | `68d0fa89316e8960e8d8c6c1e0d0c63cd6effb416b9ddb55bc2098edd1da142b` |
| `contracts/conformance/node-result.json` | `943f5803eb42702e3a87604aa43daa1f17d64e81f7372625e0a8b6754a2d8bba` |

The normative package did not change during this review. This reviewer created only this review file.

## Critical Findings

None.

## High Findings

### H1 - Fact attribution accepts a command bound to fake schema and registry hashes

The registry calls itself the only wire truth and registers `fact-receipt-attribution/1.0.0` as an exact command/receipt transaction validator (`ARCHITECTURE-SPINE.md:139-143`; `CONTRACT-REGISTRY.json:508`). The schema, however, gives command `contract` fields only the generic `contractRef` shape (`panel-sync-contracts.schema.json:74-81,170-203,320-380`). `fact_attribution_semantics()` chooses WDR versus action only by whether `schema_id` ends with `#wdr-command-v1`, validates the selected shape, and fingerprints the supplied command; it never requires the exact registered schema ID or the current raw schema/registry hashes (`python_runner.py:597-602,780-817`).

An independent mutation changed an otherwise-valid WDR status command to:

```json
{"schema_id":"urn:fake#wdr-command-v1","schema_sha256":"sha256:ffff...ffff","registry_sha256":"sha256:eeee...eeee"}
```

After recomputing the command authorization graph through the runner's own `rebind_fact_graph()`, `fact_attribution_semantics()` returned `True`. The same omission applies to the capability registry, fact-generation states, receipt, journal, and marker contract references validated only by generic shape in this handler.

**Required fix:** every semantic handler must resolve each supplied contract reference through `CONTRACT-REGISTRY.json` and require exact schema ID, raw schema hash, and raw registry hash before deriving any identity. Add fake-anchor, fake-schema-hash, and fake-registry-hash negatives for each document kind in the complete fact graph.

### H2 - Ordinary WDR attribution binds target paths, not command-derived WDR state or bytes

v11 now creates complete-looking graphs for WDR status, meeting history, checkpoint owned section, refresh-actions, and create (`python_runner.py:633-748,2260-2265`; receipt vectors in `CONFORMANCE-VECTORS.json:486-510`). That closes v10's missing command-kind dispatch, but not exact effects. `expected_fact_business_paths()` derives only filenames (`python_runner.py:623-630`). The semantic validator compares the receipt targets to the journal and checks those paths, but never parses the before WDR state, compares `expected_wdr_revision`/`expected_file_generation`, runs the WDR renderer, or derives the business targets' before/after hashes from bytes (`python_runner.py:821-850`).

An independent substitution changed the first WDR business target's `after_sha256` to `sha256:ffff...ffff`, copied the altered target into the receipt, and recomputed receipt, journal, and marker identities. The unchanged command fingerprint remained `sha256:3e89264e...58ce00c`; `fact_attribution_semantics()` still returned `True`. A stale expected WDR revision or arbitrary post-image can therefore obtain a green receipt graph as long as the same assertion is echoed consistently.

**Required fix:** supply and schema-validate exact before/after WDR file state and WDR/action-sidecar bytes, verify the command's expected revisions against the before state, execute the pinned renderer, and require journal/receipt hashes to equal those derived bytes. Add stale-WDR-revision, stale-file-generation, before-byte substitution, and after-byte substitution negatives for every ordinary WDR operation.

### H3 - The reference capability graph permits risk review to overwrite Roadmap directly

The spine and plan make `adp-status-sync` the exclusive Roadmap and WDR-current-field owner; risk review may emit status intent and directly own only risk-flow/decision facts (`ARCHITECTURE-SPINE.md:81-83`; `ANALYSIS-AND-OPTIMIZATION-PLAN.md:28-30`). The fact fixture instead grants `adp-risk-dependency-change-review` `owned_sections` permission for both `decisions-evidence` and `roadmap` (`python_runner.py:665-694`).

An independent graph changed the checkpoint owned-section command to issuer `adp-risk-dependency-change-review` and section `roadmap`, then used the runner's own rebinding function. `fact_attribution_semantics()` returned `True` for a direct `replace` containing `Risk-owned roadmap overwrite`. No positive/negative vector exercises this risk command; the handler's required graph set covers checkpoint but not risk (`python_runner.py:2264-2265`).

**Required fix:** remove Roadmap WDR permission from the risk capability, represent risk-owned decision facts as their own exact business targets, and add a negative proving risk review cannot issue a WDR Roadmap/current-field command plus a positive showing its typed status intent is re-authorized by status-sync.

### H4 - Physical inventory does not perform its declared WDR or sidecar schema validation

The registered algorithm and AD-4 require a schema-valid WDR plus exact schema-valid `action-projection.json` pair before the physical inventory can define `all` (`CONTRACT-REGISTRY.json:496`; `ARCHITECTURE-SPINE.md:99-101`; protocol `WDR-AND-TRANSACTION-PROTOCOL.md:61`). The executable enumerator checks regular/readable files, extracts one Markdown `Workstream ID`, parses sidecar JSON, and compares only `sidecar_value.workstream_id`; it never validates the WDR structure/template contract or the registered `wdrActionProjectionV1` schema (`python_runner.py:1096-1139`; schema anchor at `panel-sync-contracts.schema.json:534`).

An independent temporary tree containing only:

```text
# invalid except identity
- Workstream ID: l1-bad
```

and `{"workstream_id":"l1-bad"}` was accepted as a complete physical inventory row. Because selection-policy schema validates the row metadata rather than the referenced blob contents, this can define `all` and feed publication hashes despite invalid physical facts.

**Required fix:** validate the sidecar against `wdrActionProjectionV1`, validate WDR bytes against a registered parser/template contract including required sections and identity, and bind those validation results to the same single reads used for hashes/blobs. Add invalid-WDR and invalid-sidecar content negatives.

### H5 - Canonical ordering accepts a lone non-NFC identity scalar

AD-11 says non-NFC identity scalars and normalized collisions are rejected (`ARCHITECTURE-SPINE.md:141-143`). Identity-set arrays implement both checks (`python_runner.py:1396-1432`), but the 26 canonical-array rules use `ordering_component()`, which silently normalizes a value to NFC for its sort key without comparing the original scalar to the normalized value (`python_runner.py:1229-1238,1360-1393`). The only ordering negative is a two-value NFC collision; there is no lone non-NFC ordering-key vector (`CONFORMANCE-VECTORS.json:430-438`).

An independent injection replaced the second state-audit `source_preview.path` with the single decomposed value `e\u0301.md`. The complete document remained schema-valid and `all_ordering_rules_semantics(..., "none")` returned `True`, even though the value was not NFC.

**Required fix:** reject any non-null string key component whose bytes differ from NFC before sorting, across every canonical-array rule. Add a lone non-NFC negative for scalar and composite keys, not only a normalized-collision case.

### H6 - Generic array ordering sorts integer `apply_order` lexically and conflicts with valid large journals

The registry declares `/targets` ordered by `apply_order` (`CONTRACT-REGISTRY.json:536`). The generic ordering code converts every key component to `str(value)` and compares NFC UTF-8 bytes (`python_runner.py:1229-1238`). Therefore numeric `[0,1,2,...,10]`, which journal semantics correctly requires as contiguous apply order (`python_runner.py:550-558`), is rejected by the generic rule because it expects `[0,1,10,2,...,9]`.

This is not theoretical: a Panel publication fixture can carry eight projection/panel outputs plus pointer, state, and receipt, producing eleven targets. The representative ordering document uses a smaller repair journal, so all 242 vectors miss the contradiction (`python_runner.py:1290,1360-1393`). Both Python and Node use the same string coercion.

**Required fix:** make ordering key types explicit in the registry and compare integer keys numerically. Execute the ordering validator against an 11+ target schema-valid journal and require the same `[0..n-1]` order as `transaction-journal-semantics`.

## Medium Findings

### M1 - The physical enumerator's workstream-ID grammar diverges from the schema

`workstreamId` permits lowercase alphanumeric/hyphen and explicitly excludes `program` (`panel-sync-contracts.schema.json:34-42`). The enumerator accepts `[A-Za-z0-9][A-Za-z0-9._-]*`, including `Program`, `program`, `l1_bad`, and `l1.bad` (`python_runner.py:1080-1083,1108-1110`). Selection-policy schema eventually rejects these rows, but the registered physical-enumerator claim and its standalone semantic vector are false. Reuse the schema definition or one shared validator.

### M2 - Inventory vectors omit most semantics named by the registered algorithm

The five inventory vectors cover a valid tree, WDR-only, sidecar-only, duplicated returned row, and empty inventory (`CONFORMANCE-VECTORS.json:421-425`). They do not exercise hidden artifacts, nested artifacts, symlink/reparse points, unreadability, non-UTF-8 WDR, invalid JSON, content-identity mismatch, invalid workstream grammar, non-NFC names, or normalization/case collision, despite all being named in the registry/protocol. Add actual filesystem mutations for each named branch.

### M3 - The nine scripts used for brownfield reality are not frozen by registry hashes

The spine names nine current-code sources (`ARCHITECTURE-SPINE.md:21-30`), but the 13 registered source pins cover templates, schemas, a fixture, `panel_model.py`, and the v2 reference consumer, not those production scripts (`CONTRACT-REGISTRY.json:62-143`). Harness startup can stay green after a behavior script changes. This review records their current raw hashes below, but the package should bind a reviewed commit or raw source manifest if its brownfield claims are intended to remain reproducible.

### M4 - `forbidden_source_prefix` is inspected, not executable access control

The current pinned consumer really reads only `panel.sync.canonical.status.workstream_current` and contains no `model_v1` reference (`panel_v2_consumer.mjs:21-48`). The harness proves observable legacy independence by mutating `model_v1` and comparing output (`python_runner.py:2804-2829`). It does not instrument property reads or otherwise enforce registry `forbidden_source_prefix`; a future consumer could read legacy state without changing these four outputs. Use an instrumented/proxied input or a constrained input document containing only the allowed pointer.

### M5 - The complete fact-attribution corpus omits action create and risk-owned fact commits

The registered handler's base graph set executes one action patch plus five WDR shapes (`python_runner.py:2260-2265`). It does not execute a complete action-create receipt (`null -> 1`) or a non-WDR risk decision/risk-flow fact commit, although AD-1 covers both. Add full positive and substitution negatives for those branches; schema-only vectors are not attribution evidence.

## Low Findings

### L1 - Both design receipts execute the same JS v2 consumer artifact

Python launches `node panel_v2_consumer.mjs`; Node launches the same file (`python_runner.py:1931-1942`; `node_runner.mjs:1258-1266`). This is acceptable for a pinned known-answer consumer and the receipts do not claim implementation independence, but the two 242/242 results are not two independent implementations of that consumer behavior.

### L2 - “Browser boundary” is stronger wording than the executable artifact proves

The artifact is a Node stdin/stdout renderer producing an HTML fragment (`panel_v2_consumer.mjs:51-54`), not an integrated browser/DOM consumer. The current checked-in Panel still embeds only v1 model/manifest/previews and `panel.js` reads `model.data.status` (`panel-template.html:45-50`; `panel.js:4-10,127,204-215,809-840`). Because the package keeps production conformance pending this is not a production overclaim, but call it a reference current-view renderer until a production adapter wires and observes the published view.

## Brownfield Reality Check

All five named current-production behaviors remain accurate at reviewed `HEAD`:

| Claim | Current code evidence | Result |
| --- | --- | --- |
| Meeting action handoff is create-shaped | The pinned v1 item has no `operation`, `action_id`, or revision (`sync-plan-schema.md:40-80`); normalization drops unknown identity (`sync_meeting.py:274-304`); intake creates owner/action/source/due/status rows without exact existing ID (`sync_meeting.py:1301-1403`). Status-sync separately supports exact ID lookup (`sync_status.py:544-567,840-846`), while default status remains `open` and merge writes it unconditionally (`sync_status.py:103-105,907-925`). | Confirmed |
| `wdr_update` appends history, not current fields | Meeting sync appends `render_wdr_block()` to the record (`sync_meeting.py:812-821,1244-1264`); intake processes only action items plus milestones (`sync_meeting.py:1301-1303,1405-1424`). Current fields are changed in status-sync's Project Status writer (`sync_status.py:1458-1545,1616-1619`). | Confirmed |
| Panel inspect does not re-read live WDR/ledger leaves | Artifact audit resolves projection bytes/identity (`management_panel.py:327-394`); `inspect_current()` verifies embedded manifest, bundle, and resource identities (`management_panel.py:1120-1173`). Panel audit validates source-fingerprint shape and generated age, not live source equality (`panel_audit.py:143-183,344-378,489-505`). | Confirmed |
| WDR/ledger drift is partial and misses empty ledger | Prepass compares ID sets only (`adp-state-prepass.py:911-958`) and invokes the check only when `ledger_actions` is nonempty (`adp-state-prepass.py:1127-1154`); state-audit turns only those set differences into findings (`audit_state.py:2267-2303`). | Confirmed |
| Canonical findings omit public action IDs | Raw action items carry `action_id` (`audit_state.py:3530-3541`) and identity details include it (`audit_state.py:3001-3014`), but `canonical_finding()` does not copy `action_id`/`action_ids` into the returned finding (`audit_state.py:2951-2998`). | Confirmed |

Current raw hashes for the nine named behavior sources:

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

## Registered Pin Audit

All 13 registered source pins match current raw bytes:

| Pin | Raw SHA-256 | Result |
| --- | --- | --- |
| `workstream-delivery-record/1.0.0` | `ae36419be7d9d6b7239d943c459c27bbf990cb2a0ed8f1b1c6fb89277253ccf4` | PASS |
| `meeting-sync-plan/1.x` | `99f7e5268c6549d47a28f641e645158e3303ff9d9e8682f32cbc603ae00d6226` | PASS |
| `status-sync-batch/1.x` | `ef1a00ae918c6393d14c8e12d0228656a29ba00b51e8953c1078ccc71be73c4b` | PASS |
| `action-ledger-template/1.0.0` | `9048db09d7dc37f473c9032e22b60c9eb2ee1f8c11fe097d129545e27f2eb722` | PASS |
| `management-panel-model/1.0.0` | `b607f0c142e22c247726db002ce0a305f744d7c367b2c65e1ef8074dec9f72f1` | PASS |
| `management-panel-manifest/1.0.0` | `3a0e5a436d87316da23d76bf1a565d88723e18e511f7aeaa8f55b4ef878770f0` | PASS |
| `flow-graph-payload/1.0.0` | `aee7bc26ebfd6d2b475cdddc8f005f149ea1eea0506155e7790ab688f72a3a04` | PASS |
| `program-status-progress/3.0.0` | `d8feaf0766e8e5881fc69c4a23d85cde08a73c465c01f73e413fe9602a04436a` | PASS |
| `program-status-flow-state/1.0.0` | `46a146b12319fb71554d01076570530042d98d70ea79385e801acfdddf465a38` | PASS |
| `program-status-progress/3.0.0-golden` | `106134e6316767e3a7e487276fa8c8ab6b57da3b6e3bdd5b7bbbfc2151ecd998` | PASS |
| `management-panel-v1-compatibility/1.0.0` | `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` | PASS |
| `management-panel-composer/1.0.9` | `59f804c9264b754223e451700172a085116e8adc5fd2079fe3d4b34662d4edf7` | PASS |
| `management-panel-v2-current-consumer/1.0.0` | `56cdab9ea6e03289fa238419c6c1220fc921cdd37b69543e056632263d87afce` | PASS |

## Verification Evidence

- Architecture lint: **PASS, 0 findings** using `python3 .agents/skills/bmad-architecture/scripts/lint_spine.py` (`uv` is not installed in this environment, so the script was run directly).
- Registry startup/pins: **PASS** in both reference runners for 41 contracts, 13 source pins, 9 enumerators, 7 profiles/payload bindings, 4 nested bindings, 6 Panel bindings, 15 DAG edges, 26 canonical-array rules, 8 identity-set rules, 8 runtime paths, and 8 semantic validators.
- Panel v1 compatibility regeneration: **PASS**, byte-for-byte identical; SHA-256 `3b96b780...2b6fe7`.
- Suite accounting: **242 vector rows / 242 unique IDs**. Both stored receipts contain exactly that set and zero failures.
- Python receipt reproduction: **242 passed / 0 failed**, byte-for-byte identical at fixed `executed_at=2026-07-24T21:20:00Z`; file SHA-256 `68d0fa89...da142b`; result ID `sha256:149a855e3572a7ece40532060c159d6bb384a21501819571963e2a6e486d9b02`.
- Node receipt reproduction: **242 passed / 0 failed**, byte-for-byte identical at the same timestamp; file SHA-256 `943f5803...d8bba`; result ID `sha256:c648470765c9fff479ab53a2ffa11f3832a14e4736ba89645305007028871cf0`.
- Brownfield regression baseline: **205/205 passed**: meeting-sync 25 + DingTalk intake 6, status-sync 29, state-audit 63, management-panel 28, panel-audit 12, state-prepass 10, panel-model 6, Panel contract 26.
- Additional program-lead regression set: **17/17 passed**: consume-program-status 12, render-program-views 5.
- Independent JCS cross-check: 20,000 deterministic random finite, non-unsafe-integral IEEE-754 values produced identical Python canonical bytes and Node `JSON.stringify` number spellings; the integer maximum vectors also pass. The defect found is array key typing/NFC enforcement, not the tested number serializer.

## Evidence Boundary

The package does **not** overclaim production implementation. Both main documents remain `status: draft`; registry `conformance_suite.implementation_conformance_status` is `pending` (`CONTRACT-REGISTRY.json:25-39`). Both checked-in receipts say `evidence_kind=design-fixture-check`, use `posix-design-model`/`windows-design-model`, and set `native_durability_exercised=false`. The release gate requires exact current hashes and vector set, distinct implementation/build IDs, native POSIX plus real fault injection, native Windows CI, and `production-adapter` evidence (`python_runner.py:325-353`; schema `panel-sync-contracts.schema.json:1534-1571`).

The production Panel remains v1-only, and the five diagnosed brownfield limitations remain present by design. The pinned v2 consumer is an executable reference contract, not deployed Panel code. The six High findings mean the design-fixture suite is not yet a reliable release oracle even after future production adapters exist; repair them and regenerate the hash/receipt chain before treating 242/242 as sufficient conformance evidence.
