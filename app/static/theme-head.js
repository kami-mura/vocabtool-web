/* 主题必须在首帧绘制前应用：CSP 的 script-src 'self' 会拦截内联脚本，
   所以主题初始化放在这个外部脚本里，并在 <head> 中先于样式表加载。
   夜间模式刷新时若主题晚于首帧应用，会先闪一帧日间模式再切回夜间。 */
(function () {
  "use strict";

  var saved = null;
  try {
    saved = localStorage.getItem("vocabtool.theme");
  } catch (_) { /* 隐私模式等场景继续走系统主题 */ }
  var dark =
    saved === "dark" ||
    (saved !== "light" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  var theme = dark ? "dark" : "light";
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.backgroundColor = dark ? "#0f1420" : "#f7f9fd";
  document.documentElement.style.colorScheme = theme;
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", dark ? "#0f1420" : "#007AFF");
})();
