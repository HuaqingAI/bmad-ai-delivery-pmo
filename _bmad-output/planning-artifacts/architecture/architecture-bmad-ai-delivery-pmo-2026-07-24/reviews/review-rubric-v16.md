# BMad Architecture Rubric Review v16

## Gate Verdict

**REJECT. Critical: 0. High: 2. Medium: 1. Low: 0.**

The spine now covers all five diagnosed synchronization failures, the copied registry inventories and artifact hashes are current, the named runtime policy is supportable, and the brownfield/deployment envelope is strong. The gate still fails because the raw registry does not register the authority inputs that the executable live-inspect, mutation, and repair handlers actually use. A second implementation can follow the declared scopes and omit strict activation or obtain mutation authority from the transaction graph while still matching the registry's named algorithms. The design-only strict fixture also changes the registry policy while retaining the raw production registry hash.

| Severity | Count |
| --- | ---: |
| Critical | 0 |
| High | 2 |
| Medium | 1 |
| Low | 0 |

## Frozen Review Base

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `30dd97d371a9378077d1e3416fc0ad8ec874100ba10d4b1070d93e171ef59fdc` |
| `contracts/CONTRACT-REGISTRY.json` | `68da99c0336f83cf70a2b54c93262355b7d20bf7ffdfdf6f0079be611523f064` |
| `contracts/panel-sync-contracts.schema.json` | `ea7f20f5f99f131fede251703b57f0b77e10b57a4e7a6cbf58d2f135b104a5d5` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `11f0784a667cef959a35dc9f55d0331c0ba83db8fc1947376bc1c6a36c18ec26` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `b898b48711a86eea5822b0b34099db22423e20596c888dbbf3c851762fc32a11` |
| `contracts/fixtures/PANEL-V1-COMPATIBILITY.json` | `3b96b7806e3027df17f12cc48668d13980245dc9fb34fcb8a226525a962b6fe7` |
| `contracts/conformance/python_runner.py` | `caf5b522d511c83a9affde0dc1986f99589af0706f86e72a93e5a614b5304ead` |
| `contracts/conformance/node_runner.mjs` | `a23bb74311da2f84930d18db25e7db3dfd41a9d677516670a74aa1bdae934b3f` |
| `contracts/conformance/panel_v2_consumer.mjs` | `eaf497fbf79eaedface1408b1ea1679aec7d7e85709fd9e3a386550855bbc423` |

## Critical Findings

None.

## High Findings

### H1 - Live inspect's registered scope omits the strict gate it is required to enforce

**Evidence.** AD-12 requires every restart-safe inspect to reload and close the activation state, writer-fence attestation, capability registry, production release evidence, writer build/fence inventory, current pointer, full immutable lineage, and live facts; rollback, epoch drift, writer drift, pending registry, or design-only evidence must return `migration-required` (`ARCHITECTURE-SPINE.md:145-149`). Protocol sections 6 and 9 repeat that requirement (`WDR-AND-TRANSACTION-PROTOCOL.md:78-82,98-102`).

The raw registry instead scopes `live-inspect-semantics/1.0.0` only to the lineage index, refresh status, pointer, fact state, generation/envelope/manifest/receipt/Panel contracts, `runtime_paths`, and `lock_profile` (`CONTRACT-REGISTRY.json:734`). It omits `strict-writer-fence-activation/1.0.0` and that validator's activation, attestation, capability, release trust/runtime policy, root, writer manifest/fence, ledger/action-flow/WDR-sidecar, publication receipt, and Panel-state inputs (`CONTRACT-REGISTRY.json:733`). Its registered algorithm text likewise mentions pointer/lineage/leaves but not activation or release.

The Python implementation compensates with an unregistered dependency by calling `strict_activation_control_semantics()` before lineage inspection (`python_runner.py:5782-5805`); Node does the same (`node_runner.mjs:3377-3391`). The handler-spec table merely copies the incomplete registry scope (`python_runner.py:3767-3769`), so the exact-scope closure check cannot detect the discrepancy.

**Divergence.** Child A follows AD-12 and the current handler, loading the strict gate inputs before returning `fresh`. Child B implements the registry's exact declared scope and checks only the published lineage and live leaf/fact state. After activation rollback or release/writer invalidation with unchanged leaves, A returns `migration-required` while B can return `fresh`. Both can claim the same registered validator ID/algorithm/scope.

