const fs = require("fs");
const path = require("path");
const assert = require("assert");

const ROOT = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(ROOT, "app", "static", "landing-v51.js"), "utf8");
const css = fs.readFileSync(path.join(ROOT, "app", "static", "style.css"), "utf8");

const repeatMatch = source.match(
  /function shouldRepeatReviewToday\(rating, card\) \{[\s\S]*?\n  \}/
);
if (!repeatMatch) throw new Error("shouldRepeatReviewToday 函数未找到");
const shouldRepeatReviewToday = new Function(
  `${repeatMatch[0].replace(/^function /, "function ")}\nreturn shouldRepeatReviewToday;`
)();

const learningRepeatNow = { repeat_now: true };
assert.strictEqual(shouldRepeatReviewToday("again", learningRepeatNow), true);
assert.strictEqual(shouldRepeatReviewToday("hard", learningRepeatNow), true);
assert.strictEqual(
  shouldRepeatReviewToday("good", learningRepeatNow),
  false,
  "点良好后同一卡不能立刻重新出现"
);
assert.strictEqual(
  shouldRepeatReviewToday("easy", learningRepeatNow),
  false,
  "点简单后同一卡不能立刻重新出现"
);
assert.strictEqual(
  shouldRepeatReviewToday("again", { repeat_now: false }),
  false,
  "服务端判定今天不需要回队时，前端不得自行放回队列"
);

assert.match(
  css,
  /\.demo-card-face\.back\s*\{[^}]*pointer-events:\s*none;/s,
  "未翻面时背面不能接收手机触摸"
);
assert.match(
  css,
  /\.demo-card\.flipped \.demo-card-face\.front\s*\{\s*pointer-events:\s*none;/,
  "翻面后正面不能挡住评分按钮"
);
assert.match(
  css,
  /\.demo-card\.flipped \.demo-card-face\.back\s*\{\s*pointer-events:\s*auto;/,
  "翻面后评分按钮所在的背面必须可点击"
);
assert.match(
  css,
  /#real-review \.home-review-cards \.demo-card-inner,[\s\S]*?transition:\s*none;/,
  "真实复习卡（电脑/手机一致）不能保留导致命中错层的 3D 翻面过渡"
);
assert.match(
  css,
  /#real-review \.home-review-cards \.demo-card-face\.back\s*\{\s*display:\s*none;\s*\}/,
  "未翻面时背面必须彻底退出触摸命中"
);
assert.match(
  css,
  /#real-review \.home-review-cards \.demo-card\.flipped \.demo-card-face\.back\s*\{\s*display:\s*flex;\s*\}/,
  "翻面后评分面必须立即显示"
);
// 瞬时换面规则必须在媒体查询之外定义，电脑端同样生效
const globalRuleIdx = css.indexOf("#real-review .home-review-cards .demo-card-inner,");
const mediaStartIdx = css.indexOf("@media (max-width: 820px)");
assert.ok(
  globalRuleIdx !== -1 && mediaStartIdx !== -1 && globalRuleIdx < mediaStartIdx,
  "瞬时换面规则必须定义在媒体查询之前（全局生效，电脑端评分可点）"
);
assert.match(
  css,
  /#real-review \.home-review-cards \.demo-card-inner,[\s\S]*?transition:\s*none;/,
  "全局瞬时换面：transform:none + transition:none 必须成对出现"
);

assert.match(
  source,
  /reviewActionChain\s*=\s*reviewActionChain\s*\.catch\(\(\) => undefined\)\s*\.then\(\(\) => rateRealReviewCardNow\(id, rating\)\)/,
  "上一次异常不能让后续简单评分永久失效"
);
assert.match(
  source,
  /setTimeout\(\(\) => controller\.abort\(\), 8000\)/,
  "评分请求单次超时收紧为 8s，避免慢请求长时间卡住评分链"
);
assert.match(
  source,
  /REVIEW_CHAIN_STALL_MS\s*=\s*12000/,
  "评分链看门狗：上次评分超过 12s 未完成时丢弃旧链，保证点击立即生效"
);
assert.match(
  source,
  /let reviewChainBusySince = 0;/,
  "评分链在途请求开始时间戳必须在看门狗逻辑中维护"
);
assert.match(
  source,
  /if \(chainStallMs > REVIEW_CHAIN_STALL_MS\) \{[\s\S]*?reviewActionChain = Promise\.resolve\(\);[\s\S]*?loadRealReview\(true\);/,
  "看门狗触发时丢弃卡死的评分链并重拉队列对齐"
);
assert.match(
  source,
  /AbortController\(\)[\s\S]*?setTimeout\(\(\) => controller\.abort\(\), 15000\)[\s\S]*?\/api\/cards/,
  "loadRealReview 必须带超时，防止 /api/cards 挂起卡死评分链"
);

console.log("PASS: 评分卡触摸、同卡回队、评分链超时与看门狗规则符合预期");
