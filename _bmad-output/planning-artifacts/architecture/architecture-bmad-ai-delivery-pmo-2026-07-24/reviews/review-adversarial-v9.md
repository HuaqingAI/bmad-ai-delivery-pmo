# Architecture Spine Adversarial / Data-Consistency Review v9

## Frozen Review Base

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `0839f33e8335e37d2e3a5b8a678f9226c5908d3e6b586099fbd8b79e768f885e` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `b2a74fd906105365c29ef947dcdf5512cb4e487763a5e3f94dff5e5e7a409708` |
| `contracts/CONTRACT-REGISTRY.json` | `175bc4f4ad88c0e80e1d0f55559b8dd263a36e700d08880ce7af41f529954487` |
| `contracts/panel-sync-contracts.schema.json` | `890846bec1dd502e9cb516e7b9d63e623d5dd1e83b7b447a0e6b5424b856b939` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `1da9a75d12f913ab041a3eec6aa847b5184d52f4f5ab6d100e12e61160c03236` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `46dfc65182148d53bf6b8bd9a6f7abf626f67c0da30fcf82a97629390d81b6c6` |
| `contracts/fixtures/PANEL-V1-COMPATIBILITY.json` | `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` |
| `contracts/conformance/python_runner.py` | `cc7ff6d0022f8dfaac4de4ab46b43acaaf7ebcc15e565beeb2b5c6072224a8e8` |
| `contracts/conformance/node_runner.mjs` | `24e6c0df148917f7ea51844b375a73f3372cb503cc3f06c00d5ac12332eec748` |
| `contracts/conformance/python-result.json` | `010d659dc4862e850390d43394051015828255421bd4d424915bae3423b576bf` |
| `contracts/conformance/node-result.json` | `d0c4ff6c5aa03dac3bf5b7949689faaf48dd15bcf5401745d7b81d78f985147a` |

The package contains 40 contract registrations, 12 pinned source artifacts, 7 projection profiles, 7 outer payload bindings, 4 nested bindings, 15 DAG edges, 24 ordering rules, and 7 registered semantic validators. The suite contains 169 unique vector IDs. Both reference runners reproduced their checked-in results byte-for-byte with 169 passed and 0 failed, and all registry artifact/source/runner pins recomputed exactly.

These results remain correctly labeled `design-fixture-check` with `native_durability_exercised=false`. Native POSIX fault evidence, native Windows evidence, and production-adapter receipts are intentionally pending and are not counted as package failures below.

## Verdict

**FAIL. Critical: 0. High: 10.** The v9 package closes the earlier JCS Unicode-ordering defect, exact action-delta derivation, transaction-kind role closure, and the successful repair graph. It still permits incompatible choices about the selection universe, required producer cardinality, Panel/upstream equality, catalog identity, physical leaf identity, state CAS preimages, capability scope, repair failure states, identity-set ordering, and cross-runtime Panel v1 composition.

## Critical

None.

## High

### H1 - The Panel binding map is not enforced against the published upstream envelopes

The registry declares six source-to-Panel bindings, but none of the seven semantic validators compares each Panel target pointer with the payload at the corresponding published predecessor envelope. `projection_lineage_semantics()` validates handle identities and predecessor lists only (`python_runner.py:1298-1331`), while `panel_publication_semantics()` validates target bytes and pointer entries only (`python_runner.py:1443-1486`). The one runner-side `current_ok` check covers only status and is ad hoc, outside the registered validator set (`python_runner.py:1783-1809`).

A read-only complete-graph check changed the same-generation `program-status.workstream_current[0].progress` after copying the old value into the Panel. `projection_lineage_semantics`, publication eligibility, Panel v1 composition, and `panel_publication_semantics` all returned true while the Panel showed `Payment validation ready` and the published program-status envelope showed `NEW SAME-GENERATION VALUE`.

