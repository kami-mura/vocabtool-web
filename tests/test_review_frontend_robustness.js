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

/* ---------- 阅读卡例句直接显示（正面/反面） ---------- */

// 阅读卡正面直接显示例句：不允许再出现“例句”按钮 + 默认隐藏的旧逻辑。
assert.ok(
  !source.includes("reading-reveal-sentence"),
  "阅读卡正面不再有“例句”展开按钮"
);
assert.ok(
  !source.includes("reading-sentence-hidden"),
  "阅读卡正面例句不再默认隐藏"
);
assert.ok(
  !source.includes("data-real-reveal-sentence"),
  "不再监听例句展开按钮的点击"
);
assert.match(
  source,
  /card\.card_type === "reading"[\s\S]*?reading-word-row[\s\S]*?reading-sentence-wrap[\s\S]*?demo-front-text[\s\S]*?frontInner/,
  "阅读卡正面直接渲染例句内容（目标词行 + 例句 + 发音按钮）"
);
// 阅读卡反面：释义之后追加例句区块。
assert.match(
  source,
  /card\.card_type === "reading" && sentence && sentence !== target[\s\S]*?reading-back-sentence[\s\S]*?frontInner[\s\S]*?escapeHtml\(sentence\)/,
  "阅读卡反面在释义后追加例句（含高亮与发音按钮）"
);
const css = fs.readFileSync(path.join(ROOT, "app", "static", "style.css"), "utf8");
assert.match(
  css,
  /\.reading-back-sentence\s*\{/,
  "反面例句有独立样式（与释义分隔）"
);
assert.ok(
  !css.includes(".reading-sentence-hidden") && !css.includes(".reading-reveal-sentence"),
  "已删除不再使用的例句隐藏/展开按钮样式"
);
// 反面例句字体必须与正面完全一致：.demo-card-face.back p 会把反面所有 p
// 染成 muted/13px，必须有一条 ID 前缀规则把反面例句拉回正面样式。
assert.match(
  css,
  /#real-review \.reading-back-sentence \.demo-front-text\s*\{[\s\S]*?color:\s*var\(--text\);[\s\S]*?font-size:\s*17px;[\s\S]*?font-weight:\s*700;/,
  "反面例句颜色/字号/字重与正面 demo-front-text 完全一致"
);
const backPIdx = css.indexOf(".demo-card-face.back p");
const backSentenceIdx = css.indexOf("#real-review .reading-back-sentence .demo-front-text");
assert.ok(
  backPIdx !== -1 && backSentenceIdx !== -1 && backSentenceIdx > backPIdx,
  "反面例句覆盖规则必须定义在 .demo-card-face.back p 之后（特异性更高）"
);

console.log("PASS: 评分请求超时、失效按钮对齐、撤回显示上一张卡与阅读卡例句直显符合预期");