**Required correction.** Register live inspect as an explicit composition of the strict-writer-fence validator plus its complete authority scope, or expand the live-inspect scope itself to every input it reads. Derive and compare the handler's actual authority/read set to that scope. Keep rollback, epoch, attestation, capability, writer-build, pending-registry, and design-only-evidence negatives, but make them fail specifically when a registered input is omitted or substituted. Regenerate all dependent hashes and results. Disposition: fix before handoff.

### H2 - Mutation and repair conformance still obtains external authority from the graph it validates

**Evidence.** AD-1 explicitly says serialized issuer and wire-embedded capability data do not grant authority. Runtime must load canonical raw capability-registry bytes from the registered path under the fact lock and obtain the host principal separately from the OS boundary; ordinary mutation, repair, and recovery use that same authority (`ARCHITECTURE-SPINE.md:79-83`). Protocol sections 2, 4, and 8 repeat the non-wire authority boundary (`WDR-AND-TRANSACTION-PROTOCOL.md:18-22,49-52,91-96`).

The registry scope for `fact-receipt-attribution/1.0.0` omits `strict_rollout` even though the handler reads its authoritative writers and writer specs to decide the complete active capability set and permissions (`CONTRACT-REGISTRY.json:723`; `python_runner.py:3252-3271`). The repair scope says it reuses fact attribution but also omits `strict_rollout`, `runtime_paths`, and `wdr_field_section_map` required by that path (`CONTRACT-REGISTRY.json:725`). Neither registered scope represents the fact-lock acquisition or current attestation binding that makes the loaded capability bytes authoritative.

More importantly, the positive conformance context is not independent: `runtime_authority_fixture(graph)` reads both the capability-registry bytes and the host principal from the same graph being validated (`python_runner.py:3198-3202`), and the registered handler dispatch passes those graph-derived values straight back into `fact_attribution_semantics()` (`python_runner.py:5934-5937`). Repair uses the equivalent helper (`python_runner.py:3205-3210,5939-5942`). Node mirrors this construction (`node_runner.mjs:1763-1767,3460-3462`). Mismatch negatives prove cross-field equality, not that authority came from the registered locked runtime source or OS boundary.

**Divergence.** Child A obtains raw capability bytes, current epoch/attestation, and principal independently from the locked runtime boundary. Child B derives those values from a self-consistent mutation or repair package, as the positive harness does. The latter can pass the 486-vector design gate without implementing the authority separation that prevents a serialized graph from authorizing itself.

**Required correction.** Define a registered runtime-authority context outside the wire graph, including the resolved capability-registry root/path/raw hash, acquired fact-lock profile, current activation/capability epoch or attestation binding, and non-serialized host principal. Add `strict_rollout` and all transitively consumed registry inputs to the fact and repair scopes. Build positive fixtures from an independently supplied context and add a fully self-consistent forged graph whose embedded capability/principal differs from either live source; it must fail even after all graph identities are rebound. Disposition: fix before handoff.

## Medium Findings

### M1 - The design strict-gate fixture changes registry policy without changing the registry hash

**Evidence.** The raw registry at hash `68da99c0...f064` has `implementation_conformance_status=pending` and zero production trust roots (`CONTRACT-REGISTRY.json:18,123`). `design_release_registry_fixture()` deep-copies it, changes the status to `passed`, and inserts two fixture trust roots (`python_runner.py:1573-1586`). The runner then deliberately assigns that changed object the unchanged raw registry hash (`python_runner.py:5894-5899,6413-6415`). The strict validator checks the changed object's status and compares attestation fields to the supplied hash string, but it never verifies that the registry object's canonical bytes hash to that string (`python_runner.py:2014-2049`). Node uses the same pattern (`node_runner.mjs:783-795,3441-3444,3833-3835`).

The comment labels this a design-only gate mock, and both published results remain honest `design-fixture-check` receipts with native durability false, so this is not current production authorization. It still weakens AD-11's claim that loaded raw registry bytes, rather than a hash-shaped field, are the authority (`ARCHITECTURE-SPINE.md:139-143`). The all-validator design pass exercises strict activation against policy bytes that do not correspond to the reported registry hash.

**Required correction.** Give the design mock a separately serialized registry document and recomputed hash/contract references, or make validator APIs accept raw registry bytes and verify their hash before policy access. The current raw-registry strict case should remain an expected `migration-required` result; a mock-positive case must be explicitly isolated from the raw package identity. Disposition: correct with the High fixes and regenerate evidence.

