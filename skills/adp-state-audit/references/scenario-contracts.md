# Scenario Contracts

Load only the branch that matches the current audit. `audit_state.py` and `panel_audit.py` own their deterministic checklists; their canonical findings, `execution_disposition`, and recommendations are authoritative.

Runtime context is supplied by the entry workflow: `{skill-root}` is the installed `adp-state-audit` directory, `{project-root}` is the explicit target project, `{memory-root}` is the already resolved ADP memory root, and `{workflow.audit_output_path}` / `{workflow.run_folder_pattern}` are already resolved customization values. Carry the same project and memory roots through every command below; this reference does not discover or replace them.

## Status-Sync Intake

When an input audit encounters `intake/status-sync`, executable state comes only from parsed control fields. Malformed JSON fails closed. A non-empty executable updates payload remains blocked until one exact terminal proof binds its path and raw bytes: a successful receipt in `receipts/status-sync/`, a governed legacy non-atomic `partial-closure` receipt in `receipts/status-sync-partial-closure/`, or a whole-intake `intake-retirement` receipt in `receipts/status-sync-retirement/`. Partial closure must contain non-empty reconciled and retired command partitions, exact ordinal coverage, a verified canonical-fact snapshot, principal and justification, and the no-replay policy; it closes the intake without becoming execution lineage. For migration evidence, re-read the hashed JSON. Accept either the original successful non-dry-run report with direct root-level `input_path`/`input_hash`, or the canonical durable `receipt_type: execution` receipt at `receipts/status-sync/<execution_id>.json` with exact lifecycle, count, hash, path, and `applied_at`; wrapper or attestation projections never satisfy proof. A `historical-input-change` migration additionally re-hashes its preserved original-byte snapshot, verifies the evidence against the old hash and logical path, recomputes both payload IDs and the canonical executable diff, and accepts current bytes only when executable commands are unchanged. This permits a byte-only LF-to-CRLF change while rejecting executable JSON changes. `attested_by` is attribution only. Structured dry-run, disabled, proposal, preview, candidate, or rejected payloads remain non-executable. A mutable `superseded: true` or `status: superseded` marker is still executable until a durable terminal receipt binds it; successor intake or receipt hashes are revalidated so tampering reopens the original intake. Meeting-sync successors are valid only when an applied non-dry-run receipt binds the exact generated intake, stable meeting/plan identity, durable daily-log/WDR writes, and a fully verified canonical action/fact scan.

## Flow-Graph Artifact

When an artifact transaction includes `views/flow-graph.json`, validate it with its sealed input audit:

```bash
uv run "{skill-root}/scripts/audit_state.py" "{project-root}" --memory-root "{memory-root}" --phase artifact --input-audit-json <input-audit.json> --artifact <views/flow-graph.json> --output-dir "{workflow.audit_output_path}"
```

Append `--run-folder-pattern "{workflow.run_folder_pattern}"` when the resolved pattern is non-empty. A blocked result forbids publication; the validator never repairs current or snapshot files.

## Management Panel

Seal the canonical input bundle before render:

```bash
uv run "{skill-root}/scripts/audit_state.py" "{project-root}" --memory-root "{memory-root}" --scenario management-panel --panel-input-bundle <canonical-inputs.json> --output-dir "{workflow.audit_output_path}"
```

After render, validate the panel model, sealed audit, and HTML; include the same input bundle when available:

```bash
uv run "{skill-root}/scripts/audit_state.py" "{project-root}" --memory-root "{memory-root}" --phase artifact --scenario management-panel --panel-model <panel-id.json> --input-audit-json <panel-input-audit.json> --panel-input-bundle <canonical-inputs.json> --artifact <panel.html> --output-dir "{workflow.audit_output_path}"
```

Append `--run-folder-pattern "{workflow.run_folder_pattern}"` to each command when the resolved pattern is non-empty. `blocked` forbids compose or publication; `degraded` evidence stays visible in the recovery state. The validators write only immutable audit records and never rewrite panel inputs or artifacts.
