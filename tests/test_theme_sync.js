"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "static", "theme-sync.js"),
  "utf8"
);

function loadTheme(saved, systemDark) {
  let stored = saved;
  let systemListener = null;
  const root = { dataset: {}, style: {} };
  const meta = { content: "", setAttribute(_name, value) { this.content = value; } };
  const context = {
    window: {
      localStorage: {
        getItem() { return stored; },
        setItem(_key, value) { stored = value; },
      },
      matchMedia() {
        return {
          matches: systemDark,
          addEventListener(_name, listener) { systemListener = listener; },
        };
      },
    },
    document: {
      documentElement: root,
      querySelector() { return meta; },
      getElementById() { return null; },
    },
  };
  vm.runInNewContext(source, context);
  return {
    root,
    theme: context.window.vocabTheme,
    systemChange(dark) { systemListener({ matches: dark }); },
    saved() { return stored; },
  };
}

const following = loadTheme(null, false);
assert.equal(following.root.dataset.theme, "light");
following.systemChange(true);
assert.equal(following.root.dataset.theme, "dark");

following.theme.setManual(false);
assert.equal(following.saved(), "light");
following.systemChange(true);
assert.equal(following.root.dataset.theme, "light");

const existingPreference = loadTheme("dark", false);
assert.equal(existingPreference.root.dataset.theme, "dark");

console.log("theme system-sync checks passed");
