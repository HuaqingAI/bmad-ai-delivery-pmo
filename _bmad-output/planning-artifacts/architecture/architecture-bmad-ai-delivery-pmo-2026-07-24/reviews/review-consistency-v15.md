# Architecture Package Adversarial Consistency Review v15

## Verdict

**FAIL. Critical: 3. High: 2. Medium: 2. Low: 0.**

The package does not pass the architecture gate. The prose requires raw pinned runtime authority, a non-serialized host principal, production-only release evidence, rollback-aware inspection, exact immutable lineage, and an exact registry inventory. The registered reference semantics instead permit self-consistent caller packages to stand in for several authoritative reads, accept test-constructed production-labeled evidence in the strict baseline, omit activation/epoch validation from live inspect, and accept a broader lineage shape than the claimed closure. Passing requires zero Critical and zero High findings.

This was a fresh, defensive document review. It did not modify the spine, registry, schema, protocol, vectors, runners, results, or production sources.

## Frozen Review Base

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `4e957d906894f7de10c2e814f8b5ac583fe94b68641742cc6e86b1d3d3a34cbb` |
| `contracts/CONTRACT-REGISTRY.json` | `7a36b2941ebd285d4682f8506bb12467d4525eee0b31a36c9345db47e8b81efa` |
| `contracts/panel-sync-contracts.schema.json` | `b2629be5e871d1eb8c43a839a86b8de165101b0d16788e1b8a5b094902ac4e73` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `81bcfcbc3872cd4fce0ba04899c169462a64d9aa81adb5d0c5b6b8789587de87` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `f99caeb3721503179ae7e4c70c786cc631141f232b83cfe2de6452f386c1ea8d` |
| `contracts/conformance/python_runner.py` | `12189b3af7d521a76e438c2b88376b42493af5c7fd55ab87b415c6d17c52d24b` |
| `contracts/conformance/node_runner.mjs` | `bc0acadc85bcbbb66e51d934935866498dacb5e11cc7a0bb611e6f5943b006b7` |

## Critical Findings

### C1 - Mutation and repair authority can be supplied by the graph being validated

AD-1 says serialized issuer data does not grant authority and requires the engine to validate the host OS principal against the active capability epoch (`ARCHITECTURE-SPINE.md:83`). The protocol repeats that the OS-principal capability is non-serializable and must be supplied by the host boundary (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:21`). The registered fact handler instead takes `graph.capability_registry`, receipt authorization, and proof principal fields from the same caller-provided graph (`contracts/conformance/python_runner.py:3073-3099`), derives a matching capability from that graph (`:3101-3133`), and proves only that its serialized hashes and principal IDs agree (`:3135-3143`). Repair reuses this same graph-local attribution path (`:4668-4673`). The Node handler mirrors this behavior.

**Compliant-child divergence:** child A treats the graph as evidence only and requires a separately obtained live host principal plus raw `state/writer-capabilities.json`; child B treats the graph's internally consistent registry/principal fields as sufficient. Both can satisfy the registered document shapes, but child B can authorize a mutation without the authority boundary promised by AD-1.

**Required closure:** change the semantic API so current capability-registry raw bytes and the host principal arrive through a non-document runtime context resolved from registry paths under the fact lock. Bind their raw hashes into the proof, and add negative vectors where a fully self-consistent graph disagrees with either live authority source. The repair path must receive the same context rather than rebuilding it from repair documents.

### C2 - Test-constructed evidence satisfies the strict activation baseline while the authoritative registry is pending

AD-12 says strict open/inspect/publish requires registry `implementation_conformance_status=passed`, accepted production evidence, and a live migration attestation; the current raw registry is explicitly pending (`ARCHITECTURE-SPINE.md:145-149`; `contracts/CONTRACT-REGISTRY.json:117-132`). Both checked-in receipts are honestly design-only and do not satisfy that gate (`ARCHITECTURE-SPINE.md:178-185`; `contracts/conformance/python-result.json:3-14`; `contracts/conformance/node-result.json:3-14`).

The Python fixture nevertheless constructs receipts labeled `implementation-conformance` (`contracts/conformance/python_runner.py:1648-1687`), inserts a caller field `implementation_conformance_status: "passed"` (`:1894-1897`), and the strict validator checks that caller field rather than `registry.conformance_suite.implementation_conformance_status` (`:1928-1940`). The suite then declares strict open, inspect, and publish valid on that fixture (`contracts/fixtures/CONFORMANCE-VECTORS.json:544-546`) and separately accepts its constructed release pair (`:831`). The Node fixture and validator do the same (`contracts/conformance/node_runner.mjs:915-942`).

**Compliant-child divergence:** child A reads the raw pinned registry and blocks because it says `pending`; child B follows the registered baseline, accepts the caller's `passed` flag plus test-constructed evidence, and enables strict behavior. This directly violates the requirement that test-only evidence cannot satisfy a production gate.

**Required closure:** remove the caller status field from the validation input. Resolve the raw registry status and accepted production receipts from registered authoritative runtime locations, and make the design suite's only valid baseline `migration-required` while the shipped registry remains pending. Production evidence may be tested with a mock gate in a separately labeled unit test, but must never make the normative strict-activation vector pass.

### C3 - Live inspect can report `fresh` after rollback or activation invalidation

Protocol section 9 requires current strict activation state, activation epoch, migration attestation, current capability/writer inventory, accepted production receipts, and live facts to close for **inspect**, with rollback incrementing the epoch and immediately invalidating the old attestation (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:99`). The registered live-inspect scope omits strict activation state, writer-fence attestation, capability registry, release status/evidence, and the strict activation validator itself (`contracts/CONTRACT-REGISTRY.json:733`).

