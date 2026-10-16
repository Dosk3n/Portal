# Portal

Personal HTML report library. Python standard library and SQLite; plain browser JavaScript.

- Keep application state in `/data` and report packages/snapshots in configured storage.
- Public `/api/` routes always require the database API key. Browser routes use `/internal/` behind an authentication proxy; never expose the key to report HTML.
- Render all report HTML with an opaque-origin sandbox, including private reports.
- Manual/API submissions create a new report unless an explicit revision is requested. Revisions keep the report ID and retain the configured number of versions (default three total). Watched same-path identical content may be consumed as a visible duplicate skip only after verifying the existing package. Shares are independent snapshots, replaced only explicitly.
- Watching is disabled by default. Consume input only after successful durable import; never watch managed storage or recursively follow symlinks.
- Never commit runtime data, reports, credentials, or share tokens.
- Run `python3 -m unittest discover -s tests` and browser checks before publishing a release.
- Source and GHCR packages are public, as authorized by the user. Inspect remote state before publishing; preserve existing data.
