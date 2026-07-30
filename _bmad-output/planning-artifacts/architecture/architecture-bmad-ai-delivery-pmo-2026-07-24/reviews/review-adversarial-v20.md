# BMad Architecture Adversarial Review v20

## Gate Verdict

**REJECT. Critical: 0. High: 6. Medium: 5. Low: 0.**

The v20 package repairs the five v19 composition defects, but the resulting design is still not operable as a normal Management Panel update path. The strict attestation is a snapshot of mutable business and publication state, so the first legitimate fact change invalidates the authority required to publish its replacement Panel. Separately, a WDR-only marker edit produces no action-bearing repair batch, exact producer intents still have unbound carrier cases, and the outbox contains an unauthorised `waived` escape that the publication gate accepts.

| Severity | Count |
| --- | ---: |
| Critical | 0 |
| High | 6 |
| Medium | 5 |
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

Both checked results report 643 passed and 0 failed. Both remain `design-fixture-check` with `native_durability_exercised=false`.

## Minimal Divergence Matrix

| Boundary | Unit A | Unit B | Result |
| --- | --- | --- | --- |
| Fact change -> strict refresh | Treats migration attestation as a static writer-fence proof | Enforces the documented exact mutable fact snapshot | A publishes without the registered gate; B permanently returns `migration-required` after the first fact change |
| WDR -> drift -> audit | Compares parsed WDR action markers directly with ledger expectations | Implements the pinned ledger-to-sidecar then WDR-to-sidecar algorithm | A emits action IDs and a repair batch; B emits only non-repairable `wdr-content-mismatch` |
| Meeting plan -> outbox | Creates a dedicated intent-only carrier when no history exists | Accepts the schema-valid plan literally and has no fact command to carry its intent | The same plan either converges or leaves no durable outbox entry |
| Risk decision -> outbox | Applies the protocol rule to every risk-owned command | Applies the reference runner's risk-flow-only classification | A appends the decision's exact intent; B classifies the schema-valid command as `none` |
| Outbox terminal state -> publication | Requires an authorised waiver command and receipt | Directly marks a row `waived` and relies on the current publication predicate | One blocks an unapplied mutation; the other publishes it as eligible |
| Typed finding identity | Uses the ledger row path/line as the source locator | Uses the WDR path and literal line 42 | Identical business drift receives different finding, batch, and token IDs |
| Repair terminal -> attempt journal | Serialises and rebases the shared append under a lock | Prepares from an independently read ledger/index before image | A records both attempts; B strands a terminal business repair on stale append CAS |
| Live inspect diagnostic | Preserves `(root_instance_id,path)` | Emits path-only `changed_sources` | Multi-root changes collapse to an ambiguous report and refresh target |

## High Findings

### H1 - Strict attestation makes the documented daily update sequence impossible

AD-12 and protocol section 9 require the migration attestation to exactly bind current fact generation, ledger bytes/state/action-flow, every WDR/state/sidecar, latest refresh/publication receipts, current pointer, and Panel state; any closure change makes strict open, inspect, and publication return `migration-required` (`ARCHITECTURE-SPINE.md:149`; `WDR-AND-TRANSACTION-PROTOCOL.md:111`). The schema stores all of those mutable identities in `writerFenceMigrationAttestationV1`, and the suite explicitly expects ordinary fact-generation, ledger, WDR, pointer, and Panel-state changes to invalidate strict mode (`CONFORMANCE-VECTORS.json:604-619`).

