# BMad Architecture Rubric Review v14

## Gate Verdict

**REJECT. Critical: 0. High: 4. Medium: 2. Low: 1.**

The spine covers all five requested capabilities and its registry/schema/protocol/results are internally reproducible, but the current strict-publication boundary is not yet enforceable as claimed. In particular, production conformance is accepted from self-asserted JSON, the writer fence compares an uncontracted live inventory to its own attestation, and the cross-process lock on which every CAS and publication invariant depends has no portable protocol. The proposed Python baseline also conflicts with the brownfield CI without a migration decision. `implementation_conformance_status` correctly remains `pending`; it must remain so.

## High Findings

### H1 - Production conformance receipts have integrity but no evidence provenance

**Evidence.** AD-11 and AD-12 make native POSIX fault injection and native Windows CI the gate for strict publication (`ARCHITECTURE-SPINE.md:143`, `ARCHITECTURE-SPINE.md:149`). The receipt schema only accepts caller-supplied `platform`, `host_platform`, `native_durability_exercised`, `adapter_build_id`, and evidence-class strings (`contracts/panel-sync-contracts.schema.json:1833`, `contracts/panel-sync-contracts.schema.json:1839`, `contracts/panel-sync-contracts.schema.json:1850`, `contracts/panel-sync-contracts.schema.json:1855`). The release validator checks that those strings and booleans say the right thing and that the unkeyed content hash is internally consistent (`contracts/conformance/python_runner.py:1395`, `contracts/conformance/python_runner.py:1405`; the Node implementation is equivalent at `contracts/conformance/node_runner.mjs:733`). The fixture then constructs two such "production" receipts locally from labels and the release gate accepts them (`contracts/conformance/python_runner.py:1426`, `contracts/conformance/python_runner.py:1432`). There is no trusted signer, CI run identity, protected evidence locator, test-log digest, or attestation root that proves the native platform and fault-injection claims occurred.

**Why High.** A conforming implementation can manufacture two schema-valid receipts on one host, claim `native-windows` and `real-posix-fault-injection`, and promote the registry without either native test. That defeats the safety boundary AD-11/AD-12 use to distinguish design fixtures from production evidence.

**Required correction.** Define the production evidence trust boundary. Bind each receipt to a content-addressed build manifest, exact runtime/OS identity, native test log/fault matrix, CI run identity, and an accepted signer or protected attestation root. Register the trust material and verification algorithm, add forged-signer/replayed-run/cross-platform-substitution vectors, and make the release validator verify provenance rather than evidence-class strings alone.

### H2 - Strict writer-fence activation still trusts an out-of-band live inventory