The implementation checks only the caller's `implementation_conformance_status` (`contracts/conformance/python_runner.py:5494-5497`), then reloads the published pointer/lineage and caller-provided fact state (`:5498-5521`) before returning `fresh` when leaf hashes and fact generation match (`:5522-5540`). It never validates the live activation document, epoch, attestation closure, or rollback state. The Node mirror has the same decision at `contracts/conformance/node_runner.mjs:3129-3155`. Current vectors cover leaf/fact/lineage drift but no activation rollback or capability change (`contracts/fixtures/CONFORMANCE-VECTORS.json:989-1000`).

**Compliant-child divergence:** after rollback, child A reads `state/strict-activation.json`, detects legacy/new epoch, and returns `migration-required`; child B evaluates unchanged published leaves and reports `fresh`. Both follow the currently registered live-inspect scope, so the classification is not convergent.

**Required closure:** make live inspect invoke the full strict activation semantic validator from raw runtime bytes before freshness classification. Add rollback, re-enable/new-epoch, attestation replacement, capability epoch change, writer build change, registry-pending, and design-only-evidence vectors; none may yield `fresh` unless the complete current gate closes.

## High Findings

### H1 - Immutable lineage accepts indexed extras because kind, contract, path, and cardinality are not closed

The lineage row Schema independently enums `object_kind`, `projection_kind`, `instance_key`, a patterned `contract_name`, and an arbitrary relative path; it does not bind valid combinations, required nullability, contract identity, or registry-derived path templates (`contracts/panel-sync-contracts.schema.json:1428-1453`). The loader requires store paths to equal the rows listed by the index (`contracts/conformance/python_runner.py:5333-5357`) and validates each row's chosen contract and object ID (`:5358-5377`), but derives exact cardinality only for `projection-envelope` rows (`:5382-5386`). Extra indexed rows of another legal `object_kind`/contract/path combination are loaded and ignored by the required-document reconstruction (`:5387-5421`). The existing "extra" vector adds an **unindexed** store file, so it does not cover this case (`contracts/conformance/python_runner.py:6132-6140`).

**Compliant-child divergence:** child A derives the complete expected row set, contract, nullability, and path for every object; child B accepts any additional schema-valid indexed object that does not replace a required lookup key. They compute different lineage acceptance for the same package.

**Required closure:** define a discriminated lineage-row union by `object_kind`, derive the exact full index row set from selection, registry cardinality, and publication graph, and require equality before loading any object. Add indexed-extra, wrong-kind/contract, non-null singleton metadata, duplicate semantic role, and valid-contract/wrong-template-path vectors.

### H2 - The normative exact registry inventory is stale

AD-11 and protocol section 9 claim exactly 47 contracts, 14 source pins, 43 canonical ordering rules, 14 identity-set rules, 17 runtime paths, and 13 semantic validators (`ARCHITECTURE-SPINE.md:143`; `contracts/WDR-AND-TRANSACTION-PROTOCOL.md:100`). Raw registry counts are instead 50 contracts, 23 pinned source artifacts, 46 canonical ordering rules, 15 identity-set rules, 33 runtime-path entries, and 14 semantic validators. The structured collections begin at `contracts/CONTRACT-REGISTRY.json:59`, `:154`, `:271`, `:719`, `:735`, and `:752`.

