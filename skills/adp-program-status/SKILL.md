---
name: adp-program-status
description: Computes canonical ADP project status and snapshots. Use when the user says "generate program status" or "check overall project health".
---

# ADP Program Status

## Overview

This skill turns an approved program baseline, baseline-mapped actuals, and a current input audit into the one canonical project-level status used by ADP management views. Act as a program controls lead: preserve source meaning, make every judgment traceable, and keep status independent from evidence confidence. Roadmap, meeting, and Program Lead consumers must be able to reuse the result without recomputing it or having this conversation in the room.

## Resolution rules

- Bare paths and `{skill-root}` (for example `scripts/program_status.py`) resolve from this skill's installed directory.
- `{project-root}` -> the project working directory.
- `{skill-name}` -> the skill directory's basename.

## On Activation

Resolve whether the user wants to generate the current status or inspect the latest canonical view. Use `uv run scripts/program_status.py --help` as the authoritative interface. If `uv` is unavailable, use Python 3.10+ directly. Stdlib-only means the script needs no third-party Python packages; it still requires the installed `adp-plan-baseline` contracts and, for headless artifact validation, `adp-state-audit`.

If a sibling script is missing, preserve the returned dependency name and path. Route incomplete ADP installations to `adp-setup`, missing baseline artifacts or invalid baseline content to `adp-plan-baseline`, and a missing artifact validator to `adp-state-audit` after installation recovery.

Before generation, require an approved `plans/program-baseline.md` and a current, integrity-sealed `adp-state-audit` input-phase JSON whose locale/fallback disclosure matches the effective config. Missing or candidate baseline routes to `adp-plan-baseline`; actuals that do not map to baseline milestone IDs route to `adp-status-sync`; blocked, tampered, stale, or locale-mismatched audit lineage routes back to `adp-state-audit`. Do not generate around those gates.

The script resolves the shared ADP effective config. Conversation text follows `communication_language`; generated Markdown follows `document_output_language`. Surface every fallback warning. JSON keys and canonical enum values stay English, while display labels are localized and source facts remain verbatim.

## Canonical Inputs

Milestone planned dates, tolerances, critical path, and optional approved weights come only from the baseline. For physical Workstreams, forecast, actual, milestone status, and evidence come only from baseline-mapped WDR `Roadmap` rows. The shared scope contract classifies `program` as virtual, so Program Status never searches for a program WDR or emits `actual.wdr_missing: program`. Ordinary action due dates never become milestones.

When explicit checkpoint, readiness, dependency, decision, or gate evidence carries a project-level signal that those structures cannot express, prepare a run-scoped JSON matching `assets/status-signals.example.json`. Include only canonical status, optional `forecast_date`/`actual_date`, criticality, baseline revision, and a traceable source reference. Do not infer a signal from tone, translate its source in place, or persist it back into a fact source. Ambiguous evidence remains `indeterminate` or is confirmed with the owner.

Virtual milestones derive dates or status only from source-backed milestone signals or explicit incoming `aggregation` edges with `predecessor_rule: all`. All completed predecessors produce the latest predecessor actual; otherwise all unfinished predecessors must have forecasts to produce the latest valid forecast. Lineage names every participating predecessor and the Program Status snapshot. A signal that conflicts with the aggregation blocks generation. With neither evidence source, retain the baseline milestone and apply the existing date rule without inventing a WDR gap.

## Judgment Contract

- `off-plan`: a critical actual or forecast exceeds the approved baseline plus tolerance, or an explicit critical signal proves the constraint off plan.
- `at-risk`: forecast is late but within approved tolerance, or a source-backed blocker, readiness, or dependency signal establishes risk without confirmed delay.
- `indeterminate`: facts cannot support a reliable judgment for an applicable critical constraint. It is never rendered as unconditional green and never hides proven `off-plan` or `at-risk` evidence.
- `on-plan`: all applicable critical constraints remain within baseline and no stronger source-backed signal applies. Future actuals are not applicable; a past due item with neither actual, forecast, nor a proving status is indeterminate rather than assumed late.

