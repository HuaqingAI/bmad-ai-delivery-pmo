# AI Delivery PMO

AI Delivery PMO is a BMad module for coordinating complex delivery programs across multiple FDE workstreams. Version 1.3 adds canonical progress/flow projections and a self-contained offline HTML management panel without replacing the existing JSON and Markdown contracts.

The module does not replace BMM. Each workstream still uses the normal BMM lifecycle for brainstorming, PRD, architecture, epics, stories, implementation, and validation. AI Delivery PMO adds the project-level management layer around those artifacts: shared state, readiness scoring, dependency/risk visibility, evidence tracking, L0 reference sync, and stakeholder-facing views.

## Core Concept

Each workstream maintains a **Workstream Delivery Record**:

- BMM artifact index: PRD, architecture, epics/stories, code, PRs, deployments, tests, evidence.
- Project-level summary: scope, non-scope, assumptions, acceptance criteria, risks, dependencies, blockers, changes, decisions, next actions.
- Readiness state: score, dimension scores, gap list, owner actions, evidence coverage, acceptance confirmation.

The record is a lightweight synchronization surface. BMM artifacts remain the source of truth.

## Scenario-Hardened Loops

The plan has been reviewed against an X-Large Shopify-to-custom-site migration scenario. The module now explicitly models four closure loops:

- Workstream closure: BMM artifacts sync into WDRs.
- Meeting closure: internal meetings, business reviews, special discussions, and offline follow-ups become logs, decisions, actions, WDR updates, business decision packets, or explicit no-ops.
- Decision closure: FDE decisions, business decisions, risk acceptance, and scope changes are classified and tracked.
- Acceptance closure: each acceptance criterion traces to evidence, confirmer, status, and gaps.

Migration/cutover projects can reference L0 outputs for data definitions, interface contracts, cutover gates, rollback/fallback standards, monitoring, and evidence rules. ADP indexes and extracts cross-line implications; it does not own L0 delivery.

## Module Skills

- `adp-setup`
- `adp-project-kickoff`
- `adp-plan-baseline`
- `adp-workstream-register`
- `adp-bmm-checkpoint-sync`
- `adp-meeting-sync`
- `adp-status-sync`
- `adp-risk-dependency-change-review`
- `adp-l0-reference-sync`
- `adp-acceptance-readiness-review`
- `adp-state-audit`
- `adp-program-status`
- `adp-roadmap-sync`
- `adp-flow-graph`
- `adp-meeting-pack`
- `adp-management-panel`
- `adp-agent-program-lead`

## Plan

The current v1.3 module plan is in [`skills/reports/adp-html-management-panel-v1.3-plan.md`](skills/reports/adp-html-management-panel-v1.3-plan.md). Release behavior and migration notes are in [`skills/reports/adp-v1.3-release-notes.md`](skills/reports/adp-v1.3-release-notes.md).

For a concise module overview and the skill-labeled delivery flow, see [`docs/adp-module-overview.md`](docs/adp-module-overview.md).
