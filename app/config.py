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


# 仅支持 SQLite，默认落在 data/ 目录。配置了其他数据库直接报错，
# 避免带着未经验证的 DATABASE_URL 静默启动。
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'vocabflow.db'}")
if not DATABASE_URL.startswith("sqlite"):
    raise RuntimeError(
        f"DATABASE_URL 仅支持 SQLite，当前值 {DATABASE_URL!r}；"
        "本项目已移除 PostgreSQL 支持"
    )

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
# 可信代理来源 IP 白名单（逗号分隔）；空表示默认只信任本机回环。
# 可写具体 IP（127.0.0.1、10.0.0.5）或 token：loopback / private。
# Docker 网络内互访需要信任私网段时显式配置 TRUSTED_PROXY_IPS=loopback,private。
TRUSTED_PROXY_IPS = os.environ.get("TRUSTED_PROXY_IPS", "").strip()
# 仅当公网入口是 Cloudflare（Tunnel 或回源）时开启：普通反代（如纯 Caddy）
# 会原样转发客户端伪造的 CF-Connecting-IP，导致按 IP 的限流身份可被轮换绕过。
# Cloudflare 同样会设置 X-Forwarded-For，关闭此开关不影响 CF 部署的限流准确性。
TRUST_CF_CONNECTING_IP = os.environ.get(
    "TRUST_CF_CONNECTING_IP", "false"
).strip().lower() in {"1", "true", "yes", "on"}
EMAIL_VERIFICATION_REQUIRED = os.environ.get(
    "EMAIL_VERIFICATION_REQUIRED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "VocabTool <verify@example.com>")
VERIFICATION_SECRET = os.environ.get("VERIFICATION_SECRET", "")
# 用户自带的 DeepSeek Key 使用独立派生用途加密；未单独配置时复用验证码
# 根密钥并通过用途标签派生，避免部署时再维护一份必需密钥。
API_KEY_ENCRYPTION_SECRET = os.environ.get(
    "API_KEY_ENCRYPTION_SECRET", VERIFICATION_SECRET
)
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
# 平台 Key 的免费用量按业务动作分别限制；用户自带 Key 不消耗这些额度。
AI_FREE_DAILY_QUERY_LIMIT = max(0, _env_int("AI_FREE_DAILY_QUERY_LIMIT", 50))
AI_FREE_DAILY_CARD_LIMIT = max(0, _env_int("AI_FREE_DAILY_CARD_LIMIT", 50))
AI_FREE_DAILY_ARTICLE_LIMIT = max(0, _env_int("AI_FREE_DAILY_ARTICLE_LIMIT", 1))

# 全站未登录用户每日 AI 查词总量上限；0 表示不限制。
GUEST_AI_DAILY_LIMIT = max(0, _env_int("GUEST_AI_DAILY_LIMIT", 500))

# TTS 音频缓存总容量上限（字节）；超出后按最旧优先清理。
TTS_CACHE_MAX_BYTES = _env_int("TTS_CACHE_MAX_BYTES", 512 * 1024 * 1024)

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
