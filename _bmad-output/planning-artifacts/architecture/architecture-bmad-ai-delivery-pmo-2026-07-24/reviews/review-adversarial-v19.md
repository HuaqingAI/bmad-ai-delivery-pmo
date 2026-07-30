# BMad Architecture Adversarial Review v19

## Gate Verdict

**REJECT. Critical: 0. High: 5. Medium: 0. Low: 0.**

The spine closes many earlier ownership, publication, recovery, and activation gaps, but the two core end-to-end chains added for this change still do not compose. A real drift verdict cannot be consumed by the registered audit/repair validator, and a real multi-source status-intent set cannot be durably carried from its producer plan through the outbox into the merged status-sync WDR transaction. The separate repair-attempt journal also has no normative restart identity at the business-to-attempt handoff.

| Severity | Count |
| --- | ---: |
| Critical | 0 |
| High | 5 |
| Medium | 0 |
| Low | 0 |

## Frozen Review Base

| Artifact | Raw SHA-256 |
| --- | --- |
| `ARCHITECTURE-SPINE.md` | `677b6df331c2fde6d6192be61ce03d39529b9fdf9cb2223a15f72f79de20e6b5` |
| `contracts/CONTRACT-REGISTRY.json` | `82fd15723a618f3edf75881c9304f34f92c83683a44d64f1bbaa263835ee7ce7` |
| `contracts/panel-sync-contracts.schema.json` | `5c3f4c916042afeea9d038839d6cbe7c694859737c27794b17268b908f85491e` |
| `contracts/WDR-AND-TRANSACTION-PROTOCOL.md` | `ef1fe1a7aa65a148a76620581003dc7a55f2c870a2a1ae175d76bc660a9af7fb` |
| `contracts/fixtures/CONFORMANCE-VECTORS.json` | `4c8ca5565db78b5e54dc6fbc6a9e6f85ba1f7a73e6e2f98c976fa1500d3f0794` |
| `contracts/conformance/python_runner.py` | `af522cb4280bd221996babda45e76316e6712ee2bdaf672a48986133879f743b` |
| `contracts/conformance/node_runner.mjs` | `99baf1fb498f7b2a6c5ce3975ae388f4e8edcca406847ec0755db63c9a3d79d7` |

Both checked results report 627 passed and 0 failed, but both are `design-fixture-check` results with `native_durability_exercised=false`. More importantly for this review, the passing drift, repair, status-intent, and outbox fixtures are separate constructions rather than one producer-to-consumer round trip.

## Minimal Divergence Matrix

| Boundary | Unit A | Unit B | Incompatibility |
| --- | --- | --- | --- |
| Drift -> audit | Emits AD-5 literal IDs such as `missing-active:A-FLOW-1` | Recomputes SHA-256 IDs from `{workstream_id,action_diff}` as the repair validator requires | The same action diff has different identity and the real drift document is rejected by repair |
| Producer plan -> outbox | Copies the exact typed status intent from `meetingSyncPlan.status_intents` | Derives an intent from the history/owned-fact command alone | The journaled command contains no binding that decides which payload is authoritative |
| Aggregated patch -> outbox | Marks every intent bound to the committed WDR patch consumed | Marks one representative outbox row consumed | Both can commit the same WDR bytes, but Panel convergence and retry state differ |
| Repair dry-run | Re-parses ledger bytes to prove each `expected_present` and revision | Trusts the self-consistent read-set claim and fingerprint | The same batch can be accepted as an orphan or rejected as a present action |
| Business repair -> attempt audit | Derives attempt transaction identity from a hash/index | Uses the reference runner's `business_tx + "-attempt"` convention | After a crash between journals, each process searches a different journal and may duplicate or lose the attempt record |

## High Findings

### H1 - The live drift finding IDs cannot enter the audit/repair graph

AD-5 fixes drift finding IDs as `ledger-*-mismatch`, `missing-active:<id>`, `extra-action:<id>`, `content-mismatch:<id>`, and `wdr-*-mismatch` (`ARCHITECTURE-SPINE.md:107`). The live drift implementation follows that rule: `expected_drift_verdict` appends those strings and includes ledger/WDR findings in the same `finding_ids` array (`python_runner.py:1493-1518`; Node mirrors this at `node_runner.mjs:750-780`).

