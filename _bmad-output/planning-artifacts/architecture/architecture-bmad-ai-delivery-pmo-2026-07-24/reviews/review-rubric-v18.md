# BMad Architecture Rubric Review v18

## Gate Verdict

**REJECT. Critical: 0. High: 2. Medium: 0. Low: 0.**

The frozen spine covers the five reported Management Panel synchronization failures and is unusually complete across ownership, freshness, drift, repair, migration, durability, and production-evidence boundaries. The gate still fails because AD-12 promises a consecutive CAS-bound activation lifecycle and fresh-process transition recovery, while the schema and both registered reference handlers do not fully enforce either promise.

| Severity | Count |
| --- | ---: |
| Critical | 0 |
| High | 2 |
| Medium | 0 |
| Low | 0 |

## Frozen Review Base

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `e8743002b5b7a5b012d5dd416d4a3d7378ad171e1484d9c143ed19f17a0cbfb8` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `260617a8ec6f6f7fe61587ab5c35a3369a05285feff619d12ddfcf3265a4ebf5` |
| `contracts/CONTRACT-REGISTRY.json` | `3e72d1148a84fe6e3a1b39845b918d527e414fe2099efe7d606a1c8bf97f9fcd` |
| `contracts/panel-sync-contracts.schema.json` | `0349ac3224d6ffba27aa5fffc5843e93790838cfb9375440450ce51d0a96c58e` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `f13331f08c1dfa914ff02342146ffbf3122b5aeb419ceb1bbb7fec6309cdd990` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `7c2aa9392f4662b124f2bb21fd77b57e1e4454ad09ff10da13062735f7cb833c` |
| `contracts/conformance/python_runner.py` | `fb8e299a2a909427af7888eb2727e0cd2ef3358027e7cba4d3b71b915efb5a29` |
| `contracts/conformance/node_runner.mjs` | `70fb642a640a8dccedb22a6c0d8322ee7c3ee5245c5af852a32938a39f1e310b` |
| `contracts/conformance/python-result.json` | `f0de178dc4eb28cb859a605cbc3670fb863da3c96a741c3b92fbbe876e4f48e0` |
| `contracts/conformance/node-result.json` | `6ffe9c6179d255f6da9e66dc88ca34b2077e282ce99da89f6b720410e10e460e` |

## Critical Findings

None.

## High Findings

### H1 - The activation validator does not enforce one consecutive state chain or the attestation replace preimage CAS

**Evidence.** AD-12 requires `rollback -> reprovision -> record-refresh -> attest -> enable` to be five consecutive, individually journaled, CAS-bound transitions and explicitly rejects wrong preimages (`ARCHITECTURE-SPINE.md:145-149`). Protocol section 9 repeats that each operation has an exact target and that preimage substitution or a skipped step is invalid (`WDR-AND-TRANSACTION-PROTOCOL.md:105-109`). The registry advertises the same algorithm as `ordered-epoch-cas-exact-target-journal-receipt-and-crash-recovery` (`CONTRACT-REGISTRY.json:860`).

The command shape only carries activation and capability epoch/state CAS fields; it has no expected attestation identity or raw hash for the `attest` replace (`panel-sync-contracts.schema.json:1421-1439`). In Python, the `attest` branch sets `expected_before=None` and accepts any non-null journal `before_sha256`, rather than comparing the hash to the actual current attestation bytes (`python_runner.py:2879-2905`). Node implements the same non-null-only check (`node_runner.mjs:2479-2497`). Both validators also keep `previous_receipt` only as a final non-null flag and never require step N's after activation/capability state to equal step N+1's before state (`python_runner.py:2841-2955`; `node_runner.mjs:2451-2527`). The existing `operation-order` vector swaps array entries; it does not cover a correctly named five-step array whose internally valid steps belong to disconnected state chains. The attestation-binding vector changes the new attestation/receipt binding, not the current attestation preimage.

**Divergence.** One implementation can require the on-disk attestation bytes and the immediately preceding committed state as CAS inputs. Another can accept any non-null attestation preimage hash and five disconnected but individually self-consistent steps, as both reference handlers currently do. Both can claim conformance to the same registered algorithm and pass 568 vectors.

**Required correction.** Add an explicit nullable `expected_attestation_id` and/or exact `expected_attestation_sha256` CAS field to the activation command, bind it to the journal before image for `attest`, and validate the actual loaded preimage bytes. Require adjacent after/before activation state IDs, capability registry IDs, epochs, refresh receipt ID, and attestation ID to form one continuous committed chain. Add negative vectors for substituted attestation before bytes and disconnected/rebound adjacent steps in both runners. Disposition: **fix before handoff**.

### H2 - Release and activation recovery vectors do not execute or validate recovery

**Evidence.** AD-12 requires release history and activation transition recovery to be fresh-process, commit/rollback correct, and unable to skip a partial step (`ARCHITECTURE-SPINE.md:145-149`). Protocol section 9 requires an uncommitted crash to restore before, a committed crash to restore after, repeated recovery to be idempotent, and activation recovery to use the ordinary journal mechanism (`WDR-AND-TRANSACTION-PROTOCOL.md:108-109`). The analysis plan likewise says readers recover the journal and write a journal-local recovery receipt (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:376-377`) and claims fresh-process recovery coverage (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:548-570`).

