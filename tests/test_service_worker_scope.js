"use strict";

// 守护 Service Worker 作用域修复：注册必须显式带 { scope: "/" }，
// 否则 SW 默认作用域是 /static/，控制不了根路径页面，
// 离线兜底（offline.html）与静态缓存整体失效（见 docs/审查整改清单.md P1-2）。

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "static", "landing-v51.js"),
  "utf8"
);

function testRegisterCallContainsRootScope() {
  const registerMatch = source.match(/serviceWorker\s*\.\s*register\(\s*"[^"]*sw\.js"(.*?)\)/s);
  assert.ok(registerMatch, "landing-v51.js 必须注册 /static/sw.js");
  const args = registerMatch[1];
  assert.ok(
    /scope\s*:\s*"\/"/.test(args),
    'register 调用必须显式传 { scope: "/" }，实际参数：' + args
  );
}

function testMiddlewareHeaderPresent() {
  const mainPy = fs.readFileSync(
    path.join(__dirname, "..", "app", "main.py"),
    "utf8"
  );
  assert.ok(
    mainPy.includes('"Service-Worker-Allowed", "/"') ||
      mainPy.includes("'Service-Worker-Allowed', '/'"),
    "main.py 必须对 /static/sw.js 响应 Service-Worker-Allowed: / 头，" +
      "否则 scope:/ 注册会被浏览器拒绝"
  );
  assert.ok(
    mainPy.includes('"/static/sw.js"'),
    "main.py 需要按路径 /static/sw.js 精确匹配下发该头"
  );
}

function testSwStillCachesOfflineShell() {
  const sw = fs.readFileSync(
    path.join(__dirname, "..", "app", "static", "sw.js"),
    "utf8"
  );
  assert.ok(sw.includes('"/static/offline.html"'), "sw.js 需保留离线兜底页");
  assert.ok(sw.includes('"/api/"'), "sw.js 不能缓存 /api/ 用户数据");
}

testRegisterCallContainsRootScope();
testMiddlewareHeaderPresent();
testSwStillCachesOfflineShell();
console.log("service worker scope checks passed");