The repair validator uses a different identity function. It hashes `{workstream_id, action_diff}`, requires each drift row's `finding_ids` to equal only those hashes, and then requires the audit finding set to equal the derived action findings (`python_runner.py:7005-7027`; Node `node_runner.mjs:4257-4277`). The schema leaves both representations legal because drift, audit, and batch finding IDs are unconstrained strings (`panel-sync-contracts.schema.json:855,1157,1179`).

Minimal executed counterexample: removing `A-FLOW-1` from the sidecar makes the live drift function emit `['missing-active:A-FLOW-1', 'wdr-content-mismatch']`; the repair fixture requires `['sha256:e4975db6f6ac37da45e631d13407c438f0dec387fee864b57a0f690a9404882d']` for the identical `missing-from-wdr` action diff. Thus no end-to-end document can satisfy both registered handlers, and ledger/WDR-only findings disappear from audit entirely.

**Required correction:** register one finding identity algorithm and one typed finding shape used by both producers. Represent non-action ledger/WDR findings explicitly and mark them repairable or non-repairable without deleting them. Run a single vector that creates drift from ledger/WDR/sidecar bytes, feeds that exact raw verdict into state-audit, constructs batches, and applies or blocks repair without replacing the verdict fixture. Disposition: **fix before handoff**.

### H2 - The outbox does not carry the producer's actual typed status intent

The producer contract places the real typed payload in `meetingSyncPlan.status_intents`, separately from `history_patches` (`panel-sync-contracts.schema.json:634-646`). A WDR history patch contains history rows and evidence but has no intent ID, intent digest, plan ID, or embedded status intent (`panel-sync-contracts.schema.json:546-580`). The fact attribution validator is command-derived and its registered scope does not include a meeting/checkpoint/risk plan that could supply this missing preimage (`CONTRACT-REGISTRY.json:929`).

The reference runners bridge the gap with implementation-local invented data. `status_intent_for_command` emits fixed values such as `progress: "Progress reviewed"`, `progress: "Checkpoint reviewed"`, or a fixed risk, and the consume branch hard-codes `origin = "adp-meeting-sync"` (`python_runner.py:4162-4185`; Node `node_runner.mjs:2229-2245`). None of those values is derived from the schema-valid additive status payload required by AD-9.

Minimal pair: one meeting plan carries `{blockers:{mode:"replace",values:["Access"]}}` while its history patch records the meeting. Unit A appends that exact typed intent to the outbox; Unit B applies the registered command-only mapping and appends `progress: "Progress reviewed"`. Both have the same authorized history command and evidence, but status-sync will mutate different WDR fields.

**Required correction:** bind exact intent IDs plus canonical intent bytes/hashes to the fact command and journal, or define a plan envelope whose exact raw bytes are an authorized fact-transaction input. Validate plan-to-command workstream/evidence/cardinality and append the exact supplied intent; never infer its current-field payload from history text or producer identity. Add meeting, checkpoint, and risk known answers with deliberately different field/value sets. Disposition: **fix before handoff**.

### H3 - One merged WDR patch cannot consume all of its contributing outbox entries

AD-10 and protocol section 2 require all accepted intents for one workstream to merge into exactly one status-sync WDR patch (`ARCHITECTURE-SPINE.md:137`; `WDR-AND-TRANSACTION-PROTOCOL.md:25`). The registered status-intent fixture demonstrates this with a meeting blocker intent plus a checkpoint progress/risk intent bound to one patch (`python_runner.py:1282-1324`), and its validator accepts the aggregation (`python_runner.py:1327-1399`).

The fact transaction that commits that patch has a contradictory outbox cardinality. Its consume branch requires `len(before_entries) == len(after_entries) == 1`, compares one synthetic expected intent, and marks only that row consumed (`python_runner.py:5444-5455`; Node `node_runner.mjs:3242-3251`). There is no command field containing the complete bound intent-ID set. Therefore the valid two-intent status batch cannot produce a conforming fact/outbox transaction: one intent remains pending forever, or an implementation mutates multiple rows using an unregistered rule.

**Required correction:** carry the exact sorted bound intent IDs/digests on each aggregated WDR command or its authorized execution envelope. The fact transaction must prefix-preserve all unrelated rows and atomically transition every bound pending row to consumed with the same fact receipt, rejecting missing, extra, duplicate, already-terminal, or cross-workstream entries. Add a round-trip vector using the existing two-intent fixture and a crash/retry after the WDR business bytes but before terminal marker. Disposition: **fix before handoff**.

