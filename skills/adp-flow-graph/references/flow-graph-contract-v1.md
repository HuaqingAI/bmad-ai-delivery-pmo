# Canonical Flow Graph Contract v1

The machine contract is `assets/adp-flow-graph-v1.schema.json`; normative examples are under `assets/fixtures/flow-contract-v1/`. Source contracts are owned by:

- `adp-plan-baseline/assets/program-baseline-flow-vnext.schema.json`
- `adp-program-status/assets/program-status-flow-state-v1.schema.json`
- `adp-status-sync/assets/action-flow-relation-v1.schema.json`
- `adp-risk-dependency-change-review/assets/risk-flow-relation-v1.schema.json`

## Topology

Nodes are approved milestones and gates from exactly one baseline revision. Milestones use their workstream lane. Gates use an explicit workstream lane or the program lane. Edges are the target node's normalized dependency objects. `aggregation` converges only on a target with `predecessor_rule: all`; that rule never changes or blocks the predecessor lanes themselves. `conditional` requires an explicit canonical condition fact. Unconfirmed conditions remain present with relationship state `pending-confirmation`; graph generation does not select a branch. Only an all-`rework` cycle is valid.

## State

Every node carries independent `execution_state` and `health_state` plus axis-specific lineage copied from program-status. Every edge carries independent relationship `state` (`pending`, `active`, `satisfied`, `inactive`, `pending-confirmation`, `not-applicable`) and `health` (`on-plan`, `at-risk`, `blocked`, `indeterminate`) with canonical lineage. `ready` never means work started; `in-progress` requires start evidence.

## Scoped overlays

Scopes are `active-as-of`, `reporting-period`, or `meeting-window`. Every scope has `as_of` and an explicit half-open `processed_window` (`start_inclusive <= timestamp < end_exclusive`); reporting and meeting scopes also carry their selection window. Counts are `pending`, `processed`, `risk`, and `blocked`. Each count equals the number of unique `source_refs` listed for that category after applying the source contract and explicit node/edge relations. A source may appear in multiple independent categories but once per category and target.

Actions and risks without usable explicit relations are never dropped. They enter `unmapped` with source identity, supplied related IDs, reason, finding code, and recovery. Unknown overlay targets or cross-revision overlays degrade the graph but do not alter topology. Invalid baseline topology blocks graph publication.

## Identity

Identity input uses UTF-8 RFC 8785-style canonical JSON: object keys sorted lexicographically, arrays kept in contract order after stable ID sorting where the contract declares a set, no insignificant whitespace, and SHA-256 serialized as `sha256:<64 lowercase hex>`.

- `topology_id`: contract version, baseline ID/revision, canonical nodes, normalized edges, topology lineage. It excludes state, scopes, overlays, and presentation.
- `state_snapshot_id`: `topology_id`, as-of, node state, relationship state, and their lineage.
- `overlay_snapshot_id`: `topology_id`, scopes, mapped overlay source refs/counts, unmapped records, and overlay lineage. It excludes node and relationship state.
- `flow_graph_id`: contract version plus the three identities above.

A topology change changes `topology_id` and downstream identities. A state-only change preserves topology and overlay identities. A count/source-only change preserves topology and state identities and changes `overlay_snapshot_id` plus `flow_graph_id`.

`layout_id` is forbidden in the canonical graph. Locale, node dimensions, filters, layout engine/version/configuration, coordinates, and routed edges belong to the panel's layout identity in Roadmap phase 6+.

## Findings and recovery

Findings are stable and deterministic:

| Code | Disposition | Recovery |
| --- | --- | --- |
| `flow.node.duplicate` | blocked | correct duplicate baseline node IDs |
| `flow.edge.duplicate` | blocked | assign unique stable edge IDs |
| `flow.reference.unknown` | blocked topology / degraded overlay | correct baseline reference or relation target |
| `flow.reference.cross-revision` | blocked topology / degraded overlay | regenerate against one approved revision |
| `flow.cycle.illegal` | blocked | remove the cycle or model every loop edge as explicit rework |
| `flow.condition.missing` | blocked | attach the canonical condition fact |
| `flow.aggregation.rule` | blocked | put `predecessor_rule: all` on the aggregation target |
| `flow.overlay.unmapped` | degraded | add explicit related plan-item or flow-edge IDs |
| `flow.source.migration-required` | degraded | migrate the named source contract; do not infer fields |

Blocked topology publishes no canonical graph. Degraded overlays may publish only when all unmapped records and findings remain visible. Recovery never reuses an old graph as though it represented current inputs.
