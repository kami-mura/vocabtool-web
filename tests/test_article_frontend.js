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

assert.match(source, /getElementById\("real-article-source"\)/);
assert.match(source, /body:\s*JSON\.stringify\(\{ source \}\)/);
assert.match(template, /id="real-article-source"/);
assert.match(template, /option value="again"/);
assert.match(source, /generation\.state === "failed"/);
assert.match(source, /waitForRealArticle\(10 \* 60\)/);
assert.doesNotMatch(source, /连接中断，正在确认文章是否已生成/);

console.log("article frontend recovery checks passed");
