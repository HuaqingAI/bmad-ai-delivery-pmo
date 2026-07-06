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
- When executing skill-owned scripts in a shell, use `{skill-root}/scripts/...`. Do not rely on the shell working directory resolving `scripts/...`, because commands usually run from `{project-root}`.

## Configuration and Language

Resolve the target `{project-root}` before any user-facing output. This is the project where ADP is installed or being run, not the module build repository.

Load BMad configuration from the target project in this order:

1. `{project-root}/_bmad/adp/config.yaml` (primary ADP install-time config)
2. `{project-root}/_bmad/config.user.yaml` and `{project-root}/_bmad/config.yaml` when present
3. `{project-root}/_bmad/core/config.yaml`
4. `{project-root}/_bmad/bmm/config.yaml` or `{project-root}/_bmad/bmb/config.yaml` as compatibility fallbacks

Use `communication_language` for all conversation and status output. Use `document_output_language` for generated project documents and report text. If no config file exists, say that explicitly and fall back to English.

## On Activation

Resolve customization with `uv run {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root} --key workflow`; if unavailable, read `customize.toml` directly. Apply `{workflow.meeting_note_template}` and `{workflow.business_decision_packet_template}` to writer commands, run any `{workflow.activation_steps_prepend}` / `{workflow.activation_steps_append}` entries at their named moments, and load `{workflow.persistent_facts}` as standing context when those files exist.

Use `{project-root}/_bmad-output/adp/memory` as the default ADP memory root. If it is missing, tell the user to run `adp-project-kickoff`; still allow sync when the user provides `--memory-root`.

Load these schema files when present, because they define local terminology and valid destinations:

- `{project-root}/_bmad-output/adp/memory/schemas/meeting-sync.md`
- `{project-root}/_bmad-output/adp/memory/schemas/decision-taxonomy.md`
- `{project-root}/_bmad-output/adp/memory/schemas/workstream-delivery-record.md`
- `{project-root}/_bmad-output/adp/memory/schemas/status-taxonomy.md`

Load member hints when present from the project charter, workstream records, registration intake, and team/member/roster files. Speech recognition corrupts names; correct obvious names against project members and DingTalk metadata. If uncertain, keep the transcript label and mark it as a gap.

## Intake

Use raw meeting evidence as the source of truth: transcript text, chat excerpts, offline notes, or a file path containing that raw content. Third-party AI summaries, including DingTalk AI Minutes summaries, may help identify the meeting and display candidate metadata, but they are not reliable enough for ADP classification because they may omit project context. Do not build the sync plan from a summary alone; ask for raw content when raw evidence is missing.

When the user has not provided raw content and DingTalk access is available, run the intake pre-pass instead of hand-listing minutes:

```bash
uv run "{skill-root}/scripts/dingtalk_intake.py" "{project-root}" --memory-root <memory-root> -o <intake.json>
```

Add `--query`, `--start`, or `--end` only from user-supplied project, workstream, date, or topic hints. The pre-pass lists 50 candidates by default, expands date-only filters to full local-day timestamps, and falls back to unfiltered listing plus local date filtering when DingTalk returns an empty server-filtered list. It marks processed meetings only by exact `taskUuid` or AI Minutes URL under ADP memory, emits same-date same-title memory hits as `possible_matches`, and emits processed/unprocessed reasons. Show likely unprocessed candidates and ask for confirmation unless the run supplied an exact `--task-uuid`.

For an exact meeting, rerun the pre-pass with `--task-uuid <id>`. It fetches info and paginated transcription, saves the transcript under ADP memory, and reports transcript completeness. If the pre-pass returns no complete raw transcript, ask for raw meeting content rather than classifying from a summary. Record DingTalk sources in the plan source field with the task id and evidence type, such as `DingTalk AI Minutes taskUuid=<id>; evidence=transcription`.

## Classify

Treat the raw meeting notes as source evidence, not as the final artifact. Separate each item into exactly one classification:

- `fact`: project state or context that belongs in the daily log and may update a WDR.
- `decision`: an FDE internal decision, scope change, risk acceptance, or other confirmed project decision.
- `action`: a follow-up with an owner and due date or trigger.
- `wdr_update`: a direct project-level update to affected Workstream Delivery Records.
- `business_decision_needed`: a question FDEs cannot decide alone; it needs a Business Decision Packet and an accountable confirmer.
- `no_op`: a discussed item that should not change project memory; the rationale is mandatory.

