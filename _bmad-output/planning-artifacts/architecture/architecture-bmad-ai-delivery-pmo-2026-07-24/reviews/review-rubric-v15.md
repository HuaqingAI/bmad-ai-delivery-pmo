# BMad Architecture Rubric Review v15

## Gate Verdict

**REJECT. Critical: 0. High: 2. Medium: 1. Low: 0.**

The spine covers the five reported synchronization failures, ratifies the checked brownfield shapes, and provides a strong deployment/recovery/strict-rollout envelope. The gate still fails because AD-11 and its normative protocol state an obsolete registry inventory for the exact registry hash they pin, and the executable production release gate positively accepts Node.js 18 after that release line reached end of life. `implementation_conformance_status` correctly remains `pending` and must not be promoted.

| Severity | Count |
| --- | ---: |
| Critical | 0 |
| High | 2 |
| Medium | 1 |
| Low | 0 |

## Critical Findings

None.

## High Findings

### H1 - AD-11 and the normative protocol misstate the inventory of the registry they make authoritative

**Evidence.** AD-11 says the exact registry contains 47 contracts, 14 source pins, 43 canonical array rules, 14 identity-set rules, 17 runtime paths, and 13 semantic validators (`ARCHITECTURE-SPINE.md:143`). Protocol section 9 repeats those same counts as normative package evidence (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:100`), and the implementation plan repeats them at its completion gate (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:423`). The registry whose raw hash is pinned by all three documents actually starts those collections at `contracts/CONTRACT-REGISTRY.json:59`, `:154`, `:271`, `:719`, `:735`, and `:752`. Independent structural counting of the loaded JSON produced:

| Registry collection | Stated | Actual |
| --- | ---: | ---: |
| schema contracts | 47 | 50 |
| pinned source artifacts | 14 | 23 |
| canonical array ordering rules | 43 | 46 |
| identity-set fields | 14 | 15 |
| runtime paths | 17 | 33 |
| semantic validators | 13 | 14 |

The other stated counts still match: 9 enumerators, 7 profiles, 7 payload bindings, 4 nested bindings, 6 Panel bindings, 15 DAG edges, and 3 semantic sequence fields.

**Why High.** This is inside AD-11's enforceable Rule and the package's normative protocol, not an incidental status note. An implementation enforcing the prose count must reject the hash-pinned registry; one following the registry will accept all 50 contracts and 14 validators. A third implementation can use the obsolete counts as a reason to omit the newly added lineage, writer-fence, runtime-path, or validator records. The architecture therefore creates the exact same-version contract divergence AD-11 says it prevents.

**Required correction.** Derive and update every inventory receipt from the loaded registry rather than maintaining copied counts. Reconcile the spine, protocol, and analysis plan; regenerate every affected protocol/registry/suite/result hash and receipt; then rerun the gate. Add a deterministic check that fails when any prose inventory receipt differs from the hashed registry. Disposition: fix before handoff.

### H2 - The production release gate positively accepts an end-of-life Node.js runtime

**Evidence.** The registry permits any Node runtime `>=18.0.0,<100.0.0` (`contracts/CONTRACT-REGISTRY.json:5-7`). The registered release handler accepts a production receipt when its runtime falls inside that range (`contracts/conformance/python_runner.py:1598-1603`). Its positive authenticated native pair deliberately uses Node `18.0.0` for the Windows production adapter (`contracts/conformance/python_runner.py:1648-1667`), and that pair is the basis of the passing `release-authenticated-native-pair-runtime-3-10-accepted` vector. The spine's Stack lists Python, JSON Schema, JCS, filesystem adapters, Markdown, and HTML but does not disclose Node as a production runtime (`ARCHITECTURE-SPINE.md:202-211`).

The official Node.js Release Working Group schedule, checked on 2026-07-25, records Node 18 end of life as 2025-04-30 and Node 20 end of life as 2026-04-30: `https://raw.githubusercontent.com/nodejs/Release/main/schedule.json`. Node 22 and 24 are the supported LTS lines on the review date.

**Why High.** This is not only a broad compatibility declaration: the executable release gate's known-good production evidence uses a runtime that had been unsupported for more than a year. The gate can therefore promote `implementation_conformance_status` using an EOL runtime, defeating the good-spine current-technology requirement and creating an unsupported production security/operations baseline. The `<100` upper bound also admits unreviewed future and non-LTS majors.

**Required correction.** Make the production Node policy explicit in the Stack and constrain it to currently supported LTS lines (prefer an allowed-major set or bounded supported bands, not `<100`). Change the positive native receipt fixture to a supported Node line and add negative vectors for Node 18, Node 20 after EOL, unsupported odd releases, and unreviewed future majors. Regenerate the package hashes/results. Disposition: fix before handoff.

## Medium Findings

### M1 - Promised freshness metrics have no cross-implementation semantics and are not Deferred

