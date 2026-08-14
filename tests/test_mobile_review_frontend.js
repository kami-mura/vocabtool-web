const fs = require("fs");
const path = require("path");
const assert = require("assert");

const ROOT = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(ROOT, "app", "static", "landing-v51.js"), "utf8");
const css = fs.readFileSync(path.join(ROOT, "app", "static", "style.css"), "utf8");

const repeatMatch = source.match(
  /function shouldRepeatReviewToday\(rating, card, now = Date\.now\(\)\) \{[\s\S]*?\n  \}/
);
if (!repeatMatch) throw new Error("shouldRepeatReviewToday 函数未找到");
const shouldRepeatReviewToday = new Function(
  `${repeatMatch[0].replace(/^function /, "function ")}\nreturn shouldRepeatReviewToday;`
)();

const soon = new Date(Date.now() + 60_000).toISOString();
const learningCard = { is_learning: true, due_at: soon };
assert.strictEqual(shouldRepeatReviewToday("again", learningCard), true);
assert.strictEqual(shouldRepeatReviewToday("hard", learningCard), true);
assert.strictEqual(
  shouldRepeatReviewToday("good", learningCard),
  false,
  "点良好后同一卡不能立刻重新出现"
);
assert.strictEqual(
  shouldRepeatReviewToday("easy", learningCard),
  false,
  "点简单后同一卡不能立刻重新出现"
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
  source,
  /reviewActionChain\s*=\s*reviewActionChain\s*\.catch\(\(\) => undefined\)\s*\.then\(\(\) => rateRealReviewCardNow\(id, rating\)\)/,
  "上一次异常不能让后续简单评分永久失效"
);

console.log("PASS: 手机端评分卡触摸和同卡回队规则符合预期");
