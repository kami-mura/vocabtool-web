"use strict";

// 守护栏杆：词库搜索必须有防抖与请求序号守卫（见 docs/审查整改清单.md P2-11），
// 防止每个按键直接发请求、慢响应覆盖快响应导致列表与输入框不一致。

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "static", "landing-v51.js"),
  "utf8"
);

function testSearchInputIsDebounced() {
  const binding = source.match(
    /realLibrarySearch\.addEventListener\("input"[\s\S]{0,400}?\}\);/
  );
  assert.ok(binding, "real-library-search 必须绑定 input 事件");
  assert.ok(
    /clearTimeout\(searchDebounceTimer\)/.test(binding[0]) &&
      /setTimeout\(\(\) => loadRealWords\(\), \d+\)/.test(binding[0]),
    "input 事件必须先 clearTimeout 再 setTimeout 防抖，实际绑定：\n" + binding[0]
  );
}

function testLoadRealWordsHasVersionGuard() {
  assert.ok(
    /const loadVersion = \+\+realWordsLoadVersion;/.test(source),
    "loadRealWords 必须递增请求序号 realWordsLoadVersion"
  );
  const guardCount = (source.match(
    /loadVersion !== realWordsLoadVersion\) return;/g
  ) || []).length;
  assert.ok(
    guardCount >= 2,
    "成功与失败两条路径都必须校验序号后丢弃过期响应，实际 " + guardCount + " 处"
  );
}

testSearchInputIsDebounced();
testLoadRealWordsHasVersionGuard();
console.log("library search debounce checks passed");
