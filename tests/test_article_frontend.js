"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "static", "landing-v51.js"),
  "utf8"
);
const template = fs.readFileSync(
  path.join(__dirname, "..", "app", "templates", "landing.html"),
  "utf8"
);

// 今日短文来源固定为今天点过「重来」的卡片：页面不再提供来源下拉框，
// 生成请求也不带 source 参数。
assert.doesNotMatch(source, /getElementById\("real-article-source"\)/);
assert.doesNotMatch(source, /JSON\.stringify\(\{ source \}\)/);
assert.doesNotMatch(template, /id="real-article-source"/);
assert.doesNotMatch(template, /option value="again"/);
assert.match(template, /使用今天点过「重来」的单词/);
assert.match(source, /generation\.state === "failed"/);
assert.match(source, /waitForRealArticle\(10 \* 60\)/);
assert.doesNotMatch(source, /连接中断，正在确认文章是否已生成/);

console.log("article frontend recovery checks passed");
