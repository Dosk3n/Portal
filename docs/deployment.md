# Deployment

## Saltbox host

Use `compose.saltbox.example.yaml` as a template, customised outside the repository. Application state stays in `/opt/portal/data`; media uses `/mnt/unionfs/Media/Portal/reports` and `/mnt/unionfs/Media/Portal/shares`. The host `/mnt` is mounted at `/mnt` so optional watcher paths match. Create an inbox such as `/mnt/unionfs/Media/Portal/watched` if wanted, then add it through Settings. Do not watch the whole Portal media directory.

1. Create `/opt/portal/data` and the report/share directories. Grant the configured container UID/GID write access only where needed. The image runs as UID/GID 1000 by default; Compose can override it to match the host.
2. Copy the example as `/opt/portal/compose.yaml`; create a private `.env` with `PORTAL_IMAGE`, `PORTAL_HOST`, UID/GID, timezone, and the installed middleware names.
3. Configure the existing Saltbox network and HTTPS certificate resolver. The private router must include Authelia. API and share routers bypass it and take priority over the private router.
4. Keep container port 8769 unpublished. HTTP-to-HTTPS normally comes from Saltbox's existing global redirect.
5. Authenticate Docker to the private registry using a read-only `read:packages` token and `docker login ghcr.io --username YOUR_ACCOUNT --password-stdin`. Keep registry credentials outside Compose and Git.
6. Run `docker compose config --quiet`, `docker compose pull`, and `docker compose up -d`.

The example's middleware names are placeholders/defaults, not proof of any live host configuration. Inspect the installed stack before deployment. Preserve standard Saltbox managed-container labels for its backup and lifecycle tools. Never change the union or mover configuration merely to install Portal; it uses the existing mount.

`PUBLIC_BASE_URL` must match the browser's actual HTTPS origin. It controls secure share-session cookies and origin checks. Header routing must overwrite client-supplied `X-Portal-Public`. Allow share password POSTs to `/share/<uuid>/unlock`; a GET-only share rule would prevent password entry. Every file request still checks share access.

## Verify

Use synthetic reports first. Verify authenticated library/settings access; API uploads with a key and HTTP 401 without one; anonymous denial of `/internal/settings`, `/files/…`, `/`, and private reader routes; public snapshot access without an Authelia login; password prompts and protected images; expiry, replacement and revocation; persistence after restart. A working `/health` does not prove proxy authentication or NAS availability.

## Backup

Back up both application data and report storage, plus the host's private Compose/environment configuration. Media movement is not a backup. Stop Portal during a simple file-copy backup so the database/WAL and share pointers are consistent. Preserve reports and shares alongside the catalogue when restoring. Never commit backups or use a sync delete against application state.

## Update

Back up, set `PORTAL_IMAGE` to a reviewed version/digest, then pull and recreate the container. Retain all mounts. Do not delete data volumes. Roll back using a complete consistent backup if a future release migrates the database; do not assume downgrading an image reverses schema changes.

## Image publication

Source and packages currently remain private. The release workflow publishes version tags and `latest` through the repository's `GITHUB_TOKEN`; no personal token belongs in workflow files. Confirm package visibility after publishing. Private images require registry authentication on the deployment host even if the source repository becomes public later. Publishing the source and changing image visibility are separate decisions.
