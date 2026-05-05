/**
 * Content script injected at document_start in the page's MAIN world.
 *
 * Why this approach: chrome.webRequest in MV3 cannot read response bodies.
 * Instead, we monkey-patch fetch() and XMLHttpRequest in the page context,
 * intercept Voyager responses as the page itself reads them, and forward to
 * the extension's background worker via window.postMessage.
 *
 * This means: zero extra requests to LinkedIn. We're observing what the
 * page already does on the user's behalf.
 */
(function () {
  const VOYAGER_RE = /\/voyager\/api\//;

  function classify(url) {
    if (/identity\/profiles|identity\/dash\/profiles/.test(url)) return "profile";
    if (/organization\/companies/.test(url)) return "company";
    return null;
  }

  // Patch fetch — preserve native behavior exactly
  const _nativeFetch = window.fetch;
  window.fetch = function (...args) {
    const promise = _nativeFetch.apply(this, args);
    promise.then(function (resp) {
      try {
        const url = typeof args[0] === "string" ? args[0] : args[0]?.url ?? "";
        if (VOYAGER_RE.test(url) && resp.ok) {
          resp.clone().json().then(function (payload) {
            const kind = classify(url);
            if (kind) {
              window.postMessage(
                { __verifier: true, type: "voyager_capture", url, kind, payload },
                "*",
              );
            }
          }).catch(function () {});
        }
      } catch (e) {}
    }).catch(function () {});
    return promise;
  };

  // Patch XHR
  const _origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__verifierUrl = url;
    return _origOpen.call(this, method, url, ...rest);
  };
  const _origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener("load", function () {
      try {
        const url = this.__verifierUrl || "";
        if (VOYAGER_RE.test(url) && this.status >= 200 && this.status < 300) {
          const kind = classify(url);
          if (kind) {
            const payload = JSON.parse(this.responseText);
            window.postMessage(
              { __verifier: true, type: "voyager_capture", url, kind, payload },
              "*",
            );
          }
        }
      } catch (e) {}
    });
    return _origSend.apply(this, args);
  };
})();
