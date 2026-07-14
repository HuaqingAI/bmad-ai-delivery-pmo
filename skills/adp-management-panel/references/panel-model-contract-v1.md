# ADP Management Panel Model and Artifact Contract v1

`assets/adp-management-panel-v1.schema.json` is the machine contract. Fixtures under `assets/fixtures/panel-contract-v1/` freeze canonical mapping, identities, recovery, safe embedding, and distribution behavior. Phase 6 defines contracts only; HTML generation, ELK layout, publication, and browser behavior belong to Roadmap phase 7.

## Ownership and allowed transformations

The panel consumes, but never owns, business meaning. `adp-program-status` owns status, confidence, progress, comparability, execution, and health. `adp-roadmap-sync` owns timeline and date variance. `adp-flow-graph` owns nodes, edges, relationship state, scoped counts, and unmapped overlays. `adp-meeting-pack` owns meeting windows, information budgets, lifecycle, and FDE/business flow subgraph selection.

Every section binding records a source artifact, source JSON pointer, target JSON pointer, and one allowed operation:

| Operation | Meaning |
| --- | --- |
| `copy` | Preserve a canonical value byte-for-value after JSON decoding. |
| `allowlist` | Retain named fields without changing their values. |
| `stable-sort` | Order a set by an explicit stable key; no ranking formula. |
| `select` | Retain explicitly named history periods, future horizons, nodes, edges, or a canonical meeting selection. |
| `redact` | Remove fields or topology under the selected distribution profile. |

No other operation is valid. In particular, the panel does not sum weights, calculate gaps or variance, fill missing forecasts, classify status/readiness, aggregate counts, infer relations, select conditional/rework branches, or reconnect topology.

## View and section identity

View IDs and section IDs are stable machine identifiers. Labels come from the locale catalog and never replace IDs.

| View | Default mode | Frozen section IDs |
| --- | --- | --- |
| `project-lead` | `quantitative-progress` | `pl-status-strip`, `pl-progress-summary`, `pl-progress-trend`, `pl-workstream-comparison`, `pl-flow`, `pl-roadmap-variance`, `pl-source-lineage` |
| `fde-morning` | `quantitative-progress` | `fde-meeting-readiness`, `fde-window-delta`, `fde-blockers-commitments`, `fde-flow-window`, `fde-source-lineage` |
| `business-biweekly` | `quantitative-progress` | `biz-meeting-readiness`, `biz-status-drivers`, `biz-next-period-progress`, `biz-decisions`, `biz-flow-spine`, `biz-roadmap-readiness`, `biz-source-lineage` |

Every view declares both `quantitative-progress` and `flow-progress`. The modes change presentation only. They do not create a fourth audience view or a second data model.

## Selection contract

- History selection is an ordered list of immutable `program_status_snapshot_id` values. Selection fails closed when a requested snapshot is absent; a missing optional predecessor may degrade period comparison but may not be represented as zero.
- Future selection is an ordered list of canonical forecast `horizon_date` values already present in progress v2. Missing dates are not filled from planned dates.
- Project-lead flow selection names canonical node and edge IDs from one `flow_graph_id`; edges survive only when both endpoints survive.
- FDE and business selections copy the scenario distillate's `flow_selection_id`, scope, nodes, edges, state, and allocations. The panel may crop further by an explicit allowlist but may not widen the meeting-pack selection.
- A selection records its parent canonical identity and stable selection identity. A changed history set, future set, meeting pack, flow graph, or flow scope must change the panel model identity.

## Identity layers

Identity input uses UTF-8 canonical JSON with lexicographically sorted object keys, contract-declared sets sorted by stable ID, no insignificant whitespace, and SHA-256 serialized as `sha256:<64 lowercase hex>`.

- `layout_id` owns presentation geometry inputs only: panel layout contract version, `topology_id`, structural `layout_scope_id` values derived from selected node/edge IDs, locale, distribution profile, fixed node-dimension version, ELK version/license/SHA-256, and ELK configuration hash. Canonical meeting `flow_selection_id` may change with graph state; it does not move layout when the selected topology is unchanged. Status, confidence, history values, and overlay counts are excluded.
- `panel_model_id` owns selected canonical content and source identities: status snapshot, roadmap fingerprint, flow graph identity layers, meeting pack IDs, history snapshot IDs, future horizons, flow selections, locale, distribution profile, redaction manifest, and exact allowlisted model data. It excludes generated coordinates and artifact timestamps.
- `panel_id` owns `panel_model_id`, `layout_id`, schema/generator version, source fingerprints, and audit IDs. A layout change therefore changes both `layout_id` and `panel_id` without changing canonical graph identities.