**Evidence.** AD-12 says the attestation closes the authoritative writer build, fence receipt, and capability inventory (`ARCHITECTURE-SPINE.md:149`), while protocol section 9 calls this raw-byte/content closure (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:99`). The attestation schema contains only hash-shaped `writer_build_id` and `fence_receipt_id` values (`contracts/panel-sync-contracts.schema.json:1337`, `contracts/panel-sync-contracts.schema.json:1343`); the registry has no writer-build manifest or writer-fence receipt contract and no runtime locator for either (`contracts/CONTRACT-REGISTRY.json:5`, `contracts/CONTRACT-REGISTRY.json:21`). The fixture derives both IDs from arbitrary label strings (`contracts/conformance/python_runner.py:1453`, `contracts/conformance/python_runner.py:1456`), and the strict validator merely compares the attested array with `package["live_writer_inventory"]` supplied by its caller (`contracts/conformance/python_runner.py:1701`, `contracts/conformance/python_runner.py:1703`; Node equivalent `contracts/conformance/node_runner.mjs:869`). It never opens a writer artifact or fence-receipt document and never derives either ID from loaded bytes.

**Why High.** A stale or coordinated forged attestation plus equally forged `live_writer_inventory` passes even when an authoritative writer binary changed or bypasses the coordinator. That is the exact divergence the writer fence is supposed to prevent.

**Required correction.** Add registered writer-build-manifest and writer-fence-receipt contracts, deterministic writer discovery/enumeration, protected runtime locators, and byte-derived build IDs. The strict validator must load and validate every referenced document and authoritative writer artifact itself. Add vectors that change underlying writer bytes or fence documents while recomputing both the attestation and caller summary; those must still fail.

### H3 - The shared fact/panel lock is load-bearing but has no interoperability contract

**Evidence.** AD-6 requires a fact read lock followed by the panel lock during publication (`ARCHITECTURE-SPINE.md:113`), and AD-10 makes shared locking the basis of CAS, recovery, and ordered mutation (`ARCHITECTURE-SPINE.md:137`). The protocol repeatedly requires shared read/fact locks (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:61`, `contracts/WDR-AND-TRANSACTION-PROTOCOL.md:74`) and only names a private locks root (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:85`). It never fixes lock identity, shared/exclusive primitives, POSIX/Windows acquisition, lock ordering beyond one publication sentence, crash/stale-owner handling, timeout, reentrancy, or supported filesystem semantics. The registry's 17 runtime paths contain no lock row or lock contract (`contracts/CONTRACT-REGISTRY.json:21`), and the 413-vector suite contains no contention, incompatible-lock, stale-lock, or deadlock vector.

**Why High.** Two independently built writers can both satisfy the prose while using incompatible lock mechanisms, so each believes it holds the fact lock and both commit against the same generation. That invalidates the CAS, single-writer, snapshot, and atomic-publication guarantees above the journal layer.

**Required correction.** Register a portable lock protocol with exact lock identities and paths, shared/exclusive modes, acquisition/upgrade rules, global order (`fact` before `panel`), process/crash semantics, timeout/error behavior, reentrancy policy, and local-filesystem/capability assumptions for POSIX and Windows. Add real multi-process contention and crash-release tests to production conformance; design vectors should at least cover key derivation and state-machine/order violations.

### H4 - The Python baseline contradicts the brownfield runtime and is not enforced by receipts

**Evidence.** The stack mandates Python `>=3.12` and says the reference baseline is 3.12 (`ARCHITECTURE-SPINE.md:206`), but the existing cross-platform workflow for a bound writer is explicitly Python 3.10 (`.github/workflows/adp-meeting-sync.yml:17`, `.github/workflows/adp-meeting-sync.yml:23`). Neither the phased plan nor Deferred declares a runtime-upgrade migration. The conformance result schema has no interpreter implementation/version/build field; `host_platform` is only an unrestricted single-line string (`contracts/panel-sync-contracts.schema.json:1833`, `contracts/panel-sync-contracts.schema.json:1840`). The checked-in Python result consequently records only `darwin` (`contracts/conformance/python-result.json:7`). During this review the same checked-in result reproduced byte-for-byte under `/usr/bin/python3` 3.9.6 from the architecture workspace, so the receipt cannot substantiate the claimed 3.12 baseline.

**Why High.** The spine binds existing Python skills but silently raises their supported runtime. One implementation can retain the brownfield 3.10 CI while another uses 3.12-only features, and both can emit acceptable receipts. That is a direct brownfield divergence at the build/deployment boundary.

**Required correction.** Either ratify Python 3.10 as the compatibility floor for this feature or explicitly adopt a repository-wide 3.12 migration with CI/workflow updates and compatibility tests. Add exact interpreter implementation/version/build identity to implementation-conformance evidence and validate it against a registry-pinned runtime policy.

## Medium Findings

### M1 - Typed error behavior is not part of the registry wire truth

The spine calls errors typed and uses them for blocked/degraded routing (`ARCHITECTURE-SPINE.md:159`), while the protocol fixes several exact errors such as `CONTRACT_NEGOTIATION_FAILED`, `SOURCE_CHANGED_DURING_REFRESH`, and `MIGRATION_REQUIRED` (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:8`, `contracts/WDR-AND-TRANSACTION-PROTOCOL.md:74`, `contracts/WDR-AND-TRANSACTION-PROTOCOL.md:99`). However producer, refresh, repair, and recovery schemas generally accept any non-empty string (`contracts/panel-sync-contracts.schema.json:1197`, `contracts/panel-sync-contracts.schema.json:1622`, `contracts/panel-sync-contracts.schema.json:1657`, `contracts/panel-sync-contracts.schema.json:1701`). Only a few legacy-adapter codes are fixed in registry records (`contracts/CONTRACT-REGISTRY.json:560`). Independent implementations can therefore return different codes and retry classes for the same failure while remaining schema-valid. Register an error catalog per operation/surface, including retryability and publication effect, and validate exact semantic error selection.

### M2 - Brownfield diagnosis sources are cited but not pinned

