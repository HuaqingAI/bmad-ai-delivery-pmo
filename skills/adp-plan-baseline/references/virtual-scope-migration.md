# Legacy Program WDR Migration

Load this runbook only when the shared scope resolver reports `ADP-LEGACY-VIRTUAL-SCOPE-WDR` for `workstreams/program/`. The directory is legacy state, not a physical Workstream and not a source for virtual milestone audit, status, or rendering.

Do not delete or rewrite the directory automatically. It may contain real actions, decisions, evidence, or source references that would be lost even though its WDR, BMM phase, sidecars, and artifact index are invalid for the virtual scope.

Before cleanup, have a human owner review every file and migrate durable facts through their owning contracts:

- Move active or historical actions to `actions/action-ledger.md` through `adp-status-sync`, preserving stable action IDs and source references.
- Move decisions to the canonical decision log or business decision packets, preserving authority and closure evidence.
- Move evidence to the physical Workstream or project evidence store that owns it, preserving immutable references.
- Record any unresolved content and the reviewer before deletion.

After the owner confirms migration, delete `workstreams/program/` manually. Do not change, reassign, or remove approved baseline milestones whose `workstream_id` is `program`, and do not modify immutable audits or Program Status snapshots.

After deletion, regenerate derived state in this order:

1. Input Audit (`adp-state-audit`).
2. Program Status (`adp-program-status`).
3. Roadmap (`adp-roadmap-sync`).
4. Flow Graph (`adp-flow-graph`).

The migration is complete only when the new Audit has the shared `scope_contract`, `program` remains in `virtual_scopes`, physical Workstreams remain in `registered_workstreams`, and no canonical source fingerprint references `workstreams/program/delivery-record.md`.
