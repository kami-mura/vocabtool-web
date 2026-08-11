# VocabTool 外部 API 调用设计

本文只统计会离开本应用进程的外部服务调用。浏览器对 `/api/*` 的请求属于站内
接口，不等于第三方 API；数据库连接也不列入第三方 API。

## 一、外部服务总览

| 服务 | 调用位置 | 用途 | 是否必需 |
| --- | --- | --- | --- |
| DeepSeek 或 OpenAI | `app/ai.py` | 查词、词源、问答、主题词、优先词筛选、制卡、AI 短文 | 可选；未配置时相关 AI 功能不可用 |
| Microsoft 在线 TTS（经 `edge-tts`） | `app/tts.py` | 卡片、查词和文章朗读音频 | 可选；失败不影响文字学习 |
| Resend | `app/email_verification.py`、`deploy/notify-failure.sh` | 注册/重置验证码、账号提醒、运维告警 | 正式多用户部署需要 |
| Cloudflare R2（经 `rclone`） | `deploy/r2-backup.sh`、`deploy/r2-setup.sh` | 加密后的异地备份 | 推荐，但不在用户请求链路中 |

目前没有网页抓取、翻译、词典或第三方登录 API。NGSL、文章、卡片和学习数据都在
本地数据库/资源中处理。

## 二、全部 AI API 调用点与推荐参数

日常学习任务统一使用 `deepseek-v4-flash`。这些任务主要要求格式稳定和低延迟，
不需要深度推理，因此默认 `thinking=disabled`。只有用户显式要求深度分析时才开启
思考；短文生成固定使用思考模式 `max`，优先保证一篇长文覆盖全部目标词。

| 功能/入口 | 后端函数 | 温度 | 思考 | 强度 | 建议输出上限 | 建议超时/重试 |
| --- | --- | ---: | --- | --- | ---: | --- |
| 通用卡、阅读卡 | `generate_card_content_in_batches` / `_call_ai_card_batch` | 0.2–0.3 | 关闭 | 无 | 每 10 词 4096 | 60 秒；仅瞬时网络错误重试 1 次 |
| Cloze 卡 | 同上，`card_template=cloze` | 0.1–0.2 | 关闭 | 无 | 每 5 词 2048 | 60 秒；重试 1 次 |
| 口语卡 | 同上，`card_template=speaking` | 0.3–0.4 | 关闭 | 无 | 每 10 条 4096 | 60 秒；重试 1 次 |
| 普通查词 `/api/lookups` | `explain_lookup` | 0.2；修复 0.15 | 关闭 | 无 | 2048 | 30 秒；错误输入修复 1 次 |
| 词源速查 `/api/lookups/quick` | `quick_lookup` | 0.15；修复 0.1 | 关闭 | 无 | 2048 | 30 秒；只对瞬时错误重试 1 次 |
| 词条简释 `/api/words/{word}/enrich` | `enrich_word` | 0.1 | 关闭 | 无 | 512 | 20 秒；重试 1 次 |
| 英语问答 `/api/lookups/question` | `answer_question` | 0.3 | 关闭 | 无 | 2048 | 45 秒；复杂问题可另设显式思考入口 |
| 主题词表 `/api/words/topic` | `generate_topic_word_list` | 0.3–0.4 | 关闭 | 无 | 1024 | 30 秒；重试 1 次 |
| 优先词筛选 `/api/words/priority-select` | `select_priority_words` | 0.1–0.2 | 关闭 | 无 | 2048 | 45 秒；模型失败时已有确定性顺序补足 |
| AI 短文 `/api/cards/article` | `generate_article` | 思考模式不传温度；修复 0.1 | 固定开启 | `max` | 65536 | 首次 120 秒；最多 2 次，第二次快速修复 |

DeepSeek V4 的思考模式不使用 `temperature`。开启思考时只发送
`reasoning_effort=low/high/max`；快速模式不发送强度参数。`max` 只适合真正需要
多步推理的任务，不适合查词、制卡或日常短文。

推荐的请求示例：

```python
# 快速模式：查词、制卡、短文默认值
client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    temperature=0.2,
    max_tokens=2048,
    extra_body={"thinking": {"type": "disabled"}},
)

# 思考模式：只给明确的复杂任务使用；不传 temperature
client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=messages,
    reasoning_effort="low",
    extra_body={"thinking": {"type": "enabled"}},
)
```

## 三、AI 短文的最终调用流程

1. 浏览器提交生成请求后立即得到“已开始生成”的响应；后端固定发送
   `thinking=enabled` 与 `reasoning_effort=max`。
2. 当天全部目标词只生成一篇文章，长度约为目标词数 × 10。
3. 后台任务生成这一篇文章；页面显示状态，浏览器连接中断也不会中止生成。
4. 只有 JSON 异常、漏词或瞬时网络错误时才进行一次快速修复（温度 0.1）。
5. 生成失败时任务状态返回真实原因；旧文章保留，未完成的新文章不入库。
6. 日志只记录目标词数量、调用次数、模式和耗时，不记录 API Key 或文章正文。

## 四、其他外部 API 的恰当设计

### TTS

- 当前：单文本最长 240 字符、20 秒超时、同文本锁、全局 6 个生成槽、原子落盘、
  SHA 内容缓存、后台预取最多 8 个任务。
- 保持：TTS 是增强功能，失败返回 503 但不影响卡片文字；不要把语音失败升级为
  学习流程失败。
- 后续：为不同语言增加语音时，把语言/voice 一并放入缓存键；正式多语言化之前
  不增加供应商抽象层。

### Resend

- 当前：10 秒超时，验证码/提醒邮件带幂等键；Key 只在服务器环境变量。
- 改进：三个发送函数应最终合并到一个内部发送器，统一记录状态码、瞬时错误最多
  重试一次；不要记录收件地址、验证码或响应正文。
- 密码重置和注册不能在后台“稍后发送”，必须同步确认供应商已经接受请求后才返回。

### R2 备份

- 备份不在用户请求链路中，失败必须让脚本非零退出并触发告警。
- 每次上传后应校验远端对象存在和大小；保留本地加密、远端保留策略和定期恢复演练。
- R2 凭据只保存在 root-only 配置中，不进入仓库、应用 `.env` 或数据库备份。

## 五、统一稳定性规则

- 所有外部调用必须有连接/读取总超时；重试只针对连接错误、超时和 5xx，最多一次。
- 401/403/参数错误/余额不足不得自动重试，立即给出可操作错误。
- 写操作必须有幂等键；读/生成操作要有配额、每用户并发限制和全局并发上限。
- 指标至少记录：服务、功能、成功/失败类别、耗时、重试次数、输入规模和输出 token；
  不记录密钥、邮箱、验证码、单词正文、文章正文或完整供应商响应。
- 外部服务故障不能拖垮 FastAPI：TTS 可降级，AI 返回明确错误，邮件阻止对应认证流程，
  备份触发运维告警。
