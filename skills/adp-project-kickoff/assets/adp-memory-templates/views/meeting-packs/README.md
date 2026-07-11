# Meeting Packs

This folder holds `adp-meeting-pack` outputs.

Meeting packs are pre-meeting preparation views. They are not sources of truth. After the meeting, sync actual outcomes through `adp-meeting-sync` and `adp-status-sync` so durable ADP state is updated.

Expected scenarios:

```text
views/meeting-packs/fde-morning/{date}.md
views/meeting-packs/business-biweekly/{date}.md
```

Every pack should include source inventory, action or decision boards with source links, audit caveats, and a post-meeting sync checklist.
