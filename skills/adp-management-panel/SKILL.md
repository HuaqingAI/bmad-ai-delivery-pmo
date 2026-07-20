---
name: adp-management-panel
description: Builds traceable offline ADP management panels. Use when user says "refresh ADP panel" or "archive management panel".
---

# ADP Management Panel

## Overview

Compose audited ADP projections into a self-contained, `file://` management panel. Act as a delivery-boundary steward: preserve upstream facts and fail closed rather than inventing business state.

## Resolution rules

- Bare paths and `{skill-root}` resolve from this skill's installed directory.
- `{project-root}` resolves to the project working directory.
- `{memory-root}` defaults to `{project-root}/_bmad-output/adp/memory`.

## Operations

Use `uv run` by default; when `uv` is unavailable, replace it with an available Python >=3.10 interpreter.

- Refresh: `uv run {skill-root}/scripts/management_panel.py {project-root} refresh --memory-root {memory-root} --selection-policy <path>`
- Inspect: `uv run {skill-root}/scripts/management_panel.py {project-root} inspect --memory-root {memory-root} [--expected-panel-id <id>]`
- Archive: `uv run {skill-root}/scripts/management_panel.py {project-root} archive --memory-root {memory-root} --selection-policy <path> --distribution-profile internal-full|shareable-summary`

Use only audited canonical inputs and an explicit selection policy supplied by the owning workflow or user; never choose business scope, visibility, reporting history, or recovery data. Treat the script's audit and validation result as authoritative. Do not bypass a blocked result or modify immutable artifacts.

Load `references/panel-model-contract-v1.md` only when changing or diagnosing model, identity, publication, redaction, or recovery behavior.

## Headless Result

Return the JSON emitted by `management_panel.py` unchanged.
