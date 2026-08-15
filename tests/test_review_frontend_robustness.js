const fs = require("fs");
const path = require("path");
const assert = require("assert");

const ROOT = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(ROOT, "app", "static", "landing-v51.js"), "utf8");

/* ---------- 电脑端评分点击“无响应”修复 ---------- */

// 评分请求必须有超时：网络/服务器长时间无响应时主动失败，
// 否则 reviewActionChain 串行链被挂起请求永久卡住，之后所有评分点击
// 都会排队、看起来“点了没反应”。
assert.match(
  source,
  /fetch\("\/api\/cards\/reviews\/batch", \{\s*method: "POST",\s*headers: \{ "Content-Type": "application\/json" \},\s*body: JSON\.stringify\(payload\),\s*signal: controller\.signal,\s*\}/,
  "评分请求携带 AbortController signal，超时后主动失败"
);
assert.match(
  source,
  /const controller = new AbortController\(\);\s*const timer = setTimeout\(\(\) => controller\.abort\(\), 15000\);/,
  "评分请求 15 秒超时"
);
assert.match(
  source,
  /if \(error\.status && !\[502, 503, 504\]\.includes\(error\.status\)\) throw error;/,
  "超时（AbortError 无 status）与 502/503/504 一样走自动重试"
);

// 按钮对应的卡已不在本地队列时，不能静默忽略（否则点击“没反应”）：
// 必须以服务端为准重拉队列，失败时保留现有队列不清空。
assert.match(
  source,
  /if \(index < 0\) \{\s*\/\/ 按钮对应的卡已不在本地队列[\s\S]*?await loadRealReview\(true\);\s*return;\s*\}/,
  "失效评分按钮点击不再静默，重拉服务端队列对齐"
);
assert.match(
  source,
  /async function loadRealReview\(preserveOnError = false, preferredHeadId = null\)/,
  "loadRealReview 支持失败保留现有队列（后台对齐不清空学习界面），并支持首选队首卡"
);
assert.match(
  source,
  /if \(preserveOnError\) return;/,
  "后台对齐失败时保留现有队列"
);

/* ---------- 撤回显示上一张卡 ---------- */

// 撤回必须把被撤回的卡放回队首立即展示（它是用户上一张评分的卡），
// 而不是按分区排序插入后展示队列里的下一张卡。
assert.match(
  source,
  /realReviewQueue = realReviewQueue\.filter\(\(q\) => q\.id !== restored\.id\);\s*realReviewQueue\.unshift\(item\);/,
  "撤回的卡移除旧副本后放回队首，立即作为当前卡展示"
);

// 后台与服务端对齐时保持撤回的卡在队首：loadRealReview 接受首选队首卡 id，
// 服务端队列按「到期复习 → 今日新学 → 学习中的卡」重新排序后，
// 仍把该卡放回队首，避免撤回的卡被排序顶走。
const headMatch = source.match(
  /function moveCardToHead\(queue, id\) \{[\s\S]*?\n  \}/
);
if (!headMatch) throw new Error("moveCardToHead 函数未找到");
const moveCardToHead = new Function(
  `${headMatch[0].replace(/^function /, "function ")}\nreturn moveCardToHead;`
)();

const q = (id, kind) => ({ id, queue_kind: kind });
// 服务端队列中撤回的卡（id=9）按新学区排序不在队首时，同步后仍移到队首。
const queue = [q(1, "new"), q(9, "new"), q(3, "new")];
moveCardToHead(queue, 9);
assert.deepStrictEqual(
  queue.map((x) => x.id),
  [9, 1, 3],
  "同步后仍把撤回的卡保持在队首"
);
// 首选卡已在队首或不在队列中时不改动队列。
const queueAtHead = [q(1, "new"), q(2, "new")];
moveCardToHead(queueAtHead, 1);
assert.deepStrictEqual(
  queueAtHead.map((x) => x.id),
  [1, 2],
  "首选卡已在队首时保持原样"
);
const queueMissing = [q(1, "new")];
moveCardToHead(queueMissing, 99);
assert.deepStrictEqual(
  queueMissing.map((x) => x.id),
  [1],
  "首选卡不在队列时不改动（如恢复的卡已不在今天队列）"
);

console.log("PASS: 评分请求超时、失效按钮对齐与撤回显示上一张卡符合预期");
