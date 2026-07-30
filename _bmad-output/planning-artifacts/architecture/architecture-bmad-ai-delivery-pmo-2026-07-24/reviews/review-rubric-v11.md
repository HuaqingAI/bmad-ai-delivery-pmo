# BMad Architecture Good-Spine Rubric Review v11

**Artifact:** `ARCHITECTURE-SPINE.md` and its normative companions
**Review lens:** independent BMad good-spine gate, adversarial executable-evidence review, and closure of the six v10 rubric/consistency findings
**Verdict:** **FAIL**
**Severity:** **0 Critical, 8 High, 5 Medium, 3 Low**

v11 is reproducible and materially stronger than v10. The safe-integer profile is now coherent, the registered semantic handlers are actually dispatched, the projection DAG performs identity changes and transitive recomputation, ordinary WDR fact graphs exist, and a pinned v2 current-field consumer is executed. Those closures are real. The package is still false-green at eight architecture boundaries: generic contract references can be rebound to fake hashes, WDR receipts do not bind command-derived effects, the reference capability graph violates ownership, physical inventory is neither content-validating nor joined to publication from a fresh root, two NFC promises are bypassed, and numeric journal ordering contradicts the generic ordering gate.

Production conformance remains explicitly `pending`. Native POSIX/Windows evidence, fault injection, two independent production adapters, and target-state rollout are intentional release gates and are **not** findings in this review.

## Frozen Review Target

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

The normative package was not modified by this review.

## Reproduced Gate Evidence

- Architecture spine lint: **PASS**, 0 findings.
- Python reference adapter: **242 passed / 0 failed**; regenerated receipt is byte-for-byte equal to the checked-in result when using its checked-in timestamp.
- Node reference adapter: **242 passed / 0 failed**; regenerated receipt is byte-for-byte equal to the checked-in result when using its checked-in timestamp.
- Panel v1 compatibility regeneration: **PASS**, byte-for-byte equal through the pinned brownfield composer.
- Brownfield regression suites: **205/205 passed**, including the 6 DingTalk intake tests and 26 Panel-contract tests; the additional Program Lead suite passed **17/17**.
- An independent Draft 2020-12 compile was not rerun because the local `jsonschema` package is unavailable. Both reference runners did validate the pinned bundles; this is an evidence limitation, not a defect finding.

## Critical Findings

None.

## High Findings

### H1 - Fact attribution accepts contract references that are not bound to the frozen registry and schema

The protocol requires contract/schema/registry raw hashes to be verified before parsing (`WDR-AND-TRANSACTION-PROTOCOL.md:8`), and AD-11 calls the registry the sole wire truth (`ARCHITECTURE-SPINE.md:139-143`). The schema's `contractRef` is only a structural object with arbitrary nonempty `schema_id` plus shaped hashes (`panel-sync-contracts.schema.json:74-82`). `command_kind()` selects WDR solely by an `endswith("#wdr-command-v1")` test (`python_runner.py:597-602`), while `fact_attribution_semantics()` shape-validates the selected command and hashes the supplied bytes without resolving their reference through the frozen registry (`python_runner.py:780-817`). The same generic-reference gap applies to the graph's capability registry, generation states, receipt, journal, and marker.

**Reproduction:** change an otherwise valid WDR command reference to `urn:fake#wdr-command-v1`, replace its schema and registry hashes with well-shaped fake SHA-256 values, then recompute the graph's capability/authorization/receipt identities through the runner's rebinding helper. `fact_attribution_semantics()` returns `True`.

**Required fix:** make every semantic handler resolve every supplied `contract` against `CONTRACT-REGISTRY.json` before shape validation or identity derivation. Require the exact registered schema ID/anchor, current raw schema hash, and current raw registry hash for every document kind. Add fake-anchor, fake-schema-hash, and fake-registry-hash negatives for each member of a complete fact graph in both runners.

### H2 - WDR attribution binds echoed paths and hashes, not the command's exact target identities, operation, CAS preimage, or rendered bytes