The same normalized inputs and explicit generation timestamp yield the same model and all IDs. A status, flow state/overlay, meeting pack, selected history/future horizon, distribution profile, or scope change cannot collide. A topology or flow-scope change also changes layout identity. Locale, node dimensions, ELK version/hash/config, and distribution layout inputs never alter canonical source identities.

## Manifest and artifact contract

The embedded manifest and immutable panel bundle repeat and agree on:

`panel_schema_version`, `panel_model_id`, `panel_id`, `generated_at`, `as_of`, `reporting_period`, `baseline_revision`, `program_status_snapshot_id`, `roadmap_fingerprint`, `topology_id`, `state_snapshot_id`, `overlay_snapshot_id`, `flow_graph_id`, meeting pack IDs, history snapshot IDs, future horizons, flow selection IDs, source fingerprints, input/artifact audit IDs, locale/fallback metadata, generator version, `layout_id`, ELK/layout resource metadata, distribution profile, redaction manifest, and recovery status.

Phase 7 publication must first create the immutable bundle JSON idempotently, then use one atomic replace of `views/management-panel/index.html` as the current-view commit point. Optional HTML archives use `panel_id`; same ID with different bytes is a collision. Those write mechanics are not implemented in phase 6.

Roadmap phase 8 adds an external, read-only `adp-state-audit` boundary: a sealed pre-render audit ID joins `input_audit_ids`, and a post-render audit validates the staged bundle and HTML before the phase 7 commit mechanics run. The post-render audit remains external to the manifest so artifact bytes do not depend circularly on their own audit hash.

## Localization

`assets/panel-locale-catalog-v1.json` freezes locale-independent view/mode/section IDs and locale-specific labels. Supported locales are `zh-CN` and `en`; unsupported locales deterministically fall back to `zh-CN`, set `locale_fallback: true`, preserve the requested locale, and add `panel.locale.fallback` as a degraded finding. Locale changes presentation identity, never source values or rule IDs.

## Safe embedding

The phase 7 renderer must serialize model JSON, then escape `<`, `>`, `&`, U+2028, and U+2029 before placing it in a non-executable JSON script element. This makes every case-insensitive `</script` sequence inert. Source text reaches HTML/SVG only through DOM `textContent`; SVG uses allowlisted elements and attributes and forbids `foreignObject`, event attributes, external `href`, and source-provided CSS. URL hash state is versioned and allowlisted and never stores source text or absolute paths.

## Distribution redaction

`internal-full` retains allowlisted canonical lineage. `shareable-summary` applies a field allowlist before embedding. It removes internal node/edge IDs, owners, scoped counts, source paths, source fingerprints, and source payloads; retained topology receives deterministic public IDs. A hidden node removes all incident edges. The redactor never creates a replacement edge, so hidden paths cannot appear complete. The manifest exposes profile, policy version, removed field names, and hidden node/edge/source/count totals without exposing removed values.

## Recovery

Recovery is deterministic and never reads lower-level facts to compensate:

| Finding | Disposition | Recovery |
| --- | --- | --- |
| Missing/old/invalid program status or progress schema | blocked | `adp-state-audit`, `adp-program-status` |
| Roadmap snapshot/progress mismatch | blocked | `adp-roadmap-sync` |
| Missing/old/mismatched flow graph | blocked | `adp-flow-graph` |
| Missing/stale/scenario-mismatched meeting pack | degraded or blocked by required view | `adp-meeting-pack` |
| Requested history or future horizon absent | degraded | `adp-program-status` |
| Unsupported locale | degraded with catalog fallback | no fact workflow |
| Input audit blocked or source identity mismatch | blocked | `adp-state-audit`, then affected canonical producers in dependency order |

Recovery workflow order is `adp-state-audit`, `adp-program-status`, `adp-roadmap-sync`, `adp-flow-graph`, `adp-meeting-pack`, `adp-management-panel`. Reusing an old panel or parsing baseline/WDR/action/risk sources is forbidden.
