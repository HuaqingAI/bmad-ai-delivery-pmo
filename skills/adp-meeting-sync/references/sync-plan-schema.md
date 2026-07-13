# Sync Plan Schema

Use this when drafting or validating an ADP meeting sync plan. Keep user-facing notes in `{communication_language}`; JSON field names and classification values stay exactly as shown.

The plan root is a JSON object with `meeting` and `items`. The script validates structure and destinations; the model must classify business meaning before the script runs.

```json
{
  "meeting": {
    "meeting_instance_id": "Optional stable caller ID; generated deterministically when omitted",
    "date": "YYYY-MM-DD",
    "started_at": "2026-07-10T09:00:00+08:00",
    "ended_at": "2026-07-10T09:30:00+08:00",
    "type": "FDE internal sync",
    "title": "Short title",
    "source": "pasted notes, transcript, or file path",
    "raw_evidence_path": "path to raw transcript or notes, when available",
    "raw_evidence_label": "transcription",
    "participants": ["Name"],
    "participant_gaps": ["Unresolved speaker label or uncertain participant, when applicable"],
    "summary": "One paragraph",
    "lineage": {
      "meeting_pack_id": "Stable id from the meeting-pack distillate",
      "meeting_pack_path": "Path to the source meeting pack Markdown",
      "scenario": "fde-morning or business-biweekly",
      "audit_path": "Path to the audit consumed by the meeting pack",
      "roadmap_version": "Roadmap generated_at value, unavailable, or not-applicable",
      "program_status_snapshot_id": "Canonical program-status snapshot ID",
      "baseline_revision": 2,
      "source_fingerprints": {"plans/program-baseline.md": "sha256:..."},
      "input_audit_id": "Input audit consumed by program-status",
      "generator_version": "Meeting-pack generator version"
    }
  },
  "items": [
    {
      "id": "M-001",
      "classification": "action",
      "text": "What was said or decided",
      "affected_workstreams": ["workstream-id"],
      "owner": "Name",
      "due": "date or trigger",
      "closure_criteria": "Observable deliverable or condition that closes an action",
      "status_confirmation": "Evidence that a past-due action is still open, done, or cancelled",
      "decision_type": "FDE internal decision",
      "confirmer": "Name",
      "status": "open",
      "wdr_update": "Project-level WDR text when applicable",
      "no_op_reason": "Required for no_op",
      "owner_gap": "Why the owner is not a resolved accountable person, when applicable",
      "closure_gap": "Why the closure criteria are not observable or verifiable, when applicable",
      "confirmer_gap": "Why the confirmer is not resolved, when applicable",
      "speaker_label_gap": "Raw speaker labels still affecting this item, when applicable",
      "gap": "Other item-level closure gap, when applicable",
      "milestones": [
        {
          "milestone_id": "Exact baseline milestone ID",
          "status": "planned | at-risk | done | blocked",
          "forecast": "YYYY-MM-DD",
          "actual": "YYYY-MM-DD",
          "evidence": ["Traceable source anchor"],
          "baseline_revision": 2
        }
      ],
      "packet": {
        "background": "Why this needs business decision",
        "decision_needed": "Question to decide",
        "options": ["Option A", "Option B"],
        "recommendation": "Recommended answer, if any",
        "risks_tradeoffs": "Impact of the choice",
        "deadline": "date or trigger",
        "confirming_owner": "Business owner"
      }
    }
  ]
}
```

Rules:

- Every item needs `id`, `classification`, and `text`.
- `meeting.lineage` is optional for standalone syncs. For meeting-pack runs, pass `--meeting-pack-distillate`; the writer extracts and verifies all ten lineage fields, while the plan supplies actual `started_at` / `ended_at` timestamps.
- `meeting_instance_id` identifies the actual meeting, not a generated pack. Reusing it with the same canonical plan is an idempotent replay; reusing it with a changed plan is a conflict.
- The writer records `plan_fingerprint`, an applied receipt, and deterministic destination markers. Only an applied receipt may advance `meetings/cursors/<scenario>.json`; dry-run and meeting-pack generation never advance it.
- `no_op` needs `no_op_reason`.
- `business_decision_needed` needs `packet.decision_needed`.
- `action` needs a specific owner, `affected_workstreams`, due trigger, and observable `closure_criteria`. The model records semantic uncertainty in `owner_gap` or `closure_gap`; the writer recognizes only missing values and exact `TBD` placeholders.
- Keep one canonical action for the same source and action. Use `affected_workstreams` for shared impact; split only when owner, due trigger, or deliverable differs, and route multi-workstream actions once through `program`.
- Past-due action backfills need `status_confirmation`; otherwise the writer calibrates them to `blocked` with a status-confirmation gap instead of defaulting to `open`.
- A milestone handoff needs exactly one affected workstream, an exact baseline milestone ID, canonical status, traceable evidence, and a positive baseline revision copied from lineage or stated on the milestone. Invalid milestone updates stay visible as gaps and are not sent to status-sync.
- Use `TBD` only for a real unresolved gap, and pair it with the narrowest gap field the script can surface.