AD-1 requires one typed command to determine exact mutations and their receipt (`ARCHITECTURE-SPINE.md:81-84`); protocol section 4 requires receipt deltas and transaction targets to be deterministically derived from that command (`WDR-AND-TRANSACTION-PROTOCOL.md:51`). The fixture constructs every non-Panel business target as `replace` (`python_runner.py:501-518`). `expected_fact_business_paths()` derives only path strings (`python_runner.py:623-630`). The validator checks that receipt rows equal journal rows and that their paths match, but it does not require command-derived roots or operations, parse exact WDR before state, enforce `expected_wdr_revision`/`expected_file_generation`, run the pinned renderer, or derive before/after hashes from bytes (`python_runner.py:827-850`; Node mirror `node_runner.mjs:577-592`).

**Reproduction:** two independent coherent substitutions remain green. First, a schema-valid `operation=create` WDR graph with two `replace` business targets and non-null before hashes is accepted. Second, replacing every WDR business target's root UUID with a different valid UUID and consistently recomputing journal/receipt identities is accepted. An arbitrary substituted WDR `after_sha256`, echoed into journal and receipt and rehashed, is accepted for the same reason.

**Required fix:** define the exact business target set as `(root_instance_id,path,operation)` per WDR operation; provide exact before WDR/state/sidecar bytes; enforce both command CAS counters against those preimages; execute the pinned create/patch renderer; and require journal and receipt hashes to equal the derived before/after bytes. Add root substitution, create-as-replace, replace-as-create, stale revision, stale generation, before-byte substitution, and after-byte substitution negatives for every WDR command family.

### H3 - The reference capability registry authorizes risk review to overwrite Roadmap directly

The ownership rule is explicit: status-sync exclusively owns current fields and Roadmap, while risk review owns risk-flow and decision facts and may only emit status intent for shared fields (`ARCHITECTURE-SPINE.md:81-83`; `WDR-AND-TRANSACTION-PROTOCOL.md:19-20`). The fact fixture instead grants `adp-risk-dependency-change-review` `owned_sections` permission for both `decisions-evidence` and `roadmap` (`python_runner.py:665-694`). The validator faithfully enforces this incorrect graph, so the executable contract contradicts the normative ownership table.

**Reproduction:** start from the checkpoint owned-section graph, change issuer to `adp-risk-dependency-change-review` and section to `roadmap`, then use the runner's graph rebinding logic to recompute capability, command, journal, and receipt identities. `fact_attribution_semantics()` returns `True` for the direct Roadmap replacement.

**Required fix:** remove Roadmap from the risk capability. Model risk review's direct writes as exact risk-flow/decision-fact targets and route all Roadmap/current-field effects through a typed intent re-authorized by status-sync. Add a negative for a risk-issued Roadmap/current-field WDR command and a positive end-to-end intent-to-status-sync transaction.

### H4 - Physical inventory accepts content that is not a schema-valid WDR/sidecar pair

AD-4 and the registered inventory algorithm require schema-valid regular WDR and `action-projection.json` documents (`ARCHITECTURE-SPINE.md:99-101`; `CONTRACT-REGISTRY.json:496`; protocol `WDR-AND-TRANSACTION-PROTOCOL.md:61`). The enumerator checks the file shape, decodes the WDR, finds exactly one `Workstream ID` line, parses sidecar JSON, and compares `workstream_id`; it never validates the WDR grammar/template or the sidecar against `wdrActionProjectionV1` (`python_runner.py:1096-1139`).

**Reproduction:** a temporary physical tree containing only `# invalid except identity` plus `- Workstream ID: l1-bad` and sidecar `{"workstream_id":"l1-bad"}` is returned as a valid inventory row. Neither document satisfies the promised complete content contract.

**Required fix:** validate the sidecar against the exact registered action-projection schema and validate WDR bytes through a registered complete WDR parser/template contract, including required sections and unique identity. Bind validation to the same one-time byte reads used for fingerprint/blob creation. Add invalid-WDR, missing-required-section, invalid-sidecar-shape, and content-identity negatives.