## Checklist Assessment

| Rubric dimension | Result | Evidence / note |
| --- | --- | --- |
| Real feature-altitude divergence points | Fail | The five product divergences are fixed, but strict inspection and mutation authority still admit two incompatible implementations through H1/H2. |
| Every AD enforceable and preventative | Fail | AD-2 through AD-10 are strongly pinned; AD-1, AD-11, and AD-12 depend on authority inputs absent from the registered semantic scopes. |
| Deferred safety | Pass | Panel scope, push/watchers, database migration, fuzzy action resolution, offline archive freshness, and quantitative lag/SLO semantics are deferred without weakening current correctness. |
| Named technology currentness | Pass | On 2026-07-25, Node 22 and 24 remain supported LTS lines; Python 3.10 remains in security support, though near its 2026-10 end; Draft 2020-12 and RFC 8785 remain current/fit. |
| Brownfield ratification | Pass | The 20-column ledger is preserved before adding column 21, Panel v1 is losslessly nested, current local CLI/file deployment remains, all 23 source pins match, and the frozen regressions pass. Missing new coordinator/engine/refresh modules are correctly treated as implementation work and strict status remains pending. |
| Five reported problems | Pass | Existing-action patching, typed WDR current updates, live-source freshness, complete ledger/WDR drift, and action IDs/repair batches are covered by AD-2/3/4/5/7 and the capability map. |
| Operational/environmental breadth | Partial | Deployment, POSIX/Windows durability, locking, crash recovery, rollback, migration, inspect, evidence, and deterministic counts are covered. H1/H2 leave the actual runtime authority envelope non-convergent. |
| Evidence and strict-production status | Partial | Hashes, counts, vectors, results, source pins, and regressions verify; M1 means the strict positive is not byte-bound to the registry hash it reports. Raw status correctly remains pending. |

## Independent Verification

- Architecture lint: **PASS**, 0 findings, using direct `python3` because `uv` is unavailable.
- Registry inventory: exactly **50 contracts, 23 source pins, 9 enumerators, 7 profiles, 7 payload bindings, 4 nested bindings, 6 Panel bindings, 15 DAG edges, 46 typed array rules, 15 identity-set rules, 3 semantic sequence rules, 33 runtime paths, and 14 semantic validators**. These now match AD-11, the protocol, and the plan.
- Contract suite: exactly **486 IDs, 486 unique IDs, no duplicates**. Fresh fixed-time CPython 3.12.13 and Node 24.16.0 runs each passed **486/486, 0 failed**, had identical passed-ID sets, and reproduced the checked-in result files byte-for-byte at SHA-256 `a6dd2e88...3d87` and `70066f1f...2fd3`.
- Both result receipts are correctly `design-fixture-check`, have `native_durability_exercised=false`, and do not satisfy production release.
- All **23/23** pinned source artifact raw hashes match. All **9/9** in-scope schemas parse and declare JSON Schema Draft 2020-12.
- Panel v1 compatibility regeneration reproduced the fixture byte-for-byte at `3b96b780...b6fe7`.
- Brownfield regression baseline: **205/205 PASS**: meeting-sync 31, status-sync 29, state-audit 63, Management Panel 28, panel-audit 12, state-prepass 10, panel-model 6, Panel contract 26. Program Lead additions: **17/17 PASS**.
- The five original production-code failures remain observable in the pinned brownfield sources; this is consistent with a draft build substrate. New `adp-fact-transaction`, `adp-wdr-mutation`, `adp-panel-refresh`, and runtime-bootstrap implementation modules are not yet present. AD-12 correctly keeps production strict publication disabled until migration and native evidence exist.
- Current technology check used the official Node release schedule (22 EOL 2027-04-30; 24 EOL 2028-04-30), Python version-support table, JSON Schema specification page, and RFC Editor text for RFC 8785.

## Gate Decision

**FAIL: 0 Critical, 2 High, 1 Medium, 0 Low.** Do not finalize the spine as an enforceable implementation contract until the live-inspect and mutation/repair authority scopes are made complete and executable from independent runtime inputs. Keep `implementation_conformance_status=pending`; after correction, regenerate the registry/protocol/suite/runner/result hashes and rerun this gate from a fresh context.
