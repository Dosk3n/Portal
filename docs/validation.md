# Validation

Run unit/integration tests with `python3 -m unittest discover -s tests -v`.

The browser test creates synthetic reports/shares, regenerates the development API key, and adds an inbox location. Run it only against isolated development data, never a production library.

```sh
mkdir -p data storage/inbox
docker compose up --build -d
npm install --prefix /tmp/portal-browser --no-audit --no-fund playwright@1.56.1
/tmp/portal-browser/node_modules/.bin/playwright install --with-deps chromium
export NODE_PATH=/tmp/portal-browser/node_modules
export CHROMIUM_PATH="$(node -e "console.log(require('playwright').chromium.executablePath())")"
node tests/browser.cjs
node tests/refresh.cjs
```

Browser dependencies are temporary development tools and never enter the application image. Screenshots go to `/tmp/portal-browser` by default. Tests exercise manual ZIP upload, packaged images, script interaction, sandbox denial of private settings reads, public password entry and assets, keyed API upload, snapshot replacement preserving URL/password, key regeneration, folder settings, revocation, search, and a 390px mobile layout. Proxy access control and union/NAS behaviour require separate deployment checks.

The release workflow runs the same checks before publishing a versioned image. Publishing runs only for version tags while the repository is private. Revisit that explicit condition if the source is later made public while keeping the image private.

The refresh regression test invokes the real 30-second poll callback directly. It verifies that unchanged results do not replace page elements, new imports update in place, active typing is preserved even when a request is already in flight, scroll/filter state survives, and failed or stale requests do not replace the current page.
