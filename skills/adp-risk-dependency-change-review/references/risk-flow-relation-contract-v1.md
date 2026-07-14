# Risk Flow Relation Contract v1

`assets/risk-flow-relation-v1.schema.json` freezes canonical risk identity and flow relations. Production publication is Roadmap phase 5.

Every risk carries a stable, case-sensitive `risk_id`; lifecycle is `open`, `monitoring`, `mitigating`, `accepted`, `closed`, or `cancelled`; relation state is independently `watching`, `at-risk`, `blocked`, `resolved`, or `not-applicable`. Lifecycle and relation state are never collapsed into one color or status.

Relations use explicit, unique `related_plan_item_ids` and `related_flow_edge_ids`, the same baseline revision as the target graph, and source-backed `rule_id` plus canonical source lineage. No text, owner, workstream, dependency note, or date is used to guess a relation. Missing IDs, unknown targets, or revision mismatch preserve the risk under `unmapped` with a deterministic finding.

For a scope instant, `risk` counts lifecycle `open`, `monitoring`, `mitigating`, or `accepted`; `blocked` additionally requires relation state `blocked`. `closed` and `cancelled` do not contribute after their terminal timestamp. The same risk may count in both `risk` and `blocked`; each category lists the exact contributing source IDs.

Legacy risk rows without stable identity or explicit relations remain available to existing Markdown views but return `ADP-RISK-FLOW-MIGRATION-REQUIRED` to graph consumers. They are never assigned a generated production ID.
