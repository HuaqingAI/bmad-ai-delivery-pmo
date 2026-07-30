# Architecture Package Data-Consistency Review v10

## Verdict

**FAIL. Critical: 0. High: 3.**

The v10 package materially closes the prior Panel source-binding/cardinality/composition, state preimage, journal namespace, repair outcome, JCS, ordering, and audit action-ID design findings. The checked-in design receipts are reproducible and correctly remain distinct from deferred production implementation evidence. Three High gaps remain: the workstream catalog can omit a physical WDR and still define `all`; normal WDR fact transactions have no registered capability-scope validator; and the claimed registry-driven semantic dispatch/invalidation evidence is still partly a static registry-shape assertion.

## Frozen Review Base

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `afe920412c07e2e86c63b2d3eafe2616a82cca2291cf5ac0df7cdc06f6a5d67f` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `bc259b4502caf956b40b3ff34111108bc79df7b693df1878f087c81945906edc` |
| `contracts/CONTRACT-REGISTRY.json` | `44933af3193aadbd507e5291c49fe298a13fb93cfbc889b6a81b5710bc207e61` |
| `contracts/panel-sync-contracts.schema.json` | `09fdae139aa006176fa303d19fb63e16214e1bac94f18c766d21d7397c2814be` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `9a6e2ceeee30cae36941a2eeb4bb9c00b86c4863debe67a16eab0513cc3abd27` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `bb2727c73c07c2f10934a4ee35c56697beea120440ad26cf85e64a059c659b75` |
| `contracts/fixtures/PANEL-V1-COMPATIBILITY.json` | `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` |
| `contracts/conformance/python_runner.py` | `99e2cdc63677982199cf87cb54f184f6a1dcf483322204b16c6915d42fad479f` |
| `contracts/conformance/node_runner.mjs` | `9ffbbbd862427e2e916f065158c222092e46de2ddf8bb7e7b3a9abf33ec39c9f` |
| `contracts/conformance/python-result.json` | `badac9e15c44c0a852e20f086384b387860705833028ad6eb6526b56394abb4c` |
| `contracts/conformance/node-result.json` | `8606efe2e1aae6dfb02ae8ea865ce55e7fcb4ab4cbb2b79afded7f4e776c51f6` |

The package contains 40 contract registrations, 12 pinned source artifacts, 7 projection profiles, 7 outer payload bindings, 4 nested bindings, 15 DAG edges, 6 Panel bindings, 25 canonical-array rules, 8 identity-set rules, and 8 semantic validators. The suite contains 210 unique vector IDs.

## High Findings

### H1 - The workstream catalog is content-addressed but not complete over the physical WDR universe

`resolved_selection()` treats the IDs already present in `policy.workstream_catalog` as the universe and resolves `all` from that list (`python_runner.py:351-358`). Publication eligibility verifies the catalog ID and that every catalog source occurs in generation leaves, but it never performs the reverse check that every physical WDR/sidecar leaf belongs to exactly one catalog row (`python_runner.py:402-435`). Lineage similarly verifies that consumed sources occur in generation leaves without requiring every catalog-relevant generation leaf to be consumed or cataloged (`python_runner.py:1689-1723`). The registry enumerator is defined as "use-selection-policy-workstream-catalog", so it cannot independently prove catalog completeness.

A read-only complete-graph probe added `workstreams/l1-payments/delivery-record.md` as a valid `selected-physical-wdr` generation leaf while leaving the catalog and all downstream scopes at only `l1-checkout`, then recomputed the generation and projection identities. The current Python reference returned `outer=true`, `lineage=true`, and `eligibility=true`. The new `panel-all-catalog-subset-rejected` vector tests a different case: it first adds the second row to the catalog and then omits it downstream. It does not test omission from the catalog itself.

**Impact:** two catalog builders can see the same physical project tree but produce different definitions of `all`; the smaller self-consistent catalog publishes successfully. This preserves the v9 scope-omission false green one level earlier.

