---
name: adp-meeting-sync
description: Closes ADP meeting sync loops. Use when the user says "adp-meeting-sync" or "sync meeting".
---

# adp-meeting-sync

## Overview

Close meeting notes, offline updates, and stakeholder conversations into ADP project memory. Act as a delivery-state facilitator: preserve source evidence, classify each item, and route it to the smallest durable destination. FDE owners and downstream workflows need every item traceable to a daily log, decision, action, WDR update, Business Decision Packet, explicit no-op, or named gap.

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `scripts/sync_meeting.py`) resolve from this skill's installed directory.
- `{project-root}` -> the project working directory.
- When executing skill-owned scripts in a shell, use `{skill-root}/scripts/...`. Do not rely on the shell working directory resolving `scripts/...`, because commands usually run from `{project-root}`.
- Prefer `uv run`; if `uv` is unavailable, run the same script and arguments with an available Python 3.10+ interpreter. If neither runtime works, preserve the plan and return the environment gap instead of writing manually.

## Configuration and Language

Resolve the target `{project-root}`, then use the installed `adp-plan-baseline/scripts/adp_effective_config.py` result for values, languages, fallbacks, and warnings. Use `communication_language` for conversation and `document_output_language` for artifacts; `--language` overrides one writer run. Localize system copy only, never source facts, canonical enums, fact-layer fields, or lineage.

## On Activation

Resolve customization with `uv run {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root} --key workflow`. On failure, merge `{skill-root}/customize.toml`, `{project-root}/_bmad/custom/adp-meeting-sync.toml`, then `{project-root}/_bmad/custom/adp-meeting-sync.user.toml` by the base file's documented rules. Apply `{workflow.meeting_note_template}` and `{workflow.business_decision_packet_template}` to writer commands, run any `{workflow.activation_steps_prepend}` / `{workflow.activation_steps_append}` entries at their named moments, and load `{workflow.persistent_facts}` as standing context when those files exist.

Use `{project-root}/_bmad-output/adp/memory` as the default ADP memory root. If it is missing, tell the user to run `adp-project-kickoff`; still allow sync when the user provides `--memory-root`.

Load these schema files when present, because they define local terminology and valid destinations:

- `{project-root}/_bmad-output/adp/memory/schemas/meeting-sync.md`
- `{project-root}/_bmad-output/adp/memory/schemas/decision-taxonomy.md`
- `{project-root}/_bmad-output/adp/memory/schemas/workstream-delivery-record.md`
- `{project-root}/_bmad-output/adp/memory/schemas/status-taxonomy.md`

Load member hints when present from the project charter, workstream records, registration intake, and team/member/roster files. Speech recognition corrupts names; correct obvious names against project members and DingTalk metadata. If uncertain, keep the transcript label and mark it as a gap.

## Intake

Use raw meeting evidence as the source of truth: transcript text, chat excerpts, offline notes, or a file path containing that raw content. Third-party AI summaries, including DingTalk AI Minutes summaries, may help identify the meeting and display candidate metadata, but they are not reliable enough for ADP classification because they may omit project context. Do not build the sync plan from a summary alone; ask for raw content when raw evidence is missing.

For an `adp-meeting-pack` readout, pass its distillate path to the writer; the model supplies actual `started_at` / `ended_at` and meeting content, while the writer owns lineage extraction and verification.

When raw content is absent and DingTalk access is available, load `references/dingtalk-intake.md` and follow its candidate, exact-fetch, persistence, and source-label contract. Otherwise continue from the supplied raw evidence.

## Classify

Treat the raw meeting notes as source evidence, not as the final artifact. Separate each item into exactly one classification:

- `fact`: project state or context that belongs in the daily log and may update a WDR.
- `decision`: an FDE internal decision, scope change, risk acceptance, or other confirmed project decision.
- `action`: a follow-up with an owner and due date or trigger.
- `wdr_update`: a typed current-field update for affected Workstream Delivery Records; meeting history remains append-only, while status, phase, progress, blockers, risks, dependencies, change notes, and action refresh travel through status-sync intents.
- `business_decision_needed`: a question FDEs cannot decide alone; it needs a Business Decision Packet and an accountable confirmer.
- `no_op`: a discussed item that should not change project memory; the rationale is mandatory.

If an item is ambiguous, choose the safest classification that exposes the gap. A pending business answer is not a no-op. A decision without an accountable confirmer is open, not confirmed. A WDR update against an unknown workstream becomes an unresolved gap and should prompt `adp-workstream-register` or an id correction.

