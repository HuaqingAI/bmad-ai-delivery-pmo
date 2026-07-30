# Architecture Package Data-Consistency Review v11

## Verdict

**FAIL. Critical: 2. High: 8. Medium: 5. Low: 2.**

v11 closes the three v10 headline gaps at the nominal fixture level: selection now carries an independently named physical inventory and compares it byte-for-byte with the catalog; WDR commands are included in fact attribution; semantic validator IDs, algorithms, ordered scopes, and handler sets are checked exactly. The package is still not substitutable as one contract system. Contract references are not bound to the contract actually being parsed, repair commits do not re-enter active capability attribution, WDR section ownership disagrees with the permission mapper, several complete transaction target sets are incomplete, DAG invalidation is kind-level rather than instance-level, and normalization/action-reference rules admit divergent wire interpretations.

This is a static consistency review of the frozen architecture documents, registry, Schema, checked-in vectors, and the two reference harnesses. Normative files were not modified.

## Frozen Review Base

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `65e19ae9ceab8e3301154363db01064be01b15203c7e39e61b06eed6b3196e2d` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `17f250f0a783ce77e9da7162b911b3f1b33ec9c483f67e9895cbb63f05f8a73d` |
| `contracts/CONTRACT-REGISTRY.json` | `51bb9f1d283f738cbb6a930d1947ad0066087252994cd3e76d58a2405fa4cf6f` |
| `contracts/panel-sync-contracts.schema.json` | `293caf12342777ae1d44c62bc50a9dff407e833f9a0a1bfaf13cda4d86881055` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `4941fc1b78a7962b6299d367903cfd206bcba178edbaca7dd2904cb62c44b15d` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `b8eed03e2c052ef995bb6db1d76fff7e059080a7f51df5630d3ab5eae4cd0ce1` |
| `contracts/conformance/python_runner.py` | `ac5f3c32ef56b20ce7ff595858fde08b18a973c1eb321602aa18afcaddad47f9` |
| `contracts/conformance/node_runner.mjs` | `477368005c6e9ef9f4326c4ee06a94145cdeb5bde007b722ae209f81aa560581` |

The checked-in result documents report 242 passed vectors and zero failed vectors for each reference harness. The findings below explain why that set does not yet prove the stronger cross-contract claims in the prose.

## Critical Findings

### C1 - Contract ID and raw-hash negotiation is not part of most contract validation

The protocol requires exact `schema_id`, schema raw hash, and registry raw hash to be checked before parsing, with mismatch reported as `CONTRACT_NEGOTIATION_FAILED` (`WDR-AND-TRANSACTION-PROTOCOL.md:8`). The shared `contractRef` Schema only requires a non-empty `schema_id` and hash-shaped strings; it does not bind a document definition to its own anchor or to the frozen hashes (`panel-sync-contracts.schema.json:74-82`). The normal fact validator selects the command definition and validates registry/state/receipt shapes, but does not compare their embedded contract references with the actual schema/registry values (`python_runner.py:780-784`). Repair is the exception because it explicitly compares exact references (`python_runner.py:1633-1640,1694-1705`), proving this check is neither inherent in the Schema nor uniformly dispatched.

**Conforming-but-incompatible result:** one implementation can reject a command/receipt whose embedded schema hash is not the loaded schema, while another can accept it after validating only the selected `$defs` shape. Both can pass all current fact vectors because none changes a command or receipt `contract.schema_id|schema_sha256|registry_sha256` (`CONFORMANCE-VECTORS.json:496-525`).

**Required closure:** every semantic handler must receive the loaded raw hashes and exact registered contract ID, and compare all embedded `contract` references before shape validation. Add wrong-ID, wrong-schema-hash, and wrong-registry-hash vectors for every command, state, manifest, receipt, policy, generation, projection envelope, and audit contract; route them through registry dispatch.

### C2 - A committed repair fact receipt is not closed over the active capability registry

