// Мост между страницей Адал и расширением. Работает только на сайте Адал.
// Страница пишет через window.postMessage с source "adal-page", расширение отвечает с source "adal-guard".
(function () {
  "use strict";
  var VERSION = chrome.runtime.getManifest().version;
  var ORIGIN = location.origin;

  function toPage(msg) {
    msg.source = "adal-guard";
    window.postMessage(msg, ORIGIN);
  }
  function toBackground(msg) {
    try { chrome.runtime.sendMessage(msg); } catch (e) { /* расширение перезагружено */ }
  }

  document.documentElement.setAttribute("data-adal-guard", VERSION);
  toPage({type: "hello", version: VERSION});

  window.addEventListener("message", function (e) {
    if (e.source !== window || !e.data || e.data.source !== "adal-page") return;
    var d = e.data;
    if (d.type === "ping") {
      // Если расширение отключили, chrome.runtime.id пропадает и ответа не будет.
      if (chrome.runtime && chrome.runtime.id) toPage({type: "pong", version: VERSION});
    } else if (d.type === "exam-start") {
      toBackground({type: "exam-start", attempt: String(d.attempt), seconds: Number(d.seconds) || 0,
                    path: location.pathname});
    } else if (d.type === "exam-end") {
      toBackground({type: "exam-end"});
    }
  });

  chrome.runtime.onMessage.addListener(function (msg) {
    if (msg && msg.type === "violation") toPage({type: "violation", kind: msg.kind, detail: msg.detail || ""});
  });

  // Страница результата означает, что тест закончен.
  if (/^\/attempt\/\d+\/result\/?$/.test(location.pathname)) toBackground({type: "exam-end"});
})();
