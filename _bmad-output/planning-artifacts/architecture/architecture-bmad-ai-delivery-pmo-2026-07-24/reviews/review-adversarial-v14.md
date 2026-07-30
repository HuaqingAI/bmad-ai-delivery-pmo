# Independent Adversarial Architecture Review v14

Target: `ARCHITECTURE-SPINE.md` and its normative contract package.

Lens: data integrity, source-to-projection freshness, strict-rollout closure, and deterministic repair. For every finding, construct two one-level-down implementations that can both plausibly claim conformance while producing different facts, WDR bytes, projection freshness, strict activation decisions, or repair boundaries. Prior review conclusions were not treated as evidence.

## Verdict

**FAIL.** The package is substantially stronger than v13 and both checked-in reference runners agree on all 413 design vectors, but the executable contract still certifies a strict activation package that contains no actual projection or Management Panel bytes. Four additional High-severity divergence points remain in live inspection, writer attribution, Meeting Sync History idempotency, and repair grouping. Production conformance must remain `pending`; this package does not authorize strict production publication.

| Severity | Count |
| --- | ---: |
| Critical | 1 |
| High | 4 |
| Medium | 2 |
| Low | 0 |

## Reproduced Evidence

- Architecture lint: PASS, zero findings.
- Python reference runner: 413 passed, 0 failed.
- Node reference runner: 413 passed, 0 failed.
- The two passed-vector ID sets are identical.
- Current raw registry SHA-256: `sha256:55764209eb0d5806299607caed280bf6009c22040897c8790ce5203663fd3824`.
- Current suite SHA-256: `sha256:1cd33eb6f3b24fd5ac6af8f14116ce81b287ce564cbe366bed3cc667595134cc`.
- Both result files are correctly labeled `design-fixture-check`; neither is production evidence.
- `contracts/CONTRACT-REGISTRY.json:68` still declares `implementation_conformance_status: pending`.

The following false-positive probes were executed directly against the current Python reference semantics:

```text
strict_without_projection_bytes= True
strict_document_keys= [
  action_flow, activation_state, capability_registry, current_pointer,
  fact_state, ledger_raw, ledger_state, panel_state,
  publication_receipt, refresh_receipt, root_registry, workstreams
]
null_capability_writers= [
  adp-acceptance-readiness-review,
  adp-l0-reference-sync,
  adp-plan-baseline,
  adp-project-kickoff
]
duplicate_meeting_key_accepted= True
block_count= 2
split_same_group_repair_accepted= True
repair_groups= [
  (l1-checkout, refresh_actions),
  (l1-checkout, refresh_actions)
]
```

These probes are more significant than the green suite result: they demonstrate behaviors that the normative protocol prohibits but the registered reference semantics accept.

## Critical Finding

### C-1 - Strict activation succeeds without loading any canonical projection or Management Panel bytes

**Divergence pair.** Implementation A follows the current reference handler. It accepts hash-shaped projection IDs, manifest IDs, and a Panel ID from a pointer, cross-checks those IDs against refresh/publication receipts, and reports strict activation even though no generation envelope, selection policy, physical inventory attestation, Panel binding catalog, projection envelope, projection payload, dependency manifest, producer receipt, Management Panel envelope, or Panel payload is supplied. Implementation B follows AD-4 and AD-12: it resolves the purported generation and reloads every immutable output, validates raw bytes and identities, recomputes binding/cardinality/freshness closure, and rejects the same package as `migration-required`. The implementations return opposite strict decisions for the same alleged published generation.