All fact transactions, including `refresh_actions`, must bind an active capability record, epoch, principal, exact scope, command fingerprint, journal, and receipt (`ARCHITECTURE-SPINE.md:83,119`; `WDR-AND-TRANSACTION-PROTOCOL.md:21,51`). The registered fact validator includes `writer-capability-registry/1.0.0`, but the repair validator scope does not (`CONTRACT-REGISTRY.json:508,510`). The repair fixture therefore constructs hash-shaped authorization values without a capability-registry document (`python_runner.py:1531-1536`), and committed repair validation only compares the receipt authorization with the journal plus the command fingerprint (`python_runner.py:1792-1803`). It never invokes `fact_attribution_semantics()` or verifies an active record/epoch/principal.

**Conforming-but-incompatible result:** normal WDR `refresh_actions` and repair-driven `refresh_actions` have different attribution acceptance rules even though both commit a fact receipt for the same WDR operation.

**Required closure:** add the capability registry, host-principal evidence boundary, typed WDR command, and before/after fact state to the repair graph scope. Reuse the same fact-attribution handler for the embedded repair fact receipt before applying repair-specific nonce and batch checks. Add revoked epoch, wrong principal, denied field, denied section, and missing capability-registry repair vectors.

## High Findings

### H1 - `status`, `phase`, and `refresh_actions` are attributed to the wrong WDR sections

The protocol maps `status` and `phase` to `## Identity`, while `refresh_actions` changes the `Next actions` label under `## Project Status` (`WDR-AND-TRANSACTION-PROTOCOL.md:23-33`). The executable mapper groups `status|phase` with project-status fields and returns only `project-status`; for `refresh_actions` it returns only `next-actions` (`python_runner.py:605-620`). The valid WDR-status fixture covers `progress|blockers|risks`, not `status|phase` (`python_runner.py:646-650`; `CONFORMANCE-VECTORS.json:497,502`), while the denied-section vector uses an owned section, not `refresh_actions` (`CONFORMANCE-VECTORS.json:500,503`). The capability known-answer record makes the conflict worse by pairing `refresh_actions` with `allowed_sections=["identity"]` (`CONFORMANCE-VECTORS.json:505-508`).

**Required closure:** define a registry table from every WDR patch key to every physical heading/label it can modify. Require `status|phase -> identity`; require `refresh_actions -> project-status` and, if `next-actions` remains a distinct permission token, require both. Add isolated valid/denied-section vectors for each field.

### H2 - The accepted action transaction omits mandatory ledger state, command index, and action-flow targets

The protocol requires an action mutation to commit the ledger Markdown, `action-ledger-state`, and command-to-action index in one fact transaction (`WDR-AND-TRANSACTION-PROTOCOL.md:49`); the plan also states that action create/patch/close updates ledger, action-flow, and Next actions (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:349`). The fact handler derives exactly one action business path, `actions/action-ledger.md` (`python_runner.py:623-630`), and the positive action attribution vector accepts that graph (`CONFORMANCE-VECTORS.json:496`).

**Required closure:** register exact runtime paths and schemas for ledger state, command index, fact-bound action-flow, and any same-command WDR projection target. Make the action target set operation-specific and exact, then add omitted/extra/substituted target vectors.

### H3 - A valid WDR create leaves the physical inventory invalid until a later command

Physical inventory requires every visible WDR to have an exact same-directory `action-projection.json` sidecar (`ARCHITECTURE-SPINE.md:101`; `WDR-AND-TRANSACTION-PROTOCOL.md:61`). The compatibility matrix says that action projection sidecar is first created by a later `refresh_actions` transaction (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:393-394`). The accepted WDR-create fact target set contains only `delivery-record.md` and `delivery-record.state.json`; the action sidecar is added only for a patch with `refresh_actions` (`python_runner.py:623-630`; `CONFORMANCE-VECTORS.json:501`). The suite separately rejects WDR-without-sidecar publication (`CONFORMANCE-VECTORS.json:557`) but never composes that invariant immediately after its valid create graph.

