"use strict";

// 守护制卡进度显示：
// 1. 服务端明确拒绝（额度用尽/超上限/校验失败）时必须显示真实原因并停止轮询，
//    不能统一伪装成“连接中断，生成仍在后台进行”。
// 2. 网络异常、网关超时、已存在任务均通过进度轮询确认结果。
// 3. 观望模式按“进度是否前进”判断卡死，而不是固定 6 分钟总超时。
// 4. 最终进度分母用后端实际处理数（processed），避免 500/1000 上限截断时数字错乱。

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "static", "landing-v51.js"),
  "utf8"
);

const start = source.indexOf("realCardGenerate.onclick");
const end = source.indexOf("/* ---------- 登录后初始化 ---------- */");
assert.ok(start >= 0 && end > start, "找到制卡提交处理函数");
const handler = source.slice(start, end);

assert.match(
  handler,
  /if \(!res\.ok\) \{[\s\S]*?stopCardProgressPoll\(\)/,
  "非 2xx 时显示错误并停止轮询"
);
assert.match(
  handler,
  /data\.detail \|\| data\.error/,
  "非 2xx 时展示服务端返回的真实原因"
);
assert.match(
  handler,
  /catch \(err\) \{[\s\S]*?连接中断/,
  "网络异常提示连接中断并保留进度轮询"
);
assert.ok(
  !/if \(!res\.ok\) throw new Error/.test(handler),
  "非 2xx 不再走“连接中断”分支"
);

assert.ok(source.includes("lastProgressAt"), "存在进度看门狗时间戳");
assert.match(
  source,
  /const doneCount = \(result\.created \|\| 0\) \+ \(result\.existing \|\| 0\)/,
  "最终进度按 已生成+已有+失败 计算"
);
assert.match(
  source,
  /result\.processed \|\| doneCount/,
  "最终进度分母使用后端实际处理数"
);

console.log("card progress frontend checks passed");
