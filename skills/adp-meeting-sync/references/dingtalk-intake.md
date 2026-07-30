# DingTalk Meeting Intake

Use this branch only when raw meeting content was not supplied and DingTalk access is available. `{skill-root}` is the installed `adp-meeting-sync` directory, `{project-root}` is the explicit target project, and `{memory-root}` is the already resolved ADP memory root. Prefer `uv run`; use the same command with Python 3.10+ when `uv` is unavailable.

Discover candidates through the deterministic pre-pass rather than listing minutes by hand:

```bash
uv run "{skill-root}/scripts/dingtalk_intake.py" "{project-root}" --memory-root "{memory-root}" -o <intake.json>
```

Pass only user-supplied hints: `--query <text>` for the provider's minutes search term and `--start <time>` / `--end <time>` for its time-window filters. For example:

```bash
uv run "{skill-root}/scripts/dingtalk_intake.py" "{project-root}" --memory-root "{memory-root}" --query <text> --start <time> --end <time> -o <intake.json>
```

Show likely unprocessed candidates, and confirm the selection unless the caller supplied an exact `--task-uuid`.

For an exact meeting, rerun the pre-pass with `--task-uuid <id>`. It fetches meeting info and paginated transcription, saves the raw transcript under ADP memory, and reports transcript completeness. If no complete transcript is returned, ask for raw meeting content; never classify from the AI Minutes summary alone.

Record the source in the sync plan with both task identity and evidence type, for example `DingTalk AI Minutes taskUuid=<id>; evidence=transcription`.
