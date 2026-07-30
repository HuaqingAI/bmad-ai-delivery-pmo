# BMad Architecture Rubric Review v20

## Gate Verdict

**REJECT. Critical: 0. High: 1. Medium: 1. Low: 0.**

The v20 spine closes the five reported Management Panel synchronization problems at feature altitude, includes an honest post-implementation update runbook, covers the operational/environmental envelope, and keeps production strict publication fail-closed. One executable contract gap remains: a status-sync fact transaction can consume a proper subset of the pending intents for its workstream while leaving another same-workstream intent pending, even though AD-10 and the protocol require one complete aggregate consumption.

| Severity | Count |
| --- | ---: |
| Critical | 0 |
| High | 1 |
| Medium | 1 |
| Low | 0 |

## Frozen Review Base

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `88dee897e5a648e887495192198f756e8f5d7388fa23e9df4d1e2db97056569e` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `77212bdc9951595d684705612a6356bc24c4ee27cd67a8cd00e2ed302337f55a` |
| `contracts/CONTRACT-REGISTRY.json` | `07069e6d8d5bf118205d456a9d45816cdd3da77d5c27d1b37d38eb614c77623a` |
| `contracts/panel-sync-contracts.schema.json` | `30c89a0f345fab0673bb303a06a80cfa3bc287747f73a283be92076c51708416` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `d6075713bced415d0214e13ae59f50dc565ff159d25e1f48be16f307b97f3781` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `20abc93c9c7dad281896680ce639c0ec54396e8c7dc89c51ed95a4c06e56d2bb` |
| `contracts/conformance/python_runner.py` | `ad85a146f588abbd33d5043f86001afdeb9275e9ad1127df9059a4d7c75fb9d9` |
| `contracts/conformance/node_runner.mjs` | `dc1e29141bcffe334e8ddc9d2aa700e2a57956401ee378f6abab15ef11cdca5e` |
| `contracts/conformance/python-result.json` | `dc3116c3587456f433b0ab3dba00cf4b3fc00df2b6bf6bc1bec8af7c6267f4f1` |
| `contracts/conformance/node-result.json` | `e05171cdce1269cb231cb29358f43db83f515aa48e1d408373a0bc3b9cca8277` |

## Critical Findings

None.

## High Findings

### H1 - Aggregate consumption does not require the complete pending same-workstream intent set

**Evidence.** AD-10 says a status-sync journal must consume the complete pending same-workstream set and reject subset consumption (`ARCHITECTURE-SPINE.md:133-137`). Protocol section 2 likewise requires the WDR patch to carry the complete consumed set, atomically move that selected set to consumed, and reject partial consumption (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:25-27`). The analysis and runbook rely on this guarantee before Panel publication (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:360-370`).

The Python fact validator builds `selected` only from IDs already named by the command, verifies those selected rows, and explicitly preserves every unselected row unchanged (`contracts/conformance/python_runner.py:5563-5605`). It never derives all `status=pending` rows for `command.workstream_id` from the before-outbox or requires `consumed_intent_ids` to equal that set. Node has the same selection rule (`contracts/conformance/node_runner.mjs:3325-3355`). The current `omitted-consumed-intent` vector changes only the command ID list while leaving the omitted row marked consumed, so the validator correctly rejects that inconsistent graph; it does not test the stronger case where the omitted row remains pending in both before and after images (`contracts/conformance/python_runner.py:10623-10629`).

An executable counterexample against the frozen Python bytes confirmed the false positive:

1. Start from the valid `wdr-status` fact-attribution graph.
2. Add a third schema-valid pending intent for the same workstream to both before and after outboxes. Give it a distinct embedded intent ID but the same already-merged field value and evidence, so the correct complete union would not otherwise change the WDR command.
3. Recompute the outer intent hash, contiguous sequence, both outbox IDs, journal target hashes, manifest ID, and marker ID.
4. Keep the command consuming the original two IDs and leave the third same-workstream row pending.

`fact_attribution_semantics()` returned `True`; the resulting after-outbox contained one pending row for the command's workstream. The baseline graph also returned `True`. This is a fully rebound false-green, not merely a stale hash mutation.

