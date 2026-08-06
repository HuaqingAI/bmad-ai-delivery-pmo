# Batch Status Updates

Load this reference for multiple workstreams, workflow-produced structured actions, updates-file execution, durable receipts, authority-state bootstrap, historical receipt migration, or fact reconciliation of receipt-less intake. Use `{communication_language}` for user-facing review; keep JSON field names and canonical statuses unchanged.

## Runtime Context

- `{skill-root}` is the installed `adp-status-sync` directory.
- `{project-root}` is the resolved target project directory.
- `{memory-root}` is the memory path returned by the activation context command or the caller's explicit `--memory-root`; use the same value for preview, apply, and receipt migration.

## Payload Contract

The canonical batch envelope is `{"updates":[{"id":"<workstream-id>","milestones":[...],"actions":[...],"refresh_actions":false}]}`. A meeting-sync envelope may also carry `status_intents` and `action_commands`; pass it unchanged. When `updates` is present, the writer applies those typed updates and binds matching intent IDs for outbox convergence. When `updates` is absent, it deterministically derives grouped updates from `status_intents[].set` and `action_commands[]`.

`refresh_actions` is boolean and defaults to false. A milestone requires `milestone_id`, `status`, and `evidence`, with optional `forecast` or `actual`. `next_actions` is accepted only for legacy compatibility and, when supplied, wins over `refresh_actions`: write only the explicit content without merging ledger entries. Put interpreted owner or closure-quality deficiencies in the update's `unresolved_gaps` array so they survive into the writer result.

Typed action creation and patching use explicit operations:

```json
{
  "updates": [
    {
      "id": "l1-checkout",
      "actions": [
        {
          "operation": "create",
          "command_id": "cmd-create-001",
          "action_id": "ACT-MEETING-001",
          "owner": "FDE-A",
          "workstream": "l1-checkout",
          "action": "Publish validation evidence",
          "source": "meetings/2026-07-30-sync.md#M-001",
          "due": "2026-08-01",
          "status": "open",
          "closure_criteria": "Evidence URL is attached to the WDR",
          "owning_workflow": "adp-meeting-sync"
        },
        {
          "operation": "patch",
          "command_id": "cmd-patch-002",
          "action_id": "ACT-20260701-004",
          "expected_action_revision": 3,
          "owner": "FDE-B",
          "status": "in-progress",
          "evidence": [{"source": "meetings/2026-07-30-sync.md#M-002"}]
        }
      ]
    }
  ]
}
```

A typed create needs stable `command_id` and `action_id`, plus owner, workstream or affected workstreams, action, source, due, status, closure criteria, and owning workflow. A typed patch needs a stable `command_id`, exact `action_id`, positive `expected_action_revision`, evidence, and at least one mutable field. Mutable fields are owner, status, action, due/due-or-trigger, closure criteria, owning workflow, workstream, and affected workstreams. Only present fields mutate; omitted fields are preserved. Stale revisions, command replay with changed bytes, and terminal reopen fail closed. Legacy creates that omit `operation` remain accepted; they may omit `action_id`, and a supplied stable `command_id` binds replay to the writer-generated ID.

A command-only meeting envelope has this shape; each intent must have a matching workstream route and each command keeps its exact identity and evidence:

```json
{
  "schema_version": "2.0.0",
  "status_intents": [
    {"intent_id": "intent-001", "workstream_id": "l1-checkout", "set": {"progress": "80% complete", "blockers": []}}
  ],
  "action_commands": [
    {"operation": "patch", "command_id": "cmd-002", "action_id": "ACT-20260701-004", "expected_action_revision": 3, "set": {"status": "done"}, "evidence": [{"source": "meeting#M-002"}]}
  ]
}
```

For one source action affecting several workstreams, write one canonical action with `workstream: "program"` and `affected_workstreams`. Split it only when owner, due trigger, or deliverable differs. Program actions update the ledger and daily log without requiring `workstreams/program/delivery-record.md`.

When `refresh_actions` is true, project only active actions targeted to the physical update ID or explicitly listing it in `affected_workstreams`. Preserve manual WDR text without a stable action marker and reconcile ledger-backed entries by action ID. Reject refresh, milestone, or WDR-field updates targeting `program` with `ADP-VIRTUAL-SCOPE-NOT-WDR-TARGET`; action-only program updates remain valid.

## Preview And Apply

For an interactive natural-language batch or any multi-workstream interpretation, materialize the updates file and preview it first:

```bash
uv run "{skill-root}/scripts/sync_status.py" update "{project-root}" --updates-file <path> --memory-root "{memory-root}" --dry-run
```

Surface proposed changed fields and unresolved gaps. Apply only after the user accepts them, using the same unchanged input path and bytes; if the interpretation changes, preview the revised file again. A single unambiguous delta may execute directly. A headless caller may execute directly only when it explicitly authorizes apply.

