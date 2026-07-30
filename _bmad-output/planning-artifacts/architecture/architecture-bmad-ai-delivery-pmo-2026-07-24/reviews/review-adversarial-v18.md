# Adversarial Architecture Review v18

## Frozen Snapshot

- Architecture spine SHA-256: `e8743002b5b7a5b012d5dd416d4a3d7378ad171e1484d9c143ed19f17a0cbfb8`
- Contract registry SHA-256: `3e72d1148a84fe6e3a1b39845b918d527e414fe2099efe7d606a1c8bf97f9fcd`
- Transaction protocol SHA-256: `f13331f08c1dfa914ff02342146ffbf3122b5aeb419ceb1bbb7fec6309cdd990`
- Conformance suite SHA-256: `7c2aa9392f4662b124f2bb21fd77b57e1e4454ad09ff10da13062735f7cb833c`
- Deterministic lint passes. Checked-in Python and Node results each report `568/568`, but both are explicitly `design-fixture-check` with `native_durability_exercised=false`.

## Gate Verdict

**BLOCKED: 3 Critical, 12 High.** The draft correctly identifies the original product failures, but its executable proof model still permits false `fresh`, disconnected activation histories, and repair recovery that contradicts the journal. Do not finalize the spine or authorize strict production publication.

Fixture agreement is not sufficient here. Python and Node mostly reproduce the same model and therefore can agree on the same omission. The reviewer-gate question is whether independently built units can obey the ADs literally yet choose incompatible state, authority, recovery, or target derivations. They can.

## Twin-Unit Divergence

| Boundary | Unit A | Unit B | Result while both claim conformance |
|---|---|---|---|
| Live inspect reads | Executes only reads needed by its validator | Declares the registry closure as read without opening every target | Both pass the current read-set equality check; Unit B can report false `fresh` |
| Activation sequence | Requires each step to consume the immediately prior receipt/state | Validates five independently valid steps and checks only operation order | Disconnected branches can be spliced into one apparent transition |
| Rolled-back repair | Treats the repair index and receipt as restored to their before images | Recreates them after rollback for restart discovery | Retry chooses different durable truth and may duplicate a batch |
| Repair index order | Keeps append-only history with the new entry last | Maintains registry sort order by `(lookup_id,sequence)` | A lexically smaller new lookup ID cannot satisfy both |
| Owned-fact path | Uses canonical `workstreamId` grammar | Uses the runner's broader segment regex | Writers disagree on whether paths such as `workstreams/Upper_Name/decisions.md` are legal |
| Native authority | Derives principal from native UID/SID, executable handle, and service identity preimages | Accepts caller-supplied aggregate identity hashes | The same process can receive different principal IDs |
| Historical release proof | Revalidates every archived receipt/blob/signature | Validates only current-set evidence and checks historical paths/hashes shallowly | Tampered historical evidence can remain accepted |
| First-publication retry | Finds the committed receipt and returns the committed result without mutation | Re-evaluates the original absent-state fixture | Both call the behavior idempotent, but only one is a replay |
| Publication lineage | Commits lineage and publication atomically | Commits pointer/state first and writes lineage afterward | A crash can expose current Panel state without the proof strict inspect requires |
| Audit repair input | Derives action findings from the registered drift algorithm | Accepts internally consistent producer-selected action IDs | Repair batches can omit real drift or target invented drift |

## Critical Findings

### C1 - Live inspect's read-set proof is declared data, not read instrumentation

AD-4 and AD-12 require an instrumented resolver whose actual reads exactly equal the registry-derived closure (`ARCHITECTURE-SPINE.md:101,149`). The Python runner initializes `actual = set(expected)` before any read occurs (`python_runner.py:7214-7220`); Node initializes `actual` from `expected` identically (`node_runner.mjs:4334-4338`). Workstream targets and dynamic leaves are also inserted into both sets by construction (`python_runner.py:7251-7269`; `node_runner.mjs:4368-4384`). The only observable failure is an extra caller-declared addition, not an omitted filesystem read.

An implementation can skip the ledger, a WDR state, a sidecar, a release receipt, or a live leaf and still prove exact coverage. This directly permits the stale Management Panel state the architecture is intended to eliminate.

**Required correction:** make the resolver the only contract/path byte-loading API and record a read only after the target was opened and its bytes were consumed. Derive `expected` independently from the registry, derive `actual` only from resolver events, and reject duplicates, aliases, wrong roots, missing opens, and unconsumed bytes. No validator may accept caller-provided read labels as evidence.

