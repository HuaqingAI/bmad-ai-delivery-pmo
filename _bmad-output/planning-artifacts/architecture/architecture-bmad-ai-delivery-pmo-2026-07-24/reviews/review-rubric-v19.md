# BMad Architecture Rubric Review v19

## Gate Verdict

**REJECT. Critical: 0. High: 1. Medium: 0. Low: 0.**

The spine closes all five reported Management Panel synchronization problems, preserves the brownfield deployment model, covers the operational/environmental envelope, and accurately withholds production authorization. One enforceability gap remains in AD-12: the registered activation lifecycle index is journaled but is not semantically derived from the five committed transition receipts or CAS-chained between steps, so a forged or disconnected durable lifecycle history still passes both reference models.

| Severity | Count |
| --- | ---: |
| Critical | 0 |
| High | 1 |
| Medium | 0 |
| Low | 0 |

## Frozen Review Base

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `677b6df331c2fde6d6192be61ce03d39529b9fdf9cb2223a15f72f79de20e6b5` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `6d23c7ca625e5ccca31483309fae9472f89de524097e82e17797bbeac00267f3` |
| `contracts/CONTRACT-REGISTRY.json` | `82fd15723a618f3edf75881c9304f34f92c83683a44d64f1bbaa263835ee7ce7` |
| `contracts/panel-sync-contracts.schema.json` | `5c3f4c916042afeea9d038839d6cbe7c694859737c27794b17268b908f85491e` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `ef1fe1a7aa65a148a76620581003dc7a55f2c870a2a1ae175d76bc660a9af7fb` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `4c8ca5565db78b5e54dc6fbc6a9e6f85ba1f7a73e6e2f98c976fa1500d3f0794` |
| `contracts/conformance/python_runner.py` | `af522cb4280bd221996babda45e76316e6712ee2bdaf672a48986133879f743b` |
| `contracts/conformance/node_runner.mjs` | `99baf1fb498f7b2a6c5ce3975ae388f4e8edcca406847ec0755db63c9a3d79d7` |
| `contracts/conformance/python-result.json` | `d1a8b73d84e016368d01f2ee2c01b8b9c2fd11c3f655583d8d42efda619932bb` |
| `contracts/conformance/node-result.json` | `bb810362fe8237aca24aa4191fc8dcbada33e428f4f12771606ff19c5e879b3e` |

## Critical Findings

None.

## High Findings

### H1 - The durable activation lifecycle index is neither receipt-derived nor chained by CAS

**Evidence.** AD-12 requires the five activation operations to be consecutive, individually journaled, CAS-bound transitions whose crash recovery cannot skip a step (`ARCHITECTURE-SPINE.md:145-149`). The registry strengthens that promise with `one-lifecycle-five-consecutive-predecessor-receipt-bound-steps-exact-before-after-state-and-attestation-cas-lifecycle-index-and-fresh-process-image-recovery` (`CONTRACT-REGISTRY.json:934`). The protocol requires the same five-step order and fresh-process recovery, but describes only the operation-specific business target plus receipt and does not state how the lifecycle index is updated or validated (`WDR-AND-TRANSACTION-PROTOCOL.md:109`).

The index schema constrains entry shape but cannot bind entries to actual receipts (`panel-sync-contracts.schema.json:1591-1618`). Python validates only the final index schema, self-hash, terminal status, and length (`python_runner.py:3141-3145`). It correctly validates the command/receipt predecessor chain (`:3173-3197`), but never compares `lifecycle.entries` to those receipts. For each journal it merely compares the lifecycle target hashes to caller-provided `before_lifecycle_index` and `after_lifecycle_index` documents (`:3231-3240`), without requiring step N's before index to equal step N-1's after index or requiring after to be exactly before plus the current receipt. The final check only equates the package index with the last caller-provided after index (`:3280-3285`). Node has the same gap (`node_runner.mjs:2916-3025`).

An executable counterexample against the frozen Python handler confirmed both false positives:

1. Replace all five final index entries with forged transition IDs, receipt IDs, receipt paths, and receipt hashes; recompute `index_id`, the final lifecycle target hash, journal identity, and marker identity. `activation_transition_semantics()` returns `True`.
2. Replace step 2's lifecycle before image with a self-consistent empty index unrelated to step 1's lifecycle after image; rebind the lifecycle target, journal, and marker. `activation_transition_semantics()` again returns `True`.

The current suite has operation-order, predecessor-rebind, disconnected activation/capability state, attestation-CAS, and transition recovery vectors, but no lifecycle-entry forgery or lifecycle-index preimage-disconnection vector (`CONFORMANCE-VECTORS.json:1118-1134`).

**Divergence.** One implementation can reconstruct each durable lifecycle entry from the exact committed receipt and require the index's adjacent before/after bytes to form one CAS chain. Another can persist unrelated receipt references or disconnected index snapshots while enforcing the valid activation/capability/attestation state chain. Both can pass all 627 registered design vectors. On restart, operators and automation can therefore observe incompatible completed steps or retry points from the purported authoritative lifecycle index.

**Required correction.** Make `activation-lifecycle-index/1.0.0` an explicit normative target in AD-12 and protocol section 9. For every step, derive the one expected index entry from the validated command and receipt; require `before_lifecycle_index` to equal the immediately prior committed after index; require `after_lifecycle_index` to equal before plus exactly that entry; validate lifecycle ID, activation epoch, ordinal, operation, predecessor receipt, registered receipt path, exact receipt hash, and terminal-status transition. Add negative vectors for a forged final entry and a disconnected intermediate index preimage in both runners. Disposition: **fix before handoff**.

## Medium Findings