Two implementations can therefore publish the same predecessor handles but different visible Panel data. This directly preserves the original stale-Panel failure mode.

**Required fix:** add a registered semantic validator that resolves every `panel_binding_map` entry, enforces required cardinality and key merge rules, and compares the actual predecessor envelope payload bytes/pointers to the Panel target value before computing the Panel identity.

### H2 - Required projection and upstream cardinalities are not enforced

Profiles declare `one` and `one-per-meeting-kind`, but `projection_lineage_semantics()` merely iterates whatever instance lists the caller supplies. It never requires exactly one drift, audit, status, roadmap, flow, and Panel instance or exactly the policy-selected meeting instances. Publication then derives its expected target set from that incomplete `built` map (`python_runner.py:1453-1485`).

A read-only complete-graph check replaced the required `state-audit` instance list with `[]`, recomputed downstream manifest/receipt identities, and obtained `lineage=True`, `eligibility=True`, and `publication=True`. The existing omission vector removes a journal target while retaining the producer graph; it does not remove the producer and recompute the self-consistent reduced graph.

**Required fix:** derive the exact instance-key set independently from profiles plus selection policy, reject a missing or extra kind/instance before lineage validation, and make publication target closure use that independently derived set rather than the supplied graph.

### H3 - `include_workstreams="all"` is resolved circularly from Panel output

`publication_eligibility_semantics()` defines the universe for `all` as the Panel's own `status_ids` (`python_runner.py:339-341`). An implementation that emits only a subset in `workstream_current` thereby defines that subset as "all"; matching audit/drift rows pass. No independent physical workstream inventory, catalog document, or generation leaf-derived universe is supplied to the validator. The selection vectors exercise omission under an explicit list, not `all` against a larger independent universe.

**Required fix:** bind a content-addressed workstream inventory to the generation, resolve `all` from that inventory, apply exclusions, require a non-empty result, and compare the exact result to status, audit, drift, manifests, receipts, meeting instances, and published targets.

### H4 - `panel_catalog_id` is an unchecked opaque value

`generationEnvelopeV1` requires `panel_catalog_id`, and `panelBindingCatalogV1` is registered, but publication eligibility and the publication graph do not consume a catalog document or recompute its `catalog_id`. The generation fixture uses a repeated-digit placeholder, while runtime composition reads `registry.panel_binding_map` directly. The registered publication-eligibility validator scope omits `panel-binding-catalog/1.0.0`.

Two implementations can bind different catalog bytes or interpret the registry map as the catalog while producing a generation that is internally hash-consistent around the same arbitrary ID.

**Required fix:** define the catalog identity preimage, supply the schema-valid catalog to generation/publication validation, require `generation.panel_catalog_id == recomputed catalog_id`, and require the catalog rows to equal the registry binding map exactly.

### H5 - Physical leaf identity conflicts with the ordering identity, and enumerator semantics remain incomplete

Protocol section 4 says `(root_instance_id,path)` is the leaf key. The registry orders and de-duplicates generation/manifest sources by `(root_instance_id,path,category,source_kind)`. The lineage validators then construct a map keyed only by `(root_instance_id,path)` (`python_runner.py:1323`; `node_runner.mjs:811`), silently choosing one row if two schema-valid rows share a physical path but differ in category/kind. This is a normative identity conflict.

The enumerator algorithms are also labels rather than complete portable algorithms. `glob-kind-v1` does not specify hidden-file behavior, `**` zero-depth behavior, Unicode normalization during match, or error handling for unreadable entries. The design materializer does not exercise those choices: it manufactures one path per glob and hashes `root + NUL + path` instead of enumerating and reading bytes (`python_runner.py:676-712`). It also manufactures an immutable snapshot even when `previous_program_status_id` is null.

**Required fix:** use the protocol's physical pair as the uniqueness key everywhere, reject conflicting metadata for one physical leaf, fully specify each enumerator's matching/absence/error semantics, and add real temporary-tree fixtures for empty, multi-file, hidden, recursive, normalization, and inaccessible cases.

