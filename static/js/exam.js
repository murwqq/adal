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
    if (E.requireGuard && !guard.version) { showGuardNeed(); return; }
    enterFullscreen().then(function () { started = true; unlock(); });
  });

  // ---------- Расширение Адал Guard ----------
  // Расширение блокирует ИИ-сервисы и новые вкладки и сообщает о попытках сюда.
  var guard = {version: document.documentElement.getAttribute("data-adal-guard"), last: Date.now(), lost: false};
  function toGuard(msg) { msg.source = "adal-page"; window.postMessage(msg, window.location.origin); }
  function showGuardNeed() { body.classList.add("locked"); $("guard-need").classList.add("show"); }
  window.addEventListener("message", function (e) {
    if (e.source !== window || !e.data || e.data.source !== "adal-guard") return;
    var d = e.data;
    if (d.type === "hello" || d.type === "pong") {
      guard.version = d.version; guard.last = Date.now();
      if ($("guard-need").classList.contains("show")) { $("guard-need").classList.remove("show"); toGuard({type: "exam-start", attempt: E.attempt, seconds: Math.round((deadline - Date.now()) / 1000)}); }
    } else if (d.type === "violation") {
      report(d.kind, d.kind === "ai_site", d.detail);
    }
  });
  function guardStart() { toGuard({type: "exam-start", attempt: E.attempt, seconds: Math.round((deadline - Date.now()) / 1000)}); }
  guardStart();
  setTimeout(function () {
    if (guard.version) return;
    if (E.requireGuard) { showGuardNeed(); report("guard_missing", false); }
  }, 2000);
  setInterval(function () {
    if (finishing || !guard.version) return;
    toGuard({type: "ping"});
    if (!guard.lost && Date.now() - guard.last > 25000) {
      guard.lost = true;
      report("guard_lost", true);
      if (E.requireGuard) showGuardNeed();
    }
  }, 5000);
  $("btn-return").addEventListener("click", function () {
    enterFullscreen().then(unlock);
  });

  // ---------- Нарушения ----------
  function updateStrikes(n) {
    var dots = $("strikes").querySelectorAll("i");
    for (var i = 0; i < dots.length; i++) dots[i].classList.toggle("on", i < n);
  }
  function report(kind, showGuard, detail) {
    if (finishing) return;
    // События расширения приходят и до нажатия «Начать»: браузер уже заблокирован.
    if (!started && kind.indexOf("guard") < 0 && kind !== "ai_extension" && kind !== "new_tab" && kind !== "ai_site") return;
    if (showGuard) {
      body.classList.add("locked");
      $("guard-back").classList.add("show");
    }
    var now = Date.now();
    var noted = kind === "new_tab" || kind === "ai_extension" || kind === "guard_missing";
    if (!noted) {
      if (now - lastReport < DEBOUNCE_MS) return;
      lastReport = now;
    }
    post(E.urls.event, {kind: kind, detail: detail || ""}).then(function (r) {
      if (r.terminated) { goResult(); return; }
      if (typeof r.violations === "number") {
        updateStrikes(r.violations);
        var left = r.limit - r.violations;
        $("gb-text").textContent = E.t.recorded + " " +
          (left > 0 ? E.t.leftBeforeStop + " " + left + "." : "");
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
  // Правая кнопка ведёт к «Спросить Gemini» и переводчикам в Chrome, поэтому
  // меню просто не открывается. Нарушением это не считается: студент мог
  // нажать случайно. То же с копированием, вставкой и инструментами разработчика.
  // Нарушения дают только уход со страницы и выход из полноэкранного режима.
  function block(e) { e.preventDefault(); }
  ["contextmenu", "copy", "cut", "paste", "selectstart", "dragstart"].forEach(function (t) {
    document.addEventListener(t, block);
  });
  document.addEventListener("keydown", function (e) {
    var k = (e.key || "").toLowerCase(), mod = e.ctrlKey || e.metaKey;
    if (e.key === "F12" || e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) { e.preventDefault(); return; }
    if (mod && e.shiftKey && ["i", "j", "c"].indexOf(k) >= 0) { e.preventDefault(); return; }
    if (mod && ["a", "c", "f", "p", "s", "u", "v", "x"].indexOf(k) >= 0) { e.preventDefault(); }
  });
  document.addEventListener("keyup", function (e) {
    if (e.key === "PrintScreen" && navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText("").catch(function () {});
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
    state.textContent = E.t.saving;
    var pos = current;
    post(E.urls.answer, {position: pos, choice: +e.target.value}).then(function (r) {
      if (!r.ok) {
        if (r.status && r.status !== "active") { goResult(); return; }
        state.textContent = r.error || E.t.notSaved;
        return;
      }
      state.textContent = E.t.saved;
      var btn = $("palette").querySelector('[data-go="' + pos + '"]');
      if (btn) btn.classList.add("done");
      $("answered-count").textContent = answeredCount();
    }).catch(function () { state.textContent = E.t.offline; });
  });

  // ---------- Завершение ----------
  function askFinish() {
    var left = E.total - answeredCount();
    $("gf-text").textContent = left
      ? E.t.unanswered + " " + left + ". " + E.t.noChanges
      : E.t.allAnswered + " " + E.t.noChanges;
    $("guard-finish").classList.add("show");
  }
  $("btn-finish-no").addEventListener("click", function () { $("guard-finish").classList.remove("show"); });
  $("btn-finish-yes").addEventListener("click", finish);

  function finish() {
    finishing = true;
    toGuard({type: "exam-end"});
    post(E.urls.finish, {}).then(function (r) {
      if (document.exitFullscreen && isFullscreen()) document.exitFullscreen().catch(function () {});
      window.location.href = r.redirect || E.urls.result;
    }).catch(goResult);
  }

  // ---------- Связь с сервером ----------
  setInterval(function () {
    if (finishing) return;
    post(E.urls.ping, {guard: guard.version || ""}).then(function (r) {
      if (r.status === "other_session") { finishing = true; window.location.reload(); return; }
      if (r.status !== "active") { goResult(); return; }
      deadline = Date.now() + r.seconds_left * 1000;
      updateStrikes(r.violations);
    }).catch(function () {});
  }, PING_MS);
})();
