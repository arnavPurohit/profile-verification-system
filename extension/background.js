/**
 * Background service worker.
 *
 * Receives capture messages from the content script (via a relay since
 * MAIN-world scripts can't talk to chrome.* directly) and POSTs them to the
 * verification backend.
 *
 * Settings live in chrome.storage; default backend is http://localhost:8001.
 */
const DEFAULT_BACKEND = "http://localhost:8001";

async function getBackend() {
  const { backend } = await chrome.storage.local.get(["backend"]);
  return backend || DEFAULT_BACKEND;
}

async function sendCapture({ kind, url, payload }) {
  const backend = await getBackend();
  try {
    const resp = await fetch(`${backend}/captures`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ kind, url, payload }),
    });
    if (!resp.ok) {
      console.warn("[verifier] backend rejected capture", resp.status);
    }
  } catch (err) {
    console.warn("[verifier] failed to ship capture", err);
  }
}

// Relay from content script (which forwards via chrome.runtime.sendMessage from
// an isolated-world bridge — see content.js + relay.js).
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "voyager_capture") {
    sendCapture(msg);
    sendResponse({ ok: true });
  }
});
