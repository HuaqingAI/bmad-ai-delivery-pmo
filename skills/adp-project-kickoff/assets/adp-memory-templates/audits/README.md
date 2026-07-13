# State Audits

This folder holds `adp-state-audit` outputs.

Audit files are quality gates for derived views, meeting packs, weekly reports, project-lead readouts, and roadmap output. They are not a replacement for the underlying sources of truth:

- `workstreams/*/delivery-record.md`
- `actions/action-ledger.md`
- `decisions/decision-log.md`
- `decisions/business-decision-packets/*`
- `daily/*`
- `l0/*`

Generate an input audit before rendering a derived readout, embed its `input_audit_id`, then run artifact validation on the emitted files. Finding severity and execution disposition are independent: only disposition `blocked` prevents generation or publication; `degraded` output must lower confidence and show risk instead of presenting the state as clean.

Expected file pattern:

```text
audits/{date}-{scenario}-input-audit-{id}.json
audits/{date}-{scenario}-input-audit-{id}.md
audits/{date}-{scenario}-artifact-validation-{id}.json
audits/{date}-{scenario}-artifact-validation-{id}.md
```
