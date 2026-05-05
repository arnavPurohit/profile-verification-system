/**
 * Isolated-world content script that relays captures from the MAIN-world
 * patcher (content.js) to the background service worker.
 *
 * MAIN-world scripts cannot access chrome.* APIs; isolated-world scripts can.
 * This bridge runs in the isolated world, listens for the postMessage from
 * the patcher, and forwards via chrome.runtime.sendMessage.
 */
window.addEventListener("message", (event) => {
  const data = event.data;
  if (!data || !data.__verifier || data.type !== "voyager_capture") return;
  chrome.runtime.sendMessage({
    type: "voyager_capture",
    url: data.url,
    kind: data.kind,
    payload: data.payload,
  });
});