**Evidence.** AD-4 requires live open/inspect to recompute leaves, inventory, manifests, receipts, canonical identities, and Panel bindings (`ARCHITECTURE-SPINE.md:97-101`). AD-12 requires the strict attestation to close the latest projection/publication state and says any missing content fails closed (`ARCHITECTURE-SPINE.md:145-149`); protocol section 9 explicitly requires raw-byte/content identity over the publication closure (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:99`). The positive fixture creates the generation, Panel, projection, and manifest identities from arbitrary labels rather than from output bytes (`contracts/conformance/python_runner.py:1500-1513`). Its strict document package contains only roots/capabilities/fact state, ledger/flow, WDR triples, refresh/publication receipts, pointer, Panel state, and activation state (`contracts/conformance/python_runner.py:1600-1618`); the Node fixture has the same omission (`contracts/conformance/node_runner.mjs:822-831`).

The validator checks pointer path templates and compares pointer tuples to receipt tuples (`contracts/conformance/python_runner.py:1756-1775`), then verifies receipt/pointer/state relationships (`contracts/conformance/python_runner.py:1776-1813`). It never loads or validates the bytes named by any projection ID, manifest ID, generation ID, or Panel ID. The Node handler is equivalent (`contracts/conformance/node_runner.mjs:895-918`). The attestation schema binds only the published generation, publication receipt, and current pointer IDs (`contracts/panel-sync-contracts.schema.json:1318-1381`), while the pointer itself carries only output ID, manifest ID, and canonical path (`contracts/panel-sync-contracts.schema.json:1410-1433`). The direct probe therefore removed no required field: the standard positive strict fixture already has no output bytes, and `strict_writer_fence_activation_semantics(...)` returned `True`.

**Integrity impact.** A stale or fabricated Panel can be declared strict-current as long as a mutually consistent pointer and receipt graph is manufactured. This reintroduces the original failure mode at the strongest trust boundary: source facts may be current while the bytes shown to management are missing, stale, or unrelated.

**Required fix.** Redefine strict activation input as a byte-complete, path-resolved publication closure. It must load and validate the exact generation envelope, fresh physical inventory attestation, selection policy, Panel binding catalog, every required canonical projection envelope/payload, every dependency manifest and producer receipt, the Management Panel envelope/payload, refresh/publication receipts, pointer, Panel/fact/activation state, WDR/state/sidecar inventory, and accepted production receipts. Derive expected projection cardinality and immutable paths from the registry, recompute every content identity and binding, and reject missing, unreadable, substituted, subset, extra, or tampered objects. Add strict vectors that independently delete or mutate every required object class, redirect each path, substitute a schema-valid stale payload, and change a Panel byte without changing the pointer graph.

## High Findings

### H-1 - Live-inspect lineage is neither durably addressable nor semantically exercised

**Divergence pair.** Implementation A stores generation envelope, policy, inventory, manifests, producer receipts, and refresh metadata only in ephemeral staging. After a process restart it can resolve the current projection paths but not the evidence needed by live inspect, so it reports `unverifiable`. Implementation B invents local durable locations for the omitted evidence and can report `fresh`. A third implementation could trust the self-described IDs in the pointer and report `fresh` without reloading lineage. None violates a registered path or live-inspect algorithm because neither exists for this closure.

**Evidence.** Protocol section 6 requires live inspect to re-enumerate sources under the published generation/profile and compare fact generation, roots, selection, payloads, manifests, receipts, and leaf fingerprints (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:77-81`). The registry exposes only 17 runtime paths/templates, ending with canonical projection and Management Panel paths (`contracts/CONTRACT-REGISTRY.json:21-38`). It defines no durable path or template for the generation envelope, selection policy, physical inventory attestation, Panel binding catalog, dependency manifests, producer receipts, refresh-run receipt, or mutable `panel-refresh-status`. The current pointer cannot resolve that closure because each row carries `manifest_id` but no manifest path (`contracts/panel-sync-contracts.schema.json:1410-1433`).

The semantic validator registry ends with strict activation and contains no live-inspect validator (`contracts/CONTRACT-REGISTRY.json:596-609`). The only live-inspect vector supplies two caller-authored fingerprint maps (`contracts/fixtures/CONFORMANCE-VECTORS.json:884-907`); the Python dispatch merely checks that the maps differ and that the expected label is `stale` (`contracts/conformance/python_runner.py:6097-6104`). It does not load a pointer, generation, profile, leaf, manifest, receipt, or refresh status and therefore cannot prove restart-safe live inspection.

