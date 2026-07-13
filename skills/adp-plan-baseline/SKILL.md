---
name: adp-plan-baseline
description: Governs auditable ADP project plan baselines. Use when the user says "adp-plan-baseline" or "create ADP project baseline".
---

# ADP Plan Baseline

## Overview

This workflow turns confirmed project targets, gates, workstream milestones, dependencies, and critical-path decisions into the sole approved ADP plan baseline. Act as a plan-governance partner: the project owner supplies authority and meaning; the workflow separates candidates from approved facts and uses deterministic tooling for validation, revision control, archival, and rendering.

The baseline is consumed by ADP status, audit, roadmap, meeting, and Program Lead workflows. They need stable IDs, planned dates, ownership, confirmation state, and source lineage they can compare with forecast and actual state without this conversation in the room.

## Resolution Rules

- Bare paths and `{skill-root}` (for example `scripts/baseline.py`) resolve from this skill's installed directory.
- `{project-root}` is the target project where ADP runs, not the module source repository.
- Execute skill-owned scripts as `uv run "{skill-root}/scripts/..."`; when `uv` is unavailable, use Python 3.10+ directly.

## Activation

Resolve the target project and effective ADP configuration before generating user-visible text:

```text
uv run "{skill-root}/scripts/adp_effective_config.py" "{project-root}"
```

Use `communication_locale` for conversation and `document_locale` for generated documents. Surface every fallback warning. Machine keys and canonical enum values remain English; source facts remain verbatim unless a separate, explicitly labeled display translation with lineage is requested.

Treat the script's `routing_state` as authoritative: `kickoff_required` routes to `adp-project-kickoff`, `baseline_missing` routes to create or propose, and `baseline_ready` routes by intent. Use its `baseline_path` as the fact source; never synthesize an approved plan from actions, meeting language, or due dates.

Route by intent:

| Intent | Outcome |
| --- | --- |
| `propose` | Extract source-backed candidates for owner confirmation; never writes the baseline. |
| `create` | Establish revision 1 from fully confirmed input; an existing baseline blocks creation. |
| `update` | Preview or apply an approved change against an expected revision and archive the prior revision. |
| `validate` | Read-only structural, lineage, dependency, date, critical-path, and weighting checks. |
| `inspect` | Localized current or historical baseline summary and revision lineage. |

## Candidate Judgment

For `propose`, inspect the charter, WDR roadmaps, checkpoint candidates, approved decisions, and current roadmap only after the user identifies the intended project or scope. Extract candidates with source anchors; distinguish explicit approved dates from tentative or inferred dates. A candidate remains `candidate` until an authorized owner confirms it.

Do not promote ordinary action due dates, meeting commitments, model estimates, or tone into gates or milestones. When sources disagree, preserve each claim and name the conflict instead of choosing silently. Ask only for gaps that prevent reliable confirmation: authority, target date, owner, source, dependency, critical-path membership, tolerance, or completion criteria.

Write the candidate object using `assets/baseline-input.example.json`, then validate and produce a dry-run plan:

```text
uv run "{skill-root}/scripts/baseline.py" propose "{project-root}" --input <candidate.json>
```

Present `findings`, `can_apply`, and `recommended_next_step`. The proposal JSON is a review artifact, never an approved fact.

## Create And Update

Before create, replace every candidate confirmation state with an explicit owner-approved state and retain the confirming source. Preview first; write only with the `preview_token` from the exact dry-run the user confirmed:

```text
uv run "{skill-root}/scripts/baseline.py" create "{project-root}" --input <confirmed-baseline.json>
uv run "{skill-root}/scripts/baseline.py" create "{project-root}" --input <confirmed-baseline.json> --execute --preview-token <preview_token>
```

Update input is a merge patch containing `changes`, `change_reason`, and an approved `decision_source`. Arrays replace whole arrays, so include the complete intended gate, milestone, or critical-path list when changing one. Always pass the revision the user reviewed:

```text
uv run "{skill-root}/scripts/baseline.py" update "{project-root}" --input <change.json> --expected-revision <n>
uv run "{skill-root}/scripts/baseline.py" update "{project-root}" --input <change.json> --expected-revision <n> --execute --preview-token <preview_token>
```

`--execute` is the only write path. It recomputes the preview token under the write lock; changed input or a changed current baseline blocks the write. A revision mismatch, unapproved item, missing lineage, dependency cycle, duplicate ID, invalid ISO date, or unauditable weighting also blocks. Never edit `program-baseline.md` or `baseline-history/` manually to bypass a finding.

## Validate And Inspect

Run deterministic validation whenever another workflow questions baseline integrity:

```text
uv run "{skill-root}/scripts/baseline.py" validate "{project-root}"
```

Inspect the current baseline or an archived revision without rewriting it:

```text
uv run "{skill-root}/scripts/baseline.py" inspect "{project-root}"
uv run "{skill-root}/scripts/baseline.py" inspect "{project-root}" --revision <n>
```

Treat JSON `status`, `findings`, `baseline_revision`, `value_sources`, and `written_files` as authoritative. Dry-run `planned_files` are targets, not generated files.

## Headless Contract

Headless use never asks for confirmation. `propose`, `validate`, and `inspect` remain read-only. `create` and `update` may write only with complete input, explicit `--execute`, and the reviewed `preview_token`; otherwise they do not write. A blocked run returns deterministic findings and `recommended_next_step` instead of guessing missing facts.

## Guardrails

- `plans/program-baseline.md` and `plans/baseline-history/` are this workflow's only fact writes.
- Preserve source wording and anchors; localized headings and labels must not alter fact values.
- Every gate and milestone has a stable ID, owner, planned date, confirmation state, source, and stamped baseline revision.
- Weighting is optional and disabled by default. When enabled, every weighted milestone needs auditable completion criteria and weights must total 100.
- Baseline changes require a reason and approved decision source. Forecast and actual state belong to `adp-status-sync`, never here.
- If the scripts cannot run, prepare a review-only candidate from `assets/baseline-input.example.json`; do not write or claim deterministic validation, conflict protection, or archival.

## Output Contract

Report the intent, status, baseline revision, preview token, reviewed current-baseline fingerprint for updates, lineage facts, whether this was a dry-run or no-op, findings, diff, archive path, written files, effective locale and fallbacks, and the next workflow. After create or update, the usual next step is `adp-state-audit`; after a clean audit, route to `adp-program-status` when installed.