The shared Python recovery helper merely builds an `observed` hash map, then builds `recovered` and `expected` from the same selected journal fields and compares those two identical constructions. It never consumes `observed`, journal images, marker validity, a store, or a recovery receipt (`python_runner.py:2074-2086`). Both release and activation recovery vectors call this helper directly (`python_runner.py:8322-8325,8373-8377`). Node repeats the same tautological map comparison (`node_runner.mjs:1119-1130`) and routes both transition families through it (`node_runner.mjs:5042-5043,5073-5074`). Consequently these passing vectors do not exercise restart, roll-forward, rollback, exact bytes, tombstone behavior, terminal marker rules, or idempotent recovery.

**Divergence.** One implementation can recover the actual journaled target set and emit a bound recovery receipt; another can choose the expected side from marker intent without inspecting disk state or images. The current design evidence labels both conformant, despite materially different partial-crash outcomes.

**Required correction.** Replace the map equality helper with an execution fixture that materializes before/after images and every crash cut in a temporary store, starts a fresh recovery process/adapter, validates manifest and marker bytes, performs roll-forward or reverse-order rollback, verifies every recovered target byte and the journal-local recovery receipt, and repeats recovery to prove idempotency. Run this for every apply boundary of both release and all five activation operations; retain native durability as a separate production gate. Disposition: **fix before handoff**.

## Medium Findings

None.

## Low Findings

None.

## Good-Spine Checklist

| Dimension | Result | Evidence / note |
| --- | --- | --- |
| Real feature-altitude divergence points | **Fail** | The five user-visible synchronization problems are fixed, but H1 and H2 leave activation sequencing and transition recovery behavior open to incompatible implementations. |
| Every AD enforceable and preventative | **Fail** | AD-1 through AD-11 are backed by precise registry/schema/negative-vector surfaces. AD-12's activation CAS/continuity and fresh-process recovery clauses are stronger than their executable handlers. |
| Deferred safety | **Pass** | Action Center UI, push/watchers, database migration, fuzzy existing-action resolution, offline archive freshness, and numeric lag/SLO thresholds have explicit revisit conditions and do not weaken deterministic current freshness (`ARCHITECTURE-SPINE.md:254-261`). |
| Named technology currentness | **Pass** | Node 22 and 24 are supported LTS lines on the review date; Python `>=3.10,<4.0` remains supportable with a registry-enforced `2026-09-01` review deadline; Draft 2020-12 and RFC 8785 remain appropriate current pins (`ARCHITECTURE-SPINE.md:202-212`; `WDR-AND-TRANSACTION-PROTOCOL.md:110-111`). |
| Brownfield ratification | **Pass** | The design preserves the current local CLI/file deployment, 20-column ledger content before adding column 21, Panel v1 as a lossless nested model, and the existing producer boundaries. The 23 source pins and stated 205 + 17 brownfield test baseline make migration intent explicit rather than claiming the target is deployed. |
| Reported problem coverage | **Pass** | Existing-action patching is closed by AD-1/2; WDR current fields by AD-3; live source validation by AD-4/8; WDR-ledger content drift by AD-5; typed action IDs and deterministic repair batching by AD-7/10. |
| Parent/spec consistency | **Pass** | No parent spine is declared. The analysis plan, memlog decisions, protocol, and spine agree on the five capabilities, ownership, rollout, and production evidence boundary. The two findings are executable closure gaps, not contradictory product requirements. |
| Operational/environmental breadth | **Pass** | Deployment mode, POSIX/Windows adapters, lock ordering, journaling, rollback, first publication, migration, inspect, evidence retention, authority, failure states, and support review are decided. No altitude-owned operational dimension is silent. |
| Production evidence honesty | **Pass** | Registry implementation status remains `pending`, production trust roots remain `[]`, and both checked receipts are `design-fixture-check` with `native_durability_exercised=false`; the spine explicitly withholds strict production authorization (`ARCHITECTURE-SPINE.md:178-185`). |

## Evidence Check

- Architecture lint: **PASS**, 0 findings, using `python3`.
- Registry inventory agrees with the spine and plan: **59 contracts, 23 source pins, 9 enumerators, 7 profiles, 7 payload bindings, 4 nested bindings, 6 Panel bindings, 15 DAG edges, 51 typed array-ordering rules, 16 identity-set rules, 3 semantic sequences, 51 runtime paths, 4 owned-fact target profiles, 8 source-time bindings, and 17 semantic validators**.
- Checked conformance receipts report **568/568** for Python and **568/568** for Node with identical passed-vector ID sets and zero failures. This proves self-consistency of the registered test corpus, not the missing semantics described in H1/H2.
- The frozen package keeps `implementation_conformance_status=pending`, `evidence_trust.trust_roots=[]`, and design-only durability flags, so no production-readiness claim is overstated.

## Gate Decision

**FAIL: 0 Critical, 2 High, 0 Medium, 0 Low.** Keep `ARCHITECTURE-SPINE.md` at `status: draft`. Correct H1 and H2, regenerate all dependent schema/registry/protocol/suite/runner/result hashes, and run a fresh independent gate before finalization.