**Required fix.** Register immutable paths/templates for every lineage object required by inspection and a mutable path for `panel-refresh-status`. Make the current pointer or a content-addressed generation index resolve every object without process memory. Add a registered `live-inspect-semantics/1.0.0` handler that loads actual bytes, validates root and fact generation, re-enumerates each profile from the stored policy/generation, verifies payload/manifest/receipt closure, writes only refresh status, and returns exact `fresh|stale|migration-required|unverifiable` outcomes. Cover restart replay, deletion, unreadable content, path rebound, stale policy, changed root, changed fact generation, leaf change, manifest/receipt substitution, and status-sidecar write failure.

### H-2 - Four authoritative writers are attested with `capability_id: null`

**Divergence pair.** Implementation A accepts a writer build/fence receipt with a null capability and enables strict mode because the current validator treats null as the expected value when the producer is absent from the active capability registry. Implementation B requires every authoritative writer to have a current active capability whose operation/field/section scope covers that writer and blocks activation. After A activates, one of the null-capability writers can mutate projection-relevant facts outside the coordinator without advancing fact generation; B prevents the write or forces it through the fence.

**Evidence.** The registry names nine authoritative writers and one required coordinator fence (`contracts/CONTRACT-REGISTRY.json:5-19`). AD-1 requires projection-relevant fact commits to carry active capability attribution and advance fact generation (`ARCHITECTURE-SPINE.md:79-83`). The attestation schema nevertheless explicitly allows null `capability_id` in every writer row (`contracts/panel-sync-contracts.schema.json:1337-1349`). The fixture builds the inventory by assigning null whenever an authoritative producer is absent from the active registry (`contracts/conformance/python_runner.py:1451-1459`; Node equivalent `contracts/conformance/node_runner.mjs:768`). The validator then treats that null as an exact valid match (`contracts/conformance/python_runner.py:1698-1706`; Node `contracts/conformance/node_runner.mjs:867-871`).

The positive fixture concretely activates with null capabilities for `adp-acceptance-readiness-review`, `adp-l0-reference-sync`, `adp-plan-baseline`, and `adp-project-kickoff`.

**Required fix.** Make `capability_id` non-null for strict writer inventory and require exactly one active current-epoch capability for every authoritative writer. Validate the writer-specific operation, field, and owned-section coverage rather than only capability identity. Reject missing, extra, revoked, stale-epoch, wrong-principal, wrong-scope, and duplicate producer rows. Rebuild the positive fixture with all nine capabilities and add one negative vector per missing writer and per scope dimension.

### H-3 - Duplicate Meeting Sync History keys are accepted despite an exact idempotency rule

**Divergence pair.** Given an existing history block and a new append with the same `(observed_at, entry_id)`, Implementation A parses the existing region, returns a no-op for identical bytes, and rejects different bytes. Implementation B follows the reference renderer, appends the second block, advances `file_generation`, and accepts the resulting WDR. The same meeting replay therefore yields different WDR bytes, counters, fingerprints, audit history, and downstream freshness decisions.

