# Batch Status Updates

Load this reference for multiple workstreams, workflow-produced structured actions, updates-file execution, durable receipts, or historical receipt migration. Use `{communication_language}` for user-facing review; keep JSON field names and canonical statuses unchanged.

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
