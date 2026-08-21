const assert = require("assert");
const fs = require("fs");
const path = require("path");

const jsCode = fs.readFileSync(
  path.join(__dirname, "../app/static/landing-v51.js"),
  "utf8"
);

assert(
  jsCode.includes("function extractCardMnemonic"),
  "extractCardMnemonic must be defined"
);
assert(
  jsCode.includes("function renderMnemonicBox"),
  "renderMnemonicBox must be defined"
);
assert(
  jsCode.includes("data-mnemonic-card"),
  "data-mnemonic-card click handler must be supported"
);
assert(
  jsCode.includes("/api/cards/\" + cardId + \"/mnemonic"),
  "fetch mnemonic API path must match"
);

console.log("PASS: card mnemonic frontend checks passed");
