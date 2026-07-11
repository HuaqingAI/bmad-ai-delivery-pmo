---
name: adp-meeting-pack
description: Renders ADP meeting view packs. Use when the user says "adp-meeting-pack", "ADP meeting pack", "FDE morning pack", or "business biweekly pack".
---

# ADP Meeting Pack

## Overview

This workflow renders source-traceable AI Delivery PMO meeting packs from ADP shared memory. Act as a delivery-state packager: run or consume the state audit gate, select the meeting scenario slice, and produce a Markdown pack that helps FDEs or business stakeholders run the meeting without treating the pack as durable state.

The consumers are FDE owners, project leads, business stakeholders, `adp-meeting-sync`, and `adp-status-sync`. They need agenda-ready boards with sources, owners, closure criteria, and explicit gaps so meeting outcomes can be written back to ADP memory after the call.

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `scripts/render_meeting_pack.py`) resolve from this skill's installed directory.
- `{project-root}` -> the project working directory.
- When executing skill-owned scripts in a shell, use `{skill-root}/scripts/...`. Do not rely on the shell working directory resolving `scripts/...`, because commands usually run from `{project-root}`.

## On Activation

First distinguish a render request from help, explanation, inspection, or another meeting workflow. Only render intent proceeds; answer or redirect every other intent without resolving a scenario, loading project facts or hooks, reading ADP memory, or writing packs.

For render intent, resolve `{meeting_scenario}` and retain any supplied `--workstream` scope. Infer `fde-morning` from "FDE morning pack" and `business-biweekly` from "business biweekly pack"; for an ambiguous interactive "adp-meeting-pack" or "ADP meeting pack" request, ask which audience the pack serves. A headless request without `--scenario` uses `fde-morning`.

Then resolve `{workflow.*}` with `uv run "{project-root}/_bmad/scripts/resolve_customization.py" --skill "{skill-root}" --key workflow`; if resolution fails, read `customize.toml` directly and use its defaults. Load `{workflow.persistent_facts}` as standing context, run `{workflow.activation_steps_prepend}` before applying the state boundary or render defaults, and run `{workflow.activation_steps_append}` immediately before the renderer call.

## State Boundary

Use `{project-root}/_bmad-output/adp/memory` as the default ADP memory root unless the user passes `--memory-root`. If the memory root is missing, tell the user to run `adp-project-kickoff`; do not create project state from this workflow.

Meeting packs are derived views under `{workflow.meeting_pack_output_path}/{workflow.run_folder_pattern}`. They are not a source of truth. Meeting outcomes must flow back through `adp-meeting-sync` and, for action-ledger changes, `adp-status-sync`.

## Render

Run the deterministic renderer:

```bash
uv run "{skill-root}/scripts/render_meeting_pack.py" "{project-root}" --scenario {meeting_scenario} --meeting-pack-output-path "{workflow.meeting_pack_output_path}" --run-folder-pattern "{workflow.run_folder_pattern}"
```

If `uv` is unavailable, retry the same script and arguments with an available Python 3.10+ interpreter. Block only when neither runtime can execute it; never reconstruct the pack or distillate manually.

Use optional flags only when the user gives the scope:

- `--scenario fde-morning|business-biweekly` to choose the meeting type.
- `--date YYYY-MM-DD` for reproducible output names; default is today.
- `--workstream <id>` to limit the pack; repeat as needed.
- `--memory-root <path>` when ADP memory is not at the default path.
- `--audit <path>` to consume an existing audit JSON.
- `--prepass-json <path>` to consume an existing prepass JSON.
- `--meeting-pack-output-path <path>` and `--run-folder-pattern <pattern>` for configured artifact destinations.
- `--output-dir <path>` for a one-run artifact destination override.
- `--replace` only after the user or headless caller explicitly authorizes replacing the planned Markdown/JSON pair.

The renderer writes:

- `{workflow.meeting_pack_output_path}/{workflow.run_folder_pattern}/<date>.md`
- `{workflow.meeting_pack_output_path}/{workflow.run_folder_pattern}/<date>.json`

It validates both destinations before ingestion or writes. On a collision, show the reported paths and ask an interactive user to authorize `--replace` or choose a unique `--output-dir`; headless runs remain blocked unless the caller already supplied one of those choices.

Treat the renderer as the sole authority for scenario-specific board selection and distillate contents. If it cannot run, reuse only a previously completed Markdown/JSON pair; otherwise report the exact failure and renderer command needed after recovery.

## Output Contract

In interactive mode, report the resolved scenario, Markdown/JSON/audit paths, audit status, sources-read count, excluded-action count, and recommended next workflows from the renderer result. On a blocked or error result, report its reason and recovery.

In headless mode, return only the renderer's stdout JSON unchanged, with no prose or code fence. If neither `uv` nor Python 3.10+ can execute it, return only:

```json
{"ok":false,"status":"blocked","scenario":"{meeting_scenario}","outputs":{"markdown":null,"distillate":null,"audit":null},"recommended_workflows":[],"reason":"No uv or Python 3.10+ runtime can execute render_meeting_pack.py","recovery":"Install uv or Python 3.10+ and rerun the renderer command"}
```

Pass the distillate's emitted lineage unchanged as `meeting.lineage` to `adp-meeting-sync`. After a successful result is handled, run `{workflow.on_complete}` if non-empty.