```bash
uv run "{skill-root}/scripts/sync_status.py" update "{project-root}" --updates-file <path> --memory-root "{memory-root}"
```

The writer preflights the full batch and publishes WDR, daily log, action ledger, action-flow view, and receipt changes atomically. Successful non-dry-run execution writes a versioned receipt under `receipts/status-sync/`, bound to the exact resolved input path and SHA-256 of its raw bytes; surface `receipt_path`. Dry-run returns only a non-durable preview receipt and never proves application.

## Legacy Authority-State Migration

When an older project has a stale or missing `actions/action-ledger.state.json`, `delivery-record.state.json`, or `action-projection.json`, do not bypass the authority checks or hand-author sidecars. Preview one fact-bound migration:

```bash
uv run "{skill-root}/scripts/sync_status.py" migrate-authority-state "{project-root}" --memory-root "{memory-root}" --dry-run
```

Review `differences`, which records each old sidecar fingerprint, validation issue, mismatched canonical field, and desired fingerprint. The preview binds the raw ledger, every WDR, and all existing authority sidecars and returns a 15-minute single-use token. Apply only against unchanged bytes:

```bash
uv run "{skill-root}/scripts/sync_status.py" migrate-authority-state "{project-root}" --memory-root "{memory-root}" --token <single-use-token>
```

Apply atomically rebuilds the ledger state, every WDR state, and every WDR action projection from the current ledger/WDR facts; stale sidecar content is reported but never trusted as authority. The durable receipt under `receipts/authority-state-migration/` retains the original source fingerprints and output bindings. An unchanged repeat returns `already-migrated` and reuses that receipt. After migration, run a new input audit before using any repair batch.

## Historical Receipt Migration

Verify one historical input against its original successful non-dry-run execution report before writing anything. That report's root object must directly declare both the exact resolved `input_path` and raw-byte `input_hash`; values added by a wrapper, nested receipt, or later attestation are not execution evidence, and basename similarity is never evidence:

```bash
uv run "{skill-root}/scripts/sync_status.py" migrate-receipt "{project-root}" --updates-file <path> --evidence-file <report.json> --applied-at <iso-time> --attested-by "<authority>" --memory-root "{memory-root}" --dry-run
```

An unverified result writes no receipt and remains blocked. Apply a verified result only with the unchanged dry-run token:

```bash
uv run "{skill-root}/scripts/sync_status.py" migrate-receipt "{project-root}" --updates-file <path> --evidence-file <report.json> --applied-at <iso-time> --attested-by "<authority>" --memory-root "{memory-root}" --verified-plan-token <token>
```

Process historical inputs one at a time. A set with a different number of reports and intakes must be paired from declared path/hash bindings, never filenames; only `verification_status: verified` entries receive versioned receipts. `attested_by` is receipt attribution only: it proves neither execution nor authorization and cannot repair a report missing either direct binding.

If the logical intake path still exists but its bytes changed after the recorded execution, recover the exact original bytes and add `--original-updates-file <restored-original.json>` to both commands. This governed `historical-input-change` mode requires the execution report's direct `input_hash` to match the restored bytes and its direct `input_path` to match the current logical intake path. It canonicalizes the executable envelope (`updates`, status/action commands, workstream routes, owners, due/trigger, Source, milestones, status intents, and baseline controls) for both versions and fails closed on any executable difference. Formatting and top-level non-execution metadata may differ. The durable migration receipt binds the original and current hashes, both payload IDs, the canonical executable payload ID, and the exact diff; it also preserves the original bytes under `receipts/status-sync-input-migration/originals/`. Filename equality, an old absolute path alone, or operator attestation never authorizes this migration.

## Canonical WDR Field Deduplication

Do not delete duplicate canonical WDR lines manually. Dry-run the supported repair against the exact physical workstream and reviewed value:

```bash
uv run "{skill-root}/scripts/sync_status.py" repair-wdr-field "{project-root}" --memory-root "{memory-root}" --id <workstream-id> --section "Project Status" --field "<canonical-field>" --canonical-value-file <reviewed-single-line.txt> --principal <operator-id> --dry-run
```

Omit `--canonical-value-file` only when every duplicate value is identical. Conflicting values never auto-merge. Apply with the unchanged arguments and returned 15-minute token, replacing `--dry-run` with `--token <single-use-token>`. The operation binds the original WDR and authority fingerprints, then atomically publishes `delivery-record.md`, `delivery-record.state.json`, the action projection, consumed token state, and a durable receipt under `receipts/wdr-field-repair/`.

## Receipt-less Intake Fact Reconciliation

Use `reconcile-intake` only when the original successful execution report is absent and replay could duplicate facts. Reconcile one exact intake at a time; never batch replay a receipt-less backlog.

