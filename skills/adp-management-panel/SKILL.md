---
name: adp-management-panel
description: Composes and archives converged ADP panels. Use when the user says "diagnose ADP panel publication", "inspect ADP panel generation", or "archive ADP panel"; use adp-panel-refresh for end-to-end updates.
---

# ADP Management Panel

## Overview

Compose audited ADP projections into a self-contained, `file://` management panel. This is the low-level compose/publish/inspect/archive boundary; `adp-panel-refresh` owns source-to-panel orchestration and freshness-gated open.

## Resolution rules

- Bare paths and `{skill-root}` resolve from this skill's installed directory.
- `{project-root}` resolves to the project working directory.
- `{memory-root}` defaults to `{project-root}/_bmad-output/adp/memory`.

## On Activation

Resolve the project and memory roots and route only the requested low-level `refresh`, `inspect`, or `archive` operation. Load `references/panel-model-contract-v1.md` only when changing or diagnosing model identity, publication, redaction, or recovery; otherwise rely on the script contract. Route source-to-panel convergence and freshness-gated open requests to `adp-panel-refresh`.

## Operations

Use `uv run` by default; when `uv` is unavailable, replace it with an available Python >=3.10 interpreter.

- Refresh: `uv run {skill-root}/scripts/management_panel.py {project-root} refresh --memory-root {memory-root} --selection-policy <path> [--input-bundle <path>] [--locale <locale>] [--default-view project-lead|fde-morning|business-biweekly] [--max-age-days <n>] [--generated-at <RFC3339>]`
- Inspect: `uv run {skill-root}/scripts/management_panel.py {project-root} inspect --memory-root {memory-root} [--expected-panel-id <id>]`
- Archive: `uv run {skill-root}/scripts/management_panel.py {project-root} archive --memory-root {memory-root} --selection-policy <path> --distribution-profile internal-full|shareable-summary`

Use only audited canonical inputs and an explicit selection policy supplied by the owning workflow or user; never choose business scope, visibility, reporting history, or recovery data. Treat the script's audit and validation result as authoritative. Do not bypass a blocked result or modify immutable artifacts.

## Headless Result

Return the JSON emitted by `management_panel.py` unchanged.
