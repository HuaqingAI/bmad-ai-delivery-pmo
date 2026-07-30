# Adversarial Architecture Review v16

## Verdict

**REJECT. Critical: 0. High: 9. Medium: 5. Low: 0.**

The requested gate of **0 Critical / 0 High is not met**. The package has strong fail-closed intent, but its normative surfaces are not implementation-convergent. In several places the hash-authoritative registry, schema, protocol, and AD prose demand mutually exclusive behavior. Elsewhere, two independently built units can obey the written ADs yet choose different durable paths, authority bindings, restart inputs, timestamps, or renderers.

This is not a claim that strict publication is currently active. It is not: raw `CONTRACT-REGISTRY.json` states `conformance_suite.implementation_conformance_status = "pending"` (`CONTRACT-REGISTRY.json:117-124`), and `evidence_trust.trust_roots` is the empty array (`CONTRACT-REGISTRY.json:9-19`). AD-12 correctly says this package does not authorize production strict publication (`ARCHITECTURE-SPINE.md:145-149`). The two checked-in 486/486 results are explicitly `design-fixture-check` with `native_durability_exercised=false`; they are useful design evidence, not production implementation evidence.

| Severity | Count |
| --- | ---: |
| Critical | 0 |
| High | 9 |
| Medium | 5 |
| Low | 0 |

## Independent Divergence Construction

Two units were constructed conceptually without borrowing each other's hidden runner behavior:

| Surface | Unit R: registry/schema first | Unit P: AD/protocol first | Result |
| --- | --- | --- | --- |
| Action flow | Writes the registered brownfield `action_flow_schema_version/actions/compatibility` document | Writes `contract`, ledger fingerprint/revision, `index_id`, and action revision/routing/affected fields required by protocol section 4 | Mutually exclusive JSON shapes under one `action-flow-index/1.0.0` name |
| Roadmap mutation | Rejects status-sync `owned_sections: roadmap` because `owned_sections` is absent from its allowed fields | Accepts it because AD-1/protocol make status-sync the exclusive Roadmap writer | Opposite authorization decisions |
| Drift freshness | Reads only the four registered sources | Also reads action-ledger state to obtain authoritative ledger revision and applied-command state | Same projection profile, different read set and verdict authority |
| Strict restart | Discovers only registry-addressable objects | Loads a locally chosen production-receipt store and journal filenames | Different accepted release set and recovery state |
| Repair restart | Invents a nonce store because none is registered | Uses the runner's hard-coded `state/nonces/h_<hash>.json` | Different token reuse and recovery results |
| Host authority | Hashes an OS account/SID through a local adapter | Accepts a provisioned opaque principal ID | Both satisfy “OS boundary supplies principal,” but grant different principals |
| Source time | Uses selection-policy `as_of` | Uses refresh completion time | Different projection/Panel identities for the same leaves |
| First publication | Creates absent pointer/state | Requires a pre-existing sentinel and replaces it, matching the reference handler | Different bootstrap state and CAS behavior |

The first three rows are direct normative contradictions, so no unit can literally obey every AD and every registered contract at once. The remaining rows are genuine under-specification: both units can plausibly claim full AD compliance and still fail to interoperate.

## High Findings

### H1 - `action-flow-index/1.0.0` has two mutually exclusive wire shapes

Protocol section 4 requires the index to carry top-level ledger fingerprint/revision and `index_id`, and each action to contain only `action_id, action_revision, status, routing_scope_id, affected_workstreams` (`WDR-AND-TRANSACTION-PROTOCOL.md:55`). The registered schema instead permits only `action_flow_schema_version`, `actions`, and `compatibility`; each action requires lifecycle timestamps, baseline/relation fields, and `source`, and it forbids the protocol fields (`panel-sync-contracts.schema.json:340-386`). It also does not permit the `contract` field that protocol section 1 says every state/proof/receipt document MUST carry (`WDR-AND-TRANSACTION-PROTOCOL.md:8`).

The Python harness and production `sync_status.py` implement the schema/brownfield shape, not the prose shape (`python_runner.py:2624-2671`; `skills/adp-status-sync/scripts/sync_status.py:1046-1071`). The registry binds that schema to `action-flow-index/1.0.0` (`CONTRACT-REGISTRY.json:322-329`). A schema-first producer and a protocol-first producer cannot exchange this fact, and strict activation computes different action-flow fingerprints. This directly violates AD-1 and AD-11.

**Required correction:** choose one shape. If brownfield compatibility is required, specify an additive v2 envelope or keep the brownfield payload nested, register its exact identity algorithm, update the protocol, schema, producer, flow-graph consumer, fixtures, and all hashes together.

### H2 - The exclusive Roadmap owner is not authorized to mutate Roadmap

