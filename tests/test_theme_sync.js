"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "static", "theme-sync.js"),
  "utf8"
);

function loadTheme(saved, systemDark) {
  let stored = saved;
  let systemListener = null;
  const root = { dataset: {}, style: {} };
  const meta = { content: "", setAttribute(_name, value) { this.content = value; } };
  const context = {
    window: {
      localStorage: {
        getItem() { return stored; },
        setItem(_key, value) { stored = value; },
      },
      matchMedia() {
        return {
          matches: systemDark,
          addEventListener(_name, listener) { systemListener = listener; },
        };
      },
    },
    document: {
      documentElement: root,
      querySelector() { return meta; },
      getElementById() { return null; },
    },
  };
  vm.runInNewContext(source, context);
  return {
    root,
    theme: context.window.vocabTheme,
    systemChange(dark) { systemListener({ matches: dark }); },
    saved() { return stored; },
  };
}

const following = loadTheme(null, false);
assert.equal(following.root.dataset.theme, "light");
following.systemChange(true);
assert.equal(following.root.dataset.theme, "dark");

following.theme.setManual(false);
assert.equal(following.saved(), "light");
following.systemChange(true);
assert.equal(following.root.dataset.theme, "light");

const existingPreference = loadTheme("dark", false);
assert.equal(existingPreference.root.dataset.theme, "dark");

/* ---------- theme-head.js：首帧前应用主题（外部脚本，CSP 不拦截） ---------- */

const headSource = fs.readFileSync(
  path.join(__dirname, "..", "app", "static", "theme-head.js"),
  "utf8"
);

function loadHeadTheme(saved, systemDark) {
  let stored = saved;
  const root = { dataset: {}, style: {} };
  const meta = { content: "", setAttribute(_name, value) { this.content = value; } };
  const storage = {
    getItem() { return stored; },
    setItem(_key, value) { stored = value; },
  };
  const context = {
    // 浏览器里 localStorage / matchMedia 都是 window 全局；
    // vm 上下文需要同时在顶层和 window 上暴露。
    localStorage: storage,
    window: {
      localStorage: storage,
      matchMedia() {
        return { matches: systemDark, addEventListener() {}, addListener() {} };
      },
    },
    document: {
      documentElement: root,
      querySelector() { return meta; },
    },
  };
  vm.runInNewContext(headSource, context);
  return { root, meta };
}

const headFollowsSystem = loadHeadTheme(null, true);
assert.equal(headFollowsSystem.root.dataset.theme, "dark");
assert.equal(headFollowsSystem.root.style.backgroundColor, "#0f1420");
assert.equal(headFollowsSystem.root.style.colorScheme, "dark");
assert.equal(headFollowsSystem.meta.content, "#0f1420");
assert.equal(loadHeadTheme(null, false).root.dataset.theme, "light");

const headManualDark = loadHeadTheme("dark", false);
assert.equal(headManualDark.root.dataset.theme, "dark");
const headManualLight = loadHeadTheme("light", true);
assert.equal(headManualLight.root.dataset.theme, "light");

/* ---------- 模板与 CSP：主题初始化必须是外部脚本且先于样式表 ---------- */

// CSP script-src 'self' 会拦截内联脚本：若主题初始化放在内联 <script> 里，
// 首帧会以日间模式绘制，夜间模式刷新（Command+R）时闪一帧日间模式。
for (const name of ["landing.html", "login.html"]) {
  const html = fs.readFileSync(
    path.join(__dirname, "..", "app", "templates", name),
    "utf8"
  );
  assert.ok(!html.includes("<script>"), name + " 不含内联 <script>（会被 CSP 拦截）");
  const head = html.slice(0, html.indexOf("</head>"));
  const headScript = head.indexOf("theme-head.js");
  assert.ok(headScript !== -1, name + " 在 <head> 中加载 theme-head.js");
  assert.ok(
    headScript < head.indexOf('rel="stylesheet"'),
    name + " theme-head.js 先于样式表加载，保证首帧即夜间模式"
  );
}

console.log("theme system-sync checks passed");