**Required closure:** either atomically create a schema-valid empty `action-projection.json` during WDR create, or define a lifecycle state that the inventory enumerator can represent without making `all` unavailable. Add a serial create-to-inventory-to-generation vector.

### H4 - The physical inventory algorithm claims schema validity but checks only two identity fields

The registered enumerator requires a schema-valid WDR and exact action-projection sidecar (`CONTRACT-REGISTRY.json:496`). The implementation only regex-matches one `Workstream ID` line and checks that parsed sidecar JSON is an object whose `workstream_id` equals the directory name (`python_runner.py:1096-1126`). Its positive temp tree intentionally uses a skeletal WDR with only `Identity` and a skeletal `{workstream_id,actions}` sidecar (`python_runner.py:1147-1156`), so neither the pinned WDR grammar nor `wdrActionProjectionV1` is exercised.

**Required closure:** validate all required WDR headings/labels, canonical bytes, state-sidecar linkage, and the complete `wdr-action-projection/1.0.0` document before inventory admission. Replace the skeletal positive fixture with complete pinned documents and add malformed-but-identity-matching negatives.

### H5 - Dynamic invalidation is projection-kind-level and never proves leaf or per-instance propagation

The spine requires exact direct/transitive **instance** invalidation after projection or leaf identity changes (`ARCHITECTURE-SPINE.md:109-113`), and the protocol requires topological recomputation for every identity (`WDR-AND-TRANSACTION-PROTOCOL.md:91`). The executable DAG model keeps one ID per projection string in `baseline_ids`, changes only projection-kind IDs, and accumulates `set[str]` kind names (`python_runner.py:948-1003`). It has no generation leaf input and no `instance_key`; therefore the two meeting-pack instances are collapsed into one node. The vectors named changed-input and transitive omission call only this kind-level helper (`python_runner.py:2423-2425`).

**Required closure:** instantiate the DAG as `(projection_kind,instance_key)` nodes derived from selection/cardinality, add leaf identities as source nodes, and compare the exact invalidated instance set. Required cases include one changed raw leaf, program-status invalidating both meeting packs, one meeting-pack instance change invalidating Panel without invalidating its sibling, and direct-only propagation rejection.

### H6 - The Panel v2 “single source” contract is internally impossible and not instrumented

The registry declares one source pointer and forbids `/model_v1` (`CONTRACT-REGISTRY.json:593-600`); the prose says the consumer uniquely reads `/sync/canonical/status/workstream_current` (`ARCHITECTURE-SPINE.md:101`; `WDR-AND-TRANSACTION-PROTOCOL.md:64`). Yet the output Schema requires `source_panel_id` (`panel-sync-contracts.schema.json:780-785`), and the pinned consumer reads top-level `panel.panel_id` to produce it (`panel_v2_consumer.mjs:21-45`). The harness executes a black-box process and compares output (`python_runner.py:1931-1942`); current-only and legacy-only output comparisons (`node_runner.mjs:1907-1925`) cannot establish the actual read set, and `forbidden_source_prefix` is never consulted.

**Required closure:** declare the allowed read set explicitly as `/panel_id` plus the current-workstream pointer, or pass `panel_id` as trusted envelope metadata outside the consumer input. Execute the consumer behind an instrumented resolver/proxy and assert actual reads equal allowed reads and exclude every forbidden prefix.

### H7 - Numeric `apply_order` conflicts with the registry’s UTF-8 string comparator at 10+

Journal semantics requires numeric target order `0..n-1` (`python_runner.py:543-588`). The registry also registers `/targets` ordering by `apply_order` (`CONTRACT-REGISTRY.json:536`), while the common ordering helper converts every non-null key, including integers, to `str(value)` and sorts UTF-8 bytes (`python_runner.py:1229-1238`). Consequently registry order places `10` before `2`, whereas journal order places `10` after `9`. Panel publication can exceed ten targets because it journals every canonical projection plus Panel, pointer, state, and receipt. Current ordering representatives use a smaller repair journal (`python_runner.py:1290`) and vectors cover only reverse/duplicate/NFC/null, not the 9/10 boundary (`CONFORMANCE-VECTORS.json:435-439,470-474`).

