# Architecture Spine Adversarial / Data-Integrity Review v7

## Frozen Review Base

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `9a36f98d377a2d4cdc6b1748cb220148b6a675f62e6941236fcede1dcf740e70` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `518277988c606fca82664f3bca70ea33b84f6137b6580554b449e188165be769` |
| `contracts/CONTRACT-REGISTRY.json` | `fe4ce0bc88ce9bc1da4a213e54ea0521726f09a71c23f4aa31e14b4748363c5a` |
| `contracts/panel-sync-contracts.schema.json` | `db06ba082306fdac6c739a71e6e13acf60567737fb3c15a9474d744f2d33164c` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `071a5ced3da7825875a4d13054775a2606a9bf67afc77f566f3bc7c13aab1afb` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `da259f8a5c8988bcb8eb89d72f6c7f3ee9db998b0826f1c9dc09734bf5b6c983` |
| `contracts/conformance/python_runner.py` | `b52c8a672e32df0d31b878a2576f0cf7c0609252bbe100904ae380904b85936e` |
| `contracts/conformance/node_runner.mjs` | `d5e7da5588400a0abfb663949163d83d8d3861ee4d381d08b7cc3be9a8e8b0a5` |
| `contracts/conformance/python-result.json` | `b89b71ef1125f7be34d5074ad19a7c9051050e6d966e6097bc7ff74c6bdd6cf2` |
| `contracts/conformance/node-result.json` | `278e7641159be49f08e3205dd2995aceb9050b1e77f4c0962a68c689097bf66e` |

The current registry is internally consistent at rest: its 7 profile kinds, 7 outer payload-binding kinds and 7 envelope kinds are equal; its 15 DAG edges equal the profile-derived upstream edge set; the Management Panel binding map equals the Management Panel profile's upstream affects/cardinalities; and it contains 2 nested bindings and 24 array-ordering rules. The drift binding is consistently `/meta/action_projection`. Both reference runners reproduced 71 passed and zero failed vectors. These receipts remain correctly labeled `design-fixture-check` with `native_durability_exercised=false`.

## Verdict

**FAIL. Critical: 0. High: 7.** v6's path mismatch and opaque payload shells were materially improved, but the final gate still accepts a fresh Panel containing selected drift, does not exercise the registered outer binding in its claimed whole-composition proof, leaves fact attribution unbound, permits one implementation to count twice, and does not close the DAG, journal, or repair semantic validators that strict publication relies on.

Native POSIX fault injection, native Windows CI, and production-adapter receipts remain pending. That pending state is correctly disclosed and is not itself a High finding.

## Critical

None.

## High

### H1 - A Panel marked `fresh` is still accepted when a selected workstream is in drift

The schema independently permits `business_freshness="fresh"`, a drift row with `status="drift"`, and `overall_status="blocked"` (`panel-sync-contracts.schema.json:542-570,730-779`). The semantic helper only checks coverage plus the equivalence between `overall_status="in-sync"` and all rows being in sync; for any non-in-sync row, every non-`in-sync` overall value passes (`python_runner.py:227-234`; `node_runner.mjs:140-146`). The Panel composition gate then requires only schema validity plus that helper (`python_runner.py:456-468`; `node_runner.mjs:361-371`).

An in-memory counterexample with one selected row changed to `drift`, `overall_status="blocked"`, and Panel `business_freshness="fresh"` returned both `managementPanelPayloadV2 valid=true` and `drift_semantics=true`. This violates the normative rule that selected drift/fingerprint mismatch must not publish (`WDR-AND-TRANSACTION-PROTOCOL.md:58,61`) and leaves the original false-green defect open.

**Required fix:** define and pin a publication-eligibility validator distinct from verdict internal consistency. It must require every selected row and `overall_status` to be `in-sync` before a Panel can be `fresh` or current, and add negative whole-publication vectors for `drift`, `missing`, `malformed`, blocked audit, and freshness/status disagreement.

### H2 - The claimed whole Panel composition proof bypasses the registered outer payload bindings

