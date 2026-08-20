const fs = require("fs");
const path = require("path");
const assert = require("assert");

const ROOT = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(ROOT, "app", "static", "landing-v51.js"), "utf8");

function extractFunction(name) {
  const match = source.match(
    new RegExp("function " + name + "\\(([^)]*)\\) \\{[\\s\\S]*?\\n  \\}")
  );
  if (!match) throw new Error(name + " 函数未找到");
  return new Function(match[0] + "\nreturn " + name + ";")();
}

const heatLevelForDay = extractFunction("heatLevelForDay");
assert.strictEqual(heatLevelForDay(0), 0, "0 张为空格");
assert.strictEqual(heatLevelForDay(1), 1);
assert.strictEqual(heatLevelForDay(4), 1);
assert.strictEqual(heatLevelForDay(5), 2);
assert.strictEqual(heatLevelForDay(9), 2);
assert.strictEqual(heatLevelForDay(10), 3);
assert.strictEqual(heatLevelForDay(19), 3);
assert.strictEqual(heatLevelForDay(20), 4);
assert.strictEqual(heatLevelForDay(200), 4);

const heatTooltipText = extractFunction("heatTooltipText");
assert.strictEqual(
  heatTooltipText({ date: "2026-08-20", total: 15, new_count: 3, review_count: 12 }),
  "8月20日：共学 15 张（新学 3 · 复习 12）",
  "提示框显示日期、总量、新学、复习"
);
assert.strictEqual(
  heatTooltipText({ date: "2026-01-05", total: 0, new_count: 0, review_count: 0 }),
  "1月5日：未学习",
  "无记录显示未学习"
);

const heatMonthLabels = extractFunction("heatMonthLabels");
const days = [];
for (let i = 0; i < 31; i += 1) {
  days.push({ date: "2026-08-" + String(i + 1).padStart(2, "0"), total: 0 });
}
const labels = heatMonthLabels(days);
assert.deepStrictEqual(
  labels.map((l) => l.month),
  [8],
  "31 天都在 8 月，只有一个月份标签"
);
assert.strictEqual(labels[0].col, 0, "8 月从第一列开始");

// 跨月：7 月 30 日 ~ 8 月 3 日，8 月标签出现在 7 月部分所在的列之后。
const crossDays = [
  { date: "2026-07-30", total: 0 },
  { date: "2026-07-31", total: 0 },
  { date: "2026-08-01", total: 0 },
  { date: "2026-08-02", total: 0 },
  { date: "2026-08-03", total: 0 },
];
const crossLabels = heatMonthLabels(crossDays);
assert.deepStrictEqual(
  crossLabels,
  [{ month: 7, col: 0 }, { month: 8, col: 0 }],
  "8 月与 7 月同在第一列（不足一周）时按列取整"
);
