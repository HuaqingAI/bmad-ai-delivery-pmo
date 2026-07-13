# Workstream Delivery Record Schema

A Workstream Delivery Record is the minimum project-level status surface for one FDE workstream. It indexes BMM artifacts and summarizes coordination facts; it does not duplicate full PRD, architecture, story, code, or validation content.

## Required Sections

### Identity

- Workstream ID:
- Name:
- FDE owner:
- Business owner:
- Current BMM phase:
- Current ADP status:

### BMM Artifact Index

| Artifact | Path / Link | Baseline Status | Notes |
| --- | --- | --- | --- |
| PRD | TBD | draft | TBD |
| Architecture | TBD | draft | TBD |
| Epics / stories | TBD | draft | TBD |
| Code / PR | TBD | draft | TBD |
| Validation evidence | TBD | draft | TBD |

### Scope

- In scope:
- Out of scope:
- Key assumptions:
- Open questions:

### Acceptance

- Acceptance criteria:
- Acceptance owner:
- Evidence required:
- Current readiness:
- Unclosed gaps:

### Project Status

- Progress:
- Blockers:
- Risks:
- Dependencies:
- Scope or change notes:
- Next actions:

### Cross-Workstream Links

- Depends on:
- Impacts:
- L0 references:

### Decisions and Evidence

- Decision links:
- Business Decision Packet links:
- Evidence links:
- Customer/business confirmations:

### Roadmap

Optional source-backed milestones for `adp-roadmap-sync`. Use this only for verifiable milestone events; ordinary next actions and action due dates stay in the action ledger.

| Milestone ID | Milestone | Type | Status | Planned | Forecast | Actual | Owner | Confidence | Depends On | Source | Baseline Revision |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TBD | TBD | checkpoint | planned | TBD | TBD | TBD | TBD | low | TBD | TBD | TBD |

Allowed `Type`: `checkpoint`, `business-decision`, `readiness-gate`, `cutover-gate`, `dependency-release`, `delivery-window`.

Allowed `Status`: `planned`, `at-risk`, `done`, `blocked`.

`Planned`, `Forecast`, and `Actual` must come from WDR owner updates, checkpoint evidence, decision closure, or another explicit source. If the source does not give a date, write `TBD`.

Rows mapped to the program baseline require the exact case-sensitive baseline `Milestone ID` and current `Baseline Revision`. `adp-status-sync` may update status, forecast, actual, and source evidence, but only `adp-plan-baseline` may change planned facts.

## State Semantics

- `draft`: usable but incomplete; known gaps are acceptable.
- `gap`: a missing fact blocks coordination, readiness, or stakeholder judgment.
- `ready`: required project-level fields are present and evidence/decision gaps are owned or closed.

## Non-Negotiable

The Record answers: "What is this workstream's project-level state, who does it affect, and can it be delivered?" BMM artifacts answer how the workstream is built.
