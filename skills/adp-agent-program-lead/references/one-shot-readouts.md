# One-Shot Program Lead Readouts

This file is the standalone execution contract for every Program Lead readout.

## Resolve Runtime Context

- `{skill-root}` is the installed `adp-agent-program-lead` directory. Resolve every skill script from it, never from the shell working directory.
- `{project-root}` is an explicit target path. Without one, set it to the current directory only when `{project-root}/_bmad/adp/config.yaml` or `{project-root}/_bmad-output/adp/memory` exists; otherwise ask for the project root before reading state.
- Resolve `[workflow]` customization, then apply activation hooks and load `{workflow.persistent_facts}` as standing context:

```bash
uv run "{project-root}/_bmad/scripts/resolve_customization.py" --skill "{skill-root}" --key workflow
```

If the resolver is unavailable, read and merge `{skill-root}/customize.toml`, `{project-root}/_bmad/custom/adp-agent-program-lead.toml`, and `{project-root}/_bmad/custom/adp-agent-program-lead.user.toml` in order: the last scalar wins, tables deep-merge, table-array entries replace or append by `code`/`id`, and other arrays append. Read only `[workflow]`; `[agent]` remains an install-time identity contract.

Resolve configuration before either consumer:

```bash
uv run "{skill-root}/scripts/adp-state-prepass.py" "{project-root}" --activation
```

Use its `resolved.communication_language`, `resolved.document_output_language`, config source paths, `configuration_errors`, and absolute `resolved.adp_state_root`. An explicit user `--memory-root` belongs on this activation command and takes precedence. Pass the resulting root as `--memory-root "{adp-state-root}"` to every later command.

For every Python command below, prefer `uv run`. If `uv` is unavailable, run the same script and arguments with an available Python 3.10+ interpreter (`python3` or `python`). If neither runtime works, return blocked JSON with an environment reason and the concrete action to install `uv` or Python 3.10+; do not report runtime failure as missing or invalid ADP state.

## Select A Consumer

The prompt interprets the user's request and chooses one exact ID. The scripts only validate and execute that choice.

For overall, period review, recovery routing, or meeting preparation, run the canonical consumer first:

```bash
uv run "{skill-root}/scripts/consume_program_status.py" "{project-root}" --intent <overall|period-review|recovery-routing> --memory-root "{adp-state-root}"
uv run "{skill-root}/scripts/consume_program_status.py" "{project-root}" --intent meeting-preparation --scenario <fde-morning|business-biweekly> --memory-root "{adp-state-root}"
```

An explicit end-to-end refresh or resume request routes directly to `adp-panel-refresh` after runtime-root resolution. Do not gate repair of stale or missing projections on an existing canonical Panel; the refresh orchestrator owns `policy -> detect -> plan -> apply -> inspect` and returns the durable resume plan when interrupted.

Panel readiness, open, and archive requests consume the existing canonical Panel before routing. The consumer never renders HTML or writes browser state:

```bash
uv run "{skill-root}/scripts/consume_program_status.py" "{project-root}" --intent panel-readiness --panel-view <project-lead|fde-morning|business-biweekly> --memory-root "{adp-state-root}"
uv run "{skill-root}/scripts/consume_program_status.py" "{project-root}" --intent panel-open --panel-view <view> --memory-root "{adp-state-root}"
uv run "{skill-root}/scripts/consume_program_status.py" "{project-root}" --intent panel-archive --panel-view <view> --distribution-profile <internal-full|shareable-summary> --memory-root "{adp-state-root}"
```

Open/readiness verifies the embedded manifest/model, immutable bundle, and program-status snapshot identity before explanation. FDE explanation includes only pack/window/readiness/lifecycle, canonical comparable delta, related forecast milestones, blockers, commitments, owner-selected flow state, and scoped counts; business explanation includes the canonical next-period outlook, decisions/readiness, and budgeted flow spine. Archive routing marks official association pending until `adp-meeting-sync` writes an applied receipt containing that panel ID.

A blocked canonical result is terminal for project-level judgment: return its `reason` and `recommended_workflows` without substituting WDR status. The consumer lineage-validates the requested management Markdown against canonical snapshot, audit, baseline, source fingerprints, progress/flow identity, locale, generator, and render profile; a present but stale or mismatched file remains blocked. Use canonical fields over every conflicting detail field.

The legacy `scripts/render_program_views.py` entry point accepts only its read-only core options. `ADP-PL-LEGACY-RENDERER-MIGRATION-REQUIRED` means an old renderer-only option was supplied: run `adp-program-status` to regenerate canonical views, then rerun this consumer.

For operational detail, choose exactly one capability ID and optionally repeat `--workstream`:

```bash
uv run "{skill-root}/scripts/adp-state-prepass.py" "{project-root}" --capability <capability-id> --memory-root "{adp-state-root}" --workstream <workstream-id>
```

Valid IDs and their intent are:

| Capability ID | Use for |
| --- | --- |
| `global-project-readout` | Full detail supporting a canonical project readout |
| `fde-action-list` | Owner and workstream actions |
| `acceptance-readiness-view` | Acceptance and cutover evidence |
| `risk-dependency-synthesis` | Risks, dependencies, blockers, and changes |
| `weekly-report-consumption` | Detail behind the canonical weekly report |
| `gap-driven-coaching` | Missing WDR state and closing content |
| `l0-impact-sweep` | L0 references, impacts, and evidence rules |
| `decision-closure-review` | Meeting, daily, decision, packet, action, and WDR closure |

If the detail script fails despite an available runtime, inspect only the files mapped to the chosen capability, state the fallback, and make no project-level judgment from that inventory.

## Return Contract

Interactive Markdown names canonical status and confidence together when applicable, lineage and period change, detail sources, neutral observations, actions with owners and triggers, and the next owning workflow. Separate source fact, Program Lead inference, and missing state.

When JSON is requested, return:

```json
{
  "status": "complete",
  "canonical_status": {},
  "period_review": {},
  "sources_read": [],
  "gaps": [],
  "actions": [{"owner": "", "workstream": "", "action": "", "source": "", "due_or_trigger": ""}],
  "recommended_workflows": [],
  "reason": ""
}
```

For headless or automation language, return JSON only. On a canonical, state, scope, configuration, or environment block, use `"status": "blocked"`, preserve only safely validated canonical fields, give a one-line `reason`, and return the owning recovery workflow or setup action.
