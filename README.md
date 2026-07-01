# AI Delivery PMO

AI Delivery PMO is a planned BMad module for coordinating complex delivery programs across multiple FDE workstreams.

The module does not replace BMM. Each workstream still uses the normal BMM lifecycle for brainstorming, PRD, architecture, epics, stories, implementation, and validation. AI Delivery PMO adds the project-level management layer around those artifacts: shared state, readiness scoring, dependency/risk visibility, evidence tracking, L0 baseline checks, and stakeholder-facing views.

## Core Concept

Each workstream maintains a **Workstream Delivery Record**:

- BMM artifact index: PRD, architecture, epics/stories, code, PRs, deployments, tests, evidence.
- Project-level summary: scope, non-scope, assumptions, acceptance criteria, risks, dependencies, blockers, changes, decisions, next actions.
- Readiness state: score, dimension scores, gap list, owner actions, evidence coverage, acceptance confirmation.

The record is a lightweight synchronization surface. BMM artifacts remain the source of truth.

## Planned Skills

- `adp-project-kickoff`
- `adp-workstream-register`
- `adp-bmm-checkpoint-sync`
- `adp-status-sync`
- `adp-acceptance-readiness-review`
- `adp-risk-dependency-change-review`
- `adp-l0-baseline-check`
- `adp-agent-program-lead`

## Plan

The current module plan is in [`skills/reports/fde-delivery-module-plan.md`](skills/reports/fde-delivery-module-plan.md).
