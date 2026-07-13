---
name: adp-acceptance-readiness-review
description: Reviews ADP acceptance and cutover readiness. Use when the user says "adp-acceptance-readiness-review" or "review acceptance readiness".
---

# adp-acceptance-readiness-review

## Overview

This workflow reviews AI Delivery PMO workstreams for acceptance and, when the project profile or L0 inputs indicate migration risk, cutover readiness. Act as a delivery readiness reviewer: score the current state from Workstream Delivery Records, evidence, decisions, readiness schema, and L0 references, then leave FDEs and project leads with a report that names what is ready, what is not, who owns each gap, and what action closes it.

The consumer is the FDE owner, project lead, acceptance owner, and later `adp-agent-program-lead`. They need score plus evidence-backed gaps, not color labels or vague "not ready" summaries.

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `scripts/render_readiness_report.py`) resolve from this skill's installed directory.
- `{project-root}` -> the project working directory.
- `{skill-name}` -> the skill directory's basename.
- When executing skill-owned scripts in a shell, use `{skill-root}/scripts/...`. Do not rely on the shell working directory resolving `scripts/...`, because commands usually run from `{project-root}`.

## Configuration and Language

Resolve the target `{project-root}` before any user-facing output. This is the project where ADP is installed or being run, not the module build repository.

Load BMad configuration from the target project in this order:

1. `{project-root}/_bmad/adp/config.yaml` (primary ADP install-time config)
2. `{project-root}/_bmad/config.user.yaml` and `{project-root}/_bmad/config.yaml` when present
3. `{project-root}/_bmad/core/config.yaml`
4. `{project-root}/_bmad/bmm/config.yaml` or `{project-root}/_bmad/bmb/config.yaml` as compatibility fallbacks

Use `communication_language` for all conversation and status output. The writer resolves the shared ADP effective config and uses `document_output_language` for generated project documents and report text; `--language` is a one-run override. Surface resolver warnings and explicit English fallback. Language switching localizes system copy only: source facts, canonical enum values, fact-layer field names, and lineage stay unchanged.

## On Activation

Use `{project-root}/_bmad-output/adp/memory` as the default ADP memory root. If it is missing, tell the user to run `adp-project-kickoff`; still continue when the user provides `--memory-root` or points to a prepared ADP memory folder.

Review all workstreams by default. If the user names workstream IDs, review only those and call out omitted cross-line dependencies when they affect readiness judgment.

## Inputs

Read the smallest set of files that can support the judgment:

- `schemas/readiness-scorecard.md` for dimensions and scoring scale
- `schemas/status-taxonomy.md` when status wording is ambiguous
- `project-charter.md` and `cadence.md` for profile, acceptance rhythm, and escalation expectations
- `l0/reference-index.md` and extracted L0 files when L0 gates, NFRs, evidence rules, freeze windows, contracts, or cutover constraints may apply
- each selected `workstreams/{id}/delivery-record.md`, `evidence.md`, `decisions.md`, and existing `readiness.md`
- `decisions/decision-log.md` and business decision packets when a gap depends on customer, business, scope, risk acceptance, or go/no-go confirmation

If a file is absent, treat it as a visible gap rather than a blocker. ADP readiness exists to expose missing state.

## Score

Score each relevant dimension on the project scale, normally 0-3:

- `0`: missing or unknown
- `1`: partially known with major gaps
- `2`: mostly ready with owned gaps
- `3`: ready with clear owner and evidence

Generic delivery dimensions are scope clarity, acceptance clarity, BMM artifact completeness, dependency clarity, risk exposure, evidence completeness, L0 compliance, and next-action executability. For migration or cutover profiles, also expose functional migration, data sync, business confirmation, cutover, rollback/fallback, monitoring/evidence, and L0 gate readiness.

Acceptance ready and cutover ready are different states. A line can have acceptance evidence and still be cutover no-go because data reconciliation, freeze window, rollback rehearsal, monitoring, or business confirmation is missing.

Assign each acceptance and cutover result a canonical `roadmap_status`: `done` only when the reviewed gate is complete, `blocked` when work cannot progress, `at-risk` when work can progress but the gate is threatened, and `planned` otherwise. This is a readiness judgment, not a renderer inference.

## Gaps

Every gap needs a dimension, severity, owner, closing action, and due date or trigger. Use `TBD` only when the source files genuinely do not identify the fact, and make the missing owner/action itself a gap.

Escalate a gap when FDE cannot close it alone: business confirmation, risk acceptance, scope change, L0 contract/gate ambiguity, missing customer acceptance owner, unresolved cross-line dependency, or cutover go/no-go decision.

## Evidence Review

Check acceptance criteria against linked proof. A criterion is not covered just because an artifact exists; it needs a proof link, confirmation status, and a gap note when proof is missing or unverified. Keep validation details in BMM artifacts and evidence links; do not copy test logs or PR content into the readiness report.

## Write Results

Create a scorecard JSON file with the reviewed facts and run the deterministic renderer:

```bash
uv run "{skill-root}/scripts/render_readiness_report.py" "{project-root}" --input <scorecard-json> --mode acceptance|cutover|both
```

Add `--write-workstream-readiness` when the same scorecard should update `workstreams/{id}/readiness.md` generated blocks. Use `--memory-root <path>` for a non-default ADP memory root and `--output-dir <path>` when reports should go outside `{project-root}/_bmad-output/adp/memory/views`. The script only formats already-judged data into Markdown/HTML and readiness blocks; it must not decide scores, severity, or go/no-go status. If the script cannot run, write the same generated sections manually and keep existing user notes outside the generated block.

## Scorecard JSON Contract

The renderer consumes one JSON object. Required top-level field: `workstreams`, a non-empty array. Optional top-level fields: `project_name`, `generated_at`, `source`, `summary`, `acceptance_summary`, and `cutover_summary`.

Each workstream object should include:

- `id`, `name`, and `owner`
- `acceptance` object with `score`, `max_score`, `status`, `roadmap_status`, `dimensions`, and `gaps`
- `cutover` object with the same shape plus `go_no_go` when cutover applies
- `evidence` array with `criterion`, `proof`, `status`, and `gap`
- `confirmations` array with `item`, `owner`, `status`, and `action`

Each dimension uses `dimension`, `score`, `gap`, `owner`, `action`, `due`, and `severity`. Each gap uses `gap`, `dimension`, `owner`, `action`, `due`, `severity`, and `escalation`. Omit cutover dimensions only when cutover is not applicable; otherwise score missing cutover facts as gaps rather than hiding them.

## Output Contract

Report back:

- reviewed workstreams and any omitted impacted lines
- acceptance score/status per workstream
- cutover score/status when applicable
- highest-severity gaps with owner and closing action
- report paths written
- recommended next workflow, usually `adp-risk-dependency-change-review` for escalations, `adp-l0-reference-sync` for L0 ambiguity, or `adp-status-sync` after owners close gaps

Do not call a line ready because all files exist. Ready requires acceptance criteria, evidence, confirmations, risk/dependency state, and next actions to be clear enough for the acceptance owner or project lead to act.
