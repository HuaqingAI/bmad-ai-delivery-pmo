# ADP v1.3 Release Notes

## Roadmap Phase 10: P1 Operational Debt

This phase closes the v1.2 operational trust debt required before v1.3 module registration.

- `adp-plan-baseline` now exposes `lock-inspect` and `lock-recover`. Inspection distinguishes a live local owner, an orphan caused by a missing or PID-reused process, and an unverifiable remote owner. A stale baseline lock is removed only after revalidation and immutable recovery receipt publication under `plans/lock-recovery/`; audit failure or a changed lock leaves it in place.
- Program Lead now rejects management Markdown lineage that is missing, stale, or mismatched with canonical `program-status.json` and its immutable snapshot. The check binds snapshot, audit, baseline, source fingerprints, progress/flow contracts, locale, generator, and render profile before a readout is accepted.
- `render_program_views.py` keeps its former read-only core call shape: `project_root`, `--view`, `--memory-root`, `--as-of`, and `-o/--output`. Retired prepass/audit rendering options return deterministic JSON with `ADP-PL-LEGACY-RENDERER-MIGRATION-REQUIRED`; regenerate through `adp-program-status`, then consume through `adp-agent-program-lead`.

## Roadmap Phase 11: Kickoff, Setup and Module Validation

- The module and plugin marketplace now report `1.3.0`; `adp-flow-graph` and `adp-management-panel` are registered in lifecycle order.
- Setup adds the three panel settings and inspects every required flow/panel/status/risk schema, HTML template, locale/runtime asset, ELK `0.9.3` metadata, EPL-2.0 license, and pinned bundle/license SHA-256 before declaring an installation ready.
- Kickoff non-destructively adds empty `snapshots/flow-graph/`, `snapshots/management-panel/`, and `views/management-panel/` directories. It never creates placeholder graph JSON, panel bundles, or HTML; the first panel refresh remains gated on audited status, roadmap, flow-graph, and meeting inputs.
- Existing v1.2 config values and memory are preserved. New settings take defaults only when absent, anti-zombie config replacement removes retired keys, and legacy per-module config/help cleanup runs only after validated shared config/help publication.
- The 16 setup gates now cover marketplace/help registration, seven configuration values, schema/template/catalog installation, ELK integrity, fresh/update/headless/legacy migration, anti-zombie behavior, path safety, and cleanup safety. Kickoff tests cover fresh, update, headless, legacy memory, no-placeholder migration, and macOS canonical paths.
- Module Builder structural validation reports zero findings. Full ADP tests and workflow lint/path/script/license/hash scans are release gates; historical `.analysis` output is reported separately and is not treated as current source.
