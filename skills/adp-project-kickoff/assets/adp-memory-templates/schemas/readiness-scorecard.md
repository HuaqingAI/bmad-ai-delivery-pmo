# Readiness Scorecard Schema

Readiness exposes delivery gaps so they can be discussed, owned, escalated, and closed. It is not a stop sign by default.

## Default Scoring

Use 0-3 per dimension:

- `0`: missing or unknown
- `1`: partially known, major gaps remain
- `2`: mostly ready, gaps are owned
- `3`: ready, evidence and owner are clear

## Generic Delivery Dimensions

| Dimension | What It Checks | Score |
| --- | --- | --- |
| Scope clarity | In/out scope, assumptions, open questions | TBD |
| Acceptance clarity | Criteria, owner, confirmation path | TBD |
| BMM artifact completeness | PRD, architecture, stories, validation links | TBD |
| Dependency clarity | Upstream/downstream workstreams and blockers | TBD |
| Risk exposure | Known risks, impact, owner, mitigation | TBD |
| Evidence completeness | Proof links tied to acceptance criteria | TBD |
| L0 compliance | Contract, gate, NFR, evidence rule references where relevant | TBD |
| Next-action executability | Owner, action, trigger/due date | TBD |

## Migration / Cutover Additions

For `migration-cutover` profile, also expose:

- functional migration readiness
- data sync readiness
- business confirmation readiness
- cutover readiness
- rollback/fallback readiness
- monitoring and evidence readiness
- L0 gate readiness

## Gap Format

| Gap | Dimension | Owner | Action | Due / Trigger | Escalation |
| --- | --- | --- | --- | --- | --- |
| TBD | TBD | TBD | TBD | TBD | TBD |

## Output Rule

Readiness output must include a score, dimension-level gaps, owners, and next actions. Red/yellow/green alone is not enough.

Each acceptance and cutover result must also persist `roadmap_status` as exactly one of `planned`, `at-risk`, `done`, or `blocked`; the readiness review owns that evidence-backed judgment.
