(function (global) {
  "use strict";

  const storageKey = "vocabtool.theme";
  const skinStorageKey = "vocabtool.skin";
  const systemTheme = global.matchMedia("(prefers-color-scheme: dark)");

  function savedTheme() {
    try {
      const value = global.localStorage.getItem(storageKey);
      return value === "dark" || value === "light" ? value : null;
    } catch (_) {
      return null;
    }
  }

  function savedSkin() {
    try {
      const value = global.localStorage.getItem(skinStorageKey);
      return value === "studio" ? "studio" : "classic";
    } catch (_) {
      return "classic";
    }
  }

  function apply(dark) {
    const theme = dark ? "dark" : "light";
    const skin = document.documentElement.dataset.skin || savedSkin();
    document.documentElement.dataset.theme = theme;
    const bg = dark
      ? (skin === "studio" ? "#11161b" : "#0f1420")
      : (skin === "studio" ? "#faf8f5" : "#f7f9fd");
    document.documentElement.style.backgroundColor = bg;
    document.documentElement.style.colorScheme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      const themeColor = dark ? bg : (skin === "studio" ? "#b86b1b" : "#007AFF");
      meta.setAttribute("content", themeColor);
    }
    const accountLabel = document.getElementById("account-theme-toggle-label");
    if (accountLabel) accountLabel.textContent = dark ? "☀ 日间模式" : "☾ 夜间模式";
    const authToggle = document.getElementById("auth-theme-toggle");
    if (authToggle) authToggle.textContent = dark ? "☀" : "☾";
  }

  function applySkin(skinName) {
    const skin = skinName === "studio" ? "studio" : "classic";
    document.documentElement.dataset.skin = skin;
    try {
      global.localStorage.setItem(skinStorageKey, skin);
    } catch (_) { /* 隐私模式 */ }
    const dark = document.documentElement.dataset.theme === "dark";
    apply(dark);

    const isStudio = skin === "studio";
    const topbarLabel = document.getElementById("topbar-skin-label");
    if (topbarLabel) topbarLabel.textContent = isStudio ? "文墨工坊" : "经典模式";
    const accountSkinLabel = document.getElementById("account-skin-toggle-label");
    if (accountSkinLabel) accountSkinLabel.textContent = isStudio ? "切换为经典皮肤" : "切换为文墨工坊";
  }

  function getSkin() {
    return document.documentElement.dataset.skin || savedSkin();
  }

  function toggleSkin() {
    const current = getSkin();
    const target = current === "studio" ? "classic" : "studio";
    applySkin(target);
    return target;
  }

  function sync() {
    const saved = savedTheme();
    apply(saved ? saved === "dark" : systemTheme.matches);
    applySkin(savedSkin());
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

  global.vocabTheme = {
    apply,
    setManual,
    sync,
    applySkin,
    setSkin: applySkin,
    getSkin,
    toggleSkin,
  };
  sync();
})(window);
