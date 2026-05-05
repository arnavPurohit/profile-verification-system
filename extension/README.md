# Chrome extension — LinkedIn Voyager capture

Captures Voyager XHR responses from the user's authenticated LinkedIn session
and ships them to the backend `/captures` endpoint. **Zero extra requests to
LinkedIn** — we observe what the page already does on the user's behalf.

## How it works

1. `content.js` runs in the page's MAIN world at `document_start`. It
   monkey-patches `fetch()` and `XMLHttpRequest` to intercept Voyager API
   responses (`/voyager/api/identity/profiles/...`, `/organization/companies/...`).
2. When a Voyager response lands, it's posted to the page via `window.postMessage`.
3. `relay.js` runs in the isolated world (which can access `chrome.*` APIs) and
   forwards the message to the background service worker.
4. `background.js` POSTs the payload to the backend at `/captures`.

This split is required because MAIN-world scripts can patch the page but
can't talk to `chrome.runtime`; isolated-world scripts are the reverse.

## Why patch the page instead of using `chrome.webRequest`

`chrome.webRequest` in MV3 cannot read response bodies. So while we *could*
detect that a Voyager request happened, we couldn't read the data. Patching
`fetch`/`XHR` reads the body the page is already receiving.

## Install (developer mode)

1. Open `chrome://extensions`.
2. Toggle "Developer mode" on.
3. Click "Load unpacked" and select this `extension/` folder.
4. Open the extension's options and set the backend URL (default
   `http://localhost:8001`, which is the fetcher service in `docker-compose`).

## Verify it works

1. Run `docker compose up`.
2. Tail the fetcher logs: `docker compose logs -f fetcher`.
3. Open any LinkedIn profile in your browser.
4. You should see a `capture.profile` log line in the fetcher.
5. Hit `POST /verify` for that profile — cache hit, fast response.

## What this is not

- Not a public-store extension. Detection risk grows with userbase; for a
  customer-installed deployment, ship as an unlisted internal extension.
- Not the only data source. Server-side Voyager scraper is the fallback for
  profiles no user has visited.
