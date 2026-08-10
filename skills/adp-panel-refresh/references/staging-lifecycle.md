# Panel Refresh Staging Lifecycle

Panel Refresh staging is disposable execution state, not durable project memory. Each new workspace contains only:

- canonical fact files bound by the plan;
- current non-Panel projections;
- the latest current meeting pack per scenario;
- Program Status history needed for policy selection;
- the last publication binding and only the audit records referenced by copied projections or that receipt;
- all JSON terminal-closure evidence under `receipts/status-sync/`, `receipts/status-sync-partial-closure/`, and `receipts/status-sync-retirement/`.

`input-manifest.json` records every copied relative path, SHA-256, size, source type, the plan's declared source fingerprints, and a content ID. Status-sync terminal receipts are plan-bound sources with source type `status-sync-terminal-receipt`; they are not optional historical receipts because state audit needs them to prove canonical intake closure. Other old receipts, transactions, unrelated audits, Management Panel bundles, and prior inspect output remain excluded.

A staging contract version binds this coverage. Applying an active pre-contract plan performs a controlled full replan: the old dirty workspace is superseded, archived, and memory-pruned, while the replacement plan binds the terminal receipts and starts again from state audit. For a current-contract workspace whose valid manifest is only missing bound terminal receipt rows/files, `prepare_staging` atomically rehydrates those receipts and updates the manifest; operators never delete the workspace manually.

## Terminal evidence

Supersede and abandon create `state/panel-refresh/evidence/<refresh-id>.zip`. The archive contains the terminal plan snapshot, plan ID, input manifest, node results, policy candidate files, memlogs, and an output-artifact digest manifest. The run plan binds the archive path and SHA-256. After verification, `workspace/memory/` is deleted and a durable workspace-prune receipt records files and bytes removed. The compact workspace shell may remain until explicit prune.

A failed archive hash or member digest is a hard `REFRESH_EVIDENCE_INVALID`; never repair it by deleting the workspace.

## Abandon

`abandon --plan <dirty-plan> --reason <text>` is the only ordinary route that makes a dirty/planned/awaiting-policy run pruneable. It requires a different, newer successful publication, records the replacement identity, clears retry state if the abandoned run was pointed, archives evidence, and marks the plan `abandoned`. Refreshing runs cannot be abandoned.

## Prune

`prune` is dry-run unless `--apply-prune` is explicit. It may remove selected `published`, `superseded`, or `abandoned` workspace shells and unreferenced `.failed-winlock` directories. It never removes an active `planned`, `refreshing`, `dirty`, or `awaiting-policy` workspace, or the workspace named by the mutable status pointer.

Before deleting a run workspace, prune requires a durable plan and a verified evidence archive; if no archive exists for a terminal run, apply-prune creates and verifies one first. A durable prune receipt records refresh IDs, paths, file counts, freed bytes, and evidence archive IDs. Orphan deletion also emits a dedicated `orphan-cleanup` receipt with the removed `.failed-winlock` paths and exact reclaimed size. `--dry-run` writes nothing.

Retention selectors are `--keep-last`, `--older-than-days`, `--max-total-bytes`, repeatable `--refresh-id`, `--include-superseded`, `--include-abandoned`, and `--include-orphans`. Exact `--refresh-id` selection is never truncated by the budget. Otherwise, explicit age filtering runs first, `--keep-last` protects the newest matching runs, and the budget selector chooses the oldest remaining minimal byte set needed to reach the target. Explicit `--max-total-bytes` overrides the configured default age. Without an explicit age or byte target, `keep_superseded_days` is soft: preserve young runs when older candidates are sufficient, but admit the oldest young terminal runs when the staging budget cannot otherwise converge.

Every preview reports `projected_staging_bytes`, `budget_target_met`, `budget_shortfall_bytes`, `retention_blocked_bytes`, and the default-retention bytes preserved or overridden. Active/current-pointer runs never enter the available budget pool, even when the target therefore cannot be met.

## Budget and observability

Detect and inspect report staging run count, total bytes, pruneable count/bytes, orphan count, configured budget, whether it is exceeded, and a recommended dry-run command. Effective config keys are:

```yaml
adp:
  panel_refresh:
    staging:
      max_total_gb: 2
      keep_superseded_days: 7
      keep_published_runs: 1
```