Treat meeting actions as ledger candidates only when accountable owner, workstream route, due trigger, and observable closure criteria are specific. Put unresolved details into the schema's explicit gap fields; use the dry-run `action_quality_audit` to decide what may enter status-sync.

For confirmed milestone status/forecast/actual, attach `milestones` with exact ID, one workstream, canonical status, evidence, and baseline revision. Only complete entries reach status-sync; gaps never create baseline milestones.

## Sync Plan

Before writing files, produce a compact JSON plan and inspect it for closure. Load `references/sync-plan-schema.md` whenever drafting or validating the plan. The script executes the plan; it does not infer business meaning.

Use one stable `meeting_instance_id`; the writer generates it when omitted. Same ID and plan resumes or no-ops; same ID with a changed plan fingerprint conflicts.

When a meeting used an archived panel, put its immutable `panel_id`, memory-relative HTML archive path, and `internal-full|shareable-summary` profile in `meeting.panel_archive`. The writer verifies the embedded manifest before any durable write. Dry-run, validation failure, conflict, applying receipts, and cursor conflict never claim an official panel association; the final applied receipt alone records `official_panel_archive`.

## Args

Headless callers provide `{project-root}`, optional `--memory-root`, either exact `--task-uuid` or `--raw-evidence <path>`, optional prebuilt `--plan <plan.json>`, and one mode: `--dry-run` or `--execute`. DingTalk candidate discovery also accepts `--query <text>`, `--start <time>`, and `--end <time>` as optional provider search hints; they do not replace exact-source confirmation. Skip candidate confirmation only for an exact source. When no plan is supplied, save the drafted plan, initialize `.memlog.md` beside it through `{project-root}/_bmad/scripts/memlog.py`, and append only material `assumption` and `decision` entries as they occur. Return machine-readable plan, memlog, dry-run/report paths, touched paths, gaps, and next actions; supplied-plan runs create no memlog.

## Write

Save the plan, then validate with a mandatory dry run before durable writes:

For meeting-pack input, add `--meeting-pack-distillate <distillate.json>` to both writer calls; do not transcribe its lineage into the plan.

```bash
uv run "{skill-root}/scripts/sync_meeting.py" "{project-root}" --plan <plan.json> --memory-root <memory-root> --meeting-note-template "{workflow.meeting_note_template}" --business-decision-packet-template "{workflow.business_decision_packet_template}" --dry-run -o <dry-run-report.json>
```

Review the dry-run report's touched paths, unresolved gaps, and `action_quality_audit`; only its ledger-ready actions may enter status-sync. Execute the same command without `--dry-run` only after user confirmation, or in headless mode only when the caller supplied `--execute`:

```bash
uv run "{skill-root}/scripts/sync_meeting.py" "{project-root}" --plan <plan.json> --memory-root <memory-root> --meeting-note-template "{workflow.meeting_note_template}" --business-decision-packet-template "{workflow.business_decision_packet_template}" -o <execute-report.json>
```

Writes use deterministic destinations and append markers. `meetings/receipts/` makes interruption resumable; only a fully applied receipt advances `meetings/cursors/<scenario>.json`. Dry-run and pack generation never do.

Meeting actions are not written directly to the action ledger. New actions carry deterministic IDs and `operation: create`; existing actions carry exact IDs, `operation: patch`, expected revision, and field presence. Current-field mutations and action commands are durably listed in `state/status-intent-outbox.json`. After sync, run the generated intake through status-sync:

```bash
adp-status-sync update "{project-root}" --memory-root "<same-memory-root>" --updates-file "<generated-intake-file>"
```

If the runner requires direct script execution, resolve the installed `adp-status-sync` skill root from the runner first; do not invent an unresolved placeholder.

Carry the identical resolved project and memory roots through the handoff. Do not refresh the Panel while the outbox has pending intent IDs. A successful status-sync apply marks only the consumed intents complete and returns `adp-panel-refresh plan <project-root> --memory-root <same-memory-root>` when source facts changed.

If the writer cannot run, fail closed: preserve the saved plan and any diagnostic report, identify the missing dependency, and resume with the same plan after the writer is restored. Do not perform manual durable writes.

## Finish

After the writer returns, load `references/meeting-sync-finish.md` to report closure, enforce the project-memory boundary, and run the terminal hook.
