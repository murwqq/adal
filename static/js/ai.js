// ИИ-наставник на странице результата и ИИ-разбор вопроса в анализе теста.
(function () {
  function csrf() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }
  function post(url, data) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-CSRFToken": csrf()},
      body: JSON.stringify(data || {})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.error || r.statusText);
        return j;
      });
    });
  }
  function math(el) { if (window.renderMath) window.renderMath(el); }
  // Модели отвечают в Markdown. Показываем жирный текст, код и списки, остальное как текст.
  function esc(t) { return t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function inline(t) {
    return esc(t).replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  }
  function rich(el, text) {
    var out = [], list = null;
    text.split(/\n/).forEach(function (line) {
      var m = line.match(/^\s*(?:[*\-•]|\d+[.)])\s+(.*)$/);
      if (m) {
        if (!list) { list = []; }
        list.push("<li>" + inline(m[1]) + "</li>");
        return;
      }
      if (list) { out.push("<ul>" + list.join("") + "</ul>"); list = null; }
      if (line.trim()) out.push("<p>" + inline(line.trim()) + "</p>");
    });
    if (list) out.push("<ul>" + list.join("") + "</ul>");
    el.innerHTML = out.join("");
    math(el);
  }
  function bubble(box, role, text) {
    var d = document.createElement("div");
    d.className = "bubble " + role;
    if (role === "assistant") rich(d, text); else d.textContent = text;
    box.appendChild(d);
    return d;
  }

  document.querySelectorAll(".tutor-item").forEach(function (item) {
    var url = item.dataset.url, box = item.querySelector(".tutor-chat");
    var start = item.querySelector(".tutor-start"), form = item.querySelector(".tutor-form");
    var status = item.querySelector(".tutor-status");
    function busy(on) {
      item.classList.toggle("busy", on);
      if (start) start.disabled = on;
      form.querySelector("button").disabled = on;
      if (on) status.textContent = item.dataset.think;
    }
    function apply(j) {
      box.innerHTML = "";
      j.messages.forEach(function (m) { bubble(box, m.role, m.content); });
      math(box);
      if (start) start.hidden = true;
      if (j.solved) {
        form.hidden = true;
        item.classList.add("solved");
        status.textContent = item.dataset.solved;
      } else {
        form.hidden = j.left <= 0;
        status.textContent = item.dataset.left.replace("%s", j.left);
        form.querySelector("textarea").focus();
      }
    }
    if (start) start.addEventListener("click", function () {
      busy(true);
      post(url, {}).then(apply).catch(function (e) { status.textContent = e.message; })
        .then(function () { busy(false); });
    });
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var ta = form.querySelector("textarea"), text = ta.value.trim();
      if (!text) return;
      var mine = bubble(box, "user", text);
      ta.value = "";
      busy(true);
      post(url, {message: text}).then(apply).catch(function (e) {
        status.textContent = e.message; mine.remove(); ta.value = text;
      }).then(function () { busy(false); });
    });
    form.querySelector("textarea").addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); form.requestSubmit(); }
    });
  });

  document.querySelectorAll(".ai-review").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var out = btn.nextElementSibling;
      btn.disabled = true;
      out.hidden = false;
      out.classList.add("thinking");
      out.textContent = btn.dataset.think;
      post(btn.dataset.url, {}).then(function (j) {
        rich(out, j.text);
        if (j.model) {
          var note = document.createElement("p");
          note.className = "small muted ai-model";
          note.textContent = btn.dataset.modelLabel + ": " + j.model;
          out.appendChild(note);
        }
      }).catch(function (e) { out.textContent = e.message; })
        .then(function () { btn.disabled = false; out.classList.remove("thinking"); });
    });
  });
})();