AD-1 and protocol section 2 make status-sync the exclusive semantic writer for Roadmap (`ARCHITECTURE-SPINE.md:79-83`; `WDR-AND-TRANSACTION-PROTOCOL.md:17-20`). Roadmap mutation uses `set.owned_sections[]` because `wdrOwnedSectionMutation` is the only Roadmap-bearing command shape (`panel-sync-contracts.schema.json:440-480`). The strict writer spec grants status-sync the `roadmap` section but omits `owned_sections` from `allowed_fields` (`CONTRACT-REGISTRY.json:53`). The fact-attribution handler requires both field and section authorization, so the command is rejected (`python_runner.py:3279-3280`).

No other patch-capable writer can fill the gap: kickoff and workstream-register are create/bootstrap owners, and the risk writer is delegated-only. One implementation honoring ownership accepts the update; one honoring the raw capability registry rejects it. Both cannot satisfy AD-11.

**Required correction:** either add `owned_sections` to status-sync with Roadmap-specific positive/negative vectors, or introduce a typed Roadmap field/command and update the ownership rule. Do not grant broad owned-section authority without section closure tests.

### H3 - Drift freshness requires ledger state that its registered read profile forbids

AD-5/protocol require the drift producer to read ledger, ledger state, WDR, WDR state, and sidecar, then compare the sidecar ledger revision and fingerprint against authoritative ledger state (`ARCHITECTURE-SPINE.md:103-107`; `WDR-AND-TRANSACTION-PROTOCOL.md:65-66`). The registered `action-projection-drift-verdict/1.0.0` profile contains ledger, WDR, WDR state, and sidecar, but omits `state/action-ledger.json` (`CONTRACT-REGISTRY.json:802-855`). AD-4 simultaneously requires actual reads to equal the registered allowed set.

The reference content handler silently receives `package["ledger_state"]` out of band and uses it to produce the verdict (`python_runner.py:1479-1524`). A production resolver that reads it violates exact read instrumentation; one that does not cannot establish ledger revision/applied-command truth. A stale sidecar can therefore be judged differently by two “conforming” producers.

**Required correction:** add the exact action-ledger-state source to the drift profile, bind its raw hash in the manifest, add missing/extra-read vectors around it, and regenerate registry/suite/results.

### H4 - Strict restart has no durable contract or path for accepted production release evidence

AD-12 requires restart-safe open/inspect/publish to reload accepted production receipts and bind their exact set into the activation attestation (`ARCHITECTURE-SPINE.md:145-149`; `WDR-AND-TRANSACTION-PROTOCOL.md:82,100`). The attestation carries only `release_evidence_set_id` (`panel-sync-contracts.schema.json:1322-1338`). The registry has a schema for an individual conformance result, but no release-evidence-set document, no runtime path/enumerator for accepted receipts or evidence blobs, and no durable acceptance receipt.

The reference handler receives `release_receipts` and `evidence_blobs` as in-memory package members and hashes sorted result IDs (`python_runner.py:1895-1900,2025-2043`). After a real process restart, two implementations may scan different directories or retain different subsets and still compute internally valid attestations. Strict authority then depends on unregistered local state.

**Required correction:** define a content-addressed release-evidence-set contract, exact receipt/blob store paths and enumeration/error rules, issuer/rotation ownership, and bind raw receipt/blob hashes rather than only result IDs. Include restart discovery and extra/unindexed-receipt vectors.

### H5 - Capability rotation/revocation cannot satisfy the typed-command transaction rule

Protocol section 2 says rotation/revocation is a fact transaction with capability-epoch CAS (`WDR-AND-TRANSACTION-PROTOCOL.md:21`). AD-1/AD-10 require every fact transaction to bind one schema-valid typed command and derive exact targets/CAS/authorization from it (`ARCHITECTURE-SPINE.md:83,133-137`). The 50 registered contracts include the capability registry state but no capability create/rotate/revoke command, no transition receipt, and no semantic validator for the lifecycle transaction (`CONTRACT-REGISTRY.json:335-342,719-734`). The generic fact-attribution validator discriminates only action and WDR commands (`python_runner.py:3230-3242`).

Thus one bootstrap implementation can replace the entire registry under an invented command while another models per-producer revocation; both can increment epoch, but neither has a shared wire transaction. Recovery cannot reconstruct command-derived authority for the most security-sensitive state.

**Required correction:** add a capability-registry command with expected registry ID/epoch, explicit operations, transition invariants, host authorization, exact journal targets, receipt deltas, recovery semantics, and rotation/revocation vectors.

### H6 - The second authority factor has no OS-bound principal contract