### H6 - Fact and Panel generation CAS values are not bound to state preimage/postimage bytes

Fact attribution checks `after_fact_generation == before + 1`, but it does not parse `factGenerationStateV1` before/after documents or prove that the journal's `fact-generation` target hashes are those documents (`python_runner.py:538-555`). Panel publication similarly checks numeric generation and the new state document, but does not supply the previous Panel state/pointer or bind `before_panel_generation` and `before_pointer_id` to the journal target preimages (`python_runner.py:1471-1485`).

Thus one implementation can validate numeric receipt fields only while another requires the target preimage bytes to contain those exact prior values. Both readings are plausible from the package, but they do not provide the same stale-write/CAS behavior.

**Required fix:** make both before and after state documents explicit graph inputs, validate their schemas and identities, bind their canonical byte hashes to target before/after hashes, bind transaction IDs, and add substituted-preimage vectors for fact state, Panel state, and current pointer.

### H7 - Capability scope is part of identity but not part of mutation acceptance

The protocol requires operation/field/section authorization, yet `fact_attribution_semantics()` validates only active producer identity and command fingerprint. It never compares command operation or changed fields to `allowed_operations`, `allowed_fields`, or `allowed_sections` (`python_runner.py:520-555`). The accepted fixture's status-sync capability allows `refresh_actions` while its action command patches `owner`, demonstrating that the reference acceptance path does not implement the declared ownership rule.

The semantic validator scope also omits the action command/status batch contracts even though the algorithm depends on a command. This leaves multi-command transaction attribution unspecified: the receipt schema supports multiple deltas, but the reference validator accepts one `actionCommandV2` and requires exactly one derived delta.

**Required fix:** define a command-to-capability permission mapping for action and WDR commands, include the authorized command/batch schema in the validator scope, validate exact operation/field/section coverage, and define one deterministic transaction command fingerprint and exact delta union for multi-command batches.

### H8 - Repair failure and invalidation states have no complete semantic graph

The schema allows dry-run `blocked`, nonce terminal `invalidated`, and repair outcomes `blocked|rolled-back`, while protocol section 8 allows `unused -> reserved -> consumed|invalidated`. The registered implementation accepts only a non-null token, `outcome="applicable"`, exactly `unused,reserved,consumed`, a committed journal, and a committed repair receipt (`python_runner.py:1084-1176`). No positive blocked/invalidated/rolled-back graph exists.

The wire schema worsens the ambiguity by requiring non-null `fact_receipt_id` even for blocked/rolled-back outcomes, when no committed fact receipt may exist. The validator also hard-codes `receipts/fact-tx-repair-1.json` and `receipts/repair-tx-repair-1.json` (`python_runner.py:1177-1181`; `node_runner.mjs:736-737`) instead of deriving paths from transaction identity, so a generic otherwise-equivalent transaction ID is rejected by the references.

**Required fix:** define separate complete graph branches for dry-run blocked, reserved-then-invalidated, rolled-back, and committed outcomes; make receipt/journal fields conditionally nullable/required; derive receipt paths from transaction identity; and add positive and negative vectors for every branch.

### H9 - Identity-bearing semantic sets have no declared canonical order

Protocol section 1 says semantic sets are sorted before identity and unlisted arrays retain order only when explicitly semantic. The 24 registry rules omit several identity-bearing sets, including capability `allowed_operations|allowed_fields|allowed_sections`, repair `authorization_scopes`, batch `finding_ids|action_ids`, and fact-delta `changed_fields|evidence_fingerprints`. Capability IDs, binding digests, batch IDs, and receipt IDs all hash these arrays.

The runners compensate inconsistently with local hard-coded order in some places (`expected_action_delta()` has a private field order) and input order in others. Two implementations can represent the same permission set or repair batch in different array orders and produce different identities while each follows a plausible reading of the package.

