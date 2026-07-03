---
name: adp-meeting-sync
description: Closes ADP meeting sync loops. Use when the user says "adp-meeting-sync" or "sync meeting".
---

# adp-meeting-sync

## Overview

This workflow turns meeting notes, offline updates, and stakeholder conversations into closed ADP project memory updates. Act as a delivery-state facilitator: classify each meeting item, preserve the source as a structured archive, and route the item to the smallest durable destination that keeps the project synchronized.

The consumers are FDE owners, the project lead, status-sync, risk/change review, readiness review, and the program lead agent. They need every meeting item to become a daily log entry, decision, action, Workstream Delivery Record update, Business Decision Packet, or explicit no-op with rationale. A meeting sync is incomplete when an item stays unclassified or cannot be traced to a destination or named gap.

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `scripts/sync_meeting.py`) resolve from this skill's installed directory.
- `{project-root}` -> the project working directory.
- `{skill-name}` -> the skill directory's basename.

## Configuration and Language

Resolve the target `{project-root}` before any user-facing output. This is the project where ADP is installed or being run, not the module build repository.

Load BMad configuration from the target project in this order:

1. `{project-root}/_bmad/adp/config.yaml` (primary ADP install-time config)
2. `{project-root}/_bmad/config.user.yaml` and `{project-root}/_bmad/config.yaml` when present
3. `{project-root}/_bmad/core/config.yaml`
4. `{project-root}/_bmad/bmm/config.yaml` or `{project-root}/_bmad/bmb/config.yaml` as compatibility fallbacks

Use `communication_language` for all conversation and status output. Use `document_output_language` for generated project documents and report text. If no config file exists, say that explicitly and fall back to English.

## On Activation

Use `{project-root}/_bmad-output/adp/memory` as the default ADP memory root. If it is missing, tell the user to run `adp-project-kickoff`; still allow sync when the user provides `--memory-root`.

Load these schema files when present, because they define local terminology and valid destinations:

- `{project-root}/_bmad-output/adp/memory/schemas/meeting-sync.md`
- `{project-root}/_bmad-output/adp/memory/schemas/decision-taxonomy.md`
- `{project-root}/_bmad-output/adp/memory/schemas/workstream-delivery-record.md`
- `{project-root}/_bmad-output/adp/memory/schemas/status-taxonomy.md`

## Intake

Use raw meeting evidence as the source of truth: transcript text, chat excerpts, offline notes, or a file path containing that raw content. Third-party AI summaries, including DingTalk AI Minutes summaries, may help identify the meeting and display candidate metadata, but they are not reliable enough for ADP classification because they may omit project context. Do not build the sync plan from a summary alone; ask for raw content when raw evidence is missing.

When the user has not provided raw content and DingTalk access is available through `/dws` or the `dws` CLI, attempt DingTalk intake before asking for pasted content:

- Use only `dws minutes` commands and always include `--format json`.
- Discover candidates with `dws minutes list all --max 10 --format json`; add `--query`, `--start`, or `--end` only when the user supplied useful project, workstream, date, or topic hints.
- Treat a candidate as already processed when its `taskUuid`, AI Minutes URL, or same-date same-title source already appears under the ADP memory root.
- Show only likely unprocessed candidates and ask the user to confirm the target meeting before fetching content.
- For the confirmed meeting, fetch `dws minutes get info --id <taskUuid> --format json` and `dws minutes get transcription --id <taskUuid> --format json`; paginate transcription until complete when a next token is returned.
- Fetch `summary` only as a navigation aid. If `transcription` is unavailable or incomplete, report the gap and ask the user for raw meeting content instead of classifying from the summary.

Record DingTalk sources in the plan source field with the task id and evidence type, such as `DingTalk AI Minutes taskUuid=<id>; evidence=transcription`.

## Classify

Treat the raw meeting notes as source evidence, not as the final artifact. Separate each item into exactly one classification:

- `fact`: project state or context that belongs in the daily log and may update a WDR.
- `decision`: an FDE internal decision, scope change, risk acceptance, or other confirmed project decision.
- `action`: a follow-up with an owner and due date or trigger.
- `wdr_update`: a direct project-level update to affected Workstream Delivery Records.
- `business_decision_needed`: a question FDEs cannot decide alone; it needs a Business Decision Packet and an accountable confirmer.
- `no_op`: a discussed item that should not change project memory; the rationale is mandatory.

If an item is ambiguous, choose the safest classification that exposes the gap. A pending business answer is not a no-op. A decision without an accountable confirmer is open, not confirmed. A WDR update against an unknown workstream becomes an unresolved gap and should prompt `adp-workstream-register` or an id correction.

## Sync Plan

Before writing files, produce a compact JSON plan and inspect it for closure. The script executes the plan; it does not infer business meaning.

Required shape:

```json
{
  "meeting": {
    "date": "YYYY-MM-DD",
    "type": "FDE internal sync",
    "title": "Short title",
    "source": "pasted notes, transcript, or file path",
    "participants": ["Name"],
    "summary": "One paragraph"
  },
  "items": [
    {
      "id": "M-001",
      "classification": "action",
      "text": "What was said or decided",
      "affected_workstreams": ["workstream-id"],
      "owner": "Name",
      "due": "date or trigger",
      "decision_type": "FDE internal decision",
      "confirmer": "Name",
      "status": "open",
      "wdr_update": "Project-level WDR text when applicable",
      "no_op_reason": "Required for no_op",
      "packet": {
        "background": "Why this needs business decision",
        "decision_needed": "Question to decide",
        "options": ["Option A", "Option B"],
        "recommendation": "Recommended answer, if any",
        "risks_tradeoffs": "Impact of the choice",
        "deadline": "date or trigger",
        "confirming_owner": "Business owner"
      }
    }
  ]
}
```

Keep unknown fields as `TBD` or omit them, but do not omit `id`, `classification`, or `text`.

## Write

Run the deterministic writer after the plan is ready:

```bash
uv run scripts/sync_meeting.py {project-root} --plan <plan.json>
```

Useful flags:

- `--memory-root <path>` for non-default ADP memory.
- `--dry-run` to preview target files without writing.
- `-o <path>` to write the JSON execution report.

The script writes a structured meeting archive, appends the daily log, appends decision indexes and workstream decision files where applicable, appends WDR meeting-sync updates when the workstream exists, and creates Business Decision Packets for `business_decision_needed` items. Existing files are preserved; WDRs are appended, not replaced.

If the script cannot run, manually create the same outputs from `assets/meeting-sync-templates/`. Preserve existing user content and report any item that could not be closed.

## Output Contract

After syncing, report:

- the meeting archive path
- daily log, decision log, WDRs, workstream decision files, and Business Decision Packets touched
- unresolved gaps, especially missing workstreams, missing owners, missing confirmers, or no-op items without rationale
- next useful workflow: usually `adp-status-sync` for cadence updates, `adp-risk-dependency-change-review` for open risk/change/business decisions, or `adp-workstream-register` for unknown workstreams

Do not call a meeting closed because a note exists. It is closed when every item has a classification, destination, owner where needed, and either a durable write or a visible gap.

## Guardrails

- ADP records project-level coordination state; do not copy full PRD, architecture, story, code, or validation detail out of BMM artifacts.
- Business decisions need an accountable business confirmer. FDE-only calls belong to FDE/internal decision records.
- Risk acceptance and scope change are decisions with consequences, not generic actions.
- Offline follow-ups and chat corrections count as meetings when they change project state.
- No-op is a traceable closure state, not a way to skip hard items.
