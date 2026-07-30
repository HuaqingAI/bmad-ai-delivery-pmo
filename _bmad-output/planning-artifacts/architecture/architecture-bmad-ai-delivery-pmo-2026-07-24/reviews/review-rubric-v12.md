# BMad Architecture Good-Spine Rubric Review v12

**Artifact:** `ARCHITECTURE-SPINE.md` and its normative companions
**Review lens:** independent BMad good-spine rubric walker
**Verdict:** **FAIL**
**Severity:** **0 Critical, 2 High, 2 Medium, 0 Low**

The spine covers all five reported synchronization symptoms and is mechanically clean, but it still leaves two load-bearing mutation boundaries non-convergent. Most importantly, the supposedly command-derived fact proof accepts an action owner value different from the value actually written to the ledger. The package therefore cannot yet claim that action patch semantics are enforceable, despite both design harnesses passing all registered vectors.

Production conformance is explicitly `pending`. The missing native POSIX fault-injection, native Windows CI, and two independent production adapters are correctly stated release work and are not counted as defects below.

## Frozen Review Target

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `352f2da359b3616cb77743676c5bb2a67a2509d43598941b99b2a31c2e4a1eac` |
| `ANALYSIS-AND-OPTIMIZATION-PLAN.md` | `22b9e68c6b685dc6008f44eb431871420fbc438fa34997a1a5b8e84b090f6d0d` |
| `contracts/CONTRACT-REGISTRY.json` | `f5bcb9820f1b5f755c06a0b357faaa0059d65c7e94d6e17501ca8a626de6cf99` |
| `contracts/panel-sync-contracts.schema.json` | `d700d48bdf952da9f8ea98ca1eb0eb78eb44e64f20dc67ea9b36d81f84837f1c` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `d1aaa396239fccb60696be50ca43e292c3ee29d7c406bee9ca46762a78419072` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `95ac467c7e98d1929e95c4e65622049fe70d08abebb54acc620cd68c26869a88` |
| `contracts/conformance/python_runner.py` | `8b5145da68f0ef4922da9b6b8b2350a9669da51bea5e5405e7956b49bf5cfc83` |
| `contracts/conformance/node_runner.mjs` | `d06c6b3590be333bdd72d2d21ebd2d31cffe6540ada1c5cdaeb49165f06e9792` |

The normative package was not modified by this review.

## Reproduced Gate Evidence

- Architecture lint: **PASS**, zero findings.
- Python reference adapter: **305 passed / 0 failed**; fixed-time result is byte-for-byte equal to the checked-in receipt.
- Node reference adapter: **305 passed / 0 failed**; fixed-time result is byte-for-byte equal to the checked-in receipt.
- Registry counts match AD-11: 44 contracts, 13 pins, 9 enumerators, 7 profiles/bindings, 6 Panel bindings, 15 DAG edges, 27 typed ordering rules, 9 identity-set rules, 11 runtime paths, and 8 semantic validators.
- Independent negative reproduction for H1: change the action patch command from `owner=FDE-C` to `owner=FDE-X`; retain the ledger after-image containing `FDE-C`; correctly recompute the command fingerprint, applied-command entry, ledger-state identity, target hashes, proof, receipt, journal, and marker. `fact_attribution_semantics(...)` still returns `True`.

## Critical Findings

None.

## High Findings

### H1 - Action fact attribution does not prove that the command's field values were applied

**Dimension:** enforceability, real divergence points, brownfield fit, source requirement coverage.

AD-1 requires the fact proof to bind command-derived exact targets, CAS, raw before/after bytes, and schema-valid state (`ARCHITECTURE-SPINE.md:79-83`). AD-2 then makes presence-preserving `set` the normative action patch (`ARCHITECTURE-SPINE.md:85-89`). This is the core fix for the user's existing-action owner/status problem.

