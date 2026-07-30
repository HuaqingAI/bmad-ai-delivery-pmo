# BMad Architecture Rubric Review v17

## Gate Verdict

**REJECT. Critical: 0. High: 1. Medium: 0. Low: 0.**

The spine and companions now cover all five reported synchronization failures, preserve the brownfield deployment and data shapes, and close the v16 live-inspect and design-registry findings. The gate still fails because the mutation/repair authority validator accepts a self-consistent runtime authority context whose activation epoch, attestation, and memory-root identity are not checked against current locked runtime state. Two child implementations can therefore disagree on whether authority survives an activation rollback while both pass the 512-vector design suite.

| Severity | Count |
| --- | ---: |
| Critical | 0 |
| High | 1 |
| Medium | 0 |
| Low | 0 |

## Frozen Review Base

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `b4f421c9a78514e8006e905dc43e9b5979f6259eb3a6d33758128b54380d4604` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `ed211f41ba30100668aad2b512870a2323b67c42ab202774c353c2f8205bb06f` |
| `contracts/CONTRACT-REGISTRY.json` | `f02b7af8867c846f7d13fbcf2e295fc06abf1c841fb6d0441319acf7240e1f26` |
| `contracts/panel-sync-contracts.schema.json` | `18841ac0824ef24eac64336a71f42d50d75945aff79db0b020805b18f03c64c9` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `0dd17ab3978419929610f6c54ca5b052bff0b2a2bb36f9fbdc4a59f736e280b3` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `73dcbd57ed422230029865f69183baaec4d8eba1d1b1255298a7a41737a4ea62` |
| `contracts/conformance/python_runner.py` | `acad24c7e01aad290339013a3d751f79eabbcedf57601dd3f877477a8c314e61` |
| `contracts/conformance/node_runner.mjs` | `5f9dc284c8151552058bd2ced550cdb6b1791976749c57e7788156cdb8b4fe16` |

## Critical Findings

None.

## High Findings

### H1 - Mutation and repair authority is not bound to the current activation, attestation, or memory root

**Evidence.** AD-1 requires runtime authority to come from canonical raw capability-registry bytes loaded under the fact lock plus a separate OS principal, and requires rollback to increment the activation epoch before reprovision (`ARCHITECTURE-SPINE.md:79-83`). AD-7 requires committed repair to reacquire the same external authority used by normal mutation (`ARCHITECTURE-SPINE.md:115-119`). Protocol section 2 says the native authority context binds the capability-registry root/path/raw hash, exclusive fact lock, activation/attestation/capability epochs, and native principal, and that recovery and repair must reacquire it rather than reconstruct it from serialized state (`WDR-AND-TRANSACTION-PROTOCOL.md:17-23`). Protocol section 8 repeats the same requirement for every repair attempt and restart (`WDR-AND-TRANSACTION-PROTOCOL.md:95-100`).

The raw registry now correctly declares `runtime-authority-context/1.0.0`, `strict-activation-state/1.0.0`, and `writer-fence-migration-attestation/1.0.0` in both the fact-attribution and repair scopes (`CONTRACT-REGISTRY.json:766-771`). The schema also requires `capability_registry_root_instance_id`, `activation_epoch`, `attestation_id`, and `capability_epoch` in the context (`panel-sync-contracts.schema.json:1295-1318`). This fixes the missing declarations identified in v16, but the executable validators do not consume those declarations.

The Python fixture hard-codes the memory root, sets `activation_epoch=1`, and sets `attestation_id=null` without loading either current activation document (`python_runner.py:3432-3456`). The validator recomputes the context ID and checks registry path/hash, lock profile, principal, and capability epoch, but never compares `activation_epoch` or `attestation_id` to a current activation state or attestation; it also compares the root to the fixture UUID rather than a loaded root-registry state (`python_runner.py:3459-3500`). Its registered handler passes only the graph, separately fabricated capability bytes, and that context (`python_runner.py:6260-6274`). Repair reuses the same incomplete fact-attribution gate. Node has the same construction and omissions (`node_runner.mjs:1912-1964,3692-3700`).

This is executable, not hypothetical: starting from a valid Python action graph and independent authority fixture, changing only the context to `activation_epoch=999` and `attestation_id=sha256:ffff...ffff`, then recomputing `context_id`, still returns `true` from `fact_attribution_semantics()`. The existing fully rebound forged-authority vector changes the graph's principal (`python_runner.py:7149-7162`); it does not exercise a stale/rebound activation epoch, attestation, or resolved memory root.

**Divergence.** Child A reloads current root/activation/attestation state under the exclusive fact lock and rejects a context minted before rollback. Child B validates only the context's self-hash, capability bytes, and principal, as both reference handlers do. After rollback increments the activation epoch while capability bytes remain unchanged, A rejects normal mutation/recovery/repair and B accepts it. Both can claim the same registered validator IDs and pass all current vectors.

**Required correction.** Make the native authority acquisition input include the current root-registry state, activation state, and applicable writer-fence attestation loaded under the same fact lock. Validate the context's root instance, activation epoch, attestation ID/null branch, and capability epoch against those current bytes; remove the fixture UUID as an authority check. Derive and compare the handler's actual authority/read set with the registry scope. Add normal-mutation and repair negatives for stale activation epoch, substituted attestation, substituted root, and a fully rebound graph/context where capability bytes remain valid. Regenerate all dependent schema/registry/protocol/suite/runner/result hashes and rerun the gate. Disposition: fix before handoff.

## Medium Findings

None.

## Low Findings

None.

## Checklist Assessment

