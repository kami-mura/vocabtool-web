from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
if os.environ.get("VOCABFLOW_SKIP_DOTENV", "false").strip().lower() not in {
    "1", "true", "yes", "on"
}:
    load_dotenv(BASE_DIR / ".env", override=False)
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# 默认 SQLite，适合本地/小规模；部署时用 DATABASE_URL 指向 PostgreSQL。
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'vocabflow.db'}")

COOKIE_NAME = os.environ.get("COOKIE_NAME", "vf_session")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
# 生产默认要求非 GET /api/ 请求带 Origin；测试/脚本可显式关闭。
CSRF_REQUIRE_ORIGIN = os.environ.get("CSRF_REQUIRE_ORIGIN", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
TEMPLATES_AUTO_RELOAD = os.environ.get("TEMPLATES_AUTO_RELOAD", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "30"))
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Shanghai")
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "ALLOWED_HOSTS",
        "localhost,127.0.0.1,testserver",
    ).split(",")
    if host.strip()
]
EMAIL_VERIFICATION_REQUIRED = os.environ.get(
    "EMAIL_VERIFICATION_REQUIRED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "VocabTool <verify@example.com>")
VERIFICATION_SECRET = os.environ.get("VERIFICATION_SECRET", "")
VERIFICATION_CODE_TTL_MINUTES = int(
    os.environ.get("VERIFICATION_CODE_TTL_MINUTES", "10")
)
NEW_CARDS_PER_DAY = int(os.environ.get("NEW_CARDS_PER_DAY", "10"))
DEFAULT_KNOWN_RANK = int(os.environ.get("DEFAULT_KNOWN_RANK", "3000"))
MAX_CORPUS_CHARS = int(os.environ.get("MAX_CORPUS_CHARS", "10000000"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
UPLOAD_BODY_TIMEOUT_SECONDS = float(os.environ.get("UPLOAD_BODY_TIMEOUT_SECONDS", "30"))
MAX_APKG_UPLOAD_BYTES = int(
    os.environ.get("MAX_APKG_UPLOAD_BYTES", str(50 * 1024 * 1024))
)
MAX_APKG_CARDS = int(os.environ.get("MAX_APKG_CARDS", "10000"))
USER_STORAGE_QUOTA_BYTES = min(
    50 * 1024 * 1024,
    int(os.environ.get("USER_STORAGE_QUOTA_BYTES", str(50 * 1024 * 1024))),
)
MAX_CARDS_PER_RUN = min(500, int(os.environ.get("MAX_CARDS_PER_RUN", "500")))
AI_USAGE_RETENTION_DAYS = max(
    30, int(os.environ.get("AI_USAGE_RETENTION_DAYS", "180"))
)
# 每位用户每日 AI 请求上限（按站点时区自然日计数）；0 表示不限制。
# 默认 3000：查词 100 次、制卡 2000 张（约 200-400 次批量请求）都不会触发。
AI_DAILY_REQUEST_LIMIT = max(0, int(os.environ.get("AI_DAILY_REQUEST_LIMIT", "3000")))

# 全站未登录用户每日 AI 查词总量上限；0 表示不限制。
GUEST_AI_DAILY_LIMIT = max(0, int(os.environ.get("GUEST_AI_DAILY_LIMIT", "500")))

# TTS 音频缓存总容量上限（字节）；超出后按最旧优先清理。
TTS_CACHE_MAX_BYTES = int(os.environ.get("TTS_CACHE_MAX_BYTES", str(512 * 1024 * 1024)))

# 可选：deepseek-v4-flash 释义。Key 只从服务器环境变量读取，绝不下发到浏览器。
AI_PROVIDER = os.environ.get("AI_PROVIDER", "").strip().lower()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
# deepseek-v4-flash 默认思考模式会让制卡慢 15 倍以上；关闭后同一模型
# 单批（10 词）从约 60-110 秒降到约 4 秒。设为 false 可保留思考模式。
DEEPSEEK_DISABLE_THINKING = os.environ.get("DEEPSEEK_DISABLE_THINKING", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
# AI 文章固定使用思考模式 `max`；保留此配置仅供直接调用 generate_article 时使用。
AI_ARTICLE_REASONING_EFFORT = os.environ.get(
    "AI_ARTICLE_REASONING_EFFORT", "max"
).strip().lower()