```bash
uv run "{skill-root}/scripts/sync_status.py" reconcile-intake "{project-root}" --updates-file <intake.json> --memory-root "{memory-root}" --principal <operator-id> --dry-run
```

The dry-run compares every action command to the canonical ledger by exact stable action ID or by the full normalized `action + owner + source + due/trigger + closure criteria` composite. It also compares status fields to the unique canonical WDR field, validates WDR revision/fingerprint lineage, checks milestone ID plus current baseline revision and roadmap facts, and verifies refresh/intent-consumption sidecars when requested. Filename similarity and operator attestation are never evidence.

A historical value may be `satisfied_by: superseded-lineage` only when durable receipts, exact correction intakes, or ordered daily-log entries prove the old value and a later current value. For a legacy action without `action_id`, an action-ledger artifact fingerprint may replace the historical Source only when `action`, owner, due/trigger, and closure criteria match exactly, the declared `affected_workstreams` set matches exactly, an ordered daily-log row records the candidate action ID and requested status, and exactly one current ledger row survives; multiple candidates are returned and never auto-selected. Stable actions may use ordered old/new action observations to prove later status, closure-verifiability, route, Source, or relation revisions, with every evidence path emitted in `lineage_evidence`; action text, owner, due, and closure-criteria identity never use fuzzy revision matching.

For WDR fields, receipt-bound or intake-bound daily-log commands must provide the ordered values. `change_notes` and `risks` may additionally preserve an exact historical prefix whose only appended values are fully matched structured metadata such as `risk_id`, `baseline_revision`, `related_plan_item_ids`, or `Candidate CHK-* from <artifact>:<path>`. Status and dependency changes never use substring containment. Milestones require the stable milestone ID, baseline/roadmap revision facts, an old ordered milestone observation, and a later daily-log or durable correction receipt matching the current roadmap row. If current Source references a correction intake without a durable receipt, reconciliation returns `missing_correction_receipts` instead of a generic current mismatch. Missing history, missing rows, or ambiguous candidates remain partial.

A partial result returns `verification_status: partial` and an exact `missing_commands` list; blocked errors always return `verification_status: blocked`, `missing_commands`, and `token: null`. Neither state issues a success receipt. Only an all-satisfied result issues a 15-minute, principal-bound, single-use token:

```bash
uv run "{skill-root}/scripts/sync_status.py" reconcile-intake "{project-root}" --updates-file <intake.json> --memory-root "{memory-root}" --principal <operator-id> --token <single-use-token>
```

Apply revalidates the complete read-set. Any newer WDR, ledger, baseline, projection, or intent lineage invalidates the token rather than overwriting current facts. The only committed business artifacts are the consumed token state and a content-bound durable `reconciliation` receipt under `receipts/status-sync/`, published atomically. Do not replay any of the remaining historical intakes until each one either receives this receipt or reports no missing commands under another supported evidence migration.

## Governed Historical Intake Retirement

Use retirement only for an executable historical intake that cannot legitimately receive an execution, migration, or reconciliation receipt. Retirement never changes the intake bytes and never claims the business commands executed.

```bash
uv run "{skill-root}/scripts/sync_status.py" retire-intake "{project-root}" --updates-file <intake.json> --reason superseded-by --superseded-by <successor-intake-or-durable-receipt> --principal <governance-authority> --memory-root "{memory-root}" --dry-run
```

For `never-applied` or `invalid-proposal`, omit `--superseded-by` and provide `--justification <governance-rationale>`. Those reasons fail closed if canonical reconciliation is unavailable or any command is already supported by current facts or lineage. Retirement uses a read-only legacy parser for historical terminal actions that lack modern IDs; it never makes those payloads executable. If safe normalization is impossible, the command returns `INTAKE_RETIREMENT_LEGACY_SCAN_BLOCKED` with stable blocked verification fields instead of surfacing a normal writer-schema error.

`superseded-by` binds the exact successor intake bytes, a validated durable status-sync receipt, or a strictly bound `meetings/receipts/` receipt. A meeting-sync receipt is accepted only when it is applied, non-dry-run, has stable meeting-instance and plan fingerprints matching the generated intake, touches exactly that intake, names durable daily-log/WDR writes, and every intake command is verified against canonical action/fact lineage. Ordinary meeting documents, dry-run/applying receipts, and receipts whose touched intake differs are rejected. Changing either successor invalidates retirement during audit. Apply with the unchanged arguments and returned 15-minute token, replacing `--dry-run` with `--token <single-use-token>`.

The transaction writes only consumed token state and a content-bound `intake-retirement` receipt under `receipts/status-sync-retirement/`. The receipt has `mode: retire-intake` and `status: retired`; it is never accepted as a successful `mode: update` execution receipt. Mutable intake fields such as `superseded: true` do not retire executable input.
