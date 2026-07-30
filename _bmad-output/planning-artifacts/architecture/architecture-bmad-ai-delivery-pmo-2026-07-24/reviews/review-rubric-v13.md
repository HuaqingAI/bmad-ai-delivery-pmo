# BMad Architecture Good-Spine Rubric Review v13

**Artifact:** `ARCHITECTURE-SPINE.md` and all normative companions
**Review lens:** independent BMad good-spine rubric walker
**Verdict:** **FAIL**
**Severity:** **0 Critical, 4 High, 0 Medium, 0 Low**

The package now closes the prior action-field rebound and batch failure-model findings, and both reference implementations reproducibly pass all 346 registered design vectors. The gate still fails because four load-bearing brownfield and rollout boundaries remain unenforceable. In particular, the existing 12/20-column ledger and sidecar-free WDR cannot enter the contracted transaction model, the replacement `views/action-flow.json` format is incompatible with its current consumer, stale meeting evidence can pass the full fact validator while regressing action time, and strict activation depends on an undefined migration attestation.

Production conformance is explicitly `pending`. Missing native POSIX fault injection, native Windows CI, and two independent production adapters are correctly stated release work and are not findings below.

## Frozen Review Target

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `635cf3b69d870e60410ca1c81ce6a3c0861ed3a3e38beb85e05f9ae939e55c4f` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `c76fd1fe020e1772955af408d9ed178544c1a8a92652dd12e161c6989e2ea019` |
| `contracts/CONTRACT-REGISTRY.json` | `85fb125836a77542a15ac04f8f21b281c22178f0c01ada69fbfcca6ed3b4e5aa` |
| `contracts/panel-sync-contracts.schema.json` | `7d09235d5c338b6874b588de6b9f4b00fa9c1bc74f4b7ad11c890e92ad9067ec` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `4b673c381701cae56a5f8008d1742eb2881f6b40eb25b674f311ee07b7244849` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `4d4cde15226ad27a84c733e2187b59ea5c93c4b4d7121bbe8ec8d067dabc4e9b` |
| `contracts/conformance/python_runner.py` | `93b021fe731f1881aaa75bf43324102648bb97024266bfd05114ecc86b81378b` |
| `contracts/conformance/node_runner.mjs` | `f927e59c1e97d742d37ce55b24c56df1b66f9df93c04704612b703f937f1f77a` |

The normative package and production code were not modified by this review.

## Reproduced Gate Evidence

- Architecture lint: **PASS**, zero findings.
- Python reference adapter: **346 passed / 0 failed**; fixed-time output is byte-for-byte equal to `contracts/conformance/python-result.json`.
- Node reference adapter: **346 passed / 0 failed**; fixed-time output is byte-for-byte equal to `contracts/conformance/node-result.json`.
- Python and Node passed-vector sequences are identical; the suite contains 346 unique IDs and no duplicate ID.
- Registry counts match AD-11: 44 contracts, 13 source pins, 9 enumerators, 7 profiles/payload bindings, 6 Panel bindings, 15 DAG edges, 37 typed ordering rules, 12 identity-set rules, 13 runtime paths, and 11 semantic validators.
- The regenerated Panel v1 compatibility fixture is byte-for-byte equal to the checked-in fixture.
- Focused brownfield regressions pass: status-sync 29, flow-graph 23, state-audit 75, meeting-sync 31, management-panel 60, and program-lead 27 tests.
- Direct legacy reproduction: a valid-shaped existing 20-column ledger containing all lifecycle/baseline/relation fields is rejected by the registered runner parser with `ValueError: ledger header is not canonical v2`.
- Direct stale-evidence reproduction: rebinding a complete, otherwise valid action fact graph to evidence observed at `2026-07-22T00:00:00Z` produces `Created At=2026-07-23T01:00:00Z`, `Last Updated=2026-07-22T00:00:00Z`, and the full `fact_attribution_semantics(...)` validator returns `True`.

## Critical Findings

None.