**Evidence.** The protocol requires `(observed_at, entry_id)` uniqueness, same-byte idempotency, and different-byte rejection (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:47`). AD-3 also requires duplicate meeting keys to block (`ARCHITECTURE-SPINE.md:91-95`). The Python renderer sorts only the incoming rows, concatenates rendered blocks, and appends them to the existing region without parsing existing keys (`contracts/conformance/python_runner.py:1009-1012`). The Node implementation does the same (`contracts/conformance/node_runner.mjs:574-577`). No mutation vector exercises duplicate Meeting Sync History replay; the history counter vector appends a single new key (`contracts/fixtures/CONFORMANCE-VECTORS.json:477-489`).

A direct probe appended the same key twice with different command/summary bytes; `complete_wdr_valid(...)` returned true and the final WDR contained two blocks.

**Required fix.** Parse and canonicalize the complete existing Meeting Sync History region before rendering any mutation. Enforce uniqueness across existing and incoming keys, compute the exact canonical block for each row, no-op same-key/same-byte replay, and reject same-key/different-byte replay before journal prepare. Add vectors for duplicate keys within one command, against existing content, same-byte retry, different-byte retry, reordered incoming rows, and mixed current/history commands; run all through full fact attribution so counter and receipt behavior is pinned.

### H-4 - Repair validation permits multiple batches for one mandatory group

**Divergence pair.** For two repairable findings with the same `(workflow, workstream_id, operation)`, Implementation A forms the one combined batch required by the protocol. Implementation B splits the findings across two batches, each with its own read set, command, CAS snapshot, token, journal, and retry boundary. Both pass current schema and semantic validation. A partial failure then produces different committed finding sets and different retry behavior even though the audit input is identical.

**Evidence.** Protocol section 8 defines the group key as exactly `(workflow, workstream_id, operation)` and requires one batch for the same group (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:90-94`). The registry orders repair batches only by `batch_id`, not by the group key (`contracts/CONTRACT-REGISTRY.json:640-641`). The validator checks unique batch/finding IDs and their bidirectional links (`contracts/conformance/python_runner.py:4103-4118`), then validates each batch's action union, read set, digest, and identity in isolation (`contracts/conformance/python_runner.py:4119-4136`). It never checks group-key uniqueness or that the finding union for a group is represented by exactly one batch. The Node validator has the same per-batch structure (`contracts/conformance/node_runner.mjs:2389-2409`).

A direct probe changed the second valid batch to the same `l1-checkout/refresh_actions` group as the first, recomputed the affected batch/finding identities, and `repair_graph_semantics(...)` still returned true.

**Required fix.** Derive groups from all repairable findings before validating individual batches. Require exactly one batch for every non-null group and require its `finding_ids`, action union, command, and read set to equal the deterministic aggregate of that group. Reject split, overlap, duplicate action across group batches, orphan repairable findings, and group-key mismatch. Add split-same-group, overlapping-action, combined-CAS, first-batch partial failure, and retry-after-partial-commit vectors.

## Medium Findings

### M-1 - Noncanonical `\TBD...` collection encodings are accepted

**Divergence pair.** Implementation A accepts only exact `\TBD` as the escaped literal item `TBD` and rejects `\TBDfoo`. Implementation B follows the current parser and decodes `\TBDfoo` as `TBDfoo`; its renderer then emits canonical `TBDfoo`. Two byte encodings therefore represent one logical collection value, and a read/rewrite by B silently changes WDR bytes and fingerprints.

**Evidence.** The protocol reserves bare `TBD` for empty and exact escaped `\TBD` for a literal `TBD` item (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:46`). `_parse_wdr_list` recognizes `\TBD` whenever it occurs at the start of an item, without requiring the item to end or the delimiter to follow (`contracts/conformance/python_runner.py:866-890`). It also does not require parse-then-render byte equality. `_render_wdr_list` escapes only the value exactly equal to `TBD`, so `TBDfoo` renders without the leading slash (`contracts/conformance/python_runner.py:902-911`). The current literal-TBD vector covers only exact `\TBD` and bare `TBD` (`contracts/fixtures/CONFORMANCE-VECTORS.json:477-489`).

**Required fix.** Treat `\TBD` as special only when followed by end-of-item or the exact `; ` delimiter, then require every parsed collection line to re-render byte-for-byte equal to the input. Add `\TBDfoo`, `\TBD\;x`, delimiter-adjacent, backslash-chain, and mixed-item round-trip vectors in both runners.

### M-2 - Manual/managed Next-actions interleaving has no canonical total order

**Divergence pair.** Starting with `manual-A, managed-Z, manual-B, managed-A`, Implementation A preserves manual slots and sorts only values occupying managed slots, producing `manual-A, managed-A, manual-B, managed-Z`. Implementation B compacts all manual entries first and appends sorted managed entries, producing `manual-A, manual-B, managed-A, managed-Z`. Both preserve manual relative order and sort managed IDs as the protocol states, but the WDR bytes, current signature, revision, and fingerprints differ.

**Evidence.** The protocol says manual relative order is preserved and managed entries are sorted by action ID, but does not define their interleaving (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:56`). The reference partition returns two independent lists (`contracts/conformance/python_runner.py:937-952`), and refresh always renders `manual + managed` (`contracts/conformance/python_runner.py:1001-1008`; Node `contracts/conformance/node_runner.mjs:570-572`). The only preservation vector starts with the fixture's single manual `review` entry and adds one managed entry, so it cannot distinguish compaction from slot preservation (`contracts/conformance/python_runner.py:5112-5118`; vector declaration `contracts/fixtures/CONFORMANCE-VECTORS.json:485`).