### H5 - Publication eligibility is not joined to a fresh physical-root enumeration

The spine requires `physical-workstream-inventory-v1` to enumerate the locked root before selection and forbids the policy from shrinking that physical universe (`ARCHITECTURE-SPINE.md:99-101`; protocol `WDR-AND-TRANSACTION-PROTOCOL.md:61`). `publication_eligibility_semantics()` receives only a policy/generation graph and checks their internal equality (`python_runner.py:419-449`). The actual temp-tree enumeration is a separate helper whose result is discarded (`python_runner.py:1142-1164`), dispatched only by its standalone vector branch (`python_runner.py:2428-2430`). The `inventory-catalog-omission` mutation removes a catalog relation inside the policy fixture; it never tests omission from both policy and catalog relative to an independently enumerated root (`python_runner.py:2658-2661`).

**Reproduction:** independently enumerate a tree containing `l1-checkout` and `l1-payments`, then construct an otherwise complete lineage/publication graph whose policy inventory and catalog consistently contain only `l1-checkout`. Full `publication_eligibility_semantics()` still returns `True` because no physical-root result is an input.

**Required fix:** make a locked physical-root inventory or its independently verifiable attestation a required input to publication acceptance. Require exact bidirectional byte equality among fresh enumeration, policy inventory, workstream catalog, and catalog-relevant generation leaves before selection is resolved. Add full publication graphs for omission from both policy/catalog, extra rows, unpaired files, duplicate physical identity, and empty `all`.

### H6 - Repair graph acceptance bypasses the registered NFC identity-set rules

Protocol sections 1 and 8 require each stored identity-set scalar to be NFC and normalized-key collisions to reject (`WDR-AND-TRANSACTION-PROTOCOL.md:9-10,85`). The generic `identity_set_semantics()` correctly implements these checks (`python_runner.py:1396-1432`), and the vector suite mutates `authorization_scopes` there. But the real `repair_graph_semantics()` validates and hashes repair documents without invoking the registry identity-set rules or performing equivalent normalization checks (`python_runner.py:1629-1822`). The schema supplies raw `uniqueItems`, which cannot detect NFC-equivalent strings (`panel-sync-contracts.schema.json:1297-1309`).

**Reproduction:** after recomputing all dependent binding/batch/dry-run/nonce/receipt identities, `repair_graph_semantics()` accepts both a lone non-NFC scope `["repair:e\u0301"]` and a normalized collision pair `["repair:e\u0301","repair:\u00e9"]`.

**Required fix:** route every repair graph document through the registered identity-set validator before any digest calculation, or share one normalization-aware validation primitive used by all semantic handlers. Reject stored scalars that differ from NFC and duplicate normalized keys. Add complete blocked/applicable/committed/rolled-back graph negatives for both lone non-NFC and collision cases.

### H7 - Canonical-array ordering silently normalizes a lone non-NFC identity instead of rejecting it

AD-11 promises that non-NFC identity scalars reject (`ARCHITECTURE-SPINE.md:141-143`). `ordering_component()` instead normalizes `str(value)` to NFC and uses only the normalized bytes as a sort key (`python_runner.py:1229-1238`). The all-rules test rejects a two-row normalized collision, but never compares an original scalar to its NFC form (`python_runner.py:1360-1393`). The current corpus therefore proves collision handling, not stored-value normalization.

**Reproduction:** replace one `state-audit-payload` `source_preview.path` with the lone decomposed value `e\u0301.md`, retaining canonical list order and recomputing identities. `all_ordering_rules_semantics(..., "none")` returns `True`.

**Required fix:** before sorting, reject every non-null string ordering component whose original bytes differ from NFC. Preserve normalized-key duplicate rejection. Add lone non-NFC negatives for scalar and each composite-key position, in addition to the existing collision vector.

### H8 - Generic array ordering compares integer `apply_order` lexically and contradicts valid journals with ten or more targets