AD-1 says serialized capability IDs do not grant authority and that an OS-boundary principal is the second non-wire authority input (`ARCHITECTURE-SPINE.md:83`; `WDR-AND-TRANSACTION-PROTOCOL.md:21`). The package defines only a `sha256:` string for `principal_id`; it does not define how POSIX UID/effective UID, Windows token/SID, executable identity, service account, impersonation, or privilege transitions map to that ID. It also lacks a provisioning/attestation contract for this binding.

The reference fixture obtains the alleged external principal by reading the expected principal back out of the capability registry (`python_runner.py:3198-3211`), so it tests equality, not an OS trust boundary. One adapter may trust an environment variable, another may hash a UID, and a third may bind a Windows SID. They grant different authority while passing the same wire vectors.

**Required correction:** register platform-specific principal adapters and binding receipts, define effective-identity/impersonation rules and canonical principal IDs, and require native negative tests proving wire/env substitution cannot manufacture the host principal.

### H7 - Repair nonce persistence is hard-coded outside the registry

Repair safety depends on durable single-use nonce state across process restart (`ARCHITECTURE-SPINE.md:115-119`; `WDR-AND-TRANSACTION-PROTOCOL.md:93-96`). The protocol says the nonce path is its filesystem token, but does not name a base path through the registry. The 33 `runtime_paths` contain no nonce template (`CONTRACT-REGISTRY.json:60-92`). The Python handler hard-codes `state/nonces/h_<token-hash>.json` (`python_runner.py:4576,4800-4803`).

This is exactly the kind of self-selected path AD-6/AD-11 reject elsewhere. Implementations can persist the same nonce document at different locations, miss one another's consumed token, and reapply a repair after restart.

**Required correction:** add `repair_nonce_template` to registry runtime paths, specify its root and token derivation, require journal target equality to it, and test restart against an independently enumerated on-disk nonce store.

### H8 - Journal restart discovery omits the manifest, marker, tombstone, and recovery-receipt filenames

AD-10 pins the journal directory and image locators, but neither registry nor protocol fixes where the manifest, terminal marker, remove tombstone, or journal-local recovery receipt lives inside that directory (`ARCHITECTURE-SPINE.md:133-137`; `WDR-AND-TRANSACTION-PROTOCOL.md:86-89`). Registry has only `journal_dir_template`; the publication copies are lineage artifacts, not the transaction recovery source (`CONTRACT-REGISTRY.json:60,88-90`).

The harness passes manifest and marker objects directly to `journal_semantics()` and never rediscovers them from disk (`python_runner.py:2505-2549`). Two durable adapters can choose `manifest.json` versus `journal.json`, or different marker/tombstone names, and each pass all object-level vectors while being unable to recover the other's transaction after a crash.

**Required correction:** register exact journal-local manifest, marker, tombstone, and recovery-receipt templates plus creation/flush order and directory enumeration rules. Native fault injection must restart a fresh process that discovers only on-disk bytes.

### H9 - First Panel publication has no convergent absent-state transaction

The schema anticipates bootstrap state: `panelStateV1.current_pointer_id` and publication `before_pointer_id` can be null (`panel-sync-contracts.schema.json:1488-1499,1632-1653`). Yet the registered publication semantic model and reference fixture require schema-valid before-pointer and before-state documents and always `replace` both pointer and state (`python_runner.py:5287-5333,5373-5450`). The only absent-target vector exercises a generic durability primitive, not the complete publication graph (`CONFORMANCE-VECTORS.json:619`).

A fresh memory root therefore has no specified choice among creating pointer/state, pre-seeding panel generation 0, or inventing a synthetic pointer. This blocks a deterministic first full refresh, which AD-12 requires before strict activation.

**Required correction:** define one complete first-publication graph with absent pointer/state preimages, exact generation transition, create operations, receipt nullability, recovery at every target, and idempotent retry. Add the complementary subsequent replace graph.

## Medium Findings

### M1 - AD-8 and the registry name different refresh-status paths

AD-8 and the implementation plan fix the mutable status at `views/management-panel/refresh-status.json` (`ARCHITECTURE-SPINE.md:121-125`; `ANALYSIS-AND-OPTIMIZATION-PLAN.md:368,482`). The registry fixes `state/panel-refresh-status.json` (`CONTRACT-REGISTRY.json:74`), and both reference handlers enforce the registry path (`python_runner.py:5748,5787-5800`). An inspect writer following the AD becomes invisible to a reader following AD-11.

**Required correction:** choose one path and update every normative reference and hash. The registry location is preferable because this object is mutable runtime state, not a published view.

### M2 - The registered live-inspect validator scope excludes most of the state the algorithm claims to reload

