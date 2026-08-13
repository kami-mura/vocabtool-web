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


def _env_int(name: str, default: int) -> int:
    """解析整数环境变量；值非法时抛出指明变量名的清晰错误。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        raise RuntimeError(f"环境变量 {name} 不是有效整数：{raw!r}") from None


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        raise RuntimeError(f"环境变量 {name} 不是有效数字：{raw!r}") from None


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
SESSION_TTL_DAYS = _env_int("SESSION_TTL_DAYS", 30)
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Asia/Shanghai")
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "ALLOWED_HOSTS",
        "localhost,127.0.0.1,testserver",
    ).split(",")
    if host.strip()
]
# 可信代理来源 IP 白名单（逗号分隔）；空表示沿用默认（信任本机与内网回源）。
# 可写具体 IP（127.0.0.1、10.0.0.5）或 token：loopback / private。
# 公网入口会被 Cloudflare 直接回源时，收紧为具体 IP 可防止同内网对等方伪造转发头。
TRUSTED_PROXY_IPS = os.environ.get("TRUSTED_PROXY_IPS", "").strip()
EMAIL_VERIFICATION_REQUIRED = os.environ.get(
    "EMAIL_VERIFICATION_REQUIRED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "VocabTool <verify@example.com>")
VERIFICATION_SECRET = os.environ.get("VERIFICATION_SECRET", "")
VERIFICATION_CODE_TTL_MINUTES = _env_int("VERIFICATION_CODE_TTL_MINUTES", 10)
NEW_CARDS_PER_DAY = _env_int("NEW_CARDS_PER_DAY", 10)
DEFAULT_KNOWN_RANK = _env_int("DEFAULT_KNOWN_RANK", 3000)
MAX_CORPUS_CHARS = _env_int("MAX_CORPUS_CHARS", 10000000)
MAX_UPLOAD_BYTES = _env_int("MAX_UPLOAD_BYTES", 10 * 1024 * 1024)
UPLOAD_BODY_TIMEOUT_SECONDS = _env_float("UPLOAD_BODY_TIMEOUT_SECONDS", 30)
MAX_APKG_UPLOAD_BYTES = _env_int("MAX_APKG_UPLOAD_BYTES", 50 * 1024 * 1024)
MAX_APKG_CARDS = _env_int("MAX_APKG_CARDS", 10000)
USER_STORAGE_QUOTA_BYTES = min(
    50 * 1024 * 1024,
    _env_int("USER_STORAGE_QUOTA_BYTES", 50 * 1024 * 1024),
)
MAX_CARDS_PER_RUN = min(500, _env_int("MAX_CARDS_PER_RUN", 500))
AI_USAGE_RETENTION_DAYS = max(
    30, _env_int("AI_USAGE_RETENTION_DAYS", 180)
)
# 每位用户每日 AI 请求上限（按站点时区自然日计数）；0 表示不限制。
# 默认 3000：查词 100 次、制卡 2000 张（约 200-400 次批量请求）都不会触发。
AI_DAILY_REQUEST_LIMIT = max(0, _env_int("AI_DAILY_REQUEST_LIMIT", 3000))

# 全站未登录用户每日 AI 查词总量上限；0 表示不限制。
GUEST_AI_DAILY_LIMIT = max(0, _env_int("GUEST_AI_DAILY_LIMIT", 500))

# TTS 音频缓存总容量上限（字节）；超出后按最旧优先清理。
TTS_CACHE_MAX_BYTES = _env_int("TTS_CACHE_MAX_BYTES", 512 * 1024 * 1024)

def _parse_frame_ancestors(raw: str) -> list[str]:
    """解析 CSP frame-ancestors 白名单：逗号分隔的来源列表，自动去空白。"""
    return [item.strip() for item in raw.split(",") if item.strip()]


# 允许把本站页面嵌入 iframe 的祖先来源（CSP frame-ancestors 白名单）。
# 默认仅 'self'（防止点击劫持）；需要被其他页面（如本机 DSH 界面
# http://127.0.0.1:3080）嵌入时用逗号列出完整来源。
FRAME_ANCESTORS = _parse_frame_ancestors(os.environ.get("FRAME_ANCESTORS", "self"))


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