## High Findings

### H1 - The claimed first-mutation migration has no valid transaction or proof path

**Dimension:** enforceability, brownfield compatibility, operational completeness.

AD-2 promises that the legacy 12/20-column adapter preserves the existing 20 fields and appends `Action Revision` (`ARCHITECTURE-SPINE.md:85-89`). The protocol says the first mutation upgrades those bytes under the shared fact lock (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:49-55`). The plan similarly treats a sidecar-free WDR as revision/generation zero and expects its first patch to create state (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:401-402,508`). Those are the required entry points for the current repository: the kickoff template is 12 columns (`skills/adp-project-kickoff/assets/adp-memory-templates/actions/action-ledger.md:5-6`), while the current status writer expands it to the 20 fields at `skills/adp-status-sync/scripts/sync_status.py:69-90`.

The registered execution model cannot represent either migration:

- `parse_action_ledger` accepts only the exact 21-column v2 header (`contracts/conformance/python_runner.py:215-246`), and `fact_attribution_semantics` parses the action transaction's exact before ledger through that function (`contracts/conformance/python_runner.py:1958-1968`). A real 12/20-column before image is therefore rejected before preservation can be proved.
- Every action target is unconditionally `replace`, including `state/action-ledger.json` (`contracts/conformance/python_runner.py:1490-1495`). Brownfield has no such state file, so the first transaction cannot create it and cannot supply the required schema-valid before state.
- A WDR patch likewise marks both the existing WDR and absent `delivery-record.state.json` as `replace` (`contracts/conformance/python_runner.py:1496-1501`), while its validator requires a non-null schema-valid before state (`contracts/conformance/python_runner.py:2012-2022`). A first `refresh_actions` additionally requires an existing valid action sidecar (`contracts/conformance/python_runner.py:2036-2043`).
- The registry has no ledger/WDR bootstrap or migration command, runtime target matrix, semantic validator, or vector for these absent-to-present transitions. The green `legacy-section-order-and-first-status-patch` vector exercises the text renderer, not a journaled first transaction.

This is an adoption blocker, not merely missing test coverage: a conforming implementation either rejects existing projects or performs an unregistered write outside the fact-generation fence, violating AD-1 and AD-10.

**Recommended fix:** register an exact bootstrap/migration transaction contract. It must distinguish per-target operations (`replace` existing ledger/WDR, `create` missing state/sidecar, and the chosen action-flow operation), define initial ledger revision and applied-command state, parse the pinned 12-column template and real 20-column writer format, preserve every existing cell, and prove the exact 21-column after image. Add full-journal vectors for 12- and 20-column input, absent state/sidecar, all preservation fields, crash/recovery at each target, repeat migration, and malformed/ambiguous legacy input in both adapters.

### H2 - The new action-flow contract overwrites a brownfield path with an incompatible and lossy shape

**Dimension:** brownfield fit, capability preservation, real divergence points.

AD-1 requires every action transaction to rebuild the fact-bound `views/action-flow.json` (`ARCHITECTURE-SPINE.md:79-83`), and the registry fixes that exact path (`contracts/CONTRACT-REGISTRY.json:9`). The replacement `action-flow-index/1.0.0` contains only action ID, action revision, status, routing scope, and affected workstreams (`contracts/panel-sync-contracts.schema.json:288-314`; protocol section 4 at line 54).

The existing artifact at that path is `action-flow-relation-v1`, with `action_flow_schema_version`, lifecycle timestamps, baseline revision, related plan/edge IDs, source lineage, and compatibility metadata (`skills/adp-status-sync/assets/action-flow-relation-v1.schema.json:1-51`). The current flow-graph consumer recognizes only that shape (`skills/adp-flow-graph/scripts/flow_graph.py:467-491`) and uses its lifecycle and relation fields for pending/processed overlays and topology allocation (`skills/adp-flow-graph/scripts/flow_graph.py:520-527` and following). The proposed index lacks enough information to reproduce those behaviors even if the consumer were taught its discriminator.

