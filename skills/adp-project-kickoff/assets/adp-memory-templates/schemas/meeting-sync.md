# Meeting Sync Schema

Meetings are high-frequency project state inputs. Every meeting item should close into project memory or become an explicit no-op.

## Meeting Types

- FDE internal sync
- business sync
- stakeholder review
- workstream deep dive
- offline supplemental note
- cutover / acceptance room

## Item Classification

| Type | Destination |
| --- | --- |
| Fact | `daily/YYYY-MM-DD.md` and possibly a WDR update |
| Decision | `decisions/decision-log.md` or a workstream decision file |
| Action | relevant WDR, FDE action view, or daily log |
| WDR update | `workstreams/{id}/delivery-record.md` |
| Business decision needed | `decisions/business-decision-packets/{id}.md` |
| No-op | meeting note with rationale |

## Closure Check

For each item, record:

- source meeting
- affected workstream(s)
- classification
- destination file
- owner
- due date or trigger when applicable
- unresolved gap when not closed

## Required Output

A meeting sync is incomplete if any item remains unclassified without an explicit reason.
