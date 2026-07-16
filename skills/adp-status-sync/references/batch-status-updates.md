# Batch Status Updates

Load this reference for multiple workstreams, workflow-produced structured actions, updates-file execution, durable receipts, or historical receipt migration. Use `{communication_language}` for user-facing review; keep JSON field names and canonical statuses unchanged.

## Payload Contract

Batch files use `{"updates":[{"id":"<workstream-id>","milestones":[...],"actions":[...],"refresh_actions":false}]}`. `refresh_actions` is boolean and defaults to false. A milestone requires `milestone_id`, `status`, and `evidence`, with optional `forecast` or `actual`. New action registration requires `owner`, `workstream` or `affected_workstreams`, `action`, `source`, `due`, `status`, `closure_criteria`, and `owning_workflow`; any existing-action mutation requires its exact `action_id`. `next_actions` is accepted only for legacy compatibility and, when supplied, wins over `refresh_actions`: write only the explicit content without merging ledger entries. Put interpreted owner or closure-quality deficiencies in the update's `unresolved_gaps` array so they survive into the writer result.

For one source action affecting several workstreams, write one canonical action with `workstream: "program"` and `affected_workstreams`. Split it only when owner, due trigger, or deliverable differs. Program actions update the ledger and daily log without requiring `workstreams/program/delivery-record.md`.

When `refresh_actions` is true, project only active actions targeted to the physical update ID or explicitly listing it in `affected_workstreams`. Preserve manual WDR text without a stable action marker and reconcile ledger-backed entries by action ID. Reject refresh, milestone, or WDR-field updates targeting `program` with `ADP-VIRTUAL-SCOPE-NOT-WDR-TARGET`; action-only program updates remain valid.

## Preview And Apply

For an interactive natural-language batch or any multi-workstream interpretation, materialize the updates file and preview it first:

```bash
uv run "{skill-root}/scripts/sync_status.py" update "{project-root}" --updates-file <path> --dry-run
```

Surface proposed changed fields and unresolved gaps. Apply only after the user accepts them, using the same unchanged input path and bytes; if the interpretation changes, preview the revised file again. A single unambiguous delta may execute directly. A headless caller may execute directly only when it explicitly authorizes apply.

```bash
uv run "{skill-root}/scripts/sync_status.py" update "{project-root}" --updates-file <path>
```

The writer preflights the full batch and publishes WDR, daily log, action ledger, action-flow view, and receipt changes atomically. Successful non-dry-run execution writes a versioned receipt under `receipts/status-sync/`, bound to the exact resolved input path and SHA-256 of its raw bytes; surface `receipt_path`. Dry-run returns only a non-durable preview receipt and never proves application.

## Historical Receipt Migration

Verify one historical input against its original successful non-dry-run execution report before writing anything. That report's root object must directly declare both the exact resolved `input_path` and raw-byte `input_hash`; values added by a wrapper, nested receipt, or later attestation are not execution evidence, and basename similarity is never evidence:

```bash
uv run "{skill-root}/scripts/sync_status.py" migrate-receipt "{project-root}" --updates-file <path> --evidence-file <report.json> --applied-at <iso-time> --attested-by "<authority>" --dry-run
```

An unverified result writes no receipt and remains blocked. Apply a verified result only with the unchanged dry-run token:

```bash
uv run "{skill-root}/scripts/sync_status.py" migrate-receipt "{project-root}" --updates-file <path> --evidence-file <report.json> --applied-at <iso-time> --attested-by "<authority>" --verified-plan-token <token>
```

Process historical inputs one at a time. A set with a different number of reports and intakes must be paired from declared path/hash bindings, never filenames; only `verification_status: verified` entries receive versioned receipts. `attested_by` is receipt attribution only: it proves neither execution nor authorization and cannot repair a report missing either direct binding.