The registry orders transaction targets by numeric `apply_order` (`CONTRACT-REGISTRY.json:536`). Journal semantics correctly require `[0,1,...,n-1]` (`python_runner.py:550-558`). The generic ordering helper converts all values to strings before UTF-8 comparison (`python_runner.py:1229-1238`), so it orders `10` before `2`. A valid Panel journal can naturally exceed ten rows because it carries projection outputs, Panel, pointer, state, and receipt. The representative ordering fixture is smaller, hiding the contradiction.

**Reproduction:** construct a schema-valid journal with eleven contiguous targets and apply orders `[0,1,2,3,4,5,6,7,8,9,10]`. `journal_semantics()` accepts the numeric order, while the registered generic canonical-array rule rejects it and expects `[0,1,10,2,...,9]`.

**Required fix:** declare ordering component types in the registry and compare integer components numerically, with NFC UTF-8 ordering reserved for strings. Add an 11+ target journal known-answer and require both journal semantics and the generic ordering gate to accept the same contiguous sequence.

## Medium Findings

### M1 - DAG invalidation is projection-kind synthetic, not actual instance and leaf-input invalidation

The revised helper now changes a synthetic identity for each projection kind and performs transitive recomputation (`python_runner.py:948-1003`), which is a real improvement. It still creates one ID per kind, does not expand one-per-meeting-kind instances, does not mutate actual leaf identities, and does not connect invalidation to refresh receipts. AD-6 and the protocol require the actual instance set and leaf-identity-driven invalidation (`ARCHITECTURE-SPINE.md:111-114`; `WDR-AND-TRANSACTION-PROTOCOL.md:91`). Execute the DAG test over resolved producer instances and leaf manifests, including one meeting-kind-only mutation and exact dirty/recomputed receipt assertions.

### M2 - The full fact-attribution corpus omits action create and risk-owned fact transactions

The dispatched base set covers action patch plus WDR status, meeting history, checkpoint section, refresh-actions, and WDR create (`python_runner.py:2264-2265`). It does not execute a complete action create (`null -> 1`) or direct risk-flow/decision-fact commit. This leaves important AD-1 branches represented only by schemas or unrelated logic. Add full positive and target/revision/evidence substitution negatives for both branches.

### M3 - Physical-inventory vectors cover only a small subset of the registered algorithm

The five vectors cover valid, WDR-only, sidecar-only, duplicate returned row, and empty inventory (`CONFORMANCE-VECTORS.json:421-425`). They omit hidden, nested, unreadable, symlink/reparse, non-UTF-8, invalid JSON, content-identity mismatch, case alias, non-NFC name, and normalization collision branches named by the registry/protocol. Use real temporary filesystem mutations for every named failure mode.

### M4 - The v2 consumer corpus omits its main adversarial input classes

The four vectors cover base render, current-only visible change, legacy independence, and missing current field (`CONFORMANCE-VECTORS.json:581-585`). The consumer contract also promises rejection of duplicate/non-NFC rows and deterministic escaping (`WDR-AND-TRANSACTION-PROTOCOL.md:64`), but there are no explicit duplicate-row, lone non-NFC, NFC-collision, or HTML metacharacter known answers. Add those cases and compare exact current-view JSON and HTML bytes.

### M5 - The source-access prohibition is observed indirectly, not enforced as an access capability

The pinned v2 consumer currently contains no `model_v1` read, and changing only legacy data leaves its output unchanged. That is useful observable evidence, but it does not enforce the registry's forbidden source prefix: a future consumer could read legacy values without affecting the four asserted outputs. Execute it against a proxy/instrumented input that records property reads, or pass a capability-limited document exposing only the allowed pointer, and require the actual read set to equal the declared source set.

## Low Findings

### L1 - Physical workstream ID validation diverges from the shared schema

The schema permits lowercase alphanumeric/hyphen IDs and excludes `program` (`panel-sync-contracts.schema.json:34-42`). The enumerator accepts uppercase, dot, and underscore via `[A-Za-z0-9._-]` (`python_runner.py:1081,1109`). Later policy validation usually fails these values, so this is not a publication false-green by itself, but the standalone enumerator does not implement its registered identity claim. Reuse the shared `workstreamId` validator.