The spine identifies nine production scripts as the analysis sources (`ARCHITECTURE-SPINE.md:21`), and the plan makes exact line-level claims against them (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:73`, `ANALYSIS-AND-OPTIMIZATION-PLAN.md:87`, `ANALYSIS-AND-OPTIMIZATION-PLAN.md:95`, `ANALYSIS-AND-OPTIMIZATION-PLAN.md:105`, `ANALYSIS-AND-OPTIMIZATION-PLAN.md:118`). The 14 registry pins start with contract inputs and assets (`contracts/CONTRACT-REGISTRY.json:100`) and include the Panel composer, but not those diagnosis scripts. The claims are correct at the reviewed checkout, yet a later source edit would leave all normative hashes and runners green while invalidating the brownfield analysis. Bind the analysis to a repository commit/tree ID or add a non-wire evidence manifest for these source files; keep it separate from the production contract hash if avoiding registry churn is intentional.

## Low Finding

### L1 - P2 metric definitions are not yet operationally testable

The plan names source-to-projection lag, projection-to-panel lag, pending invalidations, drift count, and refresh outcome/reuse metrics (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:476`, `ANALYSIS-AND-OPTIMIZATION-PLAN.md:482`) but does not define owner, unit, timestamp pair, aggregation window, retention, or alert threshold, and the spine neither adopts nor defers those choices. This does not block the five requested synchronization fixes, but P2 cannot be split across implementations without metric drift. Add a small telemetry contract or move metric semantics into Deferred with a revisit trigger.

## Checklist Assessment

| Rubric dimension | Result | Notes |
| --- | --- | --- |
| Load-bearing divergence points | Fail | Fact/panel locking and strict evidence provenance remain undefined. |
| Every AD enforceable | Fail | AD-11/AD-12 can be satisfied by self-asserted receipts/inventory; Python baseline is not receipt-enforced. |
| Deferred completeness | Partial | Main scope exclusions are explicit, but telemetry semantics and filesystem/lock envelope are silent rather than deferred. |
| Brownfield fit | Fail | Data shapes, Panel v1, 20-to-21-column ledger migration, and local file deployment fit; Python 3.12 conflicts with current 3.10 CI. |
| Requirements coverage | Pass | Existing action mutation, WDR current fields, live source validation, drift alerts, exact action IDs, and batch repair all map to adopted ADs (`ARCHITECTURE-SPINE.md:241`). |
| Operational/environmental envelope | Fail | Local CLI/file, POSIX/Windows durability, rollback, and pending strict rollout are stated; concurrency-lock and production evidence trust boundaries are not. |
| Evidence consistency | Pass with findings | Counts, hashes, vectors, compatibility fixture, and regressions reproduce; strict/native claims correctly remain pending, but source/runtime evidence is not fully bound. |

## Independent Verification

- Architecture lint: `ok=true`, 0 findings.
- Registry inventory recomputed from current JSON: 47 contracts, 14 source pins, 9 enumerators, 7 profiles, 7 outer payload bindings, 4 nested bindings, 6 Panel bindings, 15 DAG edges, 43 canonical array rules, 14 identity-set rules, 3 semantic sequences, 17 runtime paths, and 13 semantic validators.
- Raw SHA-256 recomputation matches every value in `ARCHITECTURE-SPINE.md:167-176` and `ANALYSIS-AND-OPTIMIZATION-PLAN.md:380-391`, including registry `55764209...3824`, schema `dd8a1af9...a94`, protocol `b81bd332...90e3`, and suite `1cd33eb6...34cc`.
- Suite contains 413 unique vector IDs. Python and Node fixed-time replays each passed 413/413 with zero failures, identical passed sets, and byte-for-byte equality with checked-in result files.
- Panel v1 compatibility fixture regenerated byte-for-byte equal to the checked-in fixture.
- Nine repository schema files parse and declare Draft 2020-12.
- Brownfield regressions reproduced: 205/205 baseline plus 17/17 additional Program Lead tests. Meeting-sync's 31 comprise 25 sync tests plus 6 DingTalk intake tests.
- All 14 pinned source hashes were rechecked by both runners.
- Registry state is still `implementation_conformance_status: pending` (`contracts/CONTRACT-REGISTRY.json:68`); both checked-in results are `design-fixture-check` with `native_durability_exercised=false` (`contracts/conformance/python-result.json:3`, `contracts/conformance/python-result.json:8`). No production implementation conformance is claimed or inferred.

## Gate Decision

Do not finalize or authorize strict production publication. Resolve H1-H4, regenerate all dependent hashes/results, rerun the deterministic and brownfield gates, and submit the updated bytes to a fresh independent review. Production implementation conformance must remain `pending` until authenticated native evidence passes the corrected release gate.