That contradicts the operational runbook, which says to commit producer/status facts and then run `refresh --apply` (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:360-366`). Immediately after step 2 or 3, the attested fact generation and WDR/ledger hashes are stale. Step 4 therefore cannot pass the strict publication gate. Even a publication with unchanged facts advances the pointer and Panel state and invalidates the same attestation for the next run. The only specified recovery is the five-step rollback/reprovision/full-refresh/attest/enable lifecycle, including capability reprovision, for every normal status change.

**Required correction:** split immutable migration/fence evidence from mutable snapshot freshness. The activation attestation should bind writer builds, capabilities, roots, schemas, and fence coverage; live inspect/publication should bind current fact and Panel generations through fresh receipts and CAS, not require them to equal the activation-time snapshot. Add an end-to-end vector: enable strict once, commit a normal WDR/action mutation, refresh and publish generation N+1 without lifecycle reprovision, then live-inspect fresh. Disposition: **fix before handoff**.

### H2 - A real WDR-only marker drift still loses every action ID and cannot enter batch repair

The pinned algorithm compares expected ledger actions with sidecar records for action-level diffs, then compares parsed WDR Next actions only with sidecar summaries and labels any difference as non-repairable `wdr-content-mismatch` (`WDR-AND-TRANSACTION-PROTOCOL.md:72`; `python_runner.py:1504-1546`). Therefore a manual WDR edit with a still-correct sidecar does not create `missing-from-wdr`, `orphan-in-wdr`, or `content-mismatch` action rows.

Executed counterexample: starting from the valid drift fixture, replacing only the WDR bytes with a valid record whose Next actions differ, and updating its state fingerprint produced `action_diffs=[]` and one finding `{kind:"wdr-content-mismatch", repairability:"non-repairable", action_id:null}`. State-audit consequently has no `action_ids` or `repair_batch_id`, although `refresh_actions` can deterministically restore the WDR. The empty-ledger/manual-WDR-marker case has the same failure whenever the sidecar is already empty.

**Required correction:** derive action-level diffs from the three-way ledger/sidecar/parsed-WDR identity map. A missing, extra, or changed managed WDR marker must carry its exact action ID and be repairable even when sidecar equals ledger; reserve aggregate non-repairable WDR findings for malformed/unattributable content. Add vectors for correct-sidecar/WDR-missing-marker, correct-sidecar/WDR-orphan-marker, correct-sidecar/WDR-content-change, and empty-ledger/WDR-marker. Disposition: **fix before handoff**.

### H3 - `meetingSyncPlanV2` permits exact intents with no durable carrier command

The producer rule now correctly requires exact intents to be embedded in the fact command that appends them to the outbox (`WDR-AND-TRANSACTION-PROTOCOL.md:25`). However, `meetingSyncPlanV2` keeps `status_intents` and `history_patches` as independent arrays and defines no cardinality or binding between them (`panel-sync-contracts.schema.json:637-650`). An executed schema counterexample with one valid meeting status intent and zero action/history commands passed validation. It has no producer-owned fact transaction to carry the intent: action commands are executed under status-sync ownership and do not admit `status_intents`, while there is no intent-only command.

Even when history patches exist, no plan-level rule says which patch must carry which intent or requires the union of command-embedded intents to equal the plan array. Two meeting implementations can attach all intents to the first history command, partition them, invent an intent-only history row, or silently omit them while still producing the same schema-valid plan.

**Required correction:** define a typed producer transaction envelope or dedicated intent-outbox command, and require a lossless bijection between plan intents and command-carried intents by exact bytes/hash, workstream, evidence, and source meeting. Cover zero-history, multiple-history, multiple-workstream, duplicate, omitted, and extra carrier cases in both runners. Disposition: **fix before handoff**.

### H4 - Risk decision commands are legal intent carriers in the contract but rejected by both reference implementations

The architecture says risk review owns both risk-flow and decision facts and that any meeting/checkpoint/risk producer command with a current-field intent must append the exact intent in the same transaction (`ARCHITECTURE-SPINE.md:83,137`; `WDR-AND-TRANSACTION-PROTOCOL.md:25`). `ownedFactCommandV1` accordingly permits `status_intents` for every owned-fact target (`panel-sync-contracts.schema.json:220-233`).

The implementations classify risk intent emission only when `target_profile_id == "risk-flow-index-v1"`; a `workstream-decision-v1` command is `none` (`python_runner.py:4200-4218`; Node mirrors it). Executed counterexample: a schema-valid `owned-decision` command carrying an exact risk intent returned `schema_valid=true` and `outbox_mode="none"`. Fact attribution then requires commands in `none` mode to omit `status_intents`, contradicting the schema and normative producer rule.

**Required correction:** either explicitly forbid intents on decision commands in schema/protocol, or classify every authorised risk-owned profile as an emitter and validate the same exact outbox append. The current architecture text strongly supports the latter. Add decision-plus-risk/dependency intent positive and substitution/omission negative vectors. Disposition: **fix before handoff**.

### H5 - Outbox failure and waiver have no authorised mutation protocol, while waiver is publication-eligible

The outbox schema admits `processing`, `failed`, and `waived` (`panel-sync-contracts.schema.json:243-291`), but the only normative fact transitions are append-as-pending and selected-pending-to-consumed. There is no typed failure/waiver command, capability, journal target rule, principal/approval policy, reason evidence, or receipt binding. The registry algorithm only says pending or failed blocks freshness (`CONTRACT-REGISTRY.json:945`).

The reference convergence validator accepts a row merely because its self-consistent status is `waived`, and the publication predicate explicitly treats both `converged` and `waived` as eligible (`python_runner.py:3529-3579,3702-3710`). The operation manual instead requires convergence to equal `converged` (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:364-366`). Thus one implementation can rewrite an unapplied blocker/risk intent to `waived` and publish stale WDR current fields without any authorised decision record; another implementation following the runbook blocks.

**Required correction:** either remove `waived|processing|failed` until their lifecycle is designed, or add exact commands, authority, CAS, journal/receipt, evidence/reason, retry, and publication policy. Waiver must never be equivalent to convergence without a separately validated policy decision. Add attempted unauthorised waiver, authorised waiver, failed retry, processing crash, and waiver-publication vectors. Disposition: **fix before handoff**.

