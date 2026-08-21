(function (global) {
  "use strict";

  const storageKey = "vocabtool.theme";
  const systemTheme = global.matchMedia("(prefers-color-scheme: dark)");

  function savedTheme() {
    try {
      const value = global.localStorage.getItem(storageKey);
      return value === "dark" || value === "light" ? value : null;
    } catch (_) {
      return null;
    }
  }

  function apply(dark) {
    const theme = dark ? "dark" : "light";
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.backgroundColor = dark ? "#0f1420" : "#f7f9fd";
    document.documentElement.style.colorScheme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", dark ? "#0f1420" : "#007AFF");
    const accountLabel = document.getElementById("account-theme-toggle-label");
    if (accountLabel) accountLabel.textContent = dark ? "☀ 日间模式" : "☾ 夜间模式";
    const authToggle = document.getElementById("auth-theme-toggle");
    if (authToggle) authToggle.textContent = dark ? "☀" : "☾";
  }

  function sync() {
    const saved = savedTheme();
    apply(saved ? saved === "dark" : systemTheme.matches);
  }

  function setManual(dark) {
    try {
      global.localStorage.setItem(storageKey, dark ? "dark" : "light");
    } catch (_) { /* 隐私模式等场景仍在当前页面应用 */ }
    apply(dark);
  }

  function onSystemChange(event) {
    if (!savedTheme()) apply(event.matches);
  }

  if (typeof systemTheme.addEventListener === "function") {
    systemTheme.addEventListener("change", onSystemChange);
  } else if (typeof systemTheme.addListener === "function") {
    systemTheme.addListener(onSystemChange);
  }

  global.vocabTheme = { apply, setManual, sync };
  sync();
})(window);
