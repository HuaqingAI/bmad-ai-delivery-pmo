---
name: adp-risk-dependency-change-review
description: Reviews ADP risks dependencies changes. Use when the user says "adp-risk-dependency-change-review" or "review ADP risks".
---

# adp-risk-dependency-change-review

## Overview

This workflow reviews AI Delivery PMO risks, dependencies, blockers, and changes across one or more FDE workstreams. Act as a delivery risk and change-control facilitator: separate facts from judgment, expose cross-line failure modes early, and turn every surfaced item into an owner-backed next action or an explicit acceptance.

The consumer is the FDE owner, project lead, readiness reviewer, and business decision maker. They need a risk matrix, dependency map, change warnings, and decision prompts that are actionable without reading every BMM artifact. BMM outputs remain the source of truth; ADP Workstream Delivery Records are the project-level synchronization surface.

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `scripts/review_risk_dependency_change.py`) resolve from this skill's installed directory.
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

Use `{project-root}/_bmad-output/adp/memory` as the default ADP memory root. If it is missing, tell the user to run `adp-project-kickoff`; still allow review when the user provides `--memory-root`.

Read project-level context only as needed: `project-charter.md`, `cadence.md`, `schemas/status-taxonomy.md`, `schemas/decision-taxonomy.md`, `l0/*`, `decisions/decision-log.md`, and the relevant `workstreams/{id}/delivery-record.md` and `decisions.md` files. Treat `views/risk-matrix.md` and `views/dependency-map.md` as derived outputs that can be regenerated.

## Review

Run the deterministic scanner:

```bash
uv run scripts/review_risk_dependency_change.py {project-root}
```

Add optional flags only when the user gives the facts:

- `--workstream <id>` to limit the review; repeat as needed.
- `--memory-root <path>` when ADP memory is not at the default path.
- `--dry-run` to preview without writing derived views or packets.
- `--packet-title "<title>"` and `--packet-question "<question>"` when an issue requires business clarification or decision.
- `--packet-background`, `--packet-option`, `--packet-impact`, `--packet-recommendation`, `--packet-deadline`, `--packet-owner`, and `--packet-workstream` to fill a Business Decision Packet from reliable facts.

If the script cannot run, manually review the same sources and update only derived artifacts under `views/` and `decisions/business-decision-packets/`. Do not directly rewrite a Workstream Delivery Record from this workflow unless the user explicitly asks for a specific edit.

## Normalization

Keep the categories distinct because each one needs a different closure path:

- A blocker is already stopping progress and needs an owner, target date or trigger, and escalation path.
- A risk may become a blocker and needs severity, likelihood, affected lines, mitigation, and owner.
- A dependency names a source line, target line or L0 reference, unresolved condition, owner, and next action.
- A change alters scope, baseline artifacts, acceptance expectations, or delivery commitments and needs decision-log treatment.
- An open question becomes a Business Decision Packet when FDE cannot decide it alone.

Every surfaced risk, dependency, blocker, or change must have an owner, affected line or lines, impact, and next action or explicit acceptance. If any field is missing, keep the item visible as a gap instead of filling it with a guess.

## Business Decision Packets

Create a packet when the next move requires business or project-lead judgment rather than FDE execution. The packet must include background, the unresolved question, options, impacts, recommendation, deadline or trigger, affected workstreams, and requested decision owner.

Use packets for business confirmation, risk acceptance, scope change, dependency tradeoffs, cutover/go-no-go concerns, and unresolved L0 constraint questions. Do not create packets for ordinary FDE implementation tasks; those belong in WDR next actions or FDE action lists.

## Output Contract

After the review, report:

- scanned workstreams and skipped or missing records
- `views/risk-matrix.md` path and entry count
- `views/dependency-map.md` path and entry count
- change warnings and escalation recommendations
- any Business Decision Packet path created
- gaps that prevent project-lead or business judgment

Do not call a project healthy just because no risk text was found. Missing risk, dependency, owner, or decision data is itself a review finding when it prevents coordination.

## Guardrails

- ADP reviews project-level coordination state; BMM artifacts remain the delivery detail source.
- Preserve source records. This workflow writes derived views and optional decision packets.
- Combine risk, dependency, and change review in one pass so escalation is not split across disconnected reports.
- Keep dependency maps useful as Markdown tables in v1; visual graphs are optional future work.
- For migration or cutover projects, surface cutover, rollback, data sync, monitoring, L0 gate, and evidence-rule concerns as risk or dependency items, but leave readiness scoring to `adp-acceptance-readiness-review`.
