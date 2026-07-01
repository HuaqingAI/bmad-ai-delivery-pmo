# AI Delivery PMO

AI Delivery PMO is a planned BMad module for coordinating complex delivery programs across multiple FDE workstreams.

The module does not replace BMM. Each workstream still uses the normal BMM lifecycle for brainstorming, PRD, architecture, epics, stories, implementation, and validation. AI Delivery PMO adds the project-level management layer around those artifacts: shared state, readiness scoring, dependency/risk visibility, evidence tracking, L0 baseline checks, and stakeholder-facing views.

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

Migration/cutover projects can use L0 baselines for data definitions, interface contracts, cutover gates, rollback/fallback standards, monitoring, and evidence rules.

## Planned Skills

- `adp-project-kickoff`
- `adp-workstream-register`
- `adp-bmm-checkpoint-sync`
- `adp-meeting-sync`
- `adp-status-sync`
- `adp-acceptance-readiness-review`
- `adp-risk-dependency-change-review`
- `adp-l0-baseline-check`
- `adp-agent-program-lead`

## Plan

The current module plan is in [`skills/reports/fde-delivery-module-plan.md`](skills/reports/fde-delivery-module-plan.md).
