/* Экран тестирования: таймер, навигация без перезагрузки, сохранение ответов
   и фиксация нарушений. Браузер не может полностью запретить списывание,
   поэтому задача скрипта — сделать уход с теста заметным для преподавателя,
   а решения (время, баллы, лимит нарушений) принимает сервер. */
(function () {
  "use strict";
  var E = window.EXAM;
  var $ = function (id) { return document.getElementById(id); };
  var body = document.body;
  var started = false, finishing = false, lastReport = 0, current = 1;
  var deadline = Date.now() + JSON.parse($("seconds-left").textContent) * 1000;
  var DEBOUNCE_MS = 2500, PING_MS = 10000;

  function post(url, data) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRFToken": E.csrf, "Accept": "application/json"},
      body: JSON.stringify(data || {})
    }).then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); });
  }

  function goResult() {
    finishing = true;
    window.location.href = E.urls.result;
  }

  // ---------- Таймер ----------
  function fmt(s) {
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    var mm = (h ? (m < 10 ? "0" : "") : "") + m;
    return (h ? h + ":" : "") + mm + ":" + (r < 10 ? "0" : "") + r;
  }
  function tick() {
    var left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
    $("timer-value").textContent = fmt(left);
    var t = $("timer");
    t.classList.toggle("low", left <= 300 && left > 60);
    t.classList.toggle("critical", left <= 60);
    if (left === 0 && !finishing) finish();
  }
  setInterval(tick, 250); tick();

  // ---------- Полноэкранный режим ----------
  function enterFullscreen() {
    var el = document.documentElement;
    var req = el.requestFullscreen || el.webkitRequestFullscreen;
    if (!req) return Promise.resolve();
    // Некоторые встроенные браузеры не отвечают на запрос — не ждём дольше секунды.
    var timeout = new Promise(function (resolve) { setTimeout(resolve, 1000); });
    var request;
    try { request = Promise.resolve(req.call(el)).catch(function () {}); }
    catch (err) { request = Promise.resolve(); }
    return Promise.race([request, timeout]);
  }
  function isFullscreen() { return !!(document.fullscreenElement || document.webkitFullscreenElement); }
  function unlock() { body.classList.remove("locked"); $("guard-start").classList.remove("show"); $("guard-back").classList.remove("show"); }

  $("btn-enter").addEventListener("click", function () {
    enterFullscreen().then(function () { started = true; unlock(); });
  });
  $("btn-return").addEventListener("click", function () {
    enterFullscreen().then(unlock);
  });

  // ---------- Нарушения ----------
  function updateStrikes(n) {
    var dots = $("strikes").querySelectorAll("i");
    for (var i = 0; i < dots.length; i++) dots[i].classList.toggle("on", i < n);
  }
  function report(kind, showGuard) {
    if (!started || finishing) return;
    if (showGuard) {
      body.classList.add("locked");
      $("guard-back").classList.add("show");
    }
    var now = Date.now();
    if (now - lastReport < DEBOUNCE_MS) return;
    lastReport = now;
    post(E.urls.event, {kind: kind}).then(function (r) {
      if (r.terminated) { goResult(); return; }
      if (typeof r.violations === "number") {
        updateStrikes(r.violations);
        var left = r.limit - r.violations;
        $("gb-text").textContent = "Нарушение записано и видно преподавателю. " +
          (left > 0 ? "До автоматического завершения теста осталось нарушений: " + left + "." : "");
      }
    }).catch(function () {});
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) report("tab_hidden", true);
  });
  window.addEventListener("blur", function () { report("window_blur", true); });
  document.addEventListener("fullscreenchange", function () {
    if (!isFullscreen()) report("fullscreen_exit", true);
  });
  document.addEventListener("webkitfullscreenchange", function () {
    if (!isFullscreen()) report("fullscreen_exit", true);
  });
  // Правая кнопка — путь к «Спросить Gemini» и переводчикам в Chrome.
  document.addEventListener("contextmenu", function (e) { e.preventDefault(); report("context_menu", false); });
  ["copy", "cut"].forEach(function (t) {
    document.addEventListener(t, function (e) { e.preventDefault(); report("copy", false); });
  });
  document.addEventListener("paste", function (e) { e.preventDefault(); report("paste", false); });
  document.addEventListener("selectstart", function (e) { e.preventDefault(); });
  document.addEventListener("keydown", function (e) {
    var k = (e.key || "").toLowerCase(), mod = e.ctrlKey || e.metaKey;
    if (e.key === "F12" || (mod && e.shiftKey && ["i", "j", "c"].indexOf(k) >= 0) || (mod && k === "u")) {
      e.preventDefault(); report("devtools", false); return;
    }
    if (mod && (k === "c" || k === "x")) { e.preventDefault(); report("copy", false); return; }
    if (mod && k === "v") { e.preventDefault(); report("paste", false); return; }
    if (mod && ["a", "p", "s", "f"].indexOf(k) >= 0) { e.preventDefault(); }
  });
  document.addEventListener("keyup", function (e) {
    if (e.key === "PrintScreen") {
      report("print_screen", false);
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText("").catch(function () {});
    }
  });
  window.addEventListener("beforeunload", function (e) {
    if (!finishing) { e.preventDefault(); e.returnValue = ""; }
  });

  // ---------- Вопросы и ответы ----------
  function markPalette() {
    var buttons = $("palette").querySelectorAll("button");
    for (var i = 0; i < buttons.length; i++) buttons[i].classList.toggle("current", +buttons[i].dataset.go === current);
  }
  function answeredCount() { return $("palette").querySelectorAll("button.done").length; }

  function loadQuestion(n) {
    if (n < 1 || n > E.total || n === current) return;
    fetch(E.urls.question.replace("/q/0/", "/q/" + n + "/"), {credentials: "same-origin"})
      .then(function (r) {
        if (r.status === 409) { goResult(); throw new Error("closed"); }
        return r.text();
      })
      .then(function (html) {
        $("question").innerHTML = html;
        current = n;
        markPalette();
        if (window.renderMath) window.renderMath($("question"));
        var first = $("question").querySelector("input[name=answer]");
        if (first) first.focus({preventScroll: true});
        window.scrollTo({top: 0});
      }).catch(function () {});
  }

  document.addEventListener("click", function (e) {
    var go = e.target.closest("[data-go]");
    if (go && !go.disabled) { loadQuestion(+go.dataset.go); return; }
    if (e.target.closest("[data-finish]")) askFinish();
  });

  document.addEventListener("change", function (e) {
    if (e.target.name !== "answer") return;
    var state = $("saved-state");
    state.textContent = "Сохраняем…";
    var pos = current;
    post(E.urls.answer, {position: pos, choice: +e.target.value}).then(function (r) {
      if (!r.ok) {
        if (r.status && r.status !== "active") { goResult(); return; }
        state.textContent = r.error || "Ответ не сохранён — выберите ещё раз";
        return;
      }
      state.textContent = "Ответ сохранён";
      var btn = $("palette").querySelector('[data-go="' + pos + '"]');
      if (btn) btn.classList.add("done");
      $("answered-count").textContent = answeredCount();
    }).catch(function () { state.textContent = "Нет связи с сервером — выберите ответ ещё раз"; });
  });

  // ---------- Завершение ----------
  function askFinish() {
    var left = E.total - answeredCount();
    $("gf-text").textContent = left
      ? "Без ответа осталось вопросов: " + left + ". После завершения изменить ответы нельзя."
      : "Вы ответили на все вопросы. После завершения изменить ответы нельзя.";
    $("guard-finish").classList.add("show");
  }
  $("btn-finish-no").addEventListener("click", function () { $("guard-finish").classList.remove("show"); });
  $("btn-finish-yes").addEventListener("click", finish);

  function finish() {
    finishing = true;
    post(E.urls.finish, {}).then(function (r) {
      if (document.exitFullscreen && isFullscreen()) document.exitFullscreen().catch(function () {});
      window.location.href = r.redirect || E.urls.result;
    }).catch(goResult);
  }

  // ---------- Связь с сервером ----------
  setInterval(function () {
    if (finishing) return;
    post(E.urls.ping).then(function (r) {
      if (r.status === "other_session") { finishing = true; window.location.reload(); return; }
      if (r.status !== "active") { goResult(); return; }
      deadline = Date.now() + r.seconds_left * 1000;
      updateStrikes(r.violations);
    }).catch(function () {});
  }, PING_MS);
})();
