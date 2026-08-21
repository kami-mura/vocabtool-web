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

/* ---------- 查询问答结果：文字颜色必须跟随日夜主题 ---------- */

const styleSource = fs.readFileSync(
  path.join(__dirname, "..", "app", "static", "style.css"),
  "utf8"
);
const glassStyleSource = fs.readFileSync(
  path.join(__dirname, "..", "app", "static", "style-glass.css"),
  "utf8"
);
const qaRichRule = styleSource.match(/\.qa-rich\s*\{[^}]*\}/);
assert.ok(qaRichRule, "存在查询问答结果 .qa-rich 样式");
assert.match(
  qaRichRule[0],
  /color\s*:\s*var\(--text\)/,
  "查询问答结果使用主题文字色，夜间模式不再显示为低对比度深色文字"
);

/* ---------- 全站目标词高亮：与阅读卡例句保持一致 ---------- */

const unifiedHighlightRule = styleSource.match(
  /mark\.word-highlight,\s*mark\.article-word,\s*\.demo-article mark,\s*\.ai-article-body mark,\s*\.target-word\s*\{[^}]*\}/
);
assert.ok(unifiedHighlightRule, "存在覆盖全站目标词的统一高亮规则");
assert.match(unifiedHighlightRule[0], /var\(--card-hl\)/, "统一使用阅读卡荧光笔背景");
assert.match(unifiedHighlightRule[0], /color\s*:\s*var\(--accent\)/, "统一使用阅读卡目标词颜色");
assert.match(
  styleSource,
  /\[data-theme="dark"\] mark\.word-highlight,[\s\S]*?var\(--card-hl\)[\s\S]*?color\s*:\s*var\(--accent\)/,
  "夜间模式沿用阅读卡的主题高亮变量"
);

/* ---------- 经典评分按钮：淡雅红、橙、绿、蓝渐变 ---------- */

const lightRatingRules = {
  again: "border-color: #f3c0c0",
  hard: "border-color: #eed9b8",
  good: "border-color: #f3d4a0",
  easy: "border-color: #b4e6cf",
};
const darkRatingRules = {
  again: "border-color: #7a3232",
  hard: "border-color: #7a5420",
  good: "border-color: #7a5420",
  easy: "border-color: #1e5c42",
};

const ratingBaseRule = styleSource.match(/(?:^|\n)\.rating\s*\{[^}]*\}/);
assert.ok(ratingBaseRule, "存在评分按钮基础样式");

for (const [rating, pattern] of Object.entries(lightRatingRules)) {
  const rule = styleSource.match(
    new RegExp(`(?:^|\\n)\\.rating\\.${rating}\\s*\\{[^}]*\\}`)
  );
  assert.ok(rule, `存在日间模式 ${rating} 评分按钮样式`);
  assert.ok(rule[0].includes(pattern), `${rating} 使用经典日间淡雅配色`);
}

for (const [rating, pattern] of Object.entries(darkRatingRules)) {
  const rule = styleSource.match(
    new RegExp(`\\[data-theme="dark"\\] \\.rating\\.${rating}\\s*\\{[^}]*\\}`)
  );
  assert.ok(rule, `存在夜间模式 ${rating} 评分按钮样式`);
  assert.ok(rule[0].includes(pattern), `${rating} 夜间模式保持经典淡雅色系`);
}

const landingSource = fs.readFileSync(
  path.join(__dirname, "..", "app", "static", "landing-v51.js"),
  "utf8"
);
for (const label of ["重来", "困难", "良好", "简单"]) {
  assert.ok(landingSource.includes(`<b>${label}</b>`), `真实评分按钮使用中文名称 ${label}`);
}
assert.ok(
  landingSource.includes('card.card_type === "general" ? " general-card"'),
  "真实通用卡带有独立字号类"
);
assert.match(
  glassStyleSource,
  /\.general-card \.demo-front-text\s*\{[^}]*font-size\s*:\s*28px/,
  "通用卡正面单词使用适中的 28px 字号"
);
assert.ok(landingSource.includes("exclude_easy: options.excludeEasy"), "提取请求携带 Easy 过滤选项");
assert.ok(landingSource.includes("result.limit_notice"), "制卡截断提示会显示给用户");
assert.ok(
  landingSource.includes('fetch("/api/card-studio/quota"'),
  "制卡前读取今日剩余额度"
);
assert.ok(landingSource.includes("今天还可免费制作"), "制卡前显示今日还能制作多少张");
assert.ok(landingSource.includes("<b>重来</b><small>现在</small>"), "重来 下方显示 现在");
assert.ok(landingSource.includes('previewLabel("hard", "现在")'), "困难 按钮支持 previewLabel 动态计算");
assert.ok(landingSource.includes('previewLabel("good", "学完")'), "良好 按钮支持 previewLabel 动态计算");
assert.ok(landingSource.includes("拼写更正："), "词源结果显示拼写更正说明");
assert.ok(
  landingSource.includes("以下显示正确拼写的词源结果"),
  "词源结果明确说明正在显示正确拼写的结果"
);

console.log("theme system-sync checks passed");
