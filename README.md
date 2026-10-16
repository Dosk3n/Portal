<p align="center">
  <img src="app/static/branding/portal-logo.svg" alt="Portal" width="220">
</p>

# A home for the useful things your AI finds.

Portal turns a folder full of HTML reports into a library you can browse, search and share. Ask an AI agent to research something, save its findings as a web page, and send it to Portal for later.

> **Inspired by a good friend.** This project was heavily inspired by [Superfishintights](https://github.com/Superfishintights). I used his implementation as the starting point for my own version of Portal, adapting it to suit how I work. A big thank you for the idea and the foundation.

![Portal's report library in light mode, showing sample reports organised into collections](docs/screenshots/library-light.png)

*Real screenshots of Portal with fictional sample reports.*

## What would I use it for?

Anything you would like to keep as a readable report:

- **Gift ideas:** compare options, keep useful links, and share a shortlist.
- **House hunting:** collect research against your requirements in one place.
- **Travel plans:** save an itinerary, ideas and notes for later.
- **Project updates:** have an agent explain what it changed, what it checked, and what comes next.
- **Everyday research:** keep product comparisons, guides and discoveries somewhere easier to revisit than a chat history.

Portal stores and presents the finished reports. Your AI agent does the research and creates the HTML file—the web page that contains the report.

## How it works

1. **Create a report.** Write one yourself, or ask an AI agent to produce an HTML report.
2. **Add it to Portal.** Upload it in your browser, have the agent send it directly, or use an optional watched folder.
3. **Find it later.** Search the contents, browse collections, and filter by tags, source or date.
4. **Share when ready.** Create a separate snapshot with an optional password and expiry date.

Reports can include images, charts and interactive elements. Upload a single HTML file, or a ZIP containing the report and its assets.

![An example HTML report open in Portal's full-height reader](docs/screenshots/report.png)

## What you get

| Feature | What it means for you |
| --- | --- |
| **Your own report library** | Keep reports on your own computer or server. |
| **Search, collections and tags** | Find useful information without remembering its filename. |
| **Three ways to add reports** | Browser uploads, direct submissions from agents, or watched folders. |
| **Reports with their assets** | Keep images, styles and charts beside the report in a ZIP bundle. |
| **Controlled sharing** | Set a password, choose when a link expires, or revoke it later. |
| **Snapshots you control** | A shared report stays as you sent it until you explicitly update it. |
| **Light and dark mode** | Switch from the menu; Portal remembers your choice in that browser. |
| **Phone-friendly pages** | Browse your library and read reports on smaller screens. |
| **A single agent API key** | View, copy or regenerate it in Settings. |

<details>
<summary>See Portal in dark mode</summary>

![Portal's report library in dark mode](docs/screenshots/library-dark.png)

</details>

## For people

Open **Upload**, choose an HTML file or ZIP, and select **Import report**. You can add a title, collection and tags to make it easier to find later.

To share a report, open it and choose **Share externally**. You can set a password and an expiry, then copy the link. The recipient does not need access to your private library.

A share is a **snapshot**, rather like sending a copy. When you are happy with a newer report, use **Shares → Update snapshot / access** to replace it while keeping the same link.

<img src="docs/screenshots/sharing.png" alt="The sharing dialog, with expiry and optional password controls" width="560">

## For AI agents

You can ask your agent something like:

> Research gift ideas for someone who enjoys cooking. Create a clear HTML report with a comparison of the options and links to your sources, then upload it to my Portal. Use the “Gift ideas” collection and appropriate tags.

Give the agent your Portal address and the API key from **Settings** through its normal secure configuration. Keep the key out of the report itself.

Agents can send reports from different computers and networks without a shared folder. Every API or manual submission creates a new private report; sharing is a separate choice.

### Agent example: send a report with the API

Use the actual Portal address in place of `https://portal.example.com`. Set `PORTAL_API_KEY` in the agent's environment to the key from Settings.

```sh
curl 'https://portal.example.com/api/reports' \
  -H "X-Api-Key: $PORTAL_API_KEY" \
  -F 'file=@report.html' \
  -F 'topic=Gift ideas' \
  -F 'source=AI agent' \
  -F 'tags=gifts,cooking'
```

Send `report.zip` instead if the report has separate images or other assets. Put `index.html` at the top of the ZIP and use relative links to its files.

The response includes `reader_url`, the private report link. Read `/api/docs` with the same key, or see the [technical reference](docs/reference.md#submit-reports).


## Install with Docker

You need a computer or server with **Docker and Docker Compose** installed. Portal runs in one container, with no separate database service to set up. Published images currently support **Linux on Intel/AMD 64-bit systems**.

**1. Create a folder for Portal** and save the following as `compose.yaml` inside it:

```yaml
services:
  portal:
    image: ghcr.io/dosk3n/portal:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:8769:8769"
    environment:
      PUBLIC_BASE_URL: http://localhost:8769
    volumes:
      - portal-data:/data
      - portal-reports:/storage

volumes:
  portal-data:
  portal-reports:
```

**2. Open a terminal in that folder** and start Portal:

```sh
docker compose up -d
```

**3. Open [http://localhost:8769](http://localhost:8769)** on that computer. You are ready to upload your first report. Docker keeps the settings and reports in persistent volumes between container restarts and updates.

This setup is for use on the same computer. **Portal has no built-in login.** To use it remotely or make share links available to other people, put it behind your existing HTTPS and authentication setup. The library stays protected; the keyed API and controlled share links have their own access rules. Follow the [server deployment guide](docs/deployment.md) for that setup, including [Saltbox and Traefik](compose.saltbox.example.yaml).

To update, run these from the same folder:

```sh
docker compose pull
docker compose up -d
```

For an installation from source, custom storage locations, backups and other options, see the guides below.

## Prefer a shared folder?

Folder watching is optional and **off by default**. Enable it in **Settings**, then add a folder that Portal can access. For a NAS or another host folder, mount it into the container first; see the [folder import notes](docs/reference.md#folder-imports).

Portal checks for completed files, imports them, and removes the incoming copy after success. Failed files stay in place so you can correct them. Exact duplicates at the same path are skipped only when the existing report is still available, and the result appears in **Recent imports**.

## More help

- [Technical reference](docs/reference.md) — report packages, API use, sharing, folder imports and running from source.
- [Server deployment](docs/deployment.md) — remote access, authentication, storage, backups and updates.
- [Architecture](docs/architecture.md) — how Portal is put together.
- [Validation](docs/validation.md) — how to run the development checks.
- [Source credits and provenance](docs/reference.md#source-provenance) — notes on the starting implementation and adapted materials.
