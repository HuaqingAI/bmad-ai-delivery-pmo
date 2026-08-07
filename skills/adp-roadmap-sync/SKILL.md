---
name: adp-roadmap-sync
description: Renders source-backed ADP roadmap views. Use when the user says "render ADP roadmap", "sync ADP roadmap", or "dry-run ADP roadmap".
---

# ADP Roadmap Sync

## Overview

This workflow renders the baseline-aware AI Delivery PMO timeline from an approved program baseline and the canonical program-status snapshot. Act as a delivery-state curator: preserve the difference between approved plan dates, forecast/actual state, unplanned source facts, open decision blockers, and ordinary follow-up actions. The output is `views/roadmap.json` for downstream workflows and `views/roadmap.md` for project leads and meeting packs.

The consumer needs a timeline they can trust in business review. Planned dates and approved dependencies come only from `plans/program-baseline.md`; forecast, actual, variance, constraint status, and the project-level judgment come only from `views/program-status.json` and its immutable snapshot. Never recompute the overall status or infer schedule from project feel, meeting phrasing, action due dates, or dependency graphs.

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `scripts/render_roadmap.py`) resolve from this skill's installed directory.
- `{project-root}` -> the project working directory.
- When executing skill-owned scripts in a shell, use `{skill-root}/scripts/...`. Do not rely on the shell working directory resolving `scripts/...`, because commands usually run from `{project-root}`.

## On Activation

Run the renderer only when the user intends to render, sync, or dry-run the roadmap. For informational questions or review requests, answer without running the script or writing roadmap views.

Resolve `{project-root}` and append only user-supplied `--date`/`--as-of`, repeatable `--workstream`, `--memory-root`, `--audit`, `--prepass-json`, `--output-dir`, and `--dry-run` flags in place of the placeholder:

```text
uv run "{skill-root}/scripts/render_roadmap.py" "{project-root}" <validated-user-supplied-flags>
```

Default to `{project-root}/_bmad-output/adp/memory` when `--memory-root` is absent. If the memory root is missing, block with the `adp-project-kickoff` recommendation; do not create project state.

Before rendering, require an approved baseline and a canonical program-status view whose baseline ID/revision, as-of date, source fingerprints, constraint IDs, and immutable snapshot agree. A missing or invalid baseline routes to `adp-plan-baseline`; a missing, stale, future-dated, or incompatible status routes to `adp-program-status` and `adp-state-audit`. Baseline revision 2+ also requires the immediately prior archived revision so the renderer can produce a traceable plan diff.

The renderer owns the quality gate. Without `--audit`, it runs sibling `adp-state-audit --scenario roadmap`, forwarding the selected memory root, scopes, date, and prepass. With `--audit`, it requires the complete audit/prepass schema, exact scope/date selection, a source inventory whose file fingerprints and missing paths match the renderer inputs, and identity equivalence when `--prepass-json` is also supplied. It compares physical `registered_workstreams` and `virtual_scopes` separately; it never treats `source_inventory.workstreams` as their union. Physical WDR discovery follows the shared scope contract, so a valid `retired-alias` directory may preserve its WDR as evidence without becoming a roadmap source or leaking into publication lineage. An audit without the shared scope contract is incompatible and must be regenerated. Missing, malformed, stale, changed, or incompatible audit input blocks rendering.

Scoped runs default to `views/roadmaps/<normalized-scope>/`; `--output-dir` overrides the destination.

`--workstream program` is a virtual-only render and reads no WDR. Repeated mixed selectors retain virtual milestones while scanning only the requested physical Workstreams. The exact baseline ID remains case-sensitive, while CLI selection may normalize supplied casing through the shared resolver.

`--dry-run` suppresses roadmap view writes, but an automatically generated audit may still persist its audit artifacts.

If only `uv` is unavailable, verify Python 3.10+ and retry the same invocation directly:

```text
python "{skill-root}/scripts/render_roadmap.py" "{project-root}" <validated-user-supplied-flags>
```

