# State Audits

This folder holds `adp-state-audit` outputs.

Audit files are quality gates for derived views, meeting packs, weekly reports, project-lead readouts, and roadmap output. They are not a replacement for the underlying sources of truth:

- `workstreams/*/delivery-record.md`
- `actions/action-ledger.md`
- `decisions/decision-log.md`
- `decisions/business-decision-packets/*`
- `daily/*`
- `l0/*`

Generate an audit before rendering a derived readout. If an audit reports blocking gaps or conflicts, the readout may still be produced, but it must show the risk instead of presenting the state as clean.

Expected file pattern:

```text
audits/{date}-{scenario}-audit.json
audits/{date}-{scenario}-audit.md
```