The protocol closes the target set but does not define a command-derived action-row transition: it says an action transaction must write exactly ledger, ledger state, and action-flow index, then describes revision behavior (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:49`). The Python semantic validator decodes the raw documents, but its action branch checks only ledger fingerprints, ledger/action revisions, applied command fingerprint, action ID presence, and ledger/flow linkage (`contracts/conformance/python_runner.py:1253-1282`). It never parses the before ledger row, applies `command.set`, or compares owner/status/action/due/closure/routing values with the after ledger and action-flow documents.

The gap is executable: after rebinding all legitimate identities, a command requesting owner `FDE-X` is accepted while the ledger after-image still contains owner `FDE-C`. The current 305-vector corpus therefore proves target attribution, not action mutation correctness. This also fails to ratify existing brownfield lifecycle behavior: terminal actions cannot reopen (`skills/adp-status-sync/scripts/sync_status.py:949-951`) and action-flow timestamps obey status-dependent ordering rules (`skills/adp-status-sync/scripts/sync_status.py:954-993`), but neither rule is pinned in AD-2 or the protocol.

**Recommended fix:** register and execute one action-ledger mutation semantic algorithm. It must parse the exact before row, verify `expected_revision`, apply only present fields, preserve omitted fields, enforce the chosen terminal-transition and timestamp rules, render the exact ledger bytes, derive ledger-state/action-flow rows, and compare all three after-images byte-for-byte. Add full-graph negatives for every mutable field value, omitted-field reset, invalid terminal transition, timestamp side effects, create defaults, and ledger/flow disagreement in both harnesses.

### H2 - The business batch promises no partial action/WDR commit but only defines per-command transactions

**Dimension:** enforceability, real divergence points, operational completeness.

AD-10 claims to prevent partial action/WDR commits within one batch (`ARCHITECTURE-SPINE.md:133-137`). AD-1 simultaneously requires one typed command per fact transaction and explicitly places `refresh_actions` in a later ordered transaction (`ARCHITECTURE-SPINE.md:79-83`); AD-5 repeats that ordering (`ARCHITECTURE-SPINE.md:103-107`). The protocol resolves this only as “split into ordered transactions and link them at the upper batch” (`contracts/WDR-AND-TRANSACTION-PROTOCOL.md:51`). It does not define that upper batch's command order, preflight rule, durable run receipt, terminal outcome, retry cursor, or whether earlier committed facts remain when a later command fails.

The wire shape cannot close the promise. `meetingSyncPlanV2` is only four command/evidence arrays (`contracts/panel-sync-contracts.schema.json:431-444`), while `statusSyncBatchV2` is a `batch_id` plus accepted intents, action commands, and WDR patches (`contracts/panel-sync-contracts.schema.json:446-458`). Neither carries ordered transaction IDs or a batch outcome. The analysis acceptance matrix nevertheless expects a stale action revision to block the whole batch with neither ledger nor WDR partially committed (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:491-494`). Two builders can therefore preserve earlier committed facts or attempt cross-transaction rollback, and both can satisfy the registered transaction contracts.

**Recommended fix:** decide and pin one business-batch failure model. The design already favors durable fact commits plus fail-visible projection drift, so the coherent option is: deterministic command order, preflight all commands under the shared lock, never roll back a committed earlier fact transaction, and emit a registered batch-run receipt with per-command transaction IDs/outcomes and a retry cursor. Narrow AD-10's `Prevents` accordingly. If all-or-nothing is actually required, replace the one-command transaction invariant with a real multi-command journal rather than implying atomicity across independent commits. Add crash/stale-CAS tests at every command boundary.

## Medium Findings

### M1 - Strict publication rollout is described in the plan but is not an enforceable spine invariant

**Dimension:** operational/environmental completeness, source requirement coverage.

