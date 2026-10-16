# Portal tools for Nexus

Let agents create, find, revise and share reports through an existing Portal instance. Requires **Portal 1.1.2 or newer** and Nexus with tool-pack support.

## Install

From a Portal checkout, use the Python environment that runs Nexus:

```sh
python -m pip install ./integrations/nexus
```

Add `nexus_tools_portal` to your existing `NEXUS_TOOL_PACKAGES` list. Set these in Nexus's private `.env` or environment:

```dotenv
PORTAL_BASE_URL=https://reports.example.com
PORTAL_API_KEY=YOUR_API_KEY
```

Copy the key from **Portal → Settings**. Restart Nexus after installing the package. If you regenerate the key, update its Nexus setting too.

For a Nexus monorepo checkout, you can instead copy this directory to `tool_packs/nexus_tools_portal`; Nexus discovers that layout without a pip install.

## Tools

| Tool | Purpose |
| --- | --- |
| `portal.search_reports` | Search text, collections, tags, source and dates. |
| `portal.get_report` | Read metadata and HTML; optionally retrieve named assets or an older version. |
| `portal.create_report` | Create a new private report and return its link. |
| `portal.update_report` | Revise an existing report while keeping its link. |
| `portal.edit_report_details` | Change the title, collection, tags, source or date. |
| `portal.list_versions` | List retained versions and preview links. |
| `portal.restore_version` | Restore a retained version as the current report. |
| `portal.list_shares` | Find snapshot links and their access settings. |
| `portal.create_share` | Create a snapshot with an optional password and expiry. |
| `portal.update_share` | Explicitly refresh a snapshot or change its access. |
| `portal.revoke_share` | Revoke a link and remove its snapshot. |

## Agent workflow

Discover tools with Nexus `search_tools` / `get_tool`, then call them through `load_tool` in `run_code`:

```python
create = load_tool("portal.create_report")
report = create(
    html='<title>Conversation notes</title><h1>What we discussed</h1><p>Our findings.</p>',
    topic="Conversations",
    tags=["notes"],
)
RESULT = {"id": report["id"], "url": report["reader_url"]}
```

To update it, read the report first and pass its version token. A stale token returns a conflict; read the report again and reconcile changes before retrying.

```python
read = load_tool("portal.get_report")
update = load_tool("portal.update_report")
existing = read(report_id="REPORT_UUID")
updated = update(
    report_id=existing["report"]["id"],
    expected_version=existing["report"]["current_version"],
    html='<title>Conversation notes</title><h1>Updated notes</h1><p>Including our new findings.</p>',
)
RESULT = updated["reader_url"]
```

Each update replaces the complete HTML/assets package. Portal applies its version-retention setting. Report creation or revision does not publish a share.

For images, styles or other files, pass `assets` alongside HTML:

```python
assets = [
    {"path": "assets/style.css", "text": "body { font-family: system-ui; }"},
    {"path": "assets/image.png", "base64": "BASE64_IMAGE_CONTENT"},
]
```

Use relative links from `index.html`. The pack builds the ZIP automatically; it does not read local filesystem paths. `get_report(asset_paths=["assets/image.png"])` returns the requested assets as base64. Individual responses and combined requested assets are limited to 8 MB; request smaller sets when necessary. Nexus also limits displayed `RESULT` size, so return selected sections or chunks when reading long HTML reports.

Sharing is separate. `create_share` defaults to seven days; `expires_in_days=None` creates a link without expiry. To publish revisions into an existing link, explicitly use `update_share(token=..., refresh_snapshot=True)`. For access-only edits, omit refresh. `change_expiry=True` enables an expiry change, including `expires_in_days=None` to remove expiry. `password=None` keeps the password; `password=""` removes it.
