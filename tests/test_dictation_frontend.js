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
assert.ok(emptyRes.includes("未输入拼写"), "空输入应显示未输入拼写");

// 2. 完全正确（忽略大小写与首尾空格）
const correctRes = renderDictationDiff(" Opportunity ", "opportunity");
assert.ok(correctRes.includes("✨ 拼写完全正确"), "完全正确应显示成功状态");
assert.ok(correctRes.includes("opportunity"), "显示正确目标词");

// 3. 拼写错误（多写/漏写/错写）
const wrongRes = renderDictationDiff("oportunity", "opportunity");
assert.ok(wrongRes.includes("is-wrong"), "错误拼写包含 is-wrong 类");
assert.ok(wrongRes.includes("is-missing") || wrongRes.includes("is-match"), "高亮匹配与缺失字符");

console.log("dictation frontend checks passed");