None.

## Low Findings

None.

## Good-Spine Checklist

| Dimension | Result | Evidence / note |
| --- | --- | --- |
| Real feature-altitude divergence points | **Fail** | The user-visible state paths are closed, but H1 leaves a durable activation/restart history divergence under AD-12. |
| Enforceable `Binds` / `Prevents` / `Rule` | **Fail** | AD-1 through AD-11 are contract-backed and preventative. AD-12's consecutive lifecycle promise is stronger than its index semantics and probes. |
| Deferred safety | **Pass** | Action Center UI, push/watchers, database migration, fuzzy action matching, offline archive freshness, and quantitative lag SLOs are deferred with safe present behavior and revisit conditions (`ARCHITECTURE-SPINE.md:257-264`). Exact-ID destructive mutation and raw-fingerprint freshness remain mandatory now. |
| Brownfield ratification | **Pass** | The target preserves local CLI/file deployment, the 20-column ledger before additive revision column 21, existing Panel v1 content inside v2, manual WDR action text, and existing producer ownership. All 23 raw source pins match current repository bytes. The current code still exhibits the diagnosed limitations, so the draft does not confuse target architecture with deployed behavior. |
| Five reported problems | **Pass** | Existing-action owner/status patch: AD-1/2. WDR current fields: AD-1/3/10. Live-source Panel inspection: AD-4/8. Ledger/WDR projection drift: AD-5. Exact action IDs and batch repair: AD-7/10. The capability map also lists all five (`ARCHITECTURE-SPINE.md:245-255`). |
| Operational/environmental breadth | **Pass** | Local deployment, migration, strict/legacy modes, authority, POSIX/Windows durability, locks, transaction boundaries, crash recovery, first publication, rollback, current pointers, inspection, alert/degraded states, support review, and production evidence gates are all decided. H1 is a defect within that coverage, not a silent dimension. |
| Named technology currentness | **Pass** | Official Node release data on 2026-07-25 keeps v22 supported through 2027-04-30 and v24 through 2028-04-30; v26 is still Current until its 2026-10-28 LTS date. Python's official versions page lists 3.10 in security support through 2026-10, and the registry imposes the earlier 2026-09-01 review deadline. The official Draft 2020-12 meta-schema remains live. |
| Evidence honesty | **Pass** | Raw registry remains `implementation_conformance_status=pending`; `evidence_trust.trust_roots=[]`; both result receipts are `design-fixture-check`, `posix-design-model`, and `native_durability_exercised=false`. AD-12 explicitly denies production strict publication until native evidence and reviewed trust roots exist. |
| Parent/spec consistency | **Pass** | No parent spine is declared. The spine and analysis plan cover the input problems without weakening a parent invariant. The repair topology now consistently uses a business fact journal followed by a separate repair-attempt journal. |
| No silent altitude-owned dimension | **Pass** | Ownership, data shape, mutation, projection, freshness, drift, repair, compatibility, security, runtime portability, observability, deployment, rollback, and deferred SLO policy are each decided or explicitly deferred. |

## Problem-to-Evidence Check

| User problem | Current brownfield evidence | Target closure |
| --- | --- | --- |
| Meeting sync only creates actions | `sync_meeting.py:266-304` has no target action identity/revision operation; `:1371-1381` emits create-shaped rows. | Typed action patch with exact ID/revision and presence-preserving `set`. |
| `wdr_update` only appends | `sync_meeting.py:1244-1264` renders a new `## Meeting Sync Update` block. | Typed status intent and shared section-aware WDR command. |
| Panel inspect ignores changed facts | `management_panel.py:1144-1150` audits the existing model/bundle/HTML without live `source_inputs`. | Live leaf re-enumeration, exact resolver read set, inventory attestation, and publication eligibility gate. |
| WDR/ledger projection drift is incomplete | `adp-state-prepass.py:911-958` compares IDs, not full owner/text/due content; `:1154` skips the check for an empty ledger. | Sidecar-backed full-record drift recomputation, including empty-active-set behavior. |
| Audit result drops concrete action IDs | `audit_state.py:2951-2998` omits action fields from the public canonical finding even though they participate in identity details at `:3001-3014`. | Finding v2 exact `action_ids`, entity refs, deterministic batches, and repair graph. |

## Deterministic Evidence

- Architecture lint: **PASS**, 0 findings, via `python3`.
- Registry inventory agrees with the spine: **66 contracts, 23 source pins, 9 enumerators, 7 profiles, 7 payload bindings, 4 nested bindings, 6 Panel bindings, 15 DAG edges, 56 typed array-ordering rules, 20 identity-set rules, 3 semantic sequences, 60 runtime paths, 4 owned-fact target profiles, 8 source-time bindings, and 20 semantic validators**.
- Source pins: **23/23** match current repository raw bytes.
- Stored Python result: **627 unique passed / 0 failed**. Stored Node result: **627 unique passed / 0 failed**. Passed-vector order and IDs are identical; all result registry/schema/protocol/suite hashes match current raw bytes.
- The prior activation attestation-CAS and transition recovery findings are closed: exact attestation hash fields exist, adjacent activation/capability/attestation receipt states are compared, and recovery materializes target images and runs in a fresh subprocess at all boundaries with idempotent receipt replay. H1 is specifically the remaining lifecycle-index linkage gap.

## Gate Decision

**FAIL: 0 Critical, 1 High, 0 Medium, 0 Low.** Keep `ARCHITECTURE-SPINE.md` at `status: draft`. Tighten the lifecycle-index invariant and executable validator, add the two negative probes, regenerate dependent pins/results, and rerun the fresh reviewer gate.
