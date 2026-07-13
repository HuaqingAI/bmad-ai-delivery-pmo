---
name: adp-l0-reference-sync
description: Syncs ADP L0 reference implications. Use when the user says "adp-l0-reference-sync" or "sync L0 references".
---

# adp-l0-reference-sync

## Overview

This workflow syncs L0 workstream artifacts into AI Delivery PMO as lightweight project-level references: source indexes, freeze rules, contracts, gates, NFR obligations, evidence rules, decision gates, open questions, and cross-workstream impacts. Act as a delivery coordination facilitator: extract what downstream workstreams must acknowledge, expose gaps, and route unresolved questions back to L0 or business decision flow.

The consumer is the FDE owner, project lead, readiness reviewer, and later ADP workflows. They need enough L0 context to judge downstream WDR/readiness gaps without treating ADP as the owner of L0 delivery.

## Resolution rules

- Bare paths and `{skill-root}` (e.g. `scripts/sync_l0_references.py`) resolve from this skill's installed directory.
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

Use `{project-root}/_bmad-output/adp/memory` as the default ADP memory root. If it is missing, tell the user to run `adp-project-kickoff`; still allow sync when the user provides `--memory-root` or asks to create the missing path.

Read only the L0 source artifacts the user gives, existing `l0/*` summaries, and WDRs needed for the requested impact scan. The structured working state is `{project-root}/_bmad-output/adp/memory/l0/*`; do not create a separate runtime memlog for this workflow.

## Sync

Start from the ADP question: what has L0 established, which workstreams are affected, what must be reflected in WDR/readiness, and what must go back to L0 or business decision process?

Prepare a compact sync plan from the L0 artifacts and user notes. Keep detailed PRD, architecture, implementation, and validation content in the L0/BMM source files; the plan carries only project-level implications. Use these plan sections when present: `source_artifacts`, `freeze_windows`, `rules`, `contracts`, `gates`, `nfrs`, `evidence_rules`, `decision_gates`, `impacts`, and `exceptions_open_questions`.

Apply the deterministic writer:

```bash
uv run "{skill-root}/scripts/sync_l0_references.py" "{project-root}" --plan <sync-plan.json>
```

Useful flags:

- `--memory-root <path>` when ADP memory is not at `{project-root}/_bmad-output/adp/memory`.
- `--workstream <id>` to restrict the WDR gap scan; repeat as needed.
- `--source-artifact "L0 PRD=path/or/url"` for a source-only registration when no plan file is needed.
- `--dry-run` to preview writes.

If the script cannot run, manually update the same `l0/*` files and perform a WDR gap scan directly from the affected records. Preserve the same boundary: ADP records references, extracted implications, open questions, and impact summaries only.

## Gap Scan

Use the script's JSON output to report:

- L0 summary files written or preserved
- affected workstreams
- WDRs missing applicable L0 references, gates, NFRs, evidence rules, or contracts
- questions that should return to the L0 workstream
- items that require a Business Decision Packet instead of an FDE-local decision

Do not rewrite workstream records automatically unless the user asks. Provide concrete WDR update suggestions the FDE can confirm.

## Output Contract

After sync, report:

- the ADP memory root
- source artifacts indexed
- L0 summary files updated
- impacted workstreams and gap suggestions
- open questions or exceptions and where they should be routed next
- the next useful workflow, usually `adp-acceptance-readiness-review` or `adp-risk-dependency-change-review`

Do not say L0 is complete or implementation-ready. This workflow only confirms what ADP can reference and which downstream coordination gaps are visible.

## Guardrails

- L0 PRD, architecture, specs, registry, evidence, and governance artifacts remain the source of truth.
- Do not duplicate L0 delivery detail into ADP summaries.
- Do not grade L0 quality; grade only whether downstream workstreams have acknowledged applicable L0 constraints.
- Make migration/cutover gates explicit when present, including freeze windows, rollback/fallback, monitoring, stale evidence, and go/no-go evidence.
- Treat draft and gap states as valid if the missing owner, evidence, or decision route is visible.