Consequently the first successful action command replaces a currently valid action-flow fact with a document that the current flow graph reports as `flow.source.migration-required`, dropping action overlays from downstream meeting packs and Panel. The Panel v1 compatibility corpus does not catch this because it tests model recomposition from fixture inputs rather than sending the new on-disk action-flow document through the production flow-graph consumer.

**Recommended fix:** preserve the existing action-flow contract at `views/action-flow.json` and derive it byte-exactly from the 21-column ledger, including all lifecycle/relation/source fields, while placing the new compact index at a separately versioned path if it is still needed. Alternatively, version the path and migrate every reader atomically without dropping fields. Pin the existing action-flow schema in the registry and add an end-to-end compatibility vector that applies an action patch, validates the produced artifact with the brownfield schema, runs the production flow-graph parser, and proves identical unaffected overlays plus the intended changed action.

### H3 - Stale evidence can overwrite a current action and regress lifecycle time while passing full attribution

**Dimension:** source-symptom coverage, command semantics, brownfield lifecycle integrity.

AD-2 explicitly claims to prevent an old meeting from overwriting newer action state (`ARCHITECTURE-SPINE.md:85-89`). The protocol defines `Last Updated` as the maximum timestamp in the current command's evidence (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:51-53`), but it never requires that value to be at least the row's existing `Last Updated`, `Created At`, or relevant lifecycle timestamp. `apply_action_command` assigns it unconditionally (`contracts/conformance/python_runner.py:271-314`), and the derived action-flow index deliberately omits all timestamps, so fact attribution has no later chronology check (`contracts/conformance/python_runner.py:1963-1992`).

The direct full-graph reproduction confirmed this is accepted: an owner patch against current action revision 4 with evidence older than the action changes the exact ledger after image to `Last Updated < Created At`; all command, proof, journal, state, flow, and receipt identities were recomputed, and `fact_attribution_semantics(...)` returned `True`. Current brownfield code rejects non-monotonic lifecycle time (`skills/adp-status-sync/scripts/sync_status.py:954-993`), so the new contract weakens an existing invariant. Revision CAS does not close the case where a delayed old meeting is converted into a command after a newer update and captures the then-current revision.