**Required fix:** define and pin a catalog-construction input and algorithm independent of the selection policy, preferably the existing brownfield `scope_contract`/valid-WDR discovery semantics or a new registry profile that enumerates every schema-valid physical WDR under the locked memory root. Require exact bidirectional equality among that inventory, `workstream_catalog` WDR/sidecar rows, and catalog-relevant generation leaves before applying include/exclude. Add full-graph vectors for an omitted catalog row, an extra catalog row, WDR-without-sidecar, sidecar-without-WDR, duplicate physical identity, and empty `all`.

### H2 - Capability scope is enforced only for action commands, not normal WDR fact transactions

The protocol says every fact transaction binds one schema-valid typed command and authorizes its actual operation, fields, and sections (`WDR-AND-TRANSACTION-PROTOCOL.md:21,51`). The schema defines WDR create/patch commands with current fields, `meeting_history_append`, and `owned_sections` (`panel-sync-contracts.schema.json:309-380`). However, the sole registered fact-attribution validator scopes only `action-command/2.0.0`, not `wdr-command/1.0.0` (`CONTRACT-REGISTRY.json:494`). Its executable implementation unconditionally validates `actionCommandV2`, derives action command fields, and requires exactly one action delta (`python_runner.py:666-733`). `allowed_sections` is sorted and hashed but is never used for authorization. The 210-vector suite has denied-operation and denied-field action substitutions, but no complete WDR fact graph or denied-section case.

**Impact:** the core target flows in AD-1/AD-3, including status-sync current fields, meeting history, checkpoint sections, workstream create, and normal `refresh_actions`, cannot be accepted by the registered fact validator as specified. An implementation may invent its own WDR field/section mapping or validate only the serialized issuer, while still matching every current design vector.

**Required fix:** make fact attribution consume a discriminated typed-command union covering at least `actionCommandV2` and `wdrCommandV1`. Register both contracts in its scope. Define a deterministic command-to-operation/field/section permission mapping, including create, current fields, `meeting_history_append`, every `owned_sections[].section`, and `refresh_actions`. Require action commands to produce the exact action delta and WDR commands to produce `action_deltas=[]` unless an independently authorized action command is the transaction command. Add complete valid graphs for each producer-owned WDR path and negative graphs for denied operation, field, section, producer, principal, command fingerprint, receipt bytes, state preimage, and target path.

### H3 - Registry-driven semantic dispatch and edge invalidation are still asserted rather than executed

The package claims both runners dispatch all eight semantic validators from the registry and that every DAG edge has changed-input invalidation coverage (`ARCHITECTURE-SPINE.md:143`; `WDR-AND-TRANSACTION-PROTOCOL.md:89`). The runner's `semantic_registry_semantics()` only compares validator IDs and algorithm label strings to a hard-coded map and checks that each scope is a non-empty list (`python_runner.py:866-885`; `node_runner.mjs:547-564`). It does not dispatch functions from those rows or verify the declared scope. A read-only probe replaced the fact validator scope with `["unrelated/9.9.9"]`; `semantic_registry_semantics()` still returned true.

The vector named `registry-dag-change-invalidates-every-edge` invokes exactly the same static `registry_dag_semantics()` call as `registry-dag-derived-complete` (`python_runner.py:2028-2035`). That helper compares declared and profile-derived edges, checks acyclicity/reachability, but never changes an input or observes an invalidated downstream node (`python_runner.py:829-863`). Node mirrors the same behavior.

**Impact:** validator scope drift or a missing runtime invocation is not detected by the claimed dispatch gate, and an orchestrator can implement the right 15-edge graph while failing to invalidate on source changes. The stored 210/210 result overstates these two vector meanings.