The plan says strict current publication may be enabled only after every projection-relevant writer is behind the fact-generation fence and production conformance has passed (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:444-454`). The spine separately says legacy/uninstrumented writers yield `migration-required` (`ARCHITECTURE-SPINE.md:127-131`) and that registry production conformance remains pending (`ARCHITECTURE-SPINE.md:139-143`), but it never binds strict-mode startup/publication to both conditions. Its capability map labels rollout as governed only by AD-9 (`ARCHITECTURE-SPINE.md:235-245`).

This leaves deployment behavior open: one implementation can expose strict current publication while the registry is still pending, while another can keep v1 current until an explicit migration. The stack and Deferred sections describe filesystems, daemon/queue deferral, and offline behavior (`ARCHITECTURE-SPINE.md:196-205,247-253`), but do not close the environment/promotion/rollback envelope.

**Recommended fix:** add a short rollout invariant: no new service/provider; local CLI/file deployment remains; strict mode is opt-in and fail-closed unless both production conformance status is `passed` and a complete writer-fence migration attestation is current; legacy mode remains readable but never claims live freshness; define the migration command, rollback behavior, and operator-visible status.

### M2 - The Python stack entry is an unverified compatibility range, not a current runtime decision

**Dimension:** named-technology currency, environmental completeness.

The stack binds `Python >=3.10` (`ARCHITECTURE-SPINE.md:196-203`). That is an open-ended compatibility floor, not a verified-current runtime or a production test matrix, and the memlog contains no version record. For a design that depends on Unicode normalization, JSON number serialization, path handling, `fsync`, and cross-platform replace semantics, builders can select materially different runtimes while still claiming stack compliance. The wire conformance gate reduces the risk, so this is not High, but it does not satisfy the reviewer-gate requirement that named technology be verified-current.

**Recommended fix:** record the verified production Python line and CI matrix, or explicitly state that runtime version is intentionally implementation-defined and production acceptance depends solely on the registered conformance adapter plus native durability suite. In either case, name the minimum supported and tested versions rather than an unbounded range.

## Low Findings

None.

## Checklist Verdict

| Good-spine dimension | Verdict | Evidence |
| --- | --- | --- |
| Real divergence points | **FAIL** | The intended action patch boundary is present, but H1 leaves field application unconstrained and H2 leaves multi-command failure semantics open. |
| AD enforceability | **FAIL** | H1 accepts command/ledger disagreement; H2's stated prevention is not supplied by its rule or contracts. |
| Deferred safety | **PASS** | Action Center, push/daemon, database migration, fuzzy action matching, and offline freshness are safely bounded at `ARCHITECTURE-SPINE.md:247-253`; destructive fuzzy patching and offline freshness overclaim are explicitly prohibited. |
| Named-tech currency | **PARTIAL** | JSON Schema Draft 2020-12 and RFC 8785 are pinned; Python is only an unverified `>=3.10` range (M2). |
| Brownfield fit | **PARTIAL** | The design preserves Panel v1, local Markdown/JSON facts, and existing ownership, but H1 omits existing action lifecycle behavior. |
| Source requirement coverage | **PARTIAL** | All five user symptoms map to AD-1 through AD-8 and the capability map, but the primary existing-action mutation remains false-green under H1. |
| Inherited invariants | **N/A** | No parent spine is declared or referenced as an inherited architecture source. |
| Operational/environmental completeness | **PARTIAL** | Crash recovery, POSIX/Windows durability, inspect/refresh, receipts, and release evidence are substantial; business-batch failure and strict rollout remain open (H2, M1). |

## Exit Conditions

1. Make action after-images a deterministic result of the exact action command, including brownfield lifecycle rules, and add field-value/lifecycle negatives to both harnesses.
2. Contract the multi-command business-batch order, partial-success behavior, durable outcome, and retry semantics; align AD-10 and the acceptance matrix with it.
3. Bind strict publication to production conformance plus complete writer-fence migration, and state the local deployment/rollback mode.
4. Replace the open-ended Python range with a verified runtime/test policy.

The gate can pass once the two High findings are closed and a fresh independent review reports zero Critical/High findings.