The same architectural phrase “status-sync validates evidence and precedence” for WDR intents is not reduced to a registered cross-state precedence algorithm (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:19-23`); the status-intent validator proves intent-to-command equality, not that older evidence cannot replace newer WDR current fields.

**Recommended fix:** pin causal precedence for both action and WDR current-field mutations. At minimum, action mutation must reject evidence whose effective timestamp precedes the current row's `Last Updated` and must validate `Created <= Started <= terminal <= Last Updated` after every command. Define the corresponding WDR per-field or record-level precedence against `Last status sync`; if deliberate backdated correction is needed, make it an explicit separately authorized operation with current correction evidence. Add full fact-graph negatives for older evidence, created/updated and lifecycle inversions, delayed-command/current-revision replay, and WDR current-field regression in both harnesses.

### H4 - Strict activation depends on an undefined writer-fence migration attestation

**Dimension:** rollout safety, operational/environmental completeness, AD enforceability.

AD-12 correctly states the desired double gate: implementation conformance must be `passed` and the current memory root must have a complete, current, valid writer-fence migration attestation (`ARCHITECTURE-SPINE.md:145-149`; protocol section 9 at line 99). The second half is not a contract. None of the 44 registered documents is a migration attestation; there is no registry runtime path, producer/reader ownership, identity algorithm, semantic validator, or conformance vector for it. The package does not define what “all projection-relevant writers,” “complete,” “current,” or “valid” mean as machine-checkable fields.

Two builders can therefore enable strict mode from incompatible evidence: one may accept a boolean written after a full refresh, while another may bind writer binaries, capability epochs, fact generation, migrated sidecars, and the release receipts. Both satisfy the prose. Because the attestation is meant to prove that no writer can bypass the generation fence, accepting the weaker interpretation creates precisely the false-freshness state AD-12 is intended to prevent. The registry being `pending` prevents immediate activation today, but it does not make the future `pending -> passed` transition safe.

**Recommended fix:** register the attestation and its activation validator. Bind at least memory-root identity, fact generation, exact complete writer inventory and implementation/build hashes, active capability registry/epoch, migrated ledger/WDR/sidecar identities, full-refresh generation/current pointer, and the accepted production conformance receipts. Define its runtime path, content identity, freshness/revocation rule, bootstrap producer, and rollback invalidation. Strict startup/open/inspect/publish must independently execute both the release gate and attestation validator. Add missing/partial/stale/root-rebound/writer-version-changed/manual-registry-flip/rollback/re-enable vectors.

## Medium Findings

None.

## Low Findings

None.

## Source-Symptom Coverage

| Reported symptom | Design response | Gate result |
| --- | --- | --- |
| meeting-sync cannot mutate an existing action owner/status | Typed create/patch command, exact ID/revision, command-derived ledger transition | **FAIL** until H1 and H3 close the real legacy entry path and stale-evidence ordering. |
| `wdr_update` appends text but leaves current fields stale | Typed status intent and status-sync-owned WDR patch; free text remains history-only | **PARTIAL**; intent binding is strong, but H1 blocks first legacy state migration and H3 leaves cross-state precedence undefined. |
| Panel checks do not verify live sources | Generation envelopes, exact profiles, live inventory attestation, live inspect and publication eligibility | **PASS at design-fixture level**; production evidence remains correctly pending. |
| No WDR/ledger projection-drift alert | Full ledger/WDR/state/sidecar recomputation with exact finding IDs and publication block | **PASS at design-fixture level**. |
| Audit results omit concrete action IDs | Typed action/entity references, bidirectional batch linkage and repair graph | **PASS at design-fixture level**. |

## Checklist Verdict

| Good-spine dimension | Verdict | Evidence |
| --- | --- | --- |
| Real divergence points | **FAIL** | Legacy bootstrap semantics, stale-evidence precedence, action-flow compatibility, and strict attestation acceptance still allow incompatible implementations. |
| AD enforceability | **FAIL** | H1 and H4 are prose-only paths with no valid registered transaction/validator; H3 is contradicted by the executable validator. |
| Deferred safety | **PASS** | Action Center, daemon/push, database migration, fuzzy action matching, and offline live verification remain safely bounded. |
| Named-tech currency | **PASS** | Python 3.12 is an explicit supported reference floor with exact runtime recorded by future native receipts; JSON Schema 2020-12 and RFC 8785 are pinned. |
| Brownfield fit | **FAIL** | H1 cannot mutate the actual 12/20-column and sidecar-free state, H2 breaks the current flow consumer, and H3 weakens current lifecycle validation. |
| Source requirement coverage | **PARTIAL** | All five symptoms have a named architecture response, but the primary mutation paths are not deployable safely against existing state. |
| Inherited invariants | **N/A** | No parent spine is declared. |
| Operational/environmental completeness | **FAIL** | Crash recovery and publication mechanics are strong, but first migration and strict activation are not executable contracts. |

## Exit Conditions

1. Add and execute exact journaled migration contracts for legacy action ledger/WDR state and sidecar absence, including preservation and crash vectors.
2. Preserve the current action-flow contract and behavior at its existing path, or complete a lossless, atomically versioned reader/writer migration with production-consumer coverage.
3. Enforce causal evidence precedence and lifecycle chronology in action and WDR mutation semantics, with full-graph stale-evidence negatives.
4. Register the writer-fence migration attestation and strict-activation algorithm, then test missing, stale, rebound, rollback, and re-enable cases.
5. Re-run the complete lint, dual-harness byte replay, compatibility fixture, brownfield regressions, and a fresh independent reviewer gate.

The architecture can pass only after all High findings are closed and a fresh independent review reports zero Critical/High findings.
