"use strict";

// 守护栏杆：
// 1) 剩余学习数必须采用服务端真实计数。队列被服务端按上限截断
//    （到期 1000 / 重复 500）时，本地队列表长度小于真实剩余数，
//    前端若用本地长度重算，会让剩余数越算越小并提前判定「今天的学习完成」。
// 2) 划词浮层必须有请求序号守卫，慢响应不得覆盖后发出的查询结果。

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "static", "landing-v51.js"),
  "utf8"
);

// 从源码中按花括号配平提取一个具名函数（跳过字符串内的括号）。
function extractFunction(name) {
  const start = source.indexOf("function " + name + "(");
  assert.ok(start >= 0, "未找到函数 " + name);
  let index = source.indexOf("{", start);
  assert.ok(index >= 0, "函数 " + name + " 缺少函数体");
  let depth = 0;
  let quote = null;
  for (; index < source.length; index += 1) {
    const char = source[index];
    if (quote) {
      if (char === "\\") index += 1;
      else if (char === quote) quote = null;
      continue;
    }
    if (char === '"' || char === "'" || char === "`") {
      quote = char;
    } else if (char === "{") {
      depth += 1;
    } else if (char === "}") {
      depth -= 1;
      if (depth === 0) break;
    }
  }
  return source.slice(start, index + 1);
}

// 把三个函数放进一个带同样状态变量的沙箱里真实执行。
const harness = new Function(
  [
    "let realReviewQueue = [];",
    "let realReviewServerRemaining = null;",
    "let realReviewLoadedCounts = null;",
    "let realReviewRemaining = {};",
    "let realReviewRemainingTotal = 0;",
    extractFunction("realReviewKindCounts"),
    extractFunction("realReviewQueueSurplus"),
    extractFunction("recalcRealReviewRemaining"),
    "return {",
    "  setState(queue, server, loaded) {",
    "    realReviewQueue = queue;",
    "    realReviewServerRemaining = server;",
    "    realReviewLoadedCounts = loaded;",
    "  },",
    "  recalc() { recalcRealReviewRemaining(); },",
    "  remaining() { return realReviewRemaining; },",
    "  total() { return realReviewRemainingTotal; },",
    "};",
  ].join("\n")
)();

function dueCards(count) {
  return Array.from({ length: count }, (_, index) => ({
    id: index + 1,
    queue_kind: "due",
  }));
}

function testTruncatedQueueKeepsServerRemaining() {
  // 服务端真实剩余 1003，队列按上限只返回 1000。
  harness.setState(dueCards(1000), { new: 0, due: 1003, again: 0 }, {
    new: 0,
    due: 1000,
    again: 0,
  });
  harness.recalc();
  assert.strictEqual(
    harness.total(),
    1003,
    "队列被截断时剩余总数必须是服务端真实值 1003，实际 " + harness.total()
  );
  assert.strictEqual(harness.remaining().due, 1003);
}

function testRemainingTracksLocalProgress() {
  harness.setState(dueCards(1000), { new: 0, due: 1003, again: 0 }, {
    new: 0,
    due: 1000,
    again: 0,
  });
  // 学完 2 张后本地队列剩 998，剩余应为 1001（1003 - 2）。
  harness.setState(dueCards(998), { new: 0, due: 1003, again: 0 }, {
    new: 0,
    due: 1000,
    again: 0,
  });
  harness.recalc();
  assert.strictEqual(
    harness.total(),
    1001,
    "本地进度必须抵扣真实剩余，实际 " + harness.total()
  );
}

function testFinishedQueueReportsZero() {
  harness.setState([], { new: 0, due: 0, again: 0 }, {
    new: 0,
    due: 0,
    again: 0,
  });
  harness.recalc();
  assert.strictEqual(
    harness.total(),
    0,
    "真的学完时剩余数必须归零，否则『今天的学习完成』永远不会出现"
  );
}

function testFallsBackToLocalCountsWithoutServerCounts() {
  harness.setState(dueCards(3), null, null);
  harness.recalc();
  assert.strictEqual(harness.total(), 3, "缺少服务端计数时退回本地计数");
}

function testLocalCountsDoNotShrinkBelowServerValue() {
  // 队列没有被截断（本地长度 == 服务端剩余）时，行为与改动前一致。
  harness.setState(dueCards(5), { new: 0, due: 5, again: 0 }, {
    new: 0,
    due: 5,
    again: 0,
  });
  harness.recalc();
  assert.strictEqual(harness.total(), 5);
}

function testFloatingLookupHasVersionGuard() {
  assert.ok(
    /const floatingVersion = \+\+floatingLookupVersion;/.test(source),
    "lookupFloating 必须递增请求序号 floatingLookupVersion"
  );
  const guardCount = (source.match(
    /floatingVersion !== floatingLookupVersion\) return;/g
  ) || []).length;
  assert.ok(
    guardCount >= 2,
    "成功与失败两条路径都必须丢弃过期响应，实际 " + guardCount + " 处"
  );
}

const tests = [
  testTruncatedQueueKeepsServerRemaining,
  testRemainingTracksLocalProgress,
  testFinishedQueueReportsZero,
  testFallsBackToLocalCountsWithoutServerCounts,
  testLocalCountsDoNotShrinkBelowServerValue,
  testFloatingLookupHasVersionGuard,
];

tests.forEach((test) => {
  test();
  console.log("ok - " + test.name);
});
console.log("tests/test_review_remaining_counts.js: " + tests.length + " passed");