**Required fix:** register an ordering rule for every set-valued array that enters an identity, or state that array order is semantically significant and define its producer algorithm. Add permutation-stability/invalidity vectors per identity type, not only representative pointer tests.

### H10 - The two references do not execute the same Panel v1 composition algorithm

Python imports the pinned `panel_model.py`, resolves the registry's source bindings from the candidate Panel, calls `compose_panel()`, and compares the result (`python_runner.py:1223-1249`). Node does not execute or independently implement that algorithm. It compares candidate inputs and output to the single frozen compatibility fixture (`node_runner.mjs:760-768`). The compatibility check in both runners also compares target hashes to golden values rather than dynamically checking candidate source-to-target transformations (`python_runner.py:1212-1218`; `node_runner.mjs:756`).

The golden vector proves only one known input. It does not prove that two implementations produce the same `model_v1` after ordinary status, roadmap, flow, meeting, history, or shareability changes. A Node adapter can hard-code the fixture and still pass all 169 vectors.

**Required fix:** publish language-neutral composition semantics or a multi-input known-answer corpus, make both runners compose each candidate from its wire inputs, and add changed-current-field/history/meeting/source-order fixtures whose expected full `model_v1` bytes are pinned.

## Medium

- Most semantic validators accept any schema-shaped `contract` reference and rely on an external parse rule; only the repair graph explicitly compares the current schema ID/hash/registry hash. Add wrong-anchor and stale-hash complete-graph vectors for fact, lineage, publication, and Panel composition.
- `journal_semantics()` checks image locator paths with `endswith("images/<order>-before|after")` but does not bind a locator namespace to `journal_id`. Define the exact journal directory identity so independent recovery implementations resolve the same image.
- The checked-in architecture documents remain `status: draft`, which is internally honest. Do not promote them to final until the High findings are closed and a new frozen gate is reviewed.

## Verified Closures And Evidence Boundary

- All reported v9 raw hashes, registry pins, counts, and result file hashes are accurate.
- The vector suite has 169 unique IDs with no duplicates; Python and Node pass sets are identical and regenerate the checked-in result files byte-for-byte.
- Python and Node now agree on the supplied JCS UTF-16 BMP/supplementary ordering, escaping, negative zero, fraction, unsafe-integer, and invalid-surrogate vectors. Curated additional boundary values also agreed in this review.
- Capability identity now consistently excludes both `capability_id` and `authorization_record_digest`, requires their equality, and includes a known-answer preimage. H7 concerns authorization semantics, not that digest preimage.
- Journal semantics now enforces transaction-kind role closure, contiguous apply order, target uniqueness, image operation shape, receipt counts, and a committed marker.
- Successful repair validation now reconstructs binding from wire documents, resolves a non-first batch, checks expiry, links three nonce states, and binds journal/fact/repair receipt bytes. H8 concerns the missing non-success branches and fixture-specific path rule.
- Panel v1 has an additive schema and a pinned composer/source map. H1 and H10 concern dynamic same-generation equivalence across independently published envelopes and runtimes.
- Production implementation conformance remains intentionally pending and is correctly gated; this review makes no claim about native durability or production adapters.

## Exit Conditions

1. Bind every Panel pointer to the exact same-generation predecessor envelope and enforce exact producer/instance cardinality.
2. Resolve `all` from an independent content-addressed workstream inventory and bind the Panel catalog document to its ID.
3. Make physical leaf identity/enumeration portable and exact, including optional and glob behavior.
4. Bind fact/Panel CAS receipt values to before/after state and pointer bytes.
5. Enforce capability scope and define multi-command fact attribution.
6. Specify and test every repair terminal branch without fixture-specific paths.
7. Declare ordering for every identity-bearing semantic set.
8. Make both references execute the same dynamic Panel v1 composition corpus.
9. Regenerate the full hash/result chain and rerun the independent gate on one frozen target.
