---
name: adp-management-panel
description: Builds traceable offline ADP management panels. Use when user says "refresh ADP panel" or "archive management panel".
---

# ADP Management Panel

## Overview

This workflow composes audited ADP projections into a self-contained, `file://` management panel for project leads, FDE morning meetings, and business biweekly meetings. Act as a delivery-boundary steward: preserve upstream facts exactly, make visible conclusions traceable, and fail closed rather than invent progress, status, counts, topology, meeting scope, or branch state.

## Resolution rules

- Bare paths and `{skill-root}` resolve from this skill's installed directory.
- `{project-root}` resolves to the project working directory.
- `{memory-root}` defaults to `{project-root}/_bmad-output/adp/memory`.

## Canonical Inputs

Accept only audited, versioned inputs from their owners:

- `adp-program-status`: status snapshot and `progress_schema_version: 3.0.0`; read `by_scope`, while `by_workstream` remains a physical-only compatibility projection.
- `adp-roadmap-sync`: timeline and the same copied canonical progress object.
- `adp-flow-graph`: `flow_graph_schema_version: 1.0.0`; layout fields are forbidden.
- `adp-meeting-pack`: FDE morning and business biweekly distillates with explicit pack ID, confirmed window, readiness, lifecycle, audit identity, information budget, and canonical flow selection.
- Selected immutable program-status history snapshots and artifact metadata.
- An explicit panel selection policy bound to the current `flow_graph_id`, with ordered history snapshot IDs, project-lead scope/node/edge IDs, and the shareable node/edge allowlist chosen by the owning workflow or user.

Read `references/panel-model-contract-v1.md` for normative mapping, identity, selection, redaction, and recovery. Validate the composed model and manifest against `assets/adp-management-panel-v1.schema.json` and `assets/adp-management-panel-manifest-v1.schema.json` before any write. The fixed offline layout resource is `assets/elk-resource-v1.json`; its bundle, version, EPL-2.0 license, and SHA-256 must agree before render. Hash and embed the bundle as UTF-8 with checkout CRLF normalized to LF; every other content change fails closed.
Every binding remains one of `copy`, `allowlist`, `stable-sort`, `select`, or `redact`; layout coordinates and localized labels are presentation metadata rather than new facts.

## Operations

- `refresh` is the default. Run `uv run {skill-root}/scripts/management_panel.py {project-root} refresh --memory-root {memory-root} --selection-policy <path>`. It composes only canonical inputs, creates `snapshots/management-panel/sha256-<64hex>.json` idempotently, then commits `views/management-panel/index.html` with one atomic replace.
- `inspect` verifies the current HTML's embedded model and manifest against its immutable bundle and fixed ELK checksum without modifying artifacts. It prefers the filesystem-safe bundle and, on POSIX only, may read an exact legacy colon-named bundle when the safe path is absent.
- `archive` requires an explicit `--distribution-profile internal-full|shareable-summary` and the same selection policy. It writes immutable `sha256-<64hex>.json` and `.html` artifacts and does not replace the current panel. `shareable-summary` removes hidden topology and incident edges without reconnecting paths and records redaction totals.

The selection policy is sealed by the input audit; the script validates graph identity, ID membership, edge closure, and history order without choosing business scope or visibility. FDE and business views copy the meeting-pack `flow_subgraph`, scoped counts, and meeting metadata; packs are resolved by exact structured scenario and generation identity, never filename text or filesystem time. Never widen the information budget, choose a branch, or recompute a meeting window. Refresh reads but never advances or repairs meeting cursors. `post-sync-official` is accepted only with a matching applied receipt panel association. Missing, stale, mismatched, unsupported, or unaudited inputs return the contract's recovery workflows and write nothing.

Every refresh or archive executes the installed `adp-state-audit` panel gates. The pre-render gate seals canonical inputs and must be non-blocking before compose. The post-render gate validates the staged bundle/HTML and immutable targets before any panel publication. Results return `panel_input_audit_id`, `panel_artifact_audit_id`, and their immutable audit paths; audit code is read-only with respect to current HTML and archives. `inspect` repeats post-render validation against the published bundle and embedded manifest.

## Presentation Boundary

The only top-level views are `project-lead`, `fde-morning`, and `business-biweekly`. Each view exposes exactly two visualization modes: `quantitative-progress` and `flow-progress`. Section IDs are versioned machine identifiers from the contract; localized labels never become identifiers.

Presentation may select, stable-sort, crop, redact, localize labels, and map canonical numbers or topology to screen coordinates. It never calculates completion, variance, forecast, readiness, execution or health state, overlay counts, graph topology, relationship state, or active branches. Source text is JSON-safe embedded, then emitted through DOM text nodes; SVG is constructed from allowlisted elements and attributes only.

## Headless Result

Return the JSON emitted by `management_panel.py` unchanged. On a blocked result, preserve its stable `reason` and ordered `recommended_workflows`.
