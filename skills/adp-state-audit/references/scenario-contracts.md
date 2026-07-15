# Scenario Contracts

Load only the branch that matches the current audit. `audit_state.py` and `panel_audit.py` own their deterministic checklists; their canonical findings, `execution_disposition`, and recommendations are authoritative.

## Status-Sync Intake

When an input audit encounters `intake/status-sync`, executable state comes only from parsed control fields. Malformed JSON fails closed. A non-empty executable updates payload remains blocked until a versioned receipt in `receipts/status-sync/` binds the exact input path and raw bytes. For a migration receipt, re-read its hashed evidence as JSON and require the original successful non-dry-run report itself to declare matching root-level `input_path` and `input_hash`; wrapper or attestation projections never satisfy that proof. `attested_by` is attribution only. Structured dry-run, disabled, proposal, preview, candidate, or superseded payloads, including `superseded: true`, are non-executable.

## Flow-Graph Artifact

When an artifact transaction includes `views/flow-graph.json`, validate it with its sealed input audit:

```bash
uv run "{skill-root}/scripts/audit_state.py" "{project-root}" --phase artifact --input-audit-json <input-audit.json> --artifact <views/flow-graph.json> --output-dir "{workflow.audit_output_path}"
```

Append `--run-folder-pattern "{workflow.run_folder_pattern}"` when the resolved pattern is non-empty. A blocked result forbids publication; the validator never repairs current or snapshot files.

## Management Panel

Seal the canonical input bundle before render:

```bash
uv run "{skill-root}/scripts/audit_state.py" "{project-root}" --scenario management-panel --panel-input-bundle <canonical-inputs.json> --output-dir "{workflow.audit_output_path}"
```

After render, validate the panel model, sealed audit, and HTML; include the same input bundle when available:

```bash
uv run "{skill-root}/scripts/audit_state.py" "{project-root}" --phase artifact --scenario management-panel --panel-model <panel-id.json> --input-audit-json <panel-input-audit.json> --panel-input-bundle <canonical-inputs.json> --artifact <panel.html> --output-dir "{workflow.audit_output_path}"
```

Append `--run-folder-pattern "{workflow.run_folder_pattern}"` to each command when the resolved pattern is non-empty. `blocked` forbids compose or publication; `degraded` evidence stays visible in the recovery state. The validators write only immutable audit records and never rewrite panel inputs or artifacts.
