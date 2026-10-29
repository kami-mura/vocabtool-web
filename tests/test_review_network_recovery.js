"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const source = fs.readFileSync(path.join(__dirname, "../app/static/landing-v51.js"), "utf8");
const code = source.slice(source.indexOf("  let reviewActionChain ="), source.indexOf("  async function undoRealReview("));

function harness(responses) {
  const status = { textContent: "" };
  const requests = [];
  let serial = 0;
  const context = {
    console, AbortController, Date,
    document: { getElementById: () => status, querySelector: () => null },
    window: { setTimeout: (callback) => { callback(); } },
    setTimeout: () => 1, clearTimeout: () => {},
    realReviewQueue: [{ id: 1, revision: 3, state: "scheduled", is_learning: true }],
    realReviewInFlight: 0, realReviewQueueVersion: 0, heldReviewAction: null,
    realUndoStatusTimer: null, realReviewHistory: [], realTodayStats: {},
    realReviewLastRatingAt: 0,
    reviewExampleExpired: () => false,
    renderRealReview: () => {}, prefetchRealAudio: () => {}, warmUpRealAudio: () => {},
    flushReviewRefreshIfIdle: () => {}, updateRealUndoButton: () => {},
    syncReviewManageRow: () => {}, loadTodayOverview: () => {}, updateTodayOverview: () => {},
    loadRealReview: async () => {}, newActionId: () => "action-" + (++serial),
    fetch: async (url, options) => {
      requests.push({ url, payload: JSON.parse(options.body), signal: options.signal });
      const response = responses.shift();
      assert.ok(response, "unexpected extra request");
      if (response instanceof Error) throw response;
      return response;
    },
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  return { context, status, requests };
}

function success() {
  return { ok: true, status: 200, json: async () => ({
    cards: [{ ok: true, card: { id: 1, revision: 4, is_learning: true, repeat_now: true } }],
    errors: [],
  }) };
}

async function testManualRetryReusesActionAfterLostResponse() {
  const h = harness([new TypeError("Failed to fetch"), new TypeError("Failed to fetch"), new TypeError("Failed to fetch"), success(), success()]);
  await h.context.rateRealReviewCardNow(1, "again");
  assert.equal(h.context.realReviewQueue.length, 1);
  assert.equal(h.context.realReviewQueue[0].revision, 3);
  assert.equal(h.context.realReviewInFlight, 0);
  assert.ok(h.status.textContent.includes("网络"));
  assert.ok(!h.status.textContent.includes("Failed to fetch"));
  await h.context.rateRealReviewCardNow(1, "again");
  const actions = h.requests.map((r) => r.payload.ratings[0].action_id);
  assert.equal(new Set(actions).size, 1, "manual retry must confirm the original action, not score again");
  assert.equal(h.context.realReviewQueue[0].revision, 4);
  assert.equal(h.status.textContent, "", "a successful manual retry must clear the failure message immediately");
  await h.context.rateRealReviewCardNow(1, "again");
  assert.notEqual(h.requests[4].payload.ratings[0].action_id, actions[0], "the next genuine rating needs a new action");
}

async function testBodyTransportFailureRetriesSameAction() {
  const h = harness([{ ok: true, status: 200, json: async () => { throw new TypeError("terminated"); } }, success()]);
  await h.context.rateRealReviewCardNow(1, "again");
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[0].payload.ratings[0].action_id, h.requests[1].payload.ratings[0].action_id);
  assert.equal(h.context.realReviewQueue[0].revision, 4);
  assert.equal(h.context.realReviewHistory.length, 1);
}

async function testAuthorizationFailureIsNotRetried() {
  const h = harness([{ ok: false, status: 401, json: async () => ({ detail: "请先登录" }) }]);
  await h.context.rateRealReviewCardNow(1, "again");
  assert.equal(h.requests.length, 1);
  assert.ok(h.status.textContent.includes("请先登录"));
  assert.equal(h.context.realReviewQueue[0].revision, 3);
}

async function testGatewayAndIncompleteResponsesRetryBoundedly() {
  const h = harness([
    { ok: false, status: 503, json: async () => ({ detail: "busy" }) },
    { ok: true, status: 200, json: async () => ({}) },
    success(),
  ]);
  await h.context.rateRealReviewCardNow(1, "again");
  assert.equal(h.requests.length, 3);
  assert.equal(new Set(h.requests.map((r) => r.payload.ratings[0].action_id)).size, 1);
  assert.equal(h.context.realReviewQueue[0].revision, 4);
  assert.equal(h.status.textContent, "");
}

async function testTimeoutPreservesCardAndStopsAfterThreeAttempts() {
  const timeout = () => Object.assign(new Error("aborted"), { name: "AbortError" });
  const h = harness([timeout(), timeout(), timeout()]);
  await h.context.rateRealReviewCardNow(1, "good");
  assert.equal(h.requests.length, 3);
  assert.equal(h.context.realReviewQueue[0].id, 1);
  assert.equal(h.context.realReviewHistory.length, 0);
  assert.ok(h.status.textContent.includes("保存超时"));
  assert.equal(h.context.realReviewInFlight, 0);
}

async function testTwoLearningCardsRecoverWithoutDuplication() {
  const h = harness([new TypeError("Failed to fetch"), new TypeError("Failed to fetch"), new TypeError("Failed to fetch"), success()]);
  h.context.realReviewQueue.push({ id: 2, revision: 1, is_learning: true });
  await h.context.rateRealReviewCardNow(1, "again");
  assert.deepEqual(Array.from(h.context.realReviewQueue, (card) => card.id), [1, 2]);
  await h.context.rateRealReviewCardNow(1, "again");
  assert.deepEqual(Array.from(h.context.realReviewQueue, (card) => card.id), [2, 1]);
  assert.equal(new Set(h.requests.map((r) => r.payload.ratings[0].action_id)).size, 1);
  assert.equal(h.context.realReviewHistory.length, 1);
}

async function testChangedRatingUsesNewAction() {
  const h = harness([new TypeError("Failed to fetch"), new TypeError("Failed to fetch"), new TypeError("Failed to fetch"), success()]);
  await h.context.rateRealReviewCardNow(1, "again");
  await h.context.rateRealReviewCardNow(1, "good");
  assert.notEqual(h.requests[3].payload.ratings[0].action_id, h.requests[0].payload.ratings[0].action_id);
  assert.equal(h.requests[3].payload.ratings[0].rating, "good");
  assert.equal(h.context.realReviewQueue.length, 0);
}

(async () => {
  await testManualRetryReusesActionAfterLostResponse();
  await testBodyTransportFailureRetriesSameAction();
  await testAuthorizationFailureIsNotRetried();
  await testGatewayAndIncompleteResponsesRetryBoundedly();
  await testTimeoutPreservesCardAndStopsAfterThreeAttempts();
  await testTwoLearningCardsRecoverWithoutDuplication();
  await testChangedRatingUsesNewAction();
  console.log("PASS: 评分网络中断后安全重试、响应读取中断恢复、成功清除错误提示");
})().catch((error) => { console.error(error); process.exitCode = 1; });
