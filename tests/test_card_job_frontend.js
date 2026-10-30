"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const source = fs.readFileSync(path.join(__dirname, "../app/static/landing-v51.js"), "utf8");
const code = source.slice(source.indexOf("  const realCardGenerate ="), source.indexOf("  /* ---------- 登录后初始化 ---------- */"));

function harness(startResponse) {
  const button = { disabled: false };
  const status = { textContent: "" };
  const progressText = { textContent: "" };
  const elements = {
    "real-card-generate": button, "real-card-status": status,
    "real-card-words": { value: "run\nwalk" }, "real-card-result": { innerHTML: "" },
    "real-card-progress-wrap": { hidden: true }, "real-card-progress-fill": { style: {} },
    "real-card-progress-text": progressText,
  };
  const timers = new Map();
  const requests = [];
  let serial = 0;
  let finished = false;
  const context = {
    Date, console, realCardType: "reading",
    document: { getElementById: (id) => elements[id] || null },
    setInterval: (fn) => { const id = ++serial; timers.set(id, fn); return id; },
    clearInterval: (id) => timers.delete(id),
    escapeHtml: String, loadRealWords: async () => {}, loadRealCardQuota: () => {},
    requestReviewRefresh: () => {},
    fetch: async (url, options) => {
      requests.push({ url, options });
      if (url.includes("/cards?")) {
        if (startResponse instanceof Error) throw startResponse;
        return startResponse;
      }
      return { ok: true, json: async () => ({ progress: finished
        ? { done: true, finished_wall: Date.now() / 1000, result: { created: 1000, existing: 0, failed: [], processed: 1000 } }
        : { completed: 10, total: 1000, detail: "生成中" } }) };
    },
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  return { button, status, progressText, timers, requests, finish: () => { finished = true; } };
}
const response = (status, data = {}) => ({ status, ok: status >= 200 && status < 300, json: async () => data });
const flush = () => new Promise(setImmediate);

async function testBackgroundStatesKeepPolling(startResponse) {
  const h = harness(startResponse);
  await h.button.onclick();
  await flush();
  assert.ok(h.requests[0].url.endsWith("?background=true"));
  assert.ok(h.button.disabled, "task must stay active while generation runs");
  assert.ok(h.timers.size > 0);
  assert.ok(!h.status.textContent.includes("生成失败"));
  const poll = Array.from(h.timers.values())[0];
  await poll();
  assert.ok(h.progressText.textContent.includes("10 / 1000"));
  h.finish();
  await poll();
  await flush();
  assert.equal(h.button.disabled, false);
  assert.equal(h.timers.size, 0);
  assert.ok(h.progressText.textContent.includes("1000 / 1000"));
}

(async () => {
  await testBackgroundStatesKeepPolling(response(202, { state: "generating" }));
  await testBackgroundStatesKeepPolling(response(409, { state: "generating" }));
  await testBackgroundStatesKeepPolling(response(524));
  await testBackgroundStatesKeepPolling(new TypeError("Failed to fetch"));
  const rejected = harness(response(429, { detail: "今日免费制卡额度已用完" }));
  await rejected.button.onclick();
  assert.equal(rejected.button.disabled, false);
  assert.equal(rejected.timers.size, 0);
  assert.ok(rejected.status.textContent.includes("今日免费制卡额度已用完"));
  console.log("PASS: 后台制卡、重复提交、网关断连继续轮询与额度拒绝");
})().catch((error) => { console.error(error); process.exitCode = 1; });
