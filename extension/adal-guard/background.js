// Адал Guard: режим экзамена в браузере.
// Пока идёт тест, браузер открывает только сайт Адал. Новые вкладки и окна закрываются,
// переходы на ИИ-сервисы блокируются, а попытки передаются на страницу теста,
// чтобы их увидел преподаватель. Решения о нарушениях принимает сервер Адал.
"use strict";

const AI_HOSTS = [
  "chatgpt.com", "openai.com", "chat.openai.com", "gemini.google.com", "bard.google.com",
  "aistudio.google.com", "notebooklm.google.com", "claude.ai", "anthropic.com", "perplexity.ai",
  "copilot.microsoft.com", "bing.com", "deepseek.com", "chat.deepseek.com", "poe.com", "you.com",
  "character.ai", "chat.mistral.ai", "mistral.ai", "grok.com", "x.ai", "kimi.com", "kimi.moonshot.cn",
  "chat.qwen.ai", "tongyi.aliyun.com", "yiyan.baidu.com", "huggingface.co", "phind.com",
  "giga.chat", "alice.yandex.ru", "ya.ru", "brainly.com", "znanija.com", "quizlet.com",
  "photomath.com", "mathway.com", "symbolab.com", "wolframalpha.com", "chegg.com", "studfile.net"
];
const AI_EXTENSION_WORDS = ["gpt", "chatgpt", "gemini", "claude", "copilot", "deepseek", "perplexity",
  "monica", "merlin", "sider", "maxai", "harpa", "ai assistant", "ии-помощник", "answer", "quizlet",
  "brainly", "photomath", "grammarly", "quillbot"];
const RULE_ALLOW = 1, RULE_BLOCK = 2, RULE_BLOCK_FRAME = 3;
const GRACE_SECONDS = 300;

function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch (e) { return ""; }
}
function isAiHost(host) {
  return AI_HOSTS.some((h) => host === h || host.endsWith("." + h));
}

async function getState() {
  return (await chrome.storage.session.get("exam")).exam || null;
}
async function setState(exam) {
  if (exam) await chrome.storage.session.set({exam});
  else await chrome.storage.session.remove("exam");
  await chrome.action.setBadgeText({text: exam ? "ON" : ""});
  if (exam) await chrome.action.setBadgeBackgroundColor({color: "#0f6b58"});
}

async function lockBrowser(host) {
  await chrome.declarativeNetRequest.updateDynamicRules({
    removeRuleIds: [RULE_ALLOW, RULE_BLOCK, RULE_BLOCK_FRAME],
    addRules: [
      {id: RULE_ALLOW, priority: 3, action: {type: "allow"},
       condition: {requestDomains: [host], resourceTypes: ["main_frame", "sub_frame"]}},
      {id: RULE_BLOCK, priority: 1, action: {type: "redirect", redirect: {extensionPath: "/blocked.html"}},
       condition: {urlFilter: "|http", resourceTypes: ["main_frame"]}},
      {id: RULE_BLOCK_FRAME, priority: 1, action: {type: "block"},
       condition: {urlFilter: "|http", resourceTypes: ["sub_frame"]}}
    ]
  });
}
async function unlockBrowser() {
  await chrome.declarativeNetRequest.updateDynamicRules({removeRuleIds: [RULE_ALLOW, RULE_BLOCK, RULE_BLOCK_FRAME]});
  await chrome.alarms.clear("exam-deadline");
}

async function report(kind, detail) {
  const exam = await getState();
  if (!exam) return;
  try {
    await chrome.tabs.sendMessage(exam.tabId, {type: "violation", kind, detail: (detail || "").slice(0, 190)});
  } catch (e) { /* вкладка теста перезагружается: событие повторится при следующей попытке */ }
}

async function scanExtensions() {
  const list = await chrome.management.getAll();
  const self = chrome.runtime.id;
  const suspicious = list.filter((x) => x.enabled && x.id !== self && x.type === "extension" &&
    AI_EXTENSION_WORDS.some((w) => (x.name + " " + x.description).toLowerCase().includes(w)));
  if (suspicious.length) report("ai_extension", suspicious.map((x) => x.name).join(", "));
}

async function startExam(msg, sender) {
  const tab = sender.tab;
  if (!tab || tab.id === undefined) return;
  const host = hostOf(tab.url);
  const previous = await getState();
  const exam = {tabId: tab.id, windowId: tab.windowId, host, attempt: msg.attempt,
                until: Date.now() + (Math.max(0, msg.seconds) + GRACE_SECONDS) * 1000};
  await setState(exam);
  await lockBrowser(host);
  chrome.alarms.create("exam-deadline", {when: exam.until});
  if (!previous || previous.attempt !== exam.attempt) {
    // Закрываем всё, что было открыто до начала теста, кроме самой вкладки теста.
    const tabs = await chrome.tabs.query({});
    for (const t of tabs) if (t.id !== tab.id) chrome.tabs.remove(t.id).catch(() => {});
    scanExtensions();
  }
}

async function endExam() {
  await setState(null);
  await unlockBrowser();
}

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (!msg) return;
  if (msg.type === "exam-start") startExam(msg, sender);
  else if (msg.type === "exam-end") endExam();
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "status") {
    getState().then((exam) => sendResponse({exam}));
    return true;
  }
});

chrome.tabs.onCreated.addListener(async (tab) => {
  const exam = await getState();
  if (!exam || tab.id === exam.tabId) return;
  chrome.tabs.remove(tab.id).catch(() => {});
  report("new_tab", "");
  chrome.tabs.update(exam.tabId, {active: true}).catch(() => {});
});

chrome.windows.onCreated.addListener(async (win) => {
  const exam = await getState();
  if (!exam || win.id === exam.windowId) return;
  chrome.windows.remove(win.id).catch(() => {});
  report("new_tab", "window");
});

chrome.webNavigation.onBeforeNavigate.addListener(async (details) => {
  if (details.frameId !== 0) return;
  const exam = await getState();
  if (!exam) return;
  const host = hostOf(details.url);
  if (!host || host === exam.host) return;
  if (isAiHost(host)) report("ai_site", host);
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const exam = await getState();
  if (exam && tabId === exam.tabId) endExam();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "exam-deadline") endExam();
});

// После перезапуска браузера режим экзамена не должен остаться включённым без теста.
chrome.runtime.onStartup.addListener(async () => {
  const exam = await getState();
  if (!exam || Date.now() > exam.until) endExam();
});
chrome.runtime.onInstalled.addListener(() => endExam());
