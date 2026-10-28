const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const source = fs.readFileSync(path.join(__dirname, "..", "app/static/landing-v51.js"), "utf8");
const escapeHtml = (text) => String(text).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));
const context = {
  escapeHtml, plainTargetWord: String, renderMarkdown: escapeHtml,
  Date: { now: () => Date.parse("2026-10-01T16:00:00Z"), parse: Date.parse },
};
vm.createContext(context);
vm.runInContext(source.slice(
  source.indexOf("  function realReviewCardHtml("),
  source.indexOf("  function renderRealReview("),
), context);
vm.runInContext(source.slice(
  source.indexOf("  function reviewExampleExpired("),
  source.indexOf('  document.addEventListener("visibilitychange"'),
), context);
const card = {
  id: 1, word: "run", card_type: "reading", front: "He **runs** to catch the bus.",
  context: "He runs to catch the bus.", back: "v. | move quickly | 跑步",
  example_number: 2, example_count: 3, example_valid_until: "2026-10-02T16:00:00+00:00",
};
const html = context.realReviewCardHtml(card);
assert.ok(html.includes("今日例句 2/3"));
assert.ok(html.includes('data-real-audio="He runs to catch the bus."'));
assert.ok(!html.includes("She runs by the river"));
assert.ok(!context.realReviewCardHtml({ ...card, example_count: 1 }).includes("今日例句"));
assert.notEqual(context.realCardSignature(card), context.realCardSignature({ ...card, example_number: 3 }));
assert.equal(context.realCardSignature(card), context.realCardSignature({ ...card }));
assert.equal(context.reviewExampleExpired(card), false);
assert.equal(context.reviewExampleExpired({ ...card, example_valid_until: "2026-10-01T16:00:00+00:00" }), true);
assert.equal(context.reviewExampleExpired({}), false);
const cloze = context.realReviewCardHtml({
  ...card, card_type: "cloze", front: "He ______ to catch the bus.",
  back: "He **runs** to catch the bus.\n\n跑步",
});
assert.ok(cloze.includes("今日例句 2/3"));
assert.ok(!cloze.split('class="demo-card-face back"')[0].includes("data-real-audio"));
const hostile = context.realReviewCardHtml({ ...card, context: 'He runs.\" onclick=\"alert(1)' });
assert.ok(!hostile.includes(' onclick="alert(1)'));
console.log("PASS: 每日例句展示、音频、重新渲染与跨天过期检查");