### C2 - The five-step activation transition has no durable predecessor chain

The command and receipt schemas carry an individual `transition_id`, operation, before/after IDs, and journal ID, but no lifecycle ID, step sequence, or predecessor receipt (`panel-sync-contracts.schema.json:1421-1465`). The validator declares `previous_receipt` but never compares it with the next step (`python_runner.py:2841,2949`). It checks only the operation list plus each step's local validity (`python_runner.py:2838-2873`). The attest journal target is weaker still: its before hash only has to be non-null, rather than equal the exact prior attestation bytes (`python_runner.py:2898-2908`).

Five valid steps from disconnected branches can therefore be spliced into a nominal `rollback -> reprovision -> record-refresh -> attest -> enable` transition. Strict mode may be enabled with a capability registry, refresh receipt, or attestation that was never produced by the preceding step.

**Required correction:** add one lifecycle ID, fixed step ordinal, predecessor receipt ID, and exact before-state IDs to every command and receipt. For step `n>1`, require its before activation/capability/attestation bytes and epochs to equal step `n-1` after bytes and bind the predecessor receipt. The attest target must use exact CAS against the old attestation or a typed absence proof. Persist a lifecycle index that admits one canonical committed chain.

### C3 - Repair rollback/restart truth contradicts its own journal

The repair validator makes the repair index and two receipts journal targets, yet the protocol's repair role closure omits `repair-index` and describes only `business+fact-generation+nonce+2 receipts` (`WDR-AND-TRANSACTION-PROTOCOL.md:93-95`). The runner requires `repair-index` (`python_runner.py:6078-6100` and the journal role check at `python_runner.py:3297-3302`). Under the journal rule, a `rolled-back` marker means every target has been restored to its before image. That necessarily removes the new repair index entry and created repair receipt.

The advertised restart probe does not recover those bytes. It synthesizes a new index from in-memory graphs and writes the index and receipts after the rollback (`python_runner.py:6274-6304`), then starts a child that reads this manually manufactured state. The test therefore assumes the very durable discovery truth the transaction erased.

**Required correction:** choose one coherent model. Recommended: journal the nonce, business targets, fact generation, fact receipt, repair receipt, and repair index atomically for committed repair; for rolled-back repair, keep failure discovery in a separate append-only attempt ledger that is not restored by the business rollback and is itself journaled. Define terminal-state recovery from registered disk paths only. Align the protocol role closure, registry, schema, and both runners.

## High Findings

### H1 - Repair index canonical ordering is incompatible with append-only prefix preservation

The registry declares `/entries` canonical order by `(lookup_id,sequence)` (`CONTRACT-REGISTRY.json:939`). Repair semantics requires the old array to remain an exact prefix and the new entry to be last (`python_runner.py:6095-6106`). A new lexically smaller lookup ID cannot meet both requirements.

**Required correction:** use a monotonic global sequence as the sole physical order and make `lookup_id` a secondary lookup field, or replace the array with a canonical map plus an append-only attempt log. Add a vector whose second lookup ID sorts before the first.

### H2 - Owned-fact target and Markdown grammars are implementation-local

Canonical `workstreamId` is lowercase and hyphen-constrained (`panel-sync-contracts.schema.json:38-41`), while the runner's `workstream-file` and `directory-file` rules accept uppercase, underscore, dot, and broader lengths (`python_runner.py:4137-4156`). `canonical-markdown-utf8-v1` is only a registry label; the implementation checks nonempty UTF-8, final LF, NFC, and absence of CR/NUL, but no registered document grammar or canonical renderer (`python_runner.py:4159-4177`).

**Required correction:** define path-rule segment schemas in the registry and reference canonical `workstreamId` where applicable. Register Markdown grammar/renderer IDs and hashes, or narrow the content contract to explicitly defined byte invariants and stop implying structural canonicality.

### H3 - Native authority preimages cannot be independently verified

The registry lists POSIX UID/device/inode/service-unit and Windows SID/elevation/file-ID preimages (`CONTRACT-REGISTRY.json:34-48`). The wire context carries only aggregate `effective_identity_sha256` and `executable_sha256` (`panel-sync-contracts.schema.json:1321-1355`). The validator recomputes the principal from those caller-supplied hashes rather than from native preimages (`python_runner.py:4191-4230`).

