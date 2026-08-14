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
  /async function loadRealReview\(preserveOnError = false\)/,
  "loadRealReview 支持失败保留现有队列（后台对齐不清空学习界面）"
);
assert.match(
  source,
  /if \(preserveOnError\) return;/,
  "后台对齐失败时保留现有队列"
);

/* ---------- 撤回卡分区插入 ---------- */

// 撤回的卡按恢复后的状态（due/new/again）插入对应分区，与服务端
// 「到期复习 → 今日新学 → 学习中的卡」排序一致，刷新后位置不变。
const insertMatch = source.match(
  /function insertRestoredCard\(queue, item\) \{[\s\S]*?\n  \}/
);
if (!insertMatch) throw new Error("insertRestoredCard 函数未找到");
const insertRestoredCard = new Function(
  `${insertMatch[0].replace(/^function /, "function ")}\nreturn insertRestoredCard;`
)();

const q = (kind, id, due) => ({ id, queue_kind: kind, due_at: due });
// 到期区按到期时间升序，新学区在中间，学习中的卡排最后。
const queue = [q("due", 1, "2026-08-10T00:00:00"), q("due", 2, "2026-08-12T00:00:00"), q("new", 3)];
insertRestoredCard(queue, q("due", 9, "2026-08-11T00:00:00"));
assert.deepStrictEqual(
  queue.map((x) => x.id),
  [1, 9, 2, 3],
  "到期卡按到期时间升序插入到期区"
);
insertRestoredCard(queue, q("again", 7));
assert.deepStrictEqual(
  queue.map((x) => x.id),
  [1, 9, 2, 3, 7],
  "学习中的卡插入队尾"
);
insertRestoredCard(queue, q("new", 8));
assert.deepStrictEqual(
  queue.map((x) => x.id),
  [1, 9, 2, 3, 8, 7],
  "今日新学插在到期区之后、学习中的卡之前"
);

console.log("PASS: 评分请求超时、失效按钮对齐与撤回卡分区插入符合预期");