### H6 - Finding identity includes a source locator whose derivation is not pinned

The finding hash includes `source_path` and nullable `source_line` (`panel-sync-contracts.schema.json:816-847`; `WDR-AND-TRANSACTION-PROTOCOL.md:102`), but no rule says which of ledger, sidecar, or WDR is the authoritative source or how a line is calculated. The reference runner always uses the WDR path and literal line 42 for every action diff (`python_runner.py:1489-1501`). The analysis plan's canonical audit example instead uses `actions/action-ledger.md` at line 42 (`ANALYSIS-AND-OPTIMIZATION-PLAN.md:308-323`).

Both units can satisfy the stated body shape and hash rule yet produce different `finding_id`, `audit_id`, `repair_batch_id`, token binding, and repair receipt for identical raw facts. Line-number inclusion also makes identity churn when unrelated preceding Markdown lines move.

**Required correction:** register an exact locator derivation per finding kind and fixture it, or remove presentation location from the stable finding identity while retaining it as non-identity diagnostic metadata. Add Python/Node known answers for all five finding kinds and for irrelevant line insertion. Disposition: **fix before handoff**.

## Medium Findings

### M1 - The promised out-of-selection drift repair queue cannot be represented

AD-5 and the plan say scope-external drift is degraded and placed in a repair queue (`ARCHITECTURE-SPINE.md:107`; `ANALYSIS-AND-OPTIMIZATION-PLAN.md:302`). The normative drift protocol and validator require workstream rows to equal `selected_workstreams` exactly and reject extra rows (`WDR-AND-TRANSACTION-PROTOCOL.md:72`). A subset Panel selection therefore has no legal place to report drift from an unselected WDR. Define a separate full-inventory drift/audit stream, or remove the out-of-scope queue promise and document that only selected scope is monitored.

### M2 - Fact commit and convergence disagree on duplicate outbox identity

Producer fact attribution checks sequence continuity and each entry's inner hash/metadata but does not reject a new entry whose content hash or source command ID duplicates an existing row (`python_runner.py:5528-5550`). The later convergence validator rejects duplicate intent IDs or source command IDs globally (`python_runner.py:3516-3527`). A producer transaction can therefore commit owned history/facts plus an outbox state that can never produce a valid convergence verdict. Move the global uniqueness check into the pre-commit outbox validator and define whether byte-identical intents from distinct source commands deduplicate or conflict.

### M3 - The claimed business-terminal crash boundary is not exercised

Protocol section 8 requires recovery after a crash immediately after the business terminal marker and after each attempt target (`WDR-AND-TRANSACTION-PROTOCOL.md:106`). In both fresh-process probes, even `applied_count=0` prewrites the complete attempt manifest and all images before starting the child (`python_runner.py:7769-7784`; `node_runner.mjs:4741-4754`). This tests target roll-forward, not recovery when the attempt journal does not yet exist. Add a true pre-manifest case that derives the attempt ID from business terminal bytes, safely creates the attempt manifest/images, and proves deterministic after images or winner selection under a create race.

### M4 - Shared attempt-ledger/index append has no explicit lock or stale-manifest rebase rule

Every repair attempt replaces the same append-only attempt ledger and repair index. The protocol specifies prefix append and CAS, but not which lock is held from business terminal through attempt commit or how an already-prepared deterministic attempt journal rebases if another repair wins the shared append first (`WDR-AND-TRANSACTION-PROTOCOL.md:105-106`). Because attempt identity and journal path are fixed by the business marker, changing stale before/after images creates a different manifest at the same immutable path. Require one named exclusive lock across the handoff, or define a registered append coordinator/rebase generation that preserves deterministic identity without overwriting a prepared journal. Add two concurrent terminal repairs with opposite interleavings.

### M5 - Live inspect drops the physical root identity and has no standalone contract envelope

All physical leaves are identified by `(root_instance_id,path)`, yet `liveInspectVerdictV1.changed_sources` contains only unique relative paths, and the nested verdict has neither `contract` nor `schema_version` (`panel-sync-contracts.schema.json:2308-2336`). In a multi-root installation, two changed leaves with the same path collapse; an inspect CLI result also cannot be independently negotiated like the other wire documents. Use typed changed-source records containing root instance, path, observed fingerprint/status, and make the verdict a registered top-level contract consumed by open, detect, refresh status, and automation.

## Decision

**FAIL: 0 Critical, 6 High, 5 Medium, 0 Low.** Keep the architecture spine in draft. The next revision must first make strict activation compatible with ordinary fact updates, then close WDR-to-action repair attribution, producer carrier coverage, outbox terminal authority, and stable finding identity. The remaining recovery and inspect defects should be covered in the same conformance increment because they affect the operator's promised `detect -> refresh -> inspect` workflow.
