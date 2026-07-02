# Status Taxonomy

Use one term per concept so project-level views do not drift.

## Workstream Status

| Status | Meaning | Use When |
| --- | --- | --- |
| `not-started` | No active BMM or ADP work yet. | The workstream is identified but not underway. |
| `drafting` | BMM artifacts or WDR are being created. | Scope or artifact paths are still forming. |
| `in-progress` | Delivery work is actively moving. | There are known next actions and no blocking gap. |
| `blocked` | Progress is blocked by a dependency, decision, access, or evidence gap. | Owner cannot continue without resolution. |
| `at-risk` | Work can continue but delivery, acceptance, or cutover confidence is threatened. | Risk requires visibility or mitigation. |
| `ready-for-review` | Workstream is ready for acceptance/readiness review. | Evidence and criteria are linked. |
| `accepted` | Business/customer/acceptance owner has confirmed. | Confirmation is recorded. |

## Decision Types

- FDE internal decision
- business decision
- risk acceptance
- scope change
- pending clarification

## Risk Severity

- low
- medium
- high
- critical

## Dependency Status

- proposed
- active
- blocked
- resolved

## Acceptance Status

- not-defined
- criteria-defined
- evidence-missing
- evidence-linked
- confirmation-pending
- confirmed

## Forbidden Mixes

- Do not mark `accepted` without confirmation evidence.
- Do not use `ready-for-review` to mean cutover-ready in migration projects.
- Do not hide a blocked business decision as an ordinary action item.
