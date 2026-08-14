const fs = require("fs");
const path = require("path");
const assert = require("assert");

const ROOT = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(ROOT, "app", "static", "landing-v51.js"), "utf8");
const css = fs.readFileSync(path.join(ROOT, "app", "static", "style.css"), "utf8");

/* ---------- 手机端与电脑端学习队列一致性 ---------- */

// 队列顺序必须完全以服务端为准：本地 sessionStorage 快照重排会让每个设备
// 各自固定一份过期顺序（重来/困难卡在本地被追加到队尾，服务端却按
// 到期时间放在重学区），导致手机端和电脑端看到不同的队列。
assert.ok(
  !source.includes("REVIEW_QUEUE_SNAPSHOT_KEY"),
  "队列顺序快照机制（sessionStorage）已移除，避免各设备本地重排"
);
assert.ok(
  !/sessionStorage\s*\.\s*(get|set|remove)Item\s*\(\s*["']vocabtool\.review\.queue/.test(source),
  "不再读写 vocabtool.review.queue 队列快照"
);
assert.match(
  source,
  /realReviewQueue\s*=\s*Array\.isArray\(data\.queue\)\s*\?\s*data\.queue\s*:\s*\[\];/,
  "加载时直接采用服务端返回的队列顺序"
);
assert.ok(
  !source.includes("restoreReviewQueueSnapshot"),
  "恢复快照的函数已删除"
);

// 评分后“重来/困难”的卡仍只在当前会话内追加到队尾（不弹回队首），
// 服务端队列同步把学习中的卡排最后，因此刷新后仍在队尾——该行为必须保留。
assert.match(
  source,
  /realReviewQueue\.push\(repeatItem\)/,
  "会话内重来卡仍追加到队尾，避免刚评过的卡弹回队首"
);
// 服务端队列顺序为「到期复习 → 今日新学 → 学习中的卡」：前端加载注释必须
// 同步该顺序，且撤回的卡按分区插入（insertRestoredCard），刷新前后位置一致。
assert.match(
  source,
  /队列顺序以服务端为准：服务端按“到期复习 → 今日新学 → 学习中的卡”/,
  "加载时直接采用服务端返回的队列顺序（学习中的卡排最后）"
);
assert.match(
  source,
  /function insertRestoredCard\(queue, item\) \{[\s\S]*?queue\.splice\(i, 0, item\);\s*\}/,
  "撤回的卡按服务端分区规则插入队列，不再无条件放到队首"
);
assert.ok(
  !/realReviewQueue\.unshift\(item\)/.test(source),
  "撤回不再用 unshift 放队首（服务端按到期时间分区排序）"
);

/* ---------- 手机端首页标语与结果栏宽度 ---------- */

// 皮肤样式表（style-liquid.css 等）在 style.css 之后加载，同级选择器会覆盖
// 媒体查询里的移动端规则：标语固定 40px 会把“查单词、记词汇，读文章”
// （9 个汉字 + 标点）挤出屏幕，并让整页横向溢出。必须用更高优先级选择器。
assert.match(
  css,
  /@media\s*\(max-width:\s*820px\)\s*\{\s*body\s+\.landing-hero\s+h1\s*\{[\s\S]*?font-size:\s*clamp\(17px,\s*5vw,\s*30px\)[\s\S]*?white-space:\s*nowrap;/,
  "移动端标语：body 前缀提升优先级，窄屏自动缩字号并保持单行"
);
assert.match(
  css,
  /@media\s*\(max-width:\s*700px\)\s*\{\s*body\s+\.landing-main\s*\{\s*padding:\s*20px\s+18px/,
  "移动端主容器：body 前缀提升优先级，恢复 18px 侧边距（结果栏不再偏窄）"
);
assert.match(
  css,
  /@media\s*\(max-width:\s*820px\)\s*\{[\s\S]*?body\s+\.landing-main\s*\{\s*padding:\s*16px\s+14px/,
  "平板宽度（701-820px）：同样不被皮肤固定 24px 边距覆盖"
);

/* ---------- 查询结果框左右内边距一致 ---------- */

// 结果框曾为关闭按钮预留右侧 44px 内边距，导致内容右侧空白（46px）明显
// 大于左侧（22px）。关闭按钮是绝对定位的，不需要内容让位。
const resultBoxRule = css.match(/\.landing-search-result\s*\{[\s\S]*?\n\}/);
assert.ok(resultBoxRule, "找到 .landing-search-result 规则");
assert.ok(
  !/padding-right\s*:\s*44px/.test(resultBoxRule[0]),
  "结果框不再为关闭按钮预留右侧 44px 内边距"
);
assert.match(
  resultBoxRule[0],
  /padding\s*:\s*16px\s+20px/,
  "结果框左右内边距一致（16px 20px），内容不再右侧留白过多"
);

console.log("PASS: 学习队列跨设备一致，移动端标语单行与结果栏宽度符合预期");
