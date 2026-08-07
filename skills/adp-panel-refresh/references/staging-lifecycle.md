# Panel Refresh Staging Lifecycle

Panel Refresh staging is disposable execution state, not durable project memory. Each new workspace contains only:

- canonical fact files bound by the plan;
- current non-Panel projections;
- the latest current meeting pack per scenario;
- Program Status history needed for policy selection;
- the last publication binding and only the audit records referenced by copied projections or that receipt.

`input-manifest.json` records every copied relative path, SHA-256, size, source type, the plan's declared source fingerprints, and a content ID. Old receipts, transactions, unrelated audits, Management Panel bundles, and prior inspect output are excluded.

## Terminal evidence

Supersede and abandon create `state/panel-refresh/evidence/<refresh-id>.zip`. The archive contains the terminal plan snapshot, plan ID, input manifest, node results, policy candidate files, memlogs, and an output-artifact digest manifest. The run plan binds the archive path and SHA-256. After verification, `workspace/memory/` is deleted and a durable workspace-prune receipt records files and bytes removed. The compact workspace shell may remain until explicit prune.

A failed archive hash or member digest is a hard `REFRESH_EVIDENCE_INVALID`; never repair it by deleting the workspace.

## Abandon

`abandon --plan <dirty-plan> --reason <text>` is the only ordinary route that makes a dirty/planned/awaiting-policy run pruneable. It requires a different, newer successful publication, records the replacement identity, clears retry state if the abandoned run was pointed, archives evidence, and marks the plan `abandoned`. Refreshing runs cannot be abandoned.

## Prune

`prune` is dry-run unless `--apply-prune` is explicit. It may remove selected `published`, `superseded`, or `abandoned` workspace shells and unreferenced `.failed-winlock` directories. It never removes an active `planned`, `refreshing`, `dirty`, or `awaiting-policy` workspace, or the workspace named by the mutable status pointer.

Before deleting a run workspace, prune requires a durable plan and a verified evidence archive; if no archive exists for a terminal run, apply-prune creates and verifies one first. A durable prune receipt records refresh IDs, paths, file counts, freed bytes, and evidence archive IDs. Orphan deletion also emits a dedicated `orphan-cleanup` receipt with the removed `.failed-winlock` paths and exact reclaimed size. `--dry-run` writes nothing.

Retention selectors are `--keep-last`, `--older-than-days`, `--max-total-bytes`, repeatable `--refresh-id`, `--include-superseded`, `--include-abandoned`, and `--include-orphans`.

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
