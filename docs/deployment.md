# Setup and storage

## Basic setup

Use [docker-compose.yml](../docker-compose.yml), or copy the example from the [README](../README.md#install-with-docker). Create the folders beside that file, then start Portal:

```sh
mkdir -p portal-data portal-reports
docker compose up -d
```

Open <http://localhost:8769> on the same computer. The default port is available only on that computer.

## Your files

The example uses two persistent folders:

```yaml
volumes:
  - ./portal-data:/data
  - ./portal-reports:/storage
```

- **portal-data** holds the database, settings, API key and library catalogue.
- **portal-reports** holds imported reports and shared snapshots, in separate subfolders.

Keep both folders when updating or replacing the container. You can change the left-hand paths to use another disk; keep `/data` and `/storage` on the right. An optional `.env` file can set the image, port, public address, user/group IDs and timezone; see [.env.example](../.env.example).

### Custom storage

For a NAS or another disk, change the host side of the reports mount:

```yaml
volumes:
  - ./portal-data:/data
  - /mnt/nas/portal:/storage
```

Mount the NAS on the Docker host first. Portal still uses `/storage` inside the container, so no `command` override is needed. The database stays in the local `portal-data` folder. Optional watched folders can be mounted separately.

### Folder permissions

The container runs as user and group `1000` by default. Create the folders before starting Portal so Docker does not create them as root. They must be writable by the container's user.

On Linux, if your account has different IDs, run the following in the Portal folder to save your IDs in `.env` (preserving any other settings):

```sh
printf '\nPUID=%s\nPGID=%s\n' "$(id -u)" "$(id -g)" >> .env
docker compose up -d
```

If the folders were previously created by root, correct their ownership to the user/group configured for Portal before restarting. The application data directory is restricted to its owner.

## Folder imports

To watch a host folder, add an extra mount under the service's `volumes`:

```yaml
  - ./portal-watched:/inbox
```

Create `portal-watched` beside the Compose file, restart Portal, then enable watching in **Settings** and add `/inbox`. Keep incoming files separate from `portal-data` and `portal-reports`. A folder already accessible inside the container does not need another mount.

## Moving an existing installation

Stop Portal before moving its storage, then keep the database and report files together:

- If an older Compose setup used `./data` and `./storage`, move their contents to `./portal-data` and `./portal-reports` respectively before starting with the new file.
- If you used Docker named volumes, copy their contents into the matching folders before changing the mounts. Keep the old volumes until you have checked the new setup.

Changing a mount path does not automatically move existing files. Start Portal again and check that your reports and share links still work.

## Updates and backups

To update from the folder containing `docker-compose.yml`:

```sh
docker compose pull
docker compose up -d
```

For a specific version, change the image tag in `docker-compose.yml`, or set `PORTAL_IMAGE` in `.env` when using the repository's Compose file.

For a simple backup, stop Portal and copy **both** `portal-data` and `portal-reports`, along with your Compose file and optional `.env`. Then start it again. Restore both folders from the same backup so reports and shares match the catalogue. Keep backups outside Git.

## Access from other computers

Portal has no built-in account or login screen. The basic example is intended for local use. For remote access, use HTTPS and an authentication service in front of Portal, and set `PUBLIC_BASE_URL` to the address people actually use.

Protect the library, settings, `/internal/` and private `/files/` routes. The `/api` routes use the Settings API key, and `/share/` uses share links with optional passwords and expiry. Keep the whole `/share/` path outside the login service: it also serves scoped, temporary reader views needed for packaged assets inside the sandbox. `/files/` remains protected. The [architecture guide](architecture.md#access-boundaries) describes the route boundaries for whoever manages your server.