### L2 - The two design receipts share the same v2 consumer implementation

Python and Node both execute `panel_v2_consumer.mjs` (`python_runner.py:1931-1942`; Node equivalent at `node_runner.mjs:1258-1266`). This is acceptable for a pinned known-answer artifact and the receipts do not claim independent implementation conformance, but 242/242 twice is not two independent implementations of consumer behavior. Keep that limitation explicit when presenting the design evidence.

### L3 - “Browser boundary” is stronger wording than the current executable evidence

The pinned consumer is a Node stdin/stdout HTML-fragment renderer, not a browser/DOM integration. Because production conformance remains pending, this is not a release overclaim. Until a production adapter embeds the v2 payload and observes DOM output, call it a reference current-view consumer rather than completed browser-boundary proof.

## Six-Finding v10 Closure Audit

| v10 finding | v11 result | Evidence |
| --- | --- | --- |
| Rubric H1: v2 current fields were not consumed | **Closed at design-fixture boundary** | The pinned `panel_v2_consumer.mjs` is executed; current-only mutation changes visible HTML and legacy-only mutation does not. Production adoption remains intentionally pending. |
| Rubric H2: safe-integer profile was runtime-inconsistent | **Closed** | Schema revisions are bounded at `9007199254740991`; both canonicalizers reject unsafe mathematical integers; boundary/raw-wire vectors agree. |
| Rubric H3: NFC identity sets lacked collision-complete ordering | **Partial** | The generic registry-wide helper now rejects lone non-NFC and collision inputs, but H6 shows actual repair acceptance bypasses that helper. |
| Consistency H1: catalog could omit the physical WDR universe | **Partial** | A filesystem enumerator exists, but H4 and H5 show it neither validates promised content nor supplies publication's independent universe. |
| Consistency H2: ordinary WDR fact authorization was absent | **Partial** | WDR command kinds and field/section authorization now execute, but H1-H3 show contract, exact-effect, and ownership binding remain open. |
| Consistency H3: semantic dispatch and DAG invalidation were asserted | **Mostly closed, with residual M1** | Exact IDs/algorithms/scopes/handler set are checked and all handlers run; the DAG changes/recomputes transitive kind identities. Actual instance/leaf invalidation remains unproved. |

## Good-Spine Checklist

| Checklist item | Result | Notes |
| --- | --- | --- |
| Fixes the real divergence points | **Fail** | Current-field consumption is now executable, but H2, H3, and H5 still allow acknowledged commands/publications to diverge from physical business state. |
| Every AD rule is enforceable and prevents its stated divergence | **Fail** | AD-1, AD-4, AD-6, and AD-11 overclaim exact effect, inventory, invalidation, and canonical identity closure. |
| Deferred items cannot cause unacknowledged divergence | **Pass** | Native and production conformance remain explicit pending release gates. |
| Named technology is current and appropriate | **Pass** | RFC 8785, RFC 6901, Draft 2020-12, content addressing, CAS, and native durability APIs are appropriate. The findings are integration/semantics errors. |
| Ratifies brownfield reality | **Pass with limitation** | Brownfield suites pass and target-state migration is not presented as deployed. Production scripts are not themselves the v11 conformance implementation. |
| Covers the driving capabilities | **Partial** | The major action/WDR/Panel/repair paths exist, but exact WDR effects, risk-owned facts, and action create lack complete proof. |
| Inherited spine constraints | **N/A** | No parent architecture spine is declared. |
| All owned dimensions decided/deferred/open | **Pass** | Ownership, security, data, recovery, compatibility, operations, and rollout are explicitly addressed; several decisions are not yet enforced by the evidence. |

## Gate Decision

Do not finalize the spine or use the 242/242 design receipts as architectural closure. Resolve H1-H8, add the targeted Medium coverage, regenerate the registry/schema/protocol/vector/runner/result hash chain, and rerun the independent gate against one frozen target. Preserve `implementation_conformance_status: pending` and keep strict publication disabled until the existing native and production-adapter release requirements also pass.