The Panel fixture synthesizes a flow payload whose `topology`, `state`, `overlays`, `recovery`, and `compatibility` are placeholder one-property objects (`python_runner.py:266-288`; `node_runner.mjs:165-187`). That payload is invalid against the registry-bound `urn:adp:flow-graph:v1` schema, which requires the complete topology/state/overlay shapes. Independent validation returned `whole_fixture_flow_binding_valid=false`.

Nevertheless `panel-binding-catalog-to-schema` passes because the loop inserts bare payloads, validates only the internal Management Panel schema and the two Program Status nested bindings, and never iterates `projection_payload_bindings` to validate each upstream outer payload or its canonical envelope (`python_runner.py:456-474`; `node_runner.mjs:361-378`). It also does not verify payload hash, schema ID/hash, projection ID, generation, manifest, or producer receipt lineage. This is not the whole-envelope, schema-bound publication proof required by v6 or claimed in the plan (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:397`).

**Required fix:** build schema-valid canonical envelopes for every bound projection, resolve each outer and nested binding through its declared root/path/pointer/hash, recompute identities, verify same-generation manifest/receipt linkage, then compose and validate the Panel. Add one negative case per outer binding and lineage/hash/generation mismatch.

### H3 - Fact receipt attribution is present but not bound to authorization or mutation truth

`factMutationReceiptV1` now carries initiator and action-delta fields, but the journal has no authorized-command/capability reference and the receipt has no pinned authorization-record reference (`panel-sync-contracts.schema.json:1058-1073,1107-1153`). The schema does not require `after_fact_generation=before+1`, patch `after_revision=before+1`, target equality with journal targets, or initiator equality with an active capability record. The receipt vector checks only one happy fixture's producer, owner-only delta, and revision increment; it ignores capability ID, epoch, principal, journal, targets, and the receipt schema itself (`python_runner.py:429-439`; `node_runner.mjs:337-343`).

A schema-valid counterexample used `adp-meeting-sync`, capability epoch 999, fact generation `1 -> 99`, and action patch revision `4 -> 99`; `factMutationReceiptV1` accepted it. The protocol's required cross-check against the journal and active capability record (`WDR-AND-TRANSACTION-PROTOCOL.md:51`) therefore has no closed wire path or executable gate.

**Required fix:** add a pinned authorization reference/digest and authorized command fingerprint to the journal/receipt contract, define the cross-document equality and revision/generation rules in a registered semantic validator, and add forged producer/capability/principal, wrong target, and revision-jump rejection vectors over complete documents.

### H4 - One implementation with two builds satisfies the “two independent implementations” release gate

The registry requires `minimum_independent_implementations=2` (`CONTRACT-REGISTRY.json:17-18`), but both release helpers enforce uniqueness only on the tuple `(implementation_id, adapter_build_id)` (`python_runner.py:236-263`; `node_runner.mjs:148-163`). Two receipts with `implementation_id="same-adapter"`, different build IDs, native POSIX/Windows platforms, full vectors, valid hashes/result IDs, and required evidence classes were schema-valid and returned `release_gate_accepts=true`.

The suite has only a valid pair and a subset rejection; it has no same-implementation/different-build rejection (`CONFORMANCE-VECTORS.json:393-426`). This permits platform builds of one codebase to substitute for independent implementations and weakens the exact acceptance algorithm.

**Required fix:** require at least two distinct `implementation_id` values and define whether build IDs must also be distinct; add duplicate implementation, duplicate build, platform spoof, evidence-class omission, extra vector, hash mismatch, and result-ID mismatch rejection vectors.

### H5 - The 71-vector gate does not validate the DAG, exact read sets, or the 24 registered ordering rules

The plan says both runners validate 15 DAG edges and 24 ordering rules (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:397`), but neither runner reads `projection_dag` or `canonical_array_ordering`. The only profile inventory vector checks a short subset of source kinds for three producers and a few forbidden Panel live kinds; it does not compare profile upstreams to the DAG or exercise `actual reads == resolved allowed reads` (`CONFORMANCE-VECTORS.json:274-303`; `python_runner.py:374-411`).

