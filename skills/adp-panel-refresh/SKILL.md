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

Resolve the project and one memory root, then carry both unchanged through every operation. Route explicit `policy`, `detect`, `plan`, `apply`, `abandon`, `prune`, or `inspect` requests. For a first or general refresh, run `policy -> detect -> plan -> apply -> inspect`; later runs reuse the last validated policy unless the caller supplies a replacement. A first `policy` call may stage audited status, roadmap, and flow projections and return `status: awaiting-policy`; review its directly reusable policy file, validate it with the returned command, then apply the same `resume_plan_path`. If detect returns `resume_plan_path`, apply that exact plan instead of creating a replacement.

## Operations

- Bootstrap or validate policy: `uv run {skill-root}/scripts/panel_refresh.py policy {project-root} --memory-root <path> [--selection-policy <policy.json>] [--as-of YYYY-MM-DD] [--period-start YYYY-MM-DD --period-end YYYY-MM-DD] [--fde-period-start YYYY-MM-DD --fde-period-end YYYY-MM-DD] [--force-full]`
- Detect: `uv run {skill-root}/scripts/panel_refresh.py detect {project-root} --memory-root <path> [--selection-policy <policy.json>]`
- Plan: `uv run {skill-root}/scripts/panel_refresh.py plan {project-root} --memory-root <path> --as-of YYYY-MM-DD [--selection-policy <policy.json>] [--period-start YYYY-MM-DD --period-end YYYY-MM-DD]`
- Apply or resume: `uv run {skill-root}/scripts/panel_refresh.py apply {project-root} --memory-root <path> --plan <durable-plan.json> [--selection-policy <policy.json>]`
- Abandon a replaced run: `uv run {skill-root}/scripts/panel_refresh.py abandon {project-root} --memory-root <path> --plan <dirty-plan.json> --reason <text>`
- Preview or apply cleanup: `uv run {skill-root}/scripts/panel_refresh.py prune {project-root} --memory-root <path> --dry-run [selectors]`; replace `--dry-run` with `--apply-prune` only after reviewing the selection.
- Inspect: `uv run {skill-root}/scripts/panel_refresh.py inspect {project-root} --memory-root <path> [--selection-policy <policy.json>]`

`policy` writes a candidate artifact from canonical `views/flow-graph.json`, `views/program-status.json`, and `snapshots/program-status/*.json`. When those projections do not exist, it executes the audit-through-flow prefix in durable staging and uses the optional program/FDE period and `--force-full` arguments for that bootstrap plan. It persists the run as `awaiting-policy` and returns both the candidate and exact resume plan; no projection or Panel is published at this pause. The candidate lists exact flow graph, history, scope, node, and edge IDs plus a structurally valid all-visible starting policy marked `review_required`; the caller chooses the intended history/project/shareable selections and supplies that JSON back to `policy` for validation. Validation reuses the low-level Management Panel selection-policy contract, stores a content-addressed durable copy, and resumes the same run at meeting-pack generation.

`detect` returns the pointed active run. A terminal pointer returns no resume and never scans older dirty plans. Only an absent `current_run_id` permits fallback to the sole nonterminal run; multiple candidates fail with `REFRESH_RESUME_AMBIGUOUS`. `plan` freezes live fact fingerprints, invalidations, dates/windows, policy SHA, and retry state. Policy inheritance comes only from the last published receipt. Replaced nonterminal plans become `superseded` even when only parameters changed; compact evidence remains while the full memory clone is removed. A first run freezes policy at the `awaiting-policy` checkpoint.

`apply` blocks while typed status intents remain pending. It rebuilds projections in durable staging, verifies facts did not change during the run, rejects any producer fact write, and switches publishable outputs through one rollback-capable publication journal. Status-sync execution, partial-closure, and retirement receipts plus their transitive migration originals/evidence and retirement successor receipts are source-bound audit inputs. A pre-contract workspace, or a current-v2 workspace whose older plan omitted newly discovered closure dependencies, is automatically superseded and replanned rather than resumed with incomplete evidence. If already-bound closure files are missing from an existing workspace, staging atomically rehydrates them, archives stale node results, and restarts from state audit. Policy freshness always rechecks the durable policy content ID. A plan whose Flow Graph node is already completed revalidates that policy against the same run's staged graph, bound by refresh ID, workspace plan ID, node result path, and Flow Graph identity; it never falls back to an older live graph merely because one remains published. Plans that do not rebuild Flow Graph, including policy-only or Management-Panel-only runs, validate against the live canonical graph. Prepublication binds source coverage to this plan's completed state-audit result and binds Panel safety to the exact `panel_input_audit` and `panel_artifact_audit` returned by this plan's Management Panel node; it never selects either by newest-path guessing. Source digests accept bare or `sha256:`-prefixed 64-hex serialization. Every source declared by the state audit must exist in the plan inventory and match after normalization, while plan-only sources do not stale the audit. A blocked audit, real source mismatch, action drift, pending intent, `safe_to_render=false`, or `safe_to_publish=false` blocks publication. A degraded Panel remains publishable when its audits have no blocking findings and explicitly remain safe to render and publish; its degraded disposition, warning findings, recovery status, and workflows stay unchanged in the Panel and publication receipt. A failed node preserves the current Panel and records `retry_from_instance_key`; rerun the same plan to resume. Publication clears global `retry_from_instance_key`, `last_error`, and `pending_invalidations`, preventing stale recovery state.

`inspect` reports `artifact_integrity`, `business_freshness`, `publication_eligibility`, source and policy change, pending intent IDs, drift count, the receipt-bound state/Panel audit readiness, Panel ID, interrupted-plan path, and owning recovery workflow. An explicit `--selection-policy` checks freshness against that caller-owned policy; otherwise inspect uses the current durable policy. A completed update requires artifact integrity pass, fresh bound sources and policy, zero pending intents, zero selected drift, valid receipt integrity, and receipt-bound audits that are either ready or contract-safe degraded. Inspect preserves degraded readiness.

Follow the staging, evidence, abandon, prune, orphan, and budget contract in `references/staging-lifecycle.md`. Never manually delete a referenced workspace or edit a run into a terminal state.

When audit returns repair batches, process their exact `repair_batch_id` and `action_ids` through `adp-status-sync` one batch at a time. Stop on the first failure, re-plan that batch from current facts, then run `detect -> plan -> apply -> inspect` again. Never edit WDR current fields, projections, Panel HTML, or current pointers manually.

## Headless Result

Return the script JSON unchanged. A blocked result preserves `error_code`, `error`, and `retry_from_instance_key`; do not reinterpret it as a partial success.
