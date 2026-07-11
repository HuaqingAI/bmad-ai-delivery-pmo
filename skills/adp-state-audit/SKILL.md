---
name: adp-state-audit
description: Audits ADP project state quality. Use when the user says "adp-state-audit" or "audit ADP state".
---

# ADP State Audit

## Overview

This workflow audits AI Delivery PMO shared memory before derived reports, meeting packs, roadmap views, or Program Lead readouts consume it. Act as a delivery-state quality reviewer: reuse the deterministic ADP prepass, separate facts from missing state, and leave downstream workflows with an auditable JSON and Markdown quality gate.

The consumer is `adp-agent-program-lead`, `adp-meeting-pack`, `adp-roadmap-sync`, and project leads. They need to know whether current ADP state is fresh, complete, internally consistent, closed-loop, and safe to summarize without inventing certainty.

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `scripts/audit_state.py`) resolve from this skill's installed directory.
- `{project-root}` -> the project working directory.
- When executing skill-owned scripts in a shell, use `{skill-root}/scripts/...`. Do not rely on the shell working directory resolving `scripts/...`, because commands usually run from `{project-root}`.

## On Activation

Resolve `{workflow.*}` with `uv run {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root} --key workflow`, falling back to `customize.toml` if the resolver is unavailable. Load `{workflow.persistent_facts}` as standing context and execute `{workflow.activation_steps_prepend}` before applying the state boundary. Run the audit with user-supplied scope flags, `{workflow.audit_output_path}` as the default artifact destination, and `{workflow.run_folder_pattern}` when non-empty; execute `{workflow.activation_steps_append}` immediately before the audit call.

If the script or ADP prepass cannot run, or returns `blocked`/`error`, stop with the blocked/error Output Contract. Manual inspection is allowed only as non-gating triage and cannot satisfy the audit contract. When the audit reaches a terminal complete, blocked, or error state, execute `{workflow.on_complete}` if non-empty.

## State Boundary

Use `{project-root}/_bmad-output/adp/memory` as the default ADP memory root unless the user passes `--memory-root`. If the memory root is missing, tell the user to run `adp-project-kickoff`; do not create project state from this workflow.

Facts come from Workstream Delivery Records, `actions/action-ledger.md`, decisions, business decision packets, daily logs, meeting archives, L0 summaries, and the prepass JSON. Derived views under `views/` are never promoted to source of truth. A stale derived view creates a refresh recommendation, not a blocking contradiction against durable source records.

## Audit

Run the deterministic audit:

```bash
uv run "{skill-root}/scripts/audit_state.py" "{project-root}" --scenario global --output-dir "{workflow.audit_output_path}" --execution-mode uv
```

The audit is read-only: create audit artifacts and recommend owning workflows, but never edit ADP state. If the `uv` executable is unavailable, use an available Python 3.10+ interpreter with the same script and arguments, replacing `--execution-mode uv` with `--execution-mode python-fallback`. A blocked or failed audit is authoritative and must not be manually completed.

Use optional flags only when the user gives the scope:

- `--scenario global|fde-morning|business-biweekly|weekly-report|project-lead|roadmap` to tune the prepass capability and output name.
- `--workstream <id>` to limit the WDR scan; repeat as needed.
- `--memory-root <path>` when ADP memory is not at the default path.
- `--prepass-json <path>` to audit an already captured prepass result.
- `--as-of YYYY-MM-DD` and `--max-age-days <n>` for reproducible freshness checks.
- `--output-dir <path>` for audit artifacts; default is `{workflow.audit_output_path}`.
- `--run-folder-pattern <pattern>` when `{workflow.run_folder_pattern}` is non-empty.
- `--headless` for a non-interactive run that records effective parameters and fallback decisions in the returned memlog.

The script writes:

- `<audit-output>/<run-folder-if-configured>/<date>-<scenario>-audit.json`
- `<audit-output>/<run-folder-if-configured>/<date>-<scenario>-audit.md`

## Findings

Treat `audit_state.py` output as the canonical finding set. Report its categories, sources, and `gap_type` values as emitted; do not infer missing owners, due dates, readiness, roadmap milestones, overlap, or conflicts from prose or structural similarity.

## Output Contract

Interactive use reports the audit status, artifact paths, blocking gaps, consistency warnings, closure risks, and recommended next workflows. If blocking gaps or conflicts exist, downstream reports may still be generated, but must be labeled as risk-bearing readouts rather than green status.

Headless use passes `--headless` and returns the script result JSON fields callers need: `status`, `audit_status`, `scenario`, `outputs`, `counts`, `recommended_workflows`, and `memlog`. The memlog preserves resolved scope, effective audit parameters, customization-derived output routing, and fallback decisions. On `blocked` or `error`, return `status`, `scenario`, `outputs: {}`, `recommended_workflows`, `memlog`, and the `error` or `reason`; do not invent artifact paths.

Every returned `memlog` names an existing readable trail; if the requested path cannot be initialized, return the fallback trail path instead.

Audit JSON uses `audit_schema_version: 1` and exposes `safe_to_generate`, `safe_to_generate_green_report`, `report_confidence`, `source_inventory_items`, `blocking_gaps`, `warnings`, `duplicate_candidates`, `overlap_claims`, `conflicts`, `stale_items`, and non-gating `merge_review_evidence`. Every canonical finding carries `id`, `severity`, `kind`, `source_type`, `sources`, `workstreams`, `owner`, and `summary`. Keep the nested `findings` and `schema_version` aliases for existing ADP consumers.

Route follow-up from the script's `recommended_workflows` and per-finding recommendations; do not reconstruct routing when the audit is blocked or errors.