**Evidence.** P2 promises source-to-projection lag, projection-to-Panel lag, pending invalidation count, drift count, and refresh success/failure/reuse counts (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:476-483`). AD-8 fixes the mutable refresh-status location and high-level contents (`ARCHITECTURE-SPINE.md:121-125`), but no AD or contract fixes each metric's timestamp pair, unit, population, aggregation window, reset/retention rule, or owner. The Deferred list does not defer telemetry or name a revisit condition (`ARCHITECTURE-SPINE.md:253-259`).

**Impact.** Two implementation teams can both deliver the P2 metrics while reporting incompatible lag and outcome values, especially around reused nodes, partial failures, retries, and source timestamps. Core publication safety is unaffected, so this is not a High finding, but the operations slice is not implementation-convergent.

**Required correction.** Either add a small telemetry contract that derives every metric from registered receipt fields, or move operational metrics to Deferred with a concrete revisit trigger. Disposition: discuss, then decide or defer before implementation slicing.

## Low Findings

None.

## Checklist Assessment

| Rubric dimension | Result | Evidence / note |
| --- | --- | --- |
| Real feature-altitude divergence points | Fail | Mutation, projection, repair, and publication boundaries are strong; registry inventory and production runtime policy still diverge. |
| Every AD enforceable and preventative | Fail | AD-1 through AD-10 and AD-12 are backed by typed schemas/validators; AD-11 contradicts the registry it declares authoritative. |
| Deferred safety | Pass | Panel scope, push/watchers, database migration, fuzzy entity resolution, and offline archive freshness are excluded without allowing incompatible current implementations. |
| Named technology currentness | Fail | Python 3.10 remains in security support on the review date and Draft 2020-12/RFC 8785 remain fit; production Node 18 is EOL. |
| Brownfield ratification | Pass | The 20-column ledger is preserved before adding column 21; Panel v1 is nested intact; local CLI/file deployment is retained; 23 source pins and 205+17 regressions pass. |
| Five user-reported capabilities | Pass | Existing-action patch, typed WDR current update, live-source Panel validation, full ledger/WDR drift, and exact action IDs/repair batches are governed by AD-2/3/4/5/7 and mapped at `ARCHITECTURE-SPINE.md:241-251`. |
| Structural breadth | Partial | Deployment, POSIX/Windows environments, locks, crash recovery, rollback, migration, inspection, and strict activation are explicit. Operational metric semantics remain silent. |
| Evidence and strict-production status | Pass with H1 | Artifact hashes and result receipts validate; design evidence is correctly non-native; registry status remains pending. Inventory prose is stale. |

## Independent Verification

- Architecture lint: direct `python3 .../lint_spine.py` fallback passed with `ok=true`, 0 findings. The documented `uv run` wrapper could not be used because `uv` is not installed in this environment.
- Raw SHA-256 values exactly match the spine for registry `7a36b294...efa`, schema `b2629be5...e73`, protocol `81bcfcbc...e87`, suite `f99caeb3...a8d`, Panel v1 fixture `3b96b780...6fe7`, Python runner `12189b3a...d24b`, Node runner `bc0acadc...6b7`, and v2 consumer `eaf497fb...423`.
- Result raw hashes exactly match `ARCHITECTURE-SPINE.md:182-183`: Python `9f3134a9...5870`, Node `d5fe61bb...7943`.
- The fixture has 466 vectors, 466 unique non-null IDs, and no duplicates. Fresh Python 3.12 and Node 24 replays each passed 466/466 with zero failures and identical passed-vector sets. The Node fixed-time result reproduced byte-for-byte; the Python receipt bytes changed only because the exact runtime/executable identity is deliberately part of the result.
- Both checked-in results independently validate against `conformanceResultV1`, have correct recomputed `result_id`, contain 466 passed and 0 failed IDs, declare `evidence_kind=design-fixture-check`, and set `native_durability_exercised=false` (`contracts/conformance/python-result.json:3-14`; `contracts/conformance/node-result.json:3-14`). They are not production evidence.
- Panel v1 compatibility regenerated byte-for-byte equal to the checked-in fixture.
- All nine in-scope JSON schemas parse and declare Draft 2020-12. Python/Node runner and v2-consumer syntax checks pass.
- Brownfield regressions passed: meeting-sync 31, status-sync 29, state-audit 63, panel-audit 12, state-prepass 10, Management Panel 28, Panel model 6, Panel contract 26 = 205; Program Lead additional tests 17/17.
- Both harnesses verified all 23 current pinned source artifacts against raw bytes. The five source-code failures described by the plan remain observable at the pinned checkout and are covered by AD-2, AD-3, AD-4, AD-5, and AD-7 respectively.
- The registry still states `implementation_conformance_status: pending` (`contracts/CONTRACT-REGISTRY.json:117-124`). No production implementation receipt, native Windows run, or real POSIX fault-injection evidence is claimed by this package.

## Gate Decision

Do not finalize the spine or authorize strict production publication. Resolve H1 and H2, regenerate the dependent evidence, and submit the resulting package to a fresh independent gate. Keep `implementation_conformance_status=pending` until authenticated native production evidence passes the corrected release policy.
