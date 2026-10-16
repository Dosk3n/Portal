# Technical reference

Detailed operating notes for Portal. For an introduction and the simplest installation, see the [README](../README.md).

A personal HTML report library with immediate API imports, manual uploads, full-text search, collections, tags, and controlled public snapshots. Agents create reports wherever they run and submit the finished HTML or ZIP package.

## Run locally

Requires Docker Compose v2. No npm or pip dependencies are needed by the application.

```sh
mkdir -p portal-data portal-reports
docker compose up -d
```

Open <http://localhost:8769>. The service binds only to loopback. Settings contains the generated, retrievable API key and the optional folder importer. There is no application login: remote installations must put the private UI behind their existing authentication proxy.

The default container UID/GID is 1000. If your host account differs, set `PUID` and `PGID` in a private `.env`, or run Compose with `PUID=$(id -u) PGID=$(id -g)` on Unix. The host portal-data/portal-reports folders must be writable by that account; the application data directory is restricted to its owner.

Run without Docker on Linux/macOS with Python 3.12+ and SQLite FTS5:

```sh
python3 app/server.py --data-dir ./data --storage-dir ./storage
```

To build from source instead of using the published image, run these commands from the repository root:

```sh
mkdir -p portal-data portal-reports
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build -d
```

## Submit reports

Upload `.html`, `.htm`, `.xhtml`, or `.zip` in the browser, or send a request with the Settings API key:

```sh
curl 'https://reports.example.com/api/reports' \
  -H "X-Api-Key: $PORTAL_API_KEY" \
  -F 'file=@report.zip' \
  -F 'topic=Research' \
  -F 'source=Agent' \
  -F 'tags=ideas,comparison'
```

Each submission creates a new private report and returns `id`, `url`, and `reader_url`. Browser links require proxy authentication; keyed clients can retrieve content using `/api/reports/{id}/files/{path}`. Optional metadata fields are `title`, `source`, `topic` (collection), `date` (`YYYY-MM-DD`), and comma-separated `tags`. API documentation is returned by `/api/docs` with the same key. Regenerating the key immediately invalidates the old one; the key is stored in SQLite rather than environment configuration.

For a single-file report, embed CSS/JavaScript and optionally images directly. For a bundle, use relative paths:

```text
index.html
assets/
  image.jpg
  chart.svg
  style.css
```

A multi-page ZIP needs `index.html` at the root. A ZIP containing just one HTML file may use another entry name. Limits: 50 MB uploaded, 200 MB expanded, 2,000 archive entries. Links to assets on another computer are not reconstructed. Hosted dependencies still require their providers. HTML scripts work inside an opaque-origin sandbox; browser storage and some APIs are unavailable. Never embed API keys or other credentials in reports.

## Sharing

Open a report and select **Share externally**. Choose an expiry in days (or no expiry), and optionally a password. Every report asset is subject to the same password, expiry, and revocation checks. Share passwords use a salted PBKDF2 hash; the API key intentionally remains retrievable.

The Shares page lists snapshots, including expired links. **Update snapshot / access** can replace the report while keeping its URL and, unless explicitly changed, its password and expiry. A failed replacement preserves the old snapshot. Revocation removes its metadata and files. Expiry blocks access but leaves the snapshot available for renewal or revocation. Previously downloaded copies cannot be recalled.

## Folder imports

Watching is disabled by default. Enable it in Settings and add one or more absolute, container-visible paths. Remove a location and add another to change paths. Previously imported reports are unaffected. Locations cannot overlap each other or application-managed storage.

Inbox entries can be a standalone HTML file, ZIP bundle, or a folder containing one complete report package. For HTML plus assets, put all files in a separate submission folder; loose neighbouring images are not consumed. The importer checks every 5 seconds, waits for at least 10 seconds of unchanged size/mtime, validates and archives the submission, then removes its input. Failed imports stay in place with their error in Settings. Write to a temporary dot-prefixed name and rename when ready if producers may pause mid-copy longer than 10 seconds. A persistent import ledger prevents an unchanged submission being imported again after a failed deletion.

## Deployment and storage

See the [setup guide](deployment.md), [Compose file](../docker-compose.yml) and [architecture](architecture.md). This repository contains no deployment hostname, private host configuration, credentials, database, or reports. Runtime paths are ignored by Git and excluded from the image build context.

## Validation

```sh
python3 -m unittest discover -s tests -v
docker run --rm -v "$PWD/app/static:/src:ro" node:22-bookworm-slim node --check /src/app.js
```

Tests cover imports, asset containment, authentication boundaries, key rotation, snapshots, passwords, expiry, replacement, revocation, watcher consumption, and failed-import retention. Browser validation should also exercise uploads, report scripts, password-protected assets, settings, and mobile layout.

## Source provenance

The initial UI stylesheet and HTML metadata extraction were adapted from the HTML report portal supplied as the starting reference. The import/storage/API/sharing application and interface flows were developed for this project. Upstream redistribution rights should be confirmed before making adapted assets public; no new license is asserted over those materials. The current logo and browser icon use a native SVG redraw inspired by the supplied Portal artwork; see [branding](branding.md).

### Import validation and watcher activity

Empty HTML and missing static local assets are rejected with an explanation. Bundle HTML,
stylesheets, scripts and images together using relative paths. Remote URLs are not fetched
or validated; dependencies generated dynamically by JavaScript are not preflighted.

Manual and API submissions create new reports, including intentional duplicates. Watched
folders skip identical content at the same path only when the existing report package is
available. Changed content or a different filename creates a new report. Successful imports
and duplicate skips consume the incoming file and appear in Settings → Recent imports.
Use Refresh activity to fetch the latest watcher status. Invalid inputs stay in the inbox.

Reports can be deleted from the reader after confirmation. Shared snapshots remain available
and their access settings can still be edited. Revoke those links separately when needed.