### H4 - Repair accepts action presence and revision claims that contradict its own fact proof

AD-7 states that an orphan uses `expected_present=false, revision=null` and that the ledger fingerprint protects the absence (`ARCHITECTURE-SPINE.md:119`). The repair validator checks equality among finding, command, and read-set action IDs, but it never reparses the ledger bytes used by the fact transaction to prove each read-set row's presence and revision. Its only cross-checks are the batch ledger fingerprint, the post-refresh sidecar fingerprint, and post-refresh sidecar action IDs (`python_runner.py:7057-7068,7258-7269`). The repair semantic validator's registered scope also lacks the action-ledger state needed to recompute those claims (`CONTRACT-REGISTRY.json:932`).

This is executable, not hypothetical: `repair_graph_fixture(..., outcome="orphan")` passes `repair_graph_semantics` while the batch says `A-FLOW-1` is absent with null revision and the validator's own after sidecar still contains `A-FLOW-1`. The repair graph also accepts a self-hashed drift verdict after only schema and `verdict_id` checks (`python_runner.py:6980-7004`); it does not invoke `action_projection_drift_content_semantics` or resolve a validated producer receipt.

**Required correction:** make dry-run validation open the exact ledger and ledger-state raw bytes under the fact lock, recompute their canonical state/fingerprint, and derive every `(action_id,expected_present,revision)` row from those bytes. Bind and resolve the exact validated drift producer receipt/manifest/generation, or recompute drift directly from the same fact snapshot. Add negative vectors for absent-claim/present-row, present-claim/absent-row, wrong revision, invented action diff, and drift receipt substitution. Disposition: **fix before handoff**.

### H5 - The business-to-repair-attempt crash handoff has no normative durable identity

AD-7 and protocol section 8 require the business journal to reach a terminal committed/rolled-back state before a separate committed repair-attempt journal records the outcome (`ARCHITECTURE-SPINE.md:119`; `WDR-AND-TRANSACTION-PROTOCOL.md:100-102`). However, neither `repair-run-receipt` nor an attempt-ledger entry carries the attempt transaction ID, attempt journal ID/path, predecessor business marker ID/hash, or handoff state (`panel-sync-contracts.schema.json:2086-2104,2165-2194`). The registry has only the generic journal path template; it defines no business-transaction-to-attempt-transaction derivation or pending-handoff index.

The reference fixture silently chooses `${business_transaction_id}-attempt` (`python_runner.py:6951-6959`; Node `node_runner.mjs:4210-4218`), but that convention is not registered. The advertised restart probe does not test the gap: it constructs a new repair index from in-memory graphs, writes only the synthesized index and repair receipts, and has a child read those files; it neither reloads the attempt ledger nor recovers either journal (`python_runner.py:7310-7427`).

Minimal crash: the business terminal marker is durable and the process exits before preparing the attempt journal. Unit A derives `tx-repair-1-attempt`; Unit B derives a content-hashed attempt transaction. With no durable handoff identity, each can miss the other's partial journal, append a second attempt, re-run business, or leave a committed repair undiscoverable.

**Required correction:** register a deterministic attempt transaction ID/path derivation or persist a business-terminal-to-attempt handoff record atomically with the business terminal evidence. Bind exact business marker/recovery receipt raw hashes into the attempt command, ledger entry, index entry, and repair receipt; require distinct transaction/journal IDs. Fault-inject after the business terminal marker and after every attempt target, restart from registered disk paths only, and prove exactly one attempt sequence/receipt with idempotent recovery. Disposition: **fix before handoff**.

## Severity Tail

No Medium or Low findings. The five findings above are contract-level interoperability or recovery failures on capabilities explicitly requested by the user; none is editorial or optional hardening.

## Decision

**FAIL: 0 Critical, 5 High, 0 Medium, 0 Low.** Keep `ARCHITECTURE-SPINE.md` at `status: draft`. Unify the live drift/audit wire identity, carry exact producer intents through the outbox, consume complete aggregated intent sets, derive repair reads from actual ledger bytes and validated drift, and close the two-journal handoff before rerunning the reviewer gate.