**Required closure:** define typed ordering components: integers compare numerically, strings compare NFC UTF-8, and null ordering applies only to nullable fields. Add an 11-target schema-valid panel journal known answer in both harnesses.

### H8 - Repair accepts contradictory `entity_refs` and `action_ids`

Each finding carries both generic entity references and exact action IDs (`ARCHITECTURE-SPINE.md:115-119`; `panel-sync-contracts.schema.json:900-912`). Repair semantics compares the finding `action_ids` union with command and read-set IDs but never derives or compares action-typed `entity_refs` (`python_runner.py:1642-1662`). The current cross-field vectors compare only three standalone action-ID arrays (`node_runner.mjs:1969-1973`), so an action entity reference can name a different action while the repair graph remains internally consistent on the other arrays.

**Required closure:** require the unique set of `entity_refs` where `entity_type=action` to equal `finding.action_ids` for every repairable action finding; reject duplicates and mixed representations. Add missing, extra, mismatched, duplicate, and non-action entity-ref vectors through the full repair handler.

## Medium Findings

### M1 - Nullable `repair_batch_id` incorrectly forces action IDs to disappear

The Schema independently permits non-empty `action_ids` and `repair_batch_id=null` (`panel-sync-contracts.schema.json:909-912`), matching the spine’s requirement that findings preserve exact IDs while batch linkage is nullable (`ARCHITECTURE-SPINE.md:119`). The repair validator rejects every null-batch finding that still carries action IDs (`python_runner.py:1647-1651`). This recreates the original audit canonicalization loss for nonrepairable/deferred findings.

**Required closure:** null batch means “not currently repairable,” not “not action-related.” Preserve action/entity IDs and exclude only that finding from a repair batch. Add a full audit containing both repairable and nonrepairable action findings.

### M2 - Finding action-ID ordering is required by prose but absent from registry and actual-graph validation

The protocol says repair `action_ids` are identity sets sorted by NFC UTF-8 (`WDR-AND-TRANSACTION-PROTOCOL.md:10`). Registry ordering covers `/repair_batches/*/command/action_ids` but not `/findings/*/action_ids` (`CONTRACT-REGISTRY.json:515-523`). Repair semantics sorts temporary unions for comparison rather than requiring canonical stored order (`python_runner.py:1657-1662`). Two producers can therefore serialize the same finding with different action-ID order and produce different enclosing payload identities.

**Required closure:** register `/findings/*/action_ids` and `/repair_batches/*/read_set/action_revisions` with duplicate rejection and action-ID key ordering; validate the actual graph arrays before identity checks.

### M3 - Generation closure is reverse-checked only for physical WDR/sidecar leaves

Publication enforces exact inventory equality only for source kinds `selected-physical-wdr|wdr-action-sidecar` (`python_runner.py:428-449`). Lineage then checks that every actual read exists in generation, but never that every nonphysical generation leaf is consumed by at least one resolved profile (`python_runner.py:2007-2037`). This permits unused registered-looking leaves to alter `generation_id` without changing any producer read set.

**Required closure:** require generation leaves to equal the deduplicated union of all registry-resolved allowed read sets plus the explicitly defined all-workstream physical inventory. Reject extra nonphysical leaves and metadata variants.

### M4 - Registered dispatch executes only fixed positive fixtures; negative graphs bypass dispatch

The protocol requires every semantic registry row to execute as the validation route (`WDR-AND-TRANSACTION-PROTOCOL.md:91`). The dispatcher does verify exact IDs/scopes and calls each handler, but each handler receives only an internally generated positive graph (`python_runner.py:2243-2313`). Most negative receipt and repair vectors later call `fact_attribution_semantics()` or `repair_graph_semantics()` directly (`python_runner.py:2554-2612,2891-2941`) rather than submitting the mutated graph through the registry dispatcher.