**Divergence.** One implementation can drain every pending same-workstream intent into one aggregate WDR patch. Another can apply a partial current-field update, leave another same-workstream intent pending for a later patch, and still produce a valid fact receipt. Both pass all 643 design vectors. That reintroduces order-dependent current-field mutation and violates the architecture's one-aggregate transaction boundary; the convergence gate may block Panel publication later, but the business facts have already been partially and incompatibly mutated.

**Required correction.** In consume mode, derive `expected_consumed_ids` from every before-outbox entry whose status is `pending` and whose workstream equals the command workstream, sorted by the registered UTF-8 rule. Require exact equality with `command.consumed_intent_ids`, then derive the command field/evidence union from that complete set. If precedence intentionally excludes an intent, transition it to a typed terminal state before aggregation rather than leaving it pending. Add a negative vector that appends a fully rebound extra pending same-workstream row to both before and after outboxes and expects `INTENT_OUTBOX_INVALID` in both runners. Also assert that a successful consume leaves no pending row for that workstream while preserving unrelated workstream rows byte-for-byte. Disposition: **fix before handoff**.

## Medium Findings

### M1 - The risk-review intent carrier is narrower in the runners than in the schema and protocol

**Evidence.** `owned-fact-command/1.0.0` permits `status_intents` on every owned-fact target profile (`contracts/panel-sync-contracts.schema.json:216-233`), and the protocol says any meeting/checkpoint/risk producer command that produces a current-field intent must carry and atomically emit its exact intents (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:25`). The registry gives risk review four owned-fact profiles: risk flow plus three decision surfaces. The reference adapters, however, enter emit mode for risk review only when `target_profile_id == risk-flow-index-v1` (`contracts/conformance/python_runner.py:4200-4217`; `contracts/conformance/node_runner.mjs:2237-2242`). An intent-bearing decision command is schema-valid but is rejected by fact semantics as an outbox-free command.

**Divergence.** Implementers can reasonably choose either "any risk owned command carrying intents emits them" from the schema/protocol, or "risk-flow is the exclusive carrier" from the runners. A decision-origin status update can therefore be accepted and emitted by one implementation but blocked by another.

**Required correction.** Choose one rule and make it registry-derived. Prefer an explicit `intent_emitter_profiles` allowlist or a semantic predicate based on the presence of validated `status_intents`, then align schema, protocol, both runners, and vectors. If risk-flow is intentionally exclusive, state that invariant and constrain other owned-fact profiles from carrying `status_intents`. Disposition: **fix or explicitly defer before implementation stories**.

## Low Findings

None.

## Good-Spine Checklist

| Dimension | Result | Evidence / note |
| --- | --- | --- |
| Real feature-altitude divergence points | **Fail** | Ownership, mutation, projection, freshness, repair, and publication are all covered, but H1 leaves the central aggregate-intent boundary enforceably weaker than the stated rule. |
| Enforceable `Binds` / `Prevents` / `Rule` | **Fail** | AD-1 through AD-12 are contract-backed, but AD-10's complete-set clause has an executable false-positive and the risk carrier remains ambiguous. |
| Deferred safety | **Pass** | Push/watchers, database migration, fuzzy action matching, offline archive freshness, extra Panel views, and quantitative lag SLOs are deferred with safe current behavior and revisit conditions (`ARCHITECTURE-SPINE.md:257-264`). |
| Brownfield ratification | **Pass** | Local CLI/file deployment, the brownfield 20-column ledger plus additive revision column, existing Panel v1 model, manual WDR entries, and existing producer ownership are preserved. All 23 pinned source bytes match. |
| Five reported problems | **Pass** | Existing action mutation: AD-1/2. WDR current fields: AD-1/3/10. Live-source Panel inspection: AD-4/8. Ledger/WDR drift: AD-5. Exact action IDs and batch repair: AD-7/10. |
| Operational Panel update runbook | **Pass** | The runbook clearly separates producer dry-run/apply, status convergence, detect/refresh dry-run/apply, final inspect, drift repair, and retry behavior (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:356-370`). It correctly warns that production `skills/adp-*` are not yet changed and that current inspect does not prove live freshness. |
| Operational/environmental breadth | **Pass** | Deployment, strict/legacy modes, authority, lock ordering, POSIX/Windows durability, crash recovery, first publication, rollback, inspection, alert/degraded states, support review, and production evidence gates are decided. |
| Named technology currentness | **Pass** | As of 2026-07-30, official Node release data keeps v22 supported through 2027-04-30 and v24 through 2028-04-30; v26 remains Current until its 2026-10-28 LTS date. Python's official versions page lists 3.10 security support through 2026-10, and the registry imposes an earlier 2026-09-01 review deadline. The Draft 2020-12 meta-schema remains available. |
| Evidence honesty / production boundary | **Pass** | Raw registry remains `implementation_conformance_status=pending`; `evidence_trust.trust_roots=[]`; both checked results are design fixtures with native durability false. AD-12 explicitly denies production strict publication. |
| Parent/spec consistency | **Pass** | No parent spine is declared. The target architecture covers the user input without weakening an inherited invariant. |
| No silent altitude-owned dimension | **Pass** | Data ownership, mutation, projection, compatibility, security, runtime portability, observability, deployment, operations, rollback, and SLO policy are all decided or explicitly deferred. |

