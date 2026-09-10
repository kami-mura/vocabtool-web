"use strict";

// 守护栏杆：提取目标词的两条本地路径（粘贴词表 / AI 主题）
// 必须送后端做同类型去重（见 docs/审查整改清单.md 之外的产品修改：
// 提取时去掉已有相同卡片类型的词）。此前仅在勾选 NGSL 筛选时才走后端。

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "static", "landing-v51.js"),
  "utf8"
);

function testNoEarlyReturnInRefine() {
  // 旧实现 options.enabled 为 false 时直接返回原词，绕过去重。
  assert.ok(
    !/if\s*\(!options\.enabled\)\s*return words;/.test(source),
    "refineRealCardTargets 不允许保留「未启用筛选就跳过」的早退分支"
  );
}

function testWordlistAndTopicAlwaysRefined() {
  const refineCalls = (source.match(/refineRealCardTargets\(words, source\)/g) || []).length;
  assert.ok(
    refineCalls >= 2,
    "wordlist 与 topic 两条提取路径都必须调用 refineRealCardTargets，实际 " +
      refineCalls + " 处"
  );
}

function testRefineSendsCardType() {
  const start = source.indexOf("async function refineRealCardTargets");
  const end = source.indexOf("if (realCardNgslFilter)", start);
  assert.ok(start >= 0 && end > start, "缺少 refineRealCardTargets 函数");
  const body = source.slice(start, end);
  assert.ok(
    /card_type:\s*realCardType/.test(body),
    "去重请求必须带当前选择的卡片类型"
  );
}

testNoEarlyReturnInRefine();
testWordlistAndTopicAlwaysRefined();
testRefineSendsCardType();
console.log("card targets dedup checks passed");