**Required fix:** create an executable dispatch table keyed by registered validator ID, with an exact expected scope and an invocation counter/result for each row; reject scope changes as well as ID/algorithm changes. Route complete graph acceptance through that dispatcher instead of direct hard-coded calls. For every declared DAG edge, mutate a source payload/manifest/leaf identity, run the orchestrator invalidation function, and assert the exact direct and transitive invalidated instance set. Rename or narrow existing vectors until those behaviors are actually exercised.

## Verified v9 Closures

- **Panel same-generation binding and cardinality:** closed. Six registry bindings are resolved from same-generation envelopes; exact one/one-per-meeting-kind instance sets, merge-key uniqueness, and target equality are checked. Missing producers and substituted upstream values have negative vectors.
- **Panel v1 composition:** closed at the design boundary. Four dynamic scenarios cover baseline, independent v2 current fields, program-status overlay change, and stale model tamper. The v1 model remains additive and the new current-field pointer is explicitly separate. Production renderer adoption remains deferred evidence.
- **Panel catalog identity:** closed. The Panel catalog is rebuilt from the registry map, schema-validated, and bound to generation identity. H1 concerns workstream inventory completeness, not this Panel catalog.
- **State before/after CAS bytes:** closed for fact and Panel graphs. Fact generation and Panel pointer/state preimages and postimages are schema/identity checked and bound to journal target hashes; receipt paths and bytes are checked.
- **Journal paths and images:** closed. Journal directory and every before/after locator use exact transaction-derived paths; foreign and parent-alias vectors reject.
- **Repair outcomes:** closed at the design boundary. Blocked, committed, reserved-to-invalidated/rolled-back, non-first batch, and orphan null-revision graphs are represented; successful `refresh_actions` has three WDR-side business targets and no action delta.
- **JSON canonicalization:** closed for the reported v9 defects. Python and Node agree on the supplied RFC 8785 ordering, escaping, negative zero, integral float, exponent thresholds, rounding, unsafe-integer, subnormal, and invalid-surrogate vectors.
- **Ordering:** the registry now has 25 array rules plus 8 identity-set rules, and both runners resolve them against schema-valid representative documents with permutation, duplicate, NFC-collision, and nullable-key cases. H3 concerns semantic-validator and invalidation dispatch, not these ordering checks.
- **Audit action IDs:** closed in the target design. Repair findings, batches, command action IDs, and read-set action IDs are cross-checked, including orphan absence. The brownfield `canonical_finding()` implementation still omits public action IDs, correctly remaining production migration work.

## Reproduced Checks

- Architecture lint: **PASS, 0 findings** (run directly with Python because `uv` is unavailable).
- Registry raw pins and declared counts: **PASS**.
- Vector accounting: **210 unique IDs**.
- Python design runner: **210 passed / 0 failed**, regenerated result byte-for-byte equal to `python-result.json`.
- Node design runner: **210 passed / 0 failed**, regenerated result byte-for-byte equal to `node-result.json`.
- Brownfield regressions: **205/205 passed**: meeting-sync 25 plus DingTalk intake 6, status-sync 29, state-audit 63, management-panel 28, panel-audit 12, state-prepass 10, panel-model 6, and Panel contract 26.

## Evidence Boundary

The package correctly labels both reference results `design-fixture-check` with `native_durability_exercised=false`. The registry keeps `implementation_conformance_status=pending`; native POSIX fault injection, native Windows CI, two independent production adapter receipts, and actual target-state rollout remain deferred. Those missing production artifacts are not findings in this review. The three High findings are design-contract or design-evidence gaps that must close before the frozen package can unambiguously accept that later implementation evidence.

## Exit Conditions

1. Bind the workstream catalog bidirectionally to an independently enumerated physical WDR/sidecar universe and reject an omitted catalog row.
2. Extend registered fact attribution to WDR typed commands and enforce operation, field, and section authorization with complete receipt/state/journal graphs.
3. Make semantic validation genuinely registry-dispatched, validate exact scopes, and replace the static DAG edge label check with changed-input invalidation execution.
4. Regenerate the hash/result chain and rerun the independent gate on one frozen target.