The `live-inspect-semantics/1.0.0` registry scope lists lineage, refresh status, pointer, fact generation, envelopes/manifests/receipts, runtime paths, and lock profile, but omits activation state, migration attestation, capability registry, release evidence, roots, writer build/fence receipts, ledger state, action flow, WDR states, and sidecars (`CONTRACT-REGISTRY.json:734`). Protocol/AD-12 require all of them (`WDR-AND-TRANSACTION-PROTOCOL.md:82`; `ARCHITECTURE-SPINE.md:149`).

The Python runner compensates by calling a separate strict activation function over extra package fields (`python_runner.py:5804-5820`). A registry-driven handler dispatcher can validly load only its declared scope and produce a weaker inspect than the monolithic fixture.

**Required correction:** make the inspect validator scope exact and closed, including the new release evidence contract, and require handler declared/read sets to match.

### M3 - `source_as_of` has no cross-document derivation rule

Panel, audit, Program Status, Roadmap, meeting packs, and refresh receipts all carry `source_as_of` (`panel-sync-contracts.schema.json:904-908,1795-1811,1846-1914`). No AD, registry semantic rule, or protocol clause requires these values to equal selection-policy `as_of`, one another, refresh lock acquisition, or the maximum source observation time. The fixture simply inserts fixed literals (`python_runner.py:3607,4152,5534`).

Two producers can consume identical immutable leaves and emit different content IDs or misleading freshness timestamps. This weakens AD-8 reporting and the stated idempotency convention.

**Required correction:** define the value as a deterministic field, most simply exact equality to selection-policy `as_of`, and validate equality across all same-generation payloads and receipts.

### M4 - Owned-section patch rendering is not byte deterministic

`wdrOwnedSectionMutation` gives only `section`, `mode`, and `lines` (`panel-sync-contracts.schema.json:440-448`). The protocol pins section order and byte preservation but does not define replace/append framing for empty lines, blank-line boundaries, pre-existing trailing whitespace, or heading-like content (`WDR-AND-TRANSACTION-PROTOCOL.md:43-48`). The Python design model joins lines with LF and uses one ad hoc newline on append (`python_runner.py:1173-1183`), but the vectors check mixed-command counters rather than owned-section exact bytes.

Two WDR engines can produce different durable Markdown and fingerprints from the same valid command.

**Required correction:** specify an exact section renderer/parser, reject ambiguous heading content, and add byte-exact replace/append/no-op vectors for every owned section.

### M5 - Activation state is validated live but not content-bound by its attestation

AD-12 says the migration attestation closes strict activation state (`ARCHITECTURE-SPINE.md:149`; `WDR-AND-TRANSACTION-PROTOCOL.md:100`). `writerFenceMigrationAttestationV1` carries only `activation_epoch`; it does not bind activation `state_id`, mode, `changed_at`, or raw bytes (`panel-sync-contracts.schema.json:1322-1388`). The handler validates activation independently and compares epoch/attestation ID, but changing `changed_at` and recomputing `state_id` leaves the attestation unchanged (`python_runner.py:2035-2043,2280-2288`).

This does not currently permit a mode/epoch bypass, so it is Medium, but it disproves the claimed immutable content closure and gives different activation-state identities under one attestation.

**Required correction:** define a cycle-free activation transition receipt or attestation preimage that binds all activation fields except the back-reference, and validate its exact bytes/identity.

## Production Reality and Evidence

- The intended target modules `skills/adp-fact-transaction`, `skills/adp-wdr-mutation`, and `skills/adp-panel-refresh` are absent. Existing production Management Panel/status/audit code remains the brownfield implementation; this is consistent with `pending`, not evidence of strict conformance.
- The production action-flow writer and consumer use the brownfield action-flow schema, corroborating H1 (`skills/adp-status-sync/scripts/sync_status.py:1046`; `skills/adp-flow-graph/scripts/flow_graph.py:478`).
- Registry/schema/protocol/suite/runner raw SHA-256 values match the spine pins: registry `68da99c0...f064`, schema `ea7f20f5...a5d5`, protocol `11f0784a...ec26`, suite `b898b487...2c11`, Python runner `caf5b522...aead`, Node runner `a23bb743...34b3`.
- Both checked-in result receipts report 486 passed and 0 failed, but both are design fixtures and both explicitly say native durability was not exercised. A fresh local replay was started as a read-only probe and timeboxed before completion; no finding above depends on that incomplete replay.
- Raw registry status was probed directly: implementation conformance is `pending` and production trust roots are empty. These gates must remain unchanged until the normative contradictions are fixed and real native evidence passes.

## Gate Decision

Do not finalize the spine or authorize implementation handoff as an interoperable strict design. Resolve H1-H9, regenerate all affected raw hashes and design results, then commission two genuinely independent implementations that receive only the registry/schema/protocol package and durable on-disk inputs. The acceptance test must include cross-reading each other's action flow, capability rotation, crash journals, repair nonces, release-evidence store, first publication, and restart-safe live inspect.
