---
name: adp-roadmap-sync
description: Renders source-backed ADP roadmap views. Use when the user says "render ADP roadmap", "sync ADP roadmap", or "dry-run ADP roadmap".
---

# ADP Roadmap Sync

## Overview

This workflow renders a source-backed AI Delivery PMO roadmap from durable ADP state. Act as a delivery-state curator: preserve the difference between scheduled milestones, unscheduled but source-backed milestones, open decision blockers, and ordinary follow-up actions. The output is `views/roadmap.json` for downstream workflows and `views/roadmap.md` for project leads and meeting packs.

The consumer needs a timeline they can trust in business review. Preserve only source-declared confidence; when a date, owner, confidence, or milestone source is missing, show `TBD` or exclude the item with a reason. Never infer schedule from project feel, meeting phrasing, or dependency graphs.

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

The renderer owns the quality gate. Without `--audit`, it runs sibling `adp-state-audit --scenario roadmap`, forwarding the selected memory root, workstreams, date, and prepass. With `--audit`, it requires the complete audit/prepass schema, exact workstream/date scope, a source inventory whose file fingerprints and missing paths match the renderer inputs, and identity equivalence when `--prepass-json` is also supplied. Missing, malformed, stale, changed, or incompatible audit input blocks rendering.

Scoped runs default to `views/roadmaps/<normalized-scope>/`; `--output-dir` overrides the destination.

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

Sources allowed into the roadmap:

- WDR `### Roadmap` tables with an explicit `Source`.
- confirmed or applied `adp-bmm-checkpoint-sync` candidate JSON.
- closed decision-log rows and open business decision packets.
- readiness, cutover, and L0 gate views as gate context.

`actions/action-ledger.md` is read only to explain excluded follow-up items. A normal action `Due / Trigger` is not a milestone date and remains excluded with a reason. Derived views under `views/` are context and gates, not fact sources that can override WDRs, decisions, checkpoint candidates, or L0 summaries.

Only explicit `open` decisions block; `accepted`, `closed`, `done`, `cancelled`, `rejected`, and `superseded` are terminal and never block. L0 status maps exactly as `open|planned -> planned`, `at-risk -> at-risk`, `blocked -> blocked`, and `closed|done -> done`; other or missing states are excluded. Free-prose blocker/dependency fields remain excluded context.

## Output Contract

The Markdown and JSON contain:

- `Source Inventory`
- `Milestone Timeline`
- `Unscheduled Milestones`
- `At-Risk Dates`
- `Blocked By Decisions`
- `Changed Since Last Roadmap`
- `Excluded Items`

Every roadmap item must carry `source`, `source_type`, and `confidence`. Items without source are excluded. Items with source but no valid planned, forecast, or actual date go to `Unscheduled Milestones`.

Both outputs persist `audit_path`, `audit_status`, `report_confidence`, and a report-level risk marker. A valid `warning` or `blocked` audit may produce a triage roadmap, but the run status and Markdown must label it risk-bearing; it must never read as a green timeline.

Dry-run JSON returns `would_write` paths plus the complete proposed roadmap and Markdown under `preview`.

## Guardrails

- Do not render Mermaid Gantt or HTML. v1 is a table and JSON contract only.
- Do not change WDRs, decisions, action ledgers, checkpoint candidates, L0 files, readiness files, or risk/dependency views.
- Create readiness or L0 gate milestones only from a non-placeholder domain row, never from template prose or a `TBD` row.
- Route missing WDR roadmap facts to `adp-status-sync`, missing or stale state quality to `adp-state-audit`, open business decisions to `adp-risk-dependency-change-review`, and readiness gate gaps to `adp-acceptance-readiness-review`.
