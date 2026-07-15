# BMM Checkpoint Candidate Lifecycle

Use this contract for `discover` and `confirm` in `adp-bmm-checkpoint-sync`. Resolve `{project-root}` to the target project and `{skill-root}` to this installed skill. Conversation follows the resolved `communication_language`; candidate facts and canonical fields are not translated.

## Candidate Contract

Discover writes candidates under:

```text
_bmad-output/adp/memory/intake/bmm-checkpoints/
  index.jsonl
  candidates/
    {candidate-id}.json
    {candidate-id}.preview.md
```

Candidate status is `discovered`, `confirmed`, `applied`, `superseded`, or `dismissed`. Its stable id derives from `workstream_id`, `checkpoint`, `source_scope_key`, `source_revision`, and normalized discovered claims. Repeated discovery of the same revision and claims returns the existing candidate. A new revision for the same scope supersedes the old active candidate.

`source_prepass` carries deterministic frontmatter, sections, tables, JSON fields, paths, and line refs; these parsed facts are not decisions, risks, readiness gaps, or project implications until confirm classifies them. Authority is explicit through `authority.asserted_by`, `authority.authority_scope`, `authority.affected_workstreams`, `authority.required_confirmers`, and `authority.confirmation_state`; cross-line impact stays `cross-line-pending` until its required confirmer is recorded.

`claims.actions` rows carry `owner`, `workstream`, optional `affected_workstreams`, `action`, `source`, `reason`, `due_or_trigger`, `status`, `closure_criteria`, and `owning_workflow`. `claims.next_actions` is only a free-form WDR/daily summary and never creates action-ledger intake.

## Discover

Run discover when the user supplies a BMM/TEA artifact or asks to sync without a candidate:

```bash
uv run "{skill-root}/scripts/sync_bmm_checkpoint.py" discover "{project-root}" --workstream-id <workstream-id> --checkpoint <checkpoint> --artifact <key=path-or-url> --summary "<project-level summary>"
```

Use `--asserted-by`, `--authority-scope`, `--affected-workstream`, `--required-confirmer`, and `--dry-run` only when supported by the input. Return the confirmation checklist with selected and ignored artifacts, authority scope, review paths, and confirm/dismiss commands, then stop for explicit confirmation. Headless callers use `confirmation_required: true` as the next-state signal. Discovery binds only the first existing artifact unless the script explicitly supports multi-source; use `packet-sync` for multi-source baseline packets.

Prefer sources in this order:

| Checkpoint | Prefer |
| --- | --- |
| `prd` | `SPEC.md + .memlog.md`, then `prd.md + .memlog.md`, `brief.md + .memlog.md`, `prfaq-*.md + distillate` |
| `architecture` | `ARCHITECTURE-SPINE.md + .memlog.md`, then reviewer outputs |
| `epic-story` | `epics.md`, story files, `sprint-status.yaml`, readiness reports |
| `implementation` | story/spec file, review findings, `deferred-work.md`, `sprint-status.yaml`, test summaries |
| `validation` | `gate-decision.json`, `e2e-trace-summary.json`, trace matrix, NFR/test review, CI artifacts |

Prefer machine-readable outputs, then `.memlog.md`, stable document sections, and owner supplementation last.

## Confirm

Confirm only project-level facts the owner can assert: scope, impacts, gaps, required confirmers, business confirmation, and next actions. Do not convert a parsed line into cross-line confirmed truth.

```bash
uv run "{skill-root}/scripts/sync_bmm_checkpoint.py" confirm "{project-root}" --candidate-id CHK-... --decision confirm --confirmed-by "<owner>" --override authority.confirmation_state=confirmed-local
```

Use `--override path=value` for corrections. Repeating the same confirmation is a no-op; different overrides append an event. Use `--decision dismiss` to reject. Replace action payloads as whole fields, preferably with `--overrides-file` containing `{"claims.actions":[...]}`.
