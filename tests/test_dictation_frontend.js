const fs = require("fs");
const path = require("path");
const assert = require("assert");

const ROOT = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(ROOT, "app", "static", "landing-v51.js"), "utf8");

function extractFunction(name) {
  const match = source.match(
    new RegExp("function " + name + "\\(([^)]*)\\) \\{[\\s\\S]*?\\n  \\}")
  );
  if (!match) throw new Error(name + " 函数未找到");
  return new Function("escapeHtml", match[0] + "\nreturn " + name + ";")((s) => String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"));
}

const renderDictationDiff = extractFunction("renderDictationDiff");

// 1. 空输入
const emptyRes = renderDictationDiff("", "opportunity");
assert.ok(emptyRes.includes("未输入"), "空输入应显示未输入标签");

// 2. 完全正确（忽略大小写与首尾空格）
const correctRes = renderDictationDiff(" Opportunity ", "opportunity");
assert.ok(correctRes.includes("✨ 拼写正确"), "完全正确应显示成功状态");

// 3. 拼写错误（多写/漏写/错写）
const wrongRes = renderDictationDiff("oportunity", "opportunity");
assert.ok(wrongRes.includes("is-wrong"), "错误拼写包含 is-wrong 类");
assert.ok(wrongRes.includes("is-missing") || wrongRes.includes("is-match"), "高亮匹配与缺失字符");

// 4. 听写卡正面释义默认隐藏且带提示释义按钮
assert.ok(source.includes('<div class="dictation-meaning-box" hidden>'), "听写卡正面释义默认隐藏");
assert.ok(source.includes("💡 提示释义"), "提示按钮初始文案为提示释义");

// 5. 听写卡反面目标词一行（含用户输入tag）与阅读卡风格例句
assert.ok(source.includes("dictation-word-row"), "目标词与用户输入同行");
assert.ok(source.includes("dictation-user-tag"), "包含用户输入对比tag");
assert.ok(source.includes("dictation-sentence-wrap"), "包含阅读卡风格例句");
assert.ok(!source.includes("dictation-sentence-row"), "例句不再单独有框");

// 6. 海报使用 VocabTool 品牌，包含日间文言格言与4项核心打卡数据
assert.ok(source.includes('ctx.fillText("VocabTool"'), "海报品牌名使用 VocabTool");
assert.ok(source.includes("VocabTool-打卡海报-"), "海报下载文件名为 VocabTool");
assert.ok(source.includes("REAL_FAMOUS_QUOTES"), "海报使用真实名人格言库");
assert.ok(source.includes("getRandomPosterQuote"), "海报随机抽取名言");
assert.ok(source.includes("学而时习之，不亦说乎？"), "包含孔子论语文言文原句");
assert.ok(source.includes("温故而知新，可以为师矣。"), "包含温故而知新原文");
assert.ok(source.includes("splitEnglishIntoLines"), "包含英文自适应折行防溢出引擎");
assert.ok(source.includes("splitCjkIntoLines"), "包含中文自适应折行防溢出引擎");
assert.ok(source.includes("drawWhaleLogo"), "包含灵动小鲸鱼动物Logo");
assert.ok(source.includes("vocabtool.com"), "包含底部网站 vocabtool.com");
assert.ok(source.includes("AI + 间隔重复"), "包含底部宗旨 AI + 间隔重复");
assert.ok(!source.includes("recent112"), "海报不再绘制热力图");

console.log("dictation frontend checks passed");