If the renderer or audit still cannot run, do not manually derive, create, or present canonical roadmap views. Return only a blocked JSON status based on any emitted failure, adding the failed dependency, actionable error, and workflow to retry after that dependency is restored:

```json
{
  "ok": false,
  "status": "blocked",
  "failed_dependency": "<renderer-or-adp-state-audit>",
  "error": "<actionable failure>",
  "recommended_workflows": ["<adp-roadmap-sync-or-adp-state-audit>"]
}
```

## State Boundary

Canonical timeline sources:

- `plans/program-baseline.md` owns planned dates, owners, dependencies, workstream mapping, and revision lineage.
- `views/program-status.json` and its immutable snapshot own forecast, actual, variance, status, rule IDs, report confidence, and overall project status.

Supplemental sources allowed only into unscheduled, unmapped, decision-block, or exclusion sections:

- WDR `### Roadmap` tables with an explicit `Source`.
- confirmed or applied `adp-bmm-checkpoint-sync` candidate JSON.
- closed decision-log rows and open business decision packets.
- readiness, cutover, and L0 gate views as gate context.

`actions/action-ledger.md` is read only to explain excluded follow-up items. A normal action `Due / Trigger` is not a milestone date and remains excluded with a reason. Other derived views under `views/` are context and gates; they cannot override the baseline or canonical program-status result. A source-backed item without a baseline mapping never enters the formal milestone timeline: dated items go to `Unmapped Items`, while items with no valid date go to `Unscheduled Milestones`.

Only explicit `open` decisions block; `accepted`, `closed`, `done`, `cancelled`, `rejected`, and `superseded` are terminal and never block. L0 status maps exactly as `open|planned -> planned`, `at-risk -> at-risk`, `blocked -> blocked`, and `closed|done -> done`; other or missing states are excluded. Free-prose blocker/dependency fields remain excluded context.

## Output Contract

The Markdown and JSON contain:

- `Source Inventory`
- `Milestone Timeline`
- `Unscheduled Milestones`
- `Unmapped Items`
- `At-Risk Dates`
- `Baseline Changes` with archived/current revision paths and fingerprints
- `Blocked By Decisions`
- `Changed Since Last Roadmap`
- `Excluded Items`

Every formal timeline item carries `baseline_revision`, `planned_source`, `forecast_source`, `actual_source`, `status_source`, `status_rule_id`, `variance_days`, source references, and canonical confidence. Planned and forecast provenance remain distinct. Supplemental items retain their source and source type; items without a source are excluded.

Virtual program milestones remain formal timeline items. Preserve their baseline source, Program Status snapshot reference, canonical status and rule ID, complete source-reference lineage, scope kind, and baseline/status/source fingerprints. Never look for a program WDR or derive aggregation from milestone names.

Both outputs persist baseline identity/revision, the canonical program-status snapshot identity and overall status, per-source SHA-256 fingerprints, `audit_path`, `audit_status`, `report_confidence`, generator version, and a report-level risk marker. A valid `warning` or `blocked` audit may produce a triage roadmap, but the run status and Markdown must label it risk-bearing; it must never read as a green timeline.

Dry-run JSON returns `would_write` paths plus the complete proposed roadmap and Markdown under `preview`.

## Guardrails

- Do not render Mermaid Gantt or HTML. The roadmap is a table and JSON contract only.
- Do not change WDRs, decisions, action ledgers, checkpoint candidates, L0 files, readiness files, or risk/dependency views.
- Create readiness or L0 gate milestones only from a non-placeholder domain row, never from template prose or a `TBD` row.
- Route missing baseline facts or revision history to `adp-plan-baseline`, missing WDR actuals to `adp-status-sync`, missing or stale canonical status to `adp-program-status`, state quality to `adp-state-audit`, open business decisions to `adp-risk-dependency-change-review`, and readiness gate gaps to `adp-acceptance-readiness-review`.
