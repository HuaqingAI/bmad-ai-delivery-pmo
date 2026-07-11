# Sync Plan Schema

Use this when drafting or validating an ADP meeting sync plan. Keep user-facing notes in `{communication_language}`; JSON field names and classification values stay exactly as shown.

The plan root is a JSON object with `meeting` and `items`. The script validates structure and destinations; the model must classify business meaning before the script runs.

```json
{
  "meeting": {
    "date": "YYYY-MM-DD",
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
      "roadmap_version": "Roadmap generated_at value, unavailable, or not-applicable"
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
      "confirmer_gap": "Why the confirmer is not resolved, when applicable",
      "speaker_label_gap": "Raw speaker labels still affecting this item, when applicable",
      "gap": "Other item-level closure gap, when applicable",
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
- `meeting.lineage` is optional for standalone syncs. When present, all five lineage fields are required and must be copied from the meeting-pack distillate without reinterpretation.
- `no_op` needs `no_op_reason`.
- `business_decision_needed` needs `packet.decision_needed`.
- `action` needs a specific owner, affected workstream route, due trigger, and observable `closure_criteria`; generic owners such as `TBD`, `各条线 FDE owner`, or `参会人员` stay as gaps and do not become open ledger actions.
- Past-due action backfills need `status_confirmation`; otherwise the writer calibrates them to `blocked` with a status-confirmation gap instead of defaulting to `open`.
- Use `TBD` only for a real unresolved gap, and pair it with the narrowest gap field the script can surface.