**Compliant-child divergence:** child A enforces the literal numeric inventory and rejects or omits the newer rows; child B treats the raw registry as the unique truth and accepts the larger inventory. The architecture cannot simultaneously make the registry unique truth and publish contradictory exact counts.

**Required closure:** remove hand-maintained counts or regenerate them from raw registry lengths. Add a package gate that compares every published inventory claim with the structured registry before hashes/results are accepted.

## Medium Findings

### M1 - WDR history replay identity does not bind an entry to its outer command

Each history record carries `command_id`, but the Schema accepts any syntactically valid value and does not relate it to the enclosing WDR command (`contracts/panel-sync-contracts.schema.json:423-438`, `:450-483`). The protocol defines replay identity only as `(observed_at,entry_id)` (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:47`), and the renderer/merger enforces only that key and rendered bytes (`contracts/conformance/python_runner.py:1152-1170`). The vectors test exact-key replay, exact-key conflict, duplicate exact keys, and order, but not outer/inner command mismatch or reuse of one inner command ID across distinct keys (`contracts/conformance/python_runner.py:5709-5739`; `contracts/fixtures/CONFORMANCE-VECTORS.json:329-344`).

**Compliant-child divergence:** child A requires every appended record's `command_id` to equal the enclosing command and treats that ID as unique replay lineage; child B accepts arbitrary/reused inner IDs so long as `(observed_at,entry_id)` differs. Both satisfy the written key rule but produce different immutable provenance.

**Required closure:** either bind inner `command_id` to the outer command or remove it and use an explicitly named source-event ID with pinned uniqueness semantics. Add outer/inner mismatch, same-command/different-key, multi-entry command, invalid calendar timestamp, and permutation vectors through full fact attribution.

### M2 - Repair partial-retry conformance is asserted from a narrated event list, not executed wire state

The protocol clearly requires batch-ID order, stop on first failure, preservation of the committed prefix, a fresh dry run/token for the failed batch, and retry from current facts (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:92-95`). The only two-batch CAS/retry vector is a prewritten event narrative (`contracts/fixtures/CONFORMANCE-VECTORS.json:953-987`). Python merely filters that list, compares expected committed/invalidated labels, checks monotonic generations, and compares two token strings (`contracts/conformance/python_runner.py:7046-7057`); Node mirrors this at `contracts/conformance/node_runner.mjs:4217-4223`. It does not construct or validate two audit batches, either repair graph, the failed CAS transition, receipt-index restart, or current-fact reread.

**Compliant-child divergence:** one client resumes from the first missing committed receipt and rebinds batch B to current facts; another can replay the supplied event labels while implementing different grouping/restart behavior. Both pass the current vector.

**Required closure:** run two complete schema-valid repair graphs against a shared fact/receipt store, force batch B's first CAS to fail, restart the client from durable receipts, and prove batch A is not rerun while batch B receives a new binding/token against generation 8. Route the sequence through registered handlers rather than comparing fixture labels.

## Covered Without Additional Finding

- **Publication closure:** the design handler derives every projection and Panel path from registry templates, requires one matching journal target per envelope, checks pointer/state/receipt raw hashes and runtime paths, and requires the journal target list to equal the complete derived sequence (`contracts/conformance/python_runner.py:5114-5188`). The publication vectors exercise omitted targets, wrong roles, redirects, substituted preimages, generation jumps, and non-committed markers (`contracts/fixtures/CONFORMANCE-VECTORS.json:473-486`). No separate publication-graph divergence was found. C2 and C3 still prevent this graph from authorizing production publication.
- **Data ownership shape:** current WDR field-to-section ownership and action/WDR target sets are consistent across registry mapping, command permission derivation, and the full fact handler (`contracts/CONTRACT-REGISTRY.json:94-105`; `contracts/conformance/python_runner.py:2447-2476`, `:3151-3166`). C1 is the remaining authority/source-of-truth failure.

## Verification

- Architecture lint: **PASS**, zero mechanical findings.
- Python design harness: **466 passed, 0 failed**.
- Node design harness: **466 passed, 0 failed**.
- Both generated receipts remain `design-fixture-check` with native durability false. These passes confirm internal agreement on the current vectors; they do not satisfy or repair C1-C3, H1, or H2.

## Gate Decision

**FAIL: 3 Critical, 2 High, 2 Medium, 0 Low.** Do not finalize the spine or treat this package as a production strict-publication contract until all Critical and High findings are closed and the gate is rerun from a fresh independent context.