| Good-spine dimension | Result | Evidence / note |
| --- | --- | --- |
| Real feature-altitude divergence points | **Fail** | The five product divergences are fixed, but H1 leaves mutation/recovery/repair behavior after activation rollback non-convergent. |
| Every AD enforceable and preventative | **Fail** | AD-2 through AD-6 and AD-8 through AD-12 are backed by precise contracts and negative vectors. AD-1/AD-7's native authority rule is declared but not fully executable because the current activation/attestation/root inputs are not consumed. |
| Deferred safety | **Pass** | Action Center UI, push/watchers, database migration, fuzzy action resolution, offline archive freshness, and quantitative lag/SLO thresholds are deferred with explicit revisit conditions and do not weaken deterministic fingerprint freshness (`ARCHITECTURE-SPINE.md:254-261`). |
| Named technology currentness | **Pass** | Node 22 and 24 remain supported LTS lines; unsupported 23/25 are rejected. Python `>=3.10,<4` is currently supportable and has a mandatory review deadline before the 3.10 floor exits security support. Draft 2020-12 and RFC 8785 remain current and fit for the pinned contract model (`ARCHITECTURE-SPINE.md:202-212`; `CONTRACT-REGISTRY.json:5-18`). |
| Brownfield ratification | **Pass** | The target preserves the 20-column ledger before adding Action Revision as column 21, nests the existing Panel v1 model, keeps local CLI/file deployment, and pins 23 current source artifacts. All source pins match and the 205 + 17 brownfield regressions pass. Production code still exhibits the diagnosed limitations, consistent with a draft target architecture rather than a false deployment claim. |
| Five reported problems | **Pass** | Existing-action patching is governed by AD-2; typed WDR current mutation by AD-3; live source validation by AD-4/AD-8; WDR-ledger drift by AD-5; typed action IDs and repair batching by AD-7. The capability map makes all five explicit (`ARCHITECTURE-SPINE.md:242-252`). |
| Operational/environmental breadth | **Pass** | Deployment mode, environment/runtime policy, POSIX/Windows durability, lock order, journal recovery, first publication, rollback, migration, inspect, release evidence, and production trust boundaries are all decided. |
| Production evidence honesty | **Pass** | Raw production roots are empty and implementation status is `pending` (`CONTRACT-REGISTRY.json:9-18,148-155`). Both checked results remain `design-fixture-check` with native durability false; the spine does not authorize strict production publication (`ARCHITECTURE-SPINE.md:178-185,145-149`). |

## Original-Problem and Brownfield Reality Check

The current production sources remain consistent with the stated diagnosis:

- meeting-sync materializes canonical action records without an existing `action_id`/patch operation and appends a `Meeting Sync Update` block (`skills/adp-meeting-sync/scripts/sync_meeting.py:1244-1270,1363-1403`);
- status-sync can update the ledger by exact ID, but it is the legacy direct file writer and not the target typed command/fact transaction path (`skills/adp-status-sync/scripts/sync_status.py:791-846`);
- the Panel loads current projection files and seals those artifact paths, not the underlying WDR/ledger live source closure (`skills/adp-management-panel/scripts/management_panel.py:570-632,1120-1173`);
- prepass compares only WDR/ledger action-ID set differences and omits complete owner/status/text/due content drift (`skills/adp-agent-program-lead/scripts/adp-state-prepass.py:911-958`);
- canonical audit findings use action IDs only inside identity details and do not emit typed `action_ids` or `repair_batch_id` in the finding surface (`skills/adp-state-audit/scripts/audit_state.py:2951-3014`).

This confirms that the package ratifies and plans a migration from current reality; it does not silently claim the target modules are deployed.

## Independent Verification

- Architecture lint: **PASS**, 0 findings, using `python3` because `uv` is unavailable.
- Registry inventory: **52 contracts, 23 source pins, 9 enumerators, 7 profiles, 7 payload bindings, 4 nested bindings, 6 Panel bindings, 15 DAG edges, 47 typed array-ordering rules, 15 identity-set rules, 3 semantic-sequence rules, 44 runtime paths, and 14 semantic validators**. The spine, protocol, and analysis plan agree.
- Production gate state: `implementation_conformance_status=pending`; production `trust_roots=[]`.
- Source pins: **23/23** current raw hashes match.
- Conformance suite: **512 IDs, 512 unique**. Fresh fixed-time Python and Node runs each passed **512/512**, failed 0, and produced identical passed-ID sets. Their differing result bytes at a different fixed time are only the requested `executed_at` and derived `result_id`; the checked-in receipts are pinned to `2026-07-24T04:00:00Z`.
- Both checked receipts are `design-fixture-check` with `native_durability_exercised=false`.
- Brownfield regressions: **205/205 PASS**: meeting-sync 31, status-sync 29, state-audit 63, Management Panel 28, panel-audit 12, state-prepass 10, panel-model 6, Panel contract 26. Additional Program Lead regressions: **17/17 PASS**.
- `state/panel-refresh-status.json` is consistent across spine, plan, protocol, and registry.
- v16 closure recheck: live inspect is now registered as a composition of strict activation with its transitive scope (`CONTRACT-REGISTRY.json:779-780`); design-mock policy uses separately serialized registry bytes and a recomputed registry hash in both runners (`python_runner.py:6224-6238`, `node_runner.mjs:3650-3665`); source-time equality, first-publication dual-create, durable release-evidence set, Node 23 rejection, and rollback-only capability lifecycle all have passing vectors.

## Gate Decision

**FAIL: 0 Critical, 1 High, 0 Medium, 0 Low.** Keep `ARCHITECTURE-SPINE.md` at `status: draft`, keep implementation conformance `pending`, and do not treat the authority design as implementation-ready until H1 is corrected and a fresh independent reviewer gate reports zero Critical and zero High findings.
