# Architecture

Portal uses Python's standard library HTTP server and SQLite FTS5. The frontend is plain HTML, CSS, and JavaScript. One process serves requests and runs a background inbox importer. No model, agent runtime, external database, or queue service is required.

## Storage

- `/data/portal.sqlite`: catalogue, FTS index, settings, single plaintext API key, watcher configuration/import ledger, share access metadata, and hashed share sessions. SQLite WAL files stay alongside the database.
- `<storage>/reports/<uuid>/`: immutable imported HTML and assets. Original upload ZIPs are not retained.
- `<storage>/shares/<uuid>/<version-uuid>/`: independent share package. Snapshot replacement writes the new package before changing its database pointer.

Storage paths are configurable startup arguments. Filesystem paths are relative inside the catalogue, so underlying local-to-remote file movement does not change report identities. Search uses local extracted text without scanning report storage. A missing file returns an unavailable response; it does not erase its catalogue entry.

Import stages and validates the entire package, copies it into a unique directory, then commits catalogue/FTS rows. Failure removes incomplete destination files. Consuming watchers record successful imports in the same transaction before removing input. Watcher edits and import operations share a process lock. The application is intended to run as one instance against each database/storage pair.

## Access boundaries

| Route | Required access |
| --- | --- |
| `/`, `/read/…`, `/upload`, `/settings`, `/shares` | Proxy authentication |
| `/internal/…` | Proxy authentication; mutations require same-origin browser request checks |
| `/files/…`, `/static/…`, `/health` | Proxy authentication |
| `/api/…` | No proxy login; `X-Api-Key` required on every request |
| `/share/<uuid>/…` | No proxy login; valid token/expiry, optional password session |

The browser never needs the API key for browsing or settings mutations. It is retrieved only on the private Settings page. No cross-origin API access is enabled. Report HTML receives a sandbox policy without `allow-same-origin`, whether read privately, directly, or through a public share. The reader iframe applies the same sandbox. Report scripts cannot read the private UI or API key. Relative assets stay within each report package. Public shares have their own file root and cannot navigate through files into other reports.

The proxy must overwrite `X-Portal-Public` to `1` for shares and `0` for private/API traffic. Public-marked requests cannot access private or API routes. Never rely on that header alone for authentication or expose the upstream container port publicly. API routes authenticate independently with the Settings key. Public shares permit GET/HEAD and the specific POST password-unlock route. Keep proxy security headers but avoid replacing Portal's sandbox policy.

Password sessions use random, scoped HttpOnly cookies and store only a token hash. Sessions expire after 12 hours or earlier share expiry. Password changes invalidate sessions; revocation invalidates all access. Password attempts are limited per peer/token in a bounded time window. Behind a shared reverse proxy, the conservative limit is shared by its clients; forwarded IP headers are deliberately not trusted by the application.

## Limits

An archive is a report package, not a complete arbitrary website capture. Absolute local asset links, JavaScript-generated filesystem paths, missing files, private external embeds, and some module/storage APIs may need report author changes. A sandbox may require authors fetching packaged JSON to account for opaque-origin CORS. Folder scans are periodic to work with network mounts; stable-file heuristics cannot know whether a paused producer has finished. Prefer complete ZIPs or atomic publication into an inbox.

The first release has no report editing/deletion, offline cache, model execution, automatic research, scheduled share cleanup, or multi-instance coordination. Metadata is supplied at import. A replacement share deliberately selects an existing report; it does not infer a revision relationship.