**Required closure:** make dispatch accept `(validator_id, graph)` and route every positive and negative semantic vector through it. Record handler ID, ordered scope, invocation, and result per vector.

### M5 - Checked-in result evidence is described but not content-closed by the registry

Registry result evidence pins runner hashes and result paths, but `result_binding` requires only that a result’s registry hash match raw registry bytes; it does not pin or derive the result raw hash (`CONTRACT-REGISTRY.json:41-59`). The plan lists result hashes (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:378-382`), but that plan is not part of the registry hash chain. The runner startup verifies schema/protocol/suite and pinned source artifacts, not the checked-in result file named by `result_evidence` (`python_runner.py:2990-3020`).

**Required closure:** add a non-circular evidence manifest outside the registry whose ID is derived from registry, runner, and result raw hashes, and validate it in the package gate. At minimum, validate each described runner hash, result path, result schema, result ID, implementation metadata, passed-ID set, and all four artifact hashes.

## Low Findings

### L1 - Physical workstream ID syntax is broader in the enumerator than in the wire Schema

`workstreamId` allows lowercase alphanumerics and hyphens only, at most 63 characters and excluding `program` (`panel-sync-contracts.schema.json:34-37`). Physical inventory accepts uppercase, dot, and underscore with no equivalent length bound (`python_runner.py:1080-1082,1108-1110`). Such a directory passes enumeration identity checks and then fails when inserted into a selection policy.

**Required closure:** use the same shared `workstreamId` validator at discovery time and add uppercase/dot/underscore/overlength directory cases.

### L2 - Timestamp Schema validates shape, not the RFC 3339 instant promised by the protocol

`utcTimestamp` is only a digit-position regex (`panel-sync-contracts.schema.json:48-50`), while the protocol requires valid RFC 3339 instants and canonical UTC seconds (`WDR-AND-TRANSACTION-PROTOCOL.md:12`). Repair code performs real date parsing, but many payload/state timestamps rely only on the shared Schema.

**Required closure:** add a semantic UTC timestamp validator used by all handlers, with invalid month/day/hour and leap-second vectors, or adopt a standards validator plus the stricter canonical `Z`/whole-second rule.

## Verified v10 Closures

- Physical inventory, catalog, and generation now have explicit IDs and exact bidirectional equality for the two physical source kinds (`python_runner.py:356-375,419-456`). H4 concerns the validity of admitted file contents; M3 concerns extra nonphysical generation leaves.
- Semantic validator IDs, algorithms, and ordered scopes are compared exactly (`python_runner.py:1006-1053`), and executed/registered/handler ID sets are compared (`python_runner.py:2301-2313`). M4 concerns whether real negative graph acceptance is routed through that dispatcher.
- Normal WDR command variants are now present in fact attribution (`CONTRACT-REGISTRY.json:508`; `python_runner.py:2264-2265`). C2 and H1 concern repair reuse and exact physical section mapping.
- Current Panel v2 output is executed from the pinned consumer and covers baseline, current-only, legacy-only, and missing-field behavior (`node_runner.mjs:1907-1925`). H6 concerns the contradictory and uninstrumented read-source contract.

## Exit Conditions

1. Enforce exact contract ID/schema hash/registry hash in every semantic handler before parsing.
2. Route repair fact commits through the same active capability attribution as normal WDR commands.
3. Replace ad hoc WDR permission mapping with a registry-owned field-to-physical-section table and close all action/WDR target sets.
4. Make physical inventory validate complete WDR/action-sidecar documents and make WDR create preserve the paired invariant.
5. Execute leaf- and instance-level DAG invalidation, including both meeting-pack instances.
6. Define and instrument the complete Panel v2 allowed read set.
7. Fix typed numeric ordering and register all repair action/reference arrays.
8. Close generation/result evidence sets bidirectionally and route all negative semantic graphs through registry dispatch.
