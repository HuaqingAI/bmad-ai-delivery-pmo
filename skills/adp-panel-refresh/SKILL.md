---
name: adp-panel-refresh
description: Orchestrates source-to-panel convergence. Use when the user says "refresh ADP panel", "update management panel end to end", "inspect panel freshness", or "resume panel refresh".
---

# ADP Panel Refresh

## Overview

Act as the orchestration boundary above ADP fact owners and projection producers. Deliver one source-backed Management Panel generation that an operator can open without manually sequencing audit, status, roadmap, flow, meeting-pack, and panel workflows. Preserve committed facts on failure, keep the prior current Panel available, and make the retry point explicit.

## Resolution Rules

- Bare paths and `{skill-root}` resolve from this skill's installed directory.
- `{project-root}` is the target project; `{memory-root}` defaults to `{project-root}/_bmad-output/adp/memory`.
- Run `scripts/panel_refresh.py` with `uv run`; use Python 3.10+ directly when `uv` is unavailable.

## On Activation

Resolve the project and one memory root, then carry both unchanged through every operation. Route explicit `policy`, `detect`, `plan`, `apply`, or `inspect` requests. For a first or general refresh, run `policy -> detect -> plan -> apply -> inspect`; later runs reuse the last validated policy unless the caller supplies a replacement. A first `policy` call may stage audited status, roadmap, and flow projections and return `status: awaiting-policy`; review its directly reusable policy file, validate it with the returned command, then apply the same `resume_plan_path`. If detect returns `resume_plan_path`, apply that exact plan instead of creating a replacement.

## Operations

- Bootstrap or validate policy: `uv run {skill-root}/scripts/panel_refresh.py policy {project-root} --memory-root <path> [--selection-policy <policy.json>] [--as-of YYYY-MM-DD] [--period-start YYYY-MM-DD --period-end YYYY-MM-DD] [--fde-period-start YYYY-MM-DD --fde-period-end YYYY-MM-DD] [--force-full]`
- Detect: `uv run {skill-root}/scripts/panel_refresh.py detect {project-root} --memory-root <path> [--selection-policy <policy.json>]`
- Plan: `uv run {skill-root}/scripts/panel_refresh.py plan {project-root} --memory-root <path> --as-of YYYY-MM-DD [--selection-policy <policy.json>] [--period-start YYYY-MM-DD --period-end YYYY-MM-DD]`
- Apply or resume: `uv run {skill-root}/scripts/panel_refresh.py apply {project-root} --memory-root <path> --plan <durable-plan.json> [--selection-policy <policy.json>]`
- Inspect: `uv run {skill-root}/scripts/panel_refresh.py inspect {project-root} --memory-root <path> [--selection-policy <policy.json>]`

`policy` writes a candidate artifact from canonical `views/flow-graph.json`, `views/program-status.json`, and `snapshots/program-status/*.json`. When those projections do not exist, it executes the audit-through-flow prefix in durable staging and uses the optional program/FDE period and `--force-full` arguments for that bootstrap plan. It persists the run as `awaiting-policy` and returns both the candidate and exact resume plan; no projection or Panel is published at this pause. The candidate lists exact flow graph, history, scope, node, and edge IDs plus a structurally valid all-visible starting policy marked `review_required`; the caller chooses the intended history/project/shareable selections and supplies that JSON back to `policy` for validation. Validation reuses the low-level Management Panel selection-policy contract, stores a content-addressed durable copy, and resumes the same run at meeting-pack generation.

`detect` is read-only and returns the exact interrupted `resume_plan_path` and retry node when present. If the mutable status pointer is absent, it discovers the sole nonterminal durable run; multiple candidates fail with `REFRESH_RESUME_AMBIGUOUS` and require explicit `--plan`. `plan` freezes live fact fingerprints, the invalidation DAG, source date, confirmed reporting/meeting windows, validated selection-policy content SHA when available, and retry state under `state/panel-refresh/runs/`. Default policy inheritance comes only from the last successful published receipt, never from the mutable status pointer of an interrupted run. Without a published policy, a source-change replacement starts with no policy and reaches a new checkpoint after rebuilding Flow Graph. A published policy is reusable only while it matches the live canonical graph; if the replacement's staged graph changes identity, the run preserves the rejected published binding as evidence and returns to `awaiting-policy`. When a newly confirmed source, date, window, or policy binding creates a different plan, the replaced planned, dirty, refreshing, or awaiting-policy run is marked `superseded` even if its source fingerprints are unchanged; its plan, staging, errors, and policy files remain intact. A first run freezes the policy SHA at the `awaiting-policy` checkpoint; changing policy content invalidates the Panel even when source facts are unchanged.

`apply` blocks while typed status intents remain pending. It rebuilds projections in durable staging, verifies facts did not change during the run, rejects any producer fact write, and switches publishable outputs through one rollback-capable publication journal. Policy freshness always rechecks the durable policy content ID. A plan whose Flow Graph node is already completed revalidates that policy against the same run's staged graph, bound by refresh ID, workspace plan ID, node result path, and Flow Graph identity; it never falls back to an older live graph merely because one remains published. Plans that do not rebuild Flow Graph, including policy-only or Management-Panel-only runs, validate against the live canonical graph. Prepublication binds source coverage to this plan's completed state-audit result and binds Panel safety to the exact `panel_input_audit` and `panel_artifact_audit` returned by this plan's Management Panel node; it never selects either by newest-path guessing. Source digests accept bare or `sha256:`-prefixed 64-hex serialization. Every source declared by the state audit must exist in the plan inventory and match after normalization, while plan-only sources do not stale the audit. A blocked audit, real source mismatch, action drift, pending intent, `safe_to_render=false`, or `safe_to_publish=false` blocks publication. A degraded Panel remains publishable when its audits have no blocking findings and explicitly remain safe to render and publish; its degraded disposition, warning findings, recovery status, and workflows stay unchanged in the Panel and publication receipt. A failed node leaves the previous current Panel untouched and records `retry_from_instance_key`; rerun the same plan to resume completed nodes.

`inspect` reports `artifact_integrity`, `business_freshness`, `publication_eligibility`, source and policy change, pending intent IDs, drift count, the receipt-bound state/Panel audit readiness, Panel ID, interrupted-plan path, and owning recovery workflow. An explicit `--selection-policy` checks freshness against that caller-owned policy; otherwise inspect uses the current durable policy. A completed update requires artifact integrity pass, fresh bound sources and policy, zero pending intents, zero selected drift, valid receipt integrity, and receipt-bound audits that are either ready or contract-safe degraded. Inspect must report degraded as degraded rather than relabeling it ready.

When audit returns repair batches, process their exact `repair_batch_id` and `action_ids` through `adp-status-sync` one batch at a time. Stop on the first failure, re-plan that batch from current facts, then run `detect -> plan -> apply -> inspect` again. Never edit WDR current fields, projections, Panel HTML, or current pointers manually.

## Headless Result

Return the script JSON unchanged. A blocked result preserves `error_code`, `error`, and `retry_from_instance_key`; do not reinterpret it as a partial success.
