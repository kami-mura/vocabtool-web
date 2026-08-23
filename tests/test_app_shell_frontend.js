const assert = require("assert");
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const template = fs.readFileSync(path.join(ROOT, "app", "templates", "landing.html"), "utf8");
const script = fs.readFileSync(path.join(ROOT, "app", "static", "landing-v51.js"), "utf8");
const styles = fs.readFileSync(path.join(ROOT, "app", "static", "style-app.css"), "utf8");

assert.ok(template.includes("style-app.css"), "页面必须加载登录态应用壳样式");
assert.ok(template.includes('id="review-focus-close"'), "复习专注模式必须提供明确退出按钮");
assert.ok(template.includes("继续向前一点"), "今日视图必须保留清晰的学习主标题");
assert.ok(template.includes("整理与制作学习卡片"), "词库视图必须有独立标题");

assert.ok(
  script.includes("return isLoggedIn || isMobileLayout();"),
  "登录后桌面和手机必须共用四视图导航"
);
assert.ok(script.includes('document.body.dataset.appView = view;'), "切换视图时必须同步页面状态");
assert.ok(script.includes('document.body.classList.add("review-focus")'), "开始学习必须进入专注模式");
assert.ok(script.includes('document.body.classList.remove("review-focus")'), "专注模式必须可以退出");

assert.ok(
  styles.includes('.landing-body[data-logged-in="true"] .mobile-nav'),
  "应用壳样式必须只作用于登录态"
);
assert.ok(styles.includes(".review-focus #real-review"), "专注复习必须拥有独立布局");
assert.ok(styles.includes("@media (max-width: 820px)"), "应用壳必须包含手机布局");

console.log("app shell frontend assertions passed");