Critical precedence is `off-plan` > `at-risk` > `indeterminate` > `on-plan`. A non-critical off-plan variance promotes overall status to `at-risk`, not `off-plan`. Report confidence is computed separately, so a proven delay can be `off-plan` with low confidence. Never emit a completion percentage unless the approved baseline enables weighting and every counted completion has actual evidence.

## Versioned Progress Contract

The production v3 contract is `references/progress-contract-v3.md`, with machine schema `assets/program-status-progress-v3.schema.json` and golden cases under `assets/fixtures/progress-v3/`. `scripts/progress_projection.py` owns its formulas, gates, lineage, compatibility, and recovery. `by_scope` carries physical and virtual projections; the compatibility `by_workstream` array carries physical delivery Workstreams only. Roadmap and meeting-pack copy the validated object. A missing or wrong version returns `ADP-PROGRESS-MIGRATION-REQUIRED`.

An actual counts only with an audit-accepted WDR, completion criteria, evidence, and a date no later than as-of. A same-baseline decrease requires `Correction ID`, `Correction Kind`, `Correction Audit ID`, `Correction Source`, and `Previous Actual`; otherwise progress is blocked.

## Versioned Flow State Contract

`references/flow-state-contract-v1.md` and `assets/program-status-flow-state-v1.schema.json` own execution and health as independent source-backed axes for each baseline node. Every generated snapshot includes canonical `flow_state`: `ready` means applicable predecessors are satisfied, only explicit start state becomes `in-progress`, aggregation targets require every predecessor, and unresolved conditional/rework paths remain planned.

## Generate

Run the deterministic generator with the project root, input audit JSON, as-of date, reporting period, and optional signals JSON. Ordinary non-interactive CLI execution returns the generation disposition before artifact validation. For headless automation, add `--headless`, pass as-of and both period boundaries explicitly, and optionally pass `--memlog`; the script records effective assumptions and decisions, runs artifact validation, and returns the only authoritative terminal result.

The generator writes:

- immutable `snapshots/program-status/<snapshot-id>.json` plus replaceable `latest.json`;
- replaceable `views/program-status.json` and localized `views/program-status.md`;
- localized `views/weekly-report.md` and `views/project-lead.md`.

The same period, as-of, baseline revision, locale, generator version, source fingerprints, and previous snapshot reuse the same snapshot ID without replacing history. Different inputs create a different snapshot. A snapshot carries the hit rule IDs, input audit ID, baseline revision, source inventory, fingerprints, period delta, progress scope identity, eligibility, forecast coverage, comparability, and correction lineage.

After ordinary generation, run `adp-state-audit` in artifact phase against the new snapshot and views with the same input audit JSON. Headless generation writes only to an audit staging area, validates those files, and publishes the immutable snapshot and canonical views only when `safe_to_publish` is true. Artifact validation reports freshness, lineage, and language defects separately and never mutates an immutable snapshot. If validation fails, return the staged paths for diagnosis and leave canonical paths unchanged.

## Inspect and Return

Inspect mode reads `views/program-status.json` and returns its canonical status, confidence, snapshot, baseline revision, input audit ID and path, as-of date, period delta, locale/fallbacks, all canonical paths, and recovery workflows without writing status artifacts. Headless inspection validates those existing artifacts against their source input audit before returning. If the view is missing, route through baseline, input audit, and generation rather than estimating a project judgment in conversation.

For either mode, report status, confidence, snapshot ID, baseline revision, input audit ID, locale/fallbacks, written or planned paths, and recommended recovery workflows. In headless mode return the script JSON only: terminal `status` is `complete` or `blocked`, `safe_to_publish` closes the artifact gate, artifact validation ID and report paths prove that gate ran, and `memlog` carries the typed assumption and decision trail. A blocked result includes a reason and recovery workflows.