## v19 Correction Recheck

| Required correction | v20 result |
| --- | --- |
| Receipt-derived activation lifecycle prefix CAS | **Pass.** Each index entry is rebuilt from the validated receipt/path/raw hash, before index equals the prior committed after index, and after is exactly prefix plus one entry (`python_runner.py:3222-3243`). Recovery runs in fresh subprocesses over all target boundaries. |
| Exact producer intent outbox emission | **Pass with M1 scope caveat.** Emission binds exact command-carried intent bytes, outer content hash, producer, source command ID/fingerprint, and one journal target. |
| Complete aggregate intent consumption | **Fail.** H1 demonstrates that an additional pending same-workstream row can remain outside the selected set. |
| Typed unified drift/audit finding identity | **Pass.** Drift computes `finding_id` over typed body bytes and repair validation reconstructs the exact audit projection without a second ID algorithm. |
| Ledger-derived repair reads | **Pass.** Repair validation reparses exact ledger/ledger-state/WDR/WDR-state/sidecar bytes and rejects self-claimed presence, revision, and drift. |
| Deterministic repair-attempt handoff and fresh-process recovery | **Pass.** Both runners derive the attempt identity from actual business terminal marker/optional recovery bytes and execute fresh-process roll-forward at each attempt target boundary. |

## Deterministic Evidence

- Architecture lint: **PASS**, 0 findings, via `python3 .agents/skills/bmad-architecture/scripts/lint_spine.py`.
- Registry inventory agrees with the spine: **66 contracts, 23 source pins, 9 enumerators, 7 profiles, 7 payload bindings, 4 nested bindings, 6 Panel bindings, 15 DAG edges, 56 typed array-ordering rules, 20 identity-set rules, 3 semantic sequences, 60 runtime paths, 4 owned-fact target profiles, 8 source-time bindings, and 20 semantic validators**.
- Fresh Python run: **643 unique passed / 0 failed**; output is byte-identical to `python-result.json`.
- Fresh Node run: **643 unique passed / 0 failed**; output is byte-identical to `node-result.json`.
- Python and Node passed-vector IDs and order are identical.
- Every spine/registry/protocol/suite/runner/result hash listed in the package matches current raw bytes.
- Production gate remains fail-closed: `implementation_conformance_status=pending`, production trust roots empty, design results marked `native_durability_exercised=false`.
- Targeted complete-consumption counterexample: baseline valid `true`; after adding and fully rebinding one extra pending same-workstream row, validator still returned `true`; pending same-workstream rows after commit: `1`.

## Gate Decision

**FAIL: 0 Critical, 1 High, 1 Medium, 0 Low.** Keep `ARCHITECTURE-SPINE.md` at `status: draft`. Close H1 by deriving and consuming the exact complete pending same-workstream set and add the consistently rebound negative probe to both runtimes. Resolve the M1 risk-carrier rule while touching the intent routing contract, regenerate all dependent hashes/results, and rerun the fresh reviewer gate.