The current static registry happens to be consistent, but an adapter that omits an upstream invalidation, consumes a declared source only conditionally, ignores a leaf, or accepts duplicate/noncanonical arrays can still pass the exact release suite. Those are direct stale-Panel and identity-divergence paths.

**Required fix:** derive every edge and allowed read from the registry in the runner, run one changed-input invalidation case per edge, execute exact allowed/actual read-set equality for every profile and selection cardinality, and apply all 24 ordering/duplicate rules to real contract instances.

### H6 - Journal recovery safety is prose-only at the release gate

`transactionJournalManifestV1` validates target field shapes but does not tie operation to images/hashes, require contiguous unique `apply_order`, require unique target identity, bind `receipt_target_paths` to role=`receipt` targets, or enforce fact/panel/repair receipt counts (`panel-sync-contracts.schema.json:1058-1073`). A counterexample with two identical create targets at order 0, non-null before image/hash, null after image/hash, and an unrelated receipt path was schema-valid.

The journal vectors operate on abstract `before|after|unknown` labels and one happy locator array; the runners do not validate a complete journal document or these cross-field invariants (`python_runner.py:413-426`; `node_runner.mjs:327-334`). Thus an implementation with unsafe recovery metadata can obtain a full suite receipt despite the protocol's stronger requirements (`WDR-AND-TRANSACTION-PROTOCOL.md:72-74`).

**Required fix:** register a journal semantic validator, exercise complete manifests for create/replace/remove and every transaction kind, and add negative vectors for operation/image mismatch, duplicate/gapped order, duplicate target, wrong receipt path/count, locator/hash mismatch, and marker/manifest mismatch.

### H7 - Repair batch linkage and exact action/read-set equality are not validated on the registered documents

The repair schema allows a finding to carry a non-null `repair_batch_id` while `repair_batches=[]`; a direct counterexample was schema-valid (`panel-sync-contracts.schema.json:819-862`). It likewise does not bind root audit ID to each batch, finding IDs to actual findings, finding `repair_batch_id` back to the batch, action-ID union to command/read records, command WDR revision to the one WDR read row, or source uniqueness.

The repair vectors reduce these rules to unrelated flat arrays and labels; the runners compare those fixture arrays instead of validating `auditFindingRepairV2`, `repairBatch`, dry-run/apply contracts, and nonce state as one linked graph (`python_runner.py:494-528`; `node_runner.mjs:396-409`). A broken bulk-repair implementation can therefore pass while dropping an exact action ID or applying a batch against a different audit/read set, reopening the user's batch-repair failure mode.

**Required fix:** implement one registered repair-graph validator over complete schema-valid documents, recompute batch/binding/token identities, and add dangling/reversed batch link, audit mismatch, action union mismatch, duplicate read/source/WDR record, command/read revision mismatch, and cross-batch token rejection cases.

## Verified Closures And Evidence Boundary

- The v6 drift binding path mismatch is closed: binding map, Management Panel profile, and Panel schema all use `/meta/action_projection`.
- Drift coverage correctly rejects a missing selected row; the remaining blocker is publication eligibility, not row-set comparison.
- Program Status outer shape and two external nested bindings, roadmap items, meeting boards, source previews, and Panel fields are materially more concrete than v6.
- Fact receipts now expose initiator and action deltas; the remaining blocker is cross-document attribution and mutation binding.
- Registry/profile/DAG state is currently internally consistent, and raw schema path/hash/pointer checks run for all 7 outer bindings and 2 nested bindings.
- Python and Node both reproduced 71/71 as design-fixture checks. They do not constitute production or native durability evidence, exactly as the receipts state.

## Exit Conditions

1. Reject `fresh`/publication whenever the selected drift verdict is not wholly `in-sync`.
2. Make the whole-composition vector validate every canonical envelope, outer/nested payload binding, identity, manifest, receipt, and same-generation relation.
3. Bind fact receipt initiator/deltas to an authorized command, active capability record, journal targets, and generation/revision transitions.
4. Require genuinely distinct implementation IDs in the release gate.
5. Add executable registry-derived DAG/read-set/ordering, journal, and repair semantic validators with negative vectors.
6. Regenerate the hash chain and rerun the independent final gate on one frozen target.