**Required fix.** State one total-order rule. The current executable behavior can be made normative by declaring that all manual entries are emitted first in their original relative order, followed by all managed entries sorted by action ID; alternatively define stable managed-slot replacement. Add before/between/after manual entries, multiple managed IDs, managed deletion, and idempotent refresh vectors so both implementations must emit identical bytes.

## Confirmed Closures From v13

The following v13 findings are materially closed in the current package and were not reopened:

- Absent, pinned legacy 12-column, and brownfield 20-column ledger bootstrap now run through attributed mixed create/replace migration semantics.
- WDR revision/file-generation transitions now distinguish no-op, current-field, and history-only changes.
- Same-workstream status intents are aggregated so split-command conflict bypass is rejected.
- Basic manual Next-actions preservation and exact managed-marker parsing are implemented; only the total interleaving rule remains open.
- Strict activation state, activation epoch, content-addressed migration attestation, and rollback invalidation are present.
- Program Status current fields and drift findings are recomputed from WDR/current ledger projection content rather than accepted as self-described values.
- Ordinary and repair `refresh_actions` bind exact ledger/ledger-state read preimages and preserve action revisions.
- Fact target derivation, raw before/after proof, journal identities, receipt attribution, and repair action/entity references are substantially closed.

## Required Gate Exit Conditions

The next package may pass this adversarial gate only when all of the following are true:

1. Strict activation byte-validates the complete generation, lineage, canonical projection, and Management Panel closure, with missing/tampered/substituted object vectors.
2. Every live-inspect dependency is durably resolvable from registry paths plus pointer/generation identity, and a registered semantic handler proves restart-safe `fresh|stale|migration-required|unverifiable` behavior from actual bytes.
3. All nine authoritative writers have non-null, active, correctly scoped capabilities in the strict attestation, with negative coverage for missing, stale, revoked, or mis-scoped records.
4. Meeting history enforces whole-region key uniqueness, exact replay idempotency, and different-byte rejection through full transaction attribution.
5. Repair audit validation enforces one deterministic batch per `(workflow, workstream_id, operation)` group and covers split/overlap/partial-retry cases.
6. WDR collection parsing rejects noncanonical escaped forms and Next-actions has one explicit total order with discriminating fixtures.
7. Architecture lint remains clean; Python and Node run the same expanded vector ID set with zero failures; their raw artifact hashes and result identities match the updated package.
8. Independent native production receipts still satisfy the release gate before `implementation_conformance_status` can change from `pending`.

## Reviewer Conclusion

The architecture now models the original meeting-to-WDR-to-Panel synchronization problem with much better fact attribution and projection semantics. The remaining Critical defect is at the final trust boundary: strict mode validates a graph of IDs without validating the output bytes those IDs purport to represent. Until that and the four High findings are closed, `status` must not be finalized for production rollout and `implementation_conformance_status` must remain `pending`.
