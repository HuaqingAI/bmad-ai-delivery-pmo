# Program Status Snapshots

This directory is owned by `adp-program-status`. Each `<snapshot-id>.json` file is an immutable reporting-period result with baseline revision, source fingerprints, input audit ID, locale, and generator version.

Kickoff creates only this guidance file. It never creates a synthetic snapshot or a `latest.json` pointer. Re-running kickoff preserves every existing snapshot.