If an item is ambiguous, choose the safest classification that exposes the gap. A pending business answer is not a no-op. A decision without an accountable confirmer is open, not confirmed. A WDR update against an unknown workstream becomes an unresolved gap and should prompt `adp-workstream-register` or an id correction.

An action is not closed when owner or due trigger is generic, missing, or still a raw speaker label. Use the best corrected project member name; otherwise keep the label and write explicit gap fields in the plan, such as `owner_gap`, `confirmer_gap`, `speaker_label_gap`, or `participant_gaps`; the writer only validates exact missing placeholders.

## Sync Plan

Before writing files, produce a compact JSON plan and inspect it for closure. Load `references/sync-plan-schema.md` whenever drafting or validating the plan. The script executes the plan; it does not infer business meaning.

Keep unknown fields as `TBD` only when the gap is real and should be visible. Do not omit `id`, `classification`, or `text`; use explicit gap fields such as `owner_gap`, `confirmer_gap`, `speaker_label_gap`, `participant_gaps`, or `gap`.

## Args

Headless callers provide `{project-root}`, optional `--memory-root`, either exact `--task-uuid` or `--raw-evidence <path>`, optional prebuilt `--plan <plan.json>`, and one mode: `--dry-run` or `--execute`. Skip candidate confirmation only when an exact source is supplied. Return machine-readable status with touched paths, unresolved gaps, and next actions; in interactive mode, explain the same fields in prose.

## Write

Save the plan, then validate with a mandatory dry run before durable writes:

```bash
uv run "{skill-root}/scripts/sync_meeting.py" "{project-root}" --plan <plan.json> --memory-root <memory-root> --meeting-note-template "{workflow.meeting_note_template}" --business-decision-packet-template "{workflow.business_decision_packet_template}" --dry-run -o <dry-run-report.json>
```

Review the dry-run report for touched paths and unresolved gaps. Execute the same command without `--dry-run` only after user confirmation, or in headless mode only when the caller supplied `--execute`:

```bash
uv run "{skill-root}/scripts/sync_meeting.py" "{project-root}" --plan <plan.json> --memory-root <memory-root> --meeting-note-template "{workflow.meeting_note_template}" --business-decision-packet-template "{workflow.business_decision_packet_template}" -o <execute-report.json>
```

The script writes a structured meeting archive, appends the daily log, appends decision indexes and workstream decision files where applicable, appends WDR meeting-sync updates when the workstream exists, creates Business Decision Packets for `business_decision_needed` items, and writes a status-sync intake file under `intake/status-sync/` when meeting items contain `classification: "action"`. Existing files are preserved; WDRs are appended, not replaced.

Meeting actions are not written directly to the action ledger. After sync, run the generated intake through status-sync:

```bash
adp-status-sync update "{project-root}" --updates-file "<generated-intake-file>"
```

If the runner requires direct script execution, resolve the installed `adp-status-sync` skill root from the runner first; do not invent an unresolved placeholder.

If the script cannot run, manually create the same outputs from `{workflow.meeting_note_template}` and `{workflow.business_decision_packet_template}`. Preserve existing user content and report any item that could not be closed.

## Output Contract

After syncing, report:

- the meeting archive path
- the durable raw evidence path, when raw evidence was available
- daily log, decision log, WDRs, workstream decision files, and Business Decision Packets touched
- generated status-sync intake files for meeting actions
- unresolved gaps, especially missing workstreams, missing owners, missing due triggers, unresolved speaker labels, missing confirmers, or no-op items without rationale
- next useful workflow: usually `adp-status-sync` for cadence updates, `adp-risk-dependency-change-review` for open risk/change/business decisions, or `adp-workstream-register` for unknown workstreams

Do not call a meeting closed because a note exists. It is closed when every item has a classification, destination, owner where needed, and either a durable write or a visible gap.

At the terminal stage after this report, run `{workflow.on_complete}` when non-empty.

## Guardrails

- ADP records project-level coordination state; do not copy full PRD, architecture, story, code, or validation detail out of BMM artifacts.
- Business decisions need an accountable business confirmer. FDE-only calls belong to FDE/internal decision records.
- Risk acceptance and scope change are decisions with consequences, not generic actions.
- Offline follow-ups and chat corrections count as meetings when they change project state.
- No-op is a traceable closure state, not a way to skip hard items.
