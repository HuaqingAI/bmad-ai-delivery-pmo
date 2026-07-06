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
    "summary": "One paragraph"
  },
  "items": [
    {
      "id": "M-001",
      "classification": "action",
      "text": "What was said or decided",
      "affected_workstreams": ["workstream-id"],
      "owner": "Name",
      "due": "date or trigger",
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
- `no_op` needs `no_op_reason`.
- `business_decision_needed` needs `packet.decision_needed`.
- Use `TBD` only for a real unresolved gap, and pair it with the narrowest gap field the script can surface.
