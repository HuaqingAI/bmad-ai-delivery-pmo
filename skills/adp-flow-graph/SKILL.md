---
name: adp-flow-graph
description: Produces canonical delivery flow graph projections. Use when the user says "build flow graph" or "refresh flow graph".
---

# ADP Flow Graph

Own the canonical topology, dual-axis state, scoped overlays, unmapped records, recovery findings, and graph identity consumed by management views. The projection exists so panel, meeting, audit, and Program Lead consumers can select or format one fact model without reconstructing dependencies, states, counts, or branches.

## Resolution rules

- Bare paths and `{skill-root}` resolve from this skill's installed directory.
- `{project-root}` is the project working directory.
- `{skill-name}` is `adp-flow-graph`.

## Contract

Load `references/flow-graph-contract-v1.md` and validate against `assets/adp-flow-graph-v1.schema.json`. The baseline dependency, program-status flow state, action relation, and risk relation contracts remain owned by their source skills and are linked from that reference.

Only approved baseline milestones and gates are nodes. Only normalized same-revision baseline dependency objects are edges. Copy execution and health as independent axes, count only explicitly related canonical actions and risks inside the selected scope, and preserve every unresolvable overlay in `unmapped`.

Canonical identity is layered as topology, state, overlay, then flow graph. Do not produce `layout_id`, coordinates, routing, dimensions, locale-derived measurements, or ELK configuration; those identities belong to `adp-management-panel`.

## Generate

Generate from the approved baseline, current program status, action-flow, and risk-flow contracts:

```bash
uv run "{skill-root}/scripts/flow_graph.py" "{project-root}"
```

Use `--dry-run` to return the complete graph without writes, `--scopes <json>` to add explicit meeting-window scopes, and input overrides only for controlled recovery or tests. A valid run writes `views/flow-graph.json`, immutable `snapshots/flow-graph/fg-<sha256>.json`, and `snapshots/flow-graph/latest.json`. Publication verifies semantic identities first, creates immutable content without overwrite, then replaces the current/latest pair with rollback on failure.

Invalid topology returns blocked findings and publishes nothing. Missing or legacy action/risk relation contracts degrade with deterministic migration findings; explicit unmapped records remain visible. After generation, validate `views/flow-graph.json` through `adp-state-audit` artifact phase. Meeting-pack may select a scenario subgraph but must not write back to this graph.
