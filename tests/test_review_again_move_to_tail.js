const fs = require("fs");
const path = require("path");
const assert = require("assert");

const ROOT = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(ROOT, "app", "static", "landing-v51.js"), "utf8");

/* ---------- 重来/困难卡“直接移到队尾”，剩余数不跳动 ---------- */

// 评“重来/困难”的卡今天还要学：前端必须先移到队尾而不是先删掉等
// 服务器确认后再补回来，否则剩余数量会 N -> N-1 -> N 跳动。
assert.match(
  source,
  /if \(willRelearn\) \{\s*\/\/ 重来\/困难卡今天还要学[\s\S]*?realReviewQueue\.splice\(index, 1\);[\s\S]*?realReviewQueue\.push\(\{[\s\S]*?queue_kind: "again",/,
  "重来/困难卡乐观更新为直接移动到队尾，而不是先删后补"
);
assert.match(
  source,
  /function shouldRepeatReviewToday\(rating, card\) \{[\s\S]*?return Boolean\(card\.repeat_now\);/,
  "回队判断只使用服务端 repeat_now"
);
assert.match(
  source,
  /else if \(willRelearn\) \{\s*\/\/ 服务端认为它今天不再回队[\s\S]*?realReviewQueue\.splice\(removeIndex, 1\);/,
  "服务端判定不再回队时移除本地暂留/乐观副本"
);
assert.match(
  source,
  /if \(willRelearn\) \{\s*\/\/ 移除“重来\/困难”的队尾乐观副本，把原卡放回原位置。/,
  "评分失败时移除乐观副本并恢复原卡位置"
);

/* ---------- 防止撤回后的旧服务端快照覆盖新评分 ---------- */

assert.match(
  source,
  /let realReviewQueueVersion = 0;/,
  "队列本地版本号已声明"
);
assert.match(
  source,
  /const loadVersion = realReviewQueueVersion;[\s\S]*?if \(loadVersion !== realReviewQueueVersion\) return;/,
  "loadRealReview 返回时丢弃过期快照"
);
assert.match(
  source,
  /realReviewInFlight \+= 1;\s*realReviewQueueVersion \+= 1;/,
  "评分开始前递增版本号，使在途旧快照失效"
);
assert.match(
  source,
  /撤回本身也是本地队列改动[\s\S]*?realReviewQueueVersion \+= 1;/,
  "撤回成功时递增版本号，防止旧快照覆盖"
);

console.log("PASS: 重来/困难卡直接移到队尾，剩余数不跳动；旧快照不会覆盖新评分");
