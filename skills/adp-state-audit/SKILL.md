---
name: adp-state-audit
description: Audits ADP project state quality. Use when the user says "adp-state-audit" or "audit ADP state".
---

# ADP State Audit

## Overview

This workflow provides two AI Delivery PMO quality gates: an immutable input audit before generation and a separate artifact validation after generation. Act as a delivery-state quality reviewer: reuse deterministic ADP contracts, separate finding severity from execution disposition, and leave downstream workflows with traceable JSON and Markdown evidence.

The consumer is `adp-program-status`, `adp-agent-program-lead`, `adp-meeting-pack`, `adp-roadmap-sync`, and project leads. They need to know whether inputs are safe to compute from and whether emitted artifacts preserve freshness, lineage, and rendering contracts.

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `scripts/audit_state.py`) resolve from this skill's installed directory.
- `{project-root}` -> the project working directory.
- When executing skill-owned scripts in a shell, use `{skill-root}/scripts/...`. Do not rely on the shell working directory resolving `scripts/...`, because commands usually run from `{project-root}`.

## On Activation

Resolve `{workflow.*}` with `uv run {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root} --key workflow`. If the resolver is unavailable, read and merge `customize.toml`, `{project-root}/_bmad/custom/adp-state-audit.toml`, and `{project-root}/_bmad/custom/adp-state-audit.user.toml` in order: the last scalar wins, tables deep-merge, table-array entries replace or append by `code`/`id`, and other arrays append. Load `{workflow.persistent_facts}` as standing context and execute `{workflow.activation_steps_prepend}` before applying the state boundary. Run the audit with user-supplied scope flags, `{workflow.audit_output_path}` as the default artifact destination, and `{workflow.run_folder_pattern}` when non-empty; execute `{workflow.activation_steps_append}` immediately before the audit call.

Before user-facing output, resolve the shared ADP effective config through the installed `adp-plan-baseline` `scripts/adp_effective_config.py`. Conversation and status text follow `communication_language`; audit Markdown follows `document_output_language`. Surface resolver warnings and explicit English fallback. Language switching localizes system copy only: JSON keys, canonical enums, paths, lineage, and source facts remain unchanged.

If the script or ADP prepass cannot run, or returns `blocked`/`error`, stop with the blocked/error Output Contract. Manual inspection is allowed only as non-gating triage and cannot satisfy the audit contract. When the audit reaches a terminal complete, blocked, or error state, execute `{workflow.on_complete}` if non-empty.

## State Boundary

Use `{project-root}/_bmad-output/adp/memory` as the default ADP memory root unless the user passes `--memory-root`. If the memory root is missing, tell the user to run `adp-project-kickoff`; do not create project state from this workflow.

Facts come from Workstream Delivery Records, `actions/action-ledger.md`, decisions, business decision packets, daily logs, meeting archives, L0 summaries, and the prepass JSON. Derived views under `views/` are never promoted to source of truth. A stale derived view creates a refresh recommendation, not a blocking contradiction against durable source records.

## Input Audit

Run the deterministic audit:

```bash
uv run "{skill-root}/scripts/audit_state.py" "{project-root}" --scenario global --output-dir "{workflow.audit_output_path}" --execution-mode uv
```

This remains the default `--phase input`. It emits a stable `input_audit_id`; repeated identical inputs reuse the same immutable audit rather than overwriting history.

The audit is read-only: create audit artifacts and recommend owning workflows, but never edit ADP state. If the `uv` executable is unavailable, use an available Python 3.10+ interpreter with the same script and arguments, replacing `--execution-mode uv` with `--execution-mode python-fallback`. A blocked or failed audit is authoritative and must not be manually completed.

Use optional flags only when the user gives the scope:

- `--scenario global|fde-morning|business-biweekly|weekly-report|project-lead|roadmap|management-panel` to tune the prepass capability and output name; management-panel routes to its scenario contract below.
- `--workstream <id>` to limit the WDR scan; repeat as needed.
- `--memory-root <path>` when ADP memory is not at the default path.
- `--prepass-json <path>` to audit an already captured prepass result.
- `--as-of YYYY-MM-DD` and `--max-age-days <n>` for reproducible freshness checks.
- `--output-dir <path>` for audit artifacts; default is `{workflow.audit_output_path}`.
- `--run-folder-pattern <pattern>` when `{workflow.run_folder_pattern}` is non-empty.
- `--headless` for a non-interactive caller; it does not change the JSON contract or create runtime state.

## Artifact Validation

After a generator writes an artifact, validate the exact files against their sealed input audit:

```bash
uv run "{skill-root}/scripts/audit_state.py" "{project-root}" --phase artifact --input-audit-json <input-audit.json> --artifact <generated-file> --output-dir "{workflow.audit_output_path}"
```

Repeat `--artifact` for every file in the same generation transaction. When `{workflow.run_folder_pattern}` is non-empty, append `--run-folder-pattern "{workflow.run_folder_pattern}"`. Treat the immutable validation result as authoritative and never modify snapshots or views.

## Scenario Contracts

Load `references/scenario-contracts.md` only when the audit encounters `intake/status-sync`, validates `views/flow-graph.json`, or gates `adp-management-panel`. It owns those branch contracts and the panel command forms.

## Findings

Treat `audit_state.py` output as the canonical finding set. `warning_findings` is the sole warning aggregation: its unique `finding_id` values drive `counts.warning_findings` and the Markdown warning table. The category groups remain compatibility projections and must not be re-counted or re-rendered. Report severity and `execution_disposition` independently: only disposition `blocked` prevents generation or publication; `degraded` requires lower confidence and a visibly risk-bearing readout. Baseline missing/invalid, unverified migration evidence, and unmapped actuals block; overdue missing actuals, stale artifacts, and explicit locale fallback degrade. Do not infer gaps from prose similarity.

## Output Contract

Interactive use reports phase, audit status, execution disposition, output paths, findings, confidence, language fallbacks, config warnings, and recovery workflows in the resolved communication language. Headless use passes `--headless` and returns the script result JSON unchanged. Never invent output paths on blocked/error results. Keep artifact validation separate from generated artifacts, and route follow-up only from returned recommendations.