**Required correction:** define a native adapter boundary that returns typed, canonical preimages plus OS verification evidence directly to the validator. Hash them inside the trusted boundary and add platform probes for namespace, executable replacement, symlink/reparse point, impersonation, service-manager, and service-unit substitutions.

### H4 - Historical release evidence bytes are not validated

The loader parses and hashes each historical release set and transition receipt, then only adds historical evidence receipt/blob paths to `expected_paths` (`python_runner.py:1877-1910`). Parsing, canonical-byte checks, receipt identity checks, blob hashes, and signature validation are performed only over `release_set["entries"]`, the current set (`python_runner.py:1913-1947`).

**Required correction:** validate every archived set's receipts and blobs with the trust policy applicable at its acceptance time, or make history a Merkle/content-addressed chain whose root commits to all verified evidence. Add historical receipt and blob byte-tamper probes where path sets remain unchanged.

### H5 - Release history does not preserve a verifiable transition journal and marker

History entries retain only archive and transition-receipt paths/hashes (`panel-sync-contracts.schema.json:2272-2300`). They do not retain the transition journal, prepared marker, or terminal marker needed to prove that the receipt represents an actual durable commit. The loader validates receipt claims but does not reopen the referenced journal chain (`python_runner.py:1877-1912`).

**Required correction:** include journal and terminal-marker descriptors in every history entry and validate their exact bytes, target closure, and committed terminal state. Preserve them content-addressably for the lifetime of the history entry.

### H6 - Release chronology is not closed

The history schema orders only `set_generation`. The loader equates each transition's `committed_at` to that set's `accepted_at`, but does not require acceptance time to increase across generations and does not bind receipt `executed_at`/`signed_at` to be at or before acceptance (`python_runner.py:1877-1904`). A generation-2 set can therefore claim a time before generation 1 or before its evidence existed.

**Required correction:** enforce strictly monotonic `accepted_at`/`committed_at`, require every evidence execution and signature time to be no later than set acceptance, and evaluate all times against the trusted clock policy.

### H7 - First-publication idempotent replay is not a replay

`panel_publication_idempotent_replay_semantics` only rechecks a generation-1 graph whose before pointer/state are absent (`python_runner.py:7367-7378`). It never retries the same transaction after pointer/state and the receipt exist. The fresh-process probe pickles an already assembled in-memory package and calls inspect again (`python_runner.py:7381-7425`); it does not reload publication state from registered durable paths or execute publication recovery.

**Required correction:** after a real generation-1 commit, start a clean process, resolve the committed receipt by transaction/command fingerprint, retry the same publication against existing pointer/state, and prove a byte-identical no-op. A different fingerprint under the same ID must conflict.

### H8 - Strict publication lineage is outside the publication transaction

The panel journal owns projection envelopes, Panel, pointer, state, and receipt (`python_runner.py:6612-6688`). The lineage store, descriptors, producer receipts, publication journal copy, marker copy, and lineage index are assembled afterward (`python_runner.py:6911-6978`). Yet strict inspect requires that lineage to validate the now-current pointer.

**Required correction:** put the complete generation lineage closure and index in the same publication journal, with the current pointer applied only after all immutable lineage targets are durable. Fault-inject after every target, especially between the publication marker and lineage-index durability.

### H9 - Ordinary fact mutations lack a durable command-to-receipt index

The protocol requires same command ID and fingerprint replay to resolve as no-op, with mismatch as conflict, but the registry provides only a fact receipt path keyed by freely chosen transaction token (`CONTRACT-REGISTRY.json:102-107`). Only repair has an explicit receipt index. Implementations must scan, derive private transaction IDs, or maintain unregistered indexes.

**Required correction:** register a general fact command index keyed by `(command_id,command_fingerprint)`, define ownership, CAS, ordering, journal target, and recovery. Reuse it for action, WDR, owned-fact, and refresh-actions transactions.

### H10 - `source_as_of` equality has no time authority

Selection policy accepts any UTC timestamp (`panel-sync-contracts.schema.json:1152-1175`). The semantic check only propagates equality from the policy into Panel documents (`python_runner.py:3022-3038`). No rule binds the value to a request, trusted evaluation time, snapshot cutoff, or maximum accepted fact time.

**Required correction:** define `source_as_of` as an explicit trusted snapshot boundary, bind it to the refresh request and lock acquisition receipt, and reject future times or times older than the selected fact snapshot. Execute registry `source_time_bindings` rather than a hard-coded document list.

