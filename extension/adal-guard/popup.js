document.getElementById("ver").textContent = chrome.runtime.getManifest().version;
chrome.runtime.sendMessage({type: "status"}, function (r) {
  var el = document.getElementById("state");
  var exam = r && r.exam;
  el.className = "state " + (exam ? "on" : "off");
  el.textContent = exam ? "Идёт тест: браузер открывает только Адал" : "Сейчас теста нет, браузер работает как обычно";
});
