const fs = require("fs");
const path = require("path");
const assert = require("assert");

const SRC = path.join(__dirname, "..", "app", "static", "landing-v51.js");
const source = fs.readFileSync(SRC, "utf8");

const match = source.match(/function syncReviewManageRow\(\) \{[\s\S]*?\n  \}/);
if (!match) throw new Error("syncReviewManageRow 函数未找到");
const body = match[0].replace(/^function syncReviewManageRow\(\) \{/, "").replace(/\n  \}$/, "");

function rowHidden(queueLen, historyLen, canUndo, totalCards) {
  const row = { hidden: false };
  const doc = {
    querySelector(sel) {
      if (sel !== ".real-review-manage-row") throw new Error("意外的选择器: " + sel);
      return row;
    },
  };
  const fn = new Function(
    "realReviewQueue",
    "realReviewHistory",
    "realReviewCanUndo",
    "realReviewTotalCards",
    "doc",
    "row",
    body.replace("document.querySelector", "doc.querySelector") + "\nreturn row;"
  );
  return fn(
    { length: queueLen },
    { length: historyLen },
    canUndo,
    totalCards,
    doc,
    row
  ).hidden;
}

assert.strictEqual(
  rowHidden(0, 0, false, 0),
  true,
  "新用户没有任何卡片：隐藏管理行"
);
assert.strictEqual(
  rowHidden(1, 0, false, 5),
  false,
  "队列非空：显示管理行"
);
assert.strictEqual(
  rowHidden(0, 1, false, 5),
  false,
  "有可撤回历史：显示管理行"
);
assert.strictEqual(
  rowHidden(0, 0, true, 5),
  false,
  "可撤回：显示管理行"
);
assert.strictEqual(
  rowHidden(0, 0, false, 5),
  false,
  "学完今天所有卡片但用户已有卡片：必须保留卡片管理菜单（桌面端管理区标签已隐藏，菜单是唯一管理入口）"
);

console.log("PASS: syncReviewManageRow 显示/隐藏规则全部符合预期");