### H11 - Audit action IDs are internally consistent but not derived from actual drift

The repair validator checks that action entity refs equal `action_ids` and that findings, commands, and read sets agree (`python_runner.py:5949-5990`). It does not recompute the findings from the registered action-projection drift verdict or enforce the finding-ID algorithm. Since repair lookup identity incorporates producer-selected finding IDs (`python_runner.py:5666-5671`), a producer can omit a drifting action, invent an action finding, or choose unstable IDs while the graph remains internally consistent.

**Required correction:** make audit findings a deterministic projection of the validated drift verdict. Recompute exact finding IDs, action IDs, severity, operation, and source references before batch construction; reject omissions and additions. This is the contract that turns the user's requested concrete action IDs into safe bulk repair targets.

### H12 - Meeting/risk intent and current-field mutation have no cross-transaction recovery contract

AD-1 deliberately separates owned meeting history/risk facts from status intent and WDR mutation (`ARCHITECTURE-SPINE.md:83`). AD-3 then assigns current fields to a separate status-sync transaction (`ARCHITECTURE-SPINE.md:95`). If the owned fact commits and the intent or WDR mutation fails, publication sees a legitimate new meeting/risk fact and legitimate old WDR current fields. The registered drift gate covers ledger/WDR action projection, not meeting-intent-to-WDR or risk-fact-to-WDR convergence.

**Required correction:** persist a typed mutation-intent outbox in the owned-fact transaction, consume it idempotently in status-sync, and project an intent convergence verdict into state-audit/publication eligibility. A committed owned fact with pending/failed current-field intent must be visible as degraded and block `fresh+eligible` until resolved or explicitly waived.

## Prioritized Optimization Plan

### P0 - Restore truthful gates and recoverable state

1. Replace synthetic inspect read sets with resolver instrumentation and omitted-read fault probes.
2. Add a durable, predecessor-bound activation lifecycle chain with exact attestation CAS.
3. Redesign repair attempts so rollback semantics, repair index visibility, receipts, and restart discovery agree.
4. Keep `implementation_conformance_status=pending`; do not publish production trust roots or enable strict publication.

### P1 - Close projection and evidence history

1. Move the full lineage closure into the panel publication journal.
2. Validate all historical release receipt/blob bytes, journal markers, chronology, and signatures.
3. Register general fact command/receipt lookup and coherent repair-index ordering.
4. Make audit findings deterministic from drift and bind `source_as_of` to a trusted snapshot boundary.

### P2 - Eliminate semantic lag at workflow boundaries

1. Add a durable meeting/checkpoint/risk status-intent outbox.
2. Add intent convergence verdicts to state-audit and Panel publication eligibility.
3. Register owned-fact path and content grammars and native authority preimages so independent writers agree on targets and principals.

## Minimum Acceptance Probes

1. Omit one required live read while keeping all metadata self-consistent; inspect must not return `fresh`.
2. Splice five valid activation steps from disconnected branches; the lifecycle validator must reject.
3. Replace an attestation using a non-null but wrong before hash; attest must fail CAS.
4. Execute a rolled-back repair, terminate the process, and discover retry state only from recovered registered disk paths.
5. Append a repair whose lookup ID sorts before the existing entry; canonical index validation and append semantics must both pass under one rule.
6. Mutate bytes of a historical receipt and blob without changing the current set; strict inspect must reject.
7. Commit generation 1, restart from disk, retry the same publication against existing pointer/state, and prove no writes.
8. Crash after publication targets/marker but before lineage closure; recovery must never expose an unverifiable current pointer.
9. Omit one real drift action and add one invented action finding; audit/repair validation must reject both.
10. Commit meeting history or a risk fact while status-sync fails; Panel must report pending convergence and be ineligible.

## Evidence Limitations

The `568/568` Python and Node result sets show that two fixture runners implement the same registered design cases. They do not prove independent production implementations, actual resolver read instrumentation, native OS authority derivation, real filesystem durability, crash recovery, or process-boundary idempotency. `native_durability_exercised=false` is therefore material to this gate, not informational metadata.

## Decision

Keep `ARCHITECTURE-SPINE.md` in `draft`. Resolve C1-C3 and H1-H12, regenerate all affected hashes and conformance results, and rerun the reviewer gate. The gate may move from `BLOCKED` only when the minimum probes execute against independent implementations and native durability adapters rather than fixture-only in-memory packages.
