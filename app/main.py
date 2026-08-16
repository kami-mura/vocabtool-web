from __future__ import annotations

import hashlib
import logging
import threading
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import config
from .api import router as api_router
from .db import SessionLocal, init_db

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
_STATIC_DIR = APP_DIR / "static"


def static_url(path: str) -> str:
    """返回带内容哈希版本号的静态资源 URL。

    版本号取文件内容 SHA-256 前 8 位：文件一改，URL 自动变，
    浏览器与 Service Worker 必然拉新，杜绝手动维护版本号导致的缓存问题。
    """
    normalized = path.lstrip("/")
    candidate = _STATIC_DIR / normalized
    if candidate.is_file():
        return f"/static/{normalized}?v=h{_static_hash(candidate)}"
    # 文件不存在（可能未来删除）：用路径本身做稳定占位，保证 URL 仍唯一。
    return f"/static/{normalized}?v=h{hashlib.sha256(normalized.encode()).hexdigest()[:8]}"


@lru_cache(maxsize=256)
def _static_hash(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()[:8]


templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
templates.env.globals["static_url"] = static_url
# 生产默认关闭模板自动重载；开发调试可设 TEMPLATES_AUTO_RELOAD=true。
templates.env.auto_reload = config.TEMPLATES_AUTO_RELOAD


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """统一安全头 + 请求体大小上限 + 简单 CSRF 防护。"""

    # 非上传端点请求体硬上限：防已登录用户用超大 JSON body 打爆内存。
    # 上传端点有各自的流式限额（10MB），这里放行由路由处理。
    _MAX_API_BODY_BYTES = 48 * 1024 * 1024
    _UPLOAD_PATH_PREFIXES = (
        "/api/card-studio/targets-file",
        "/api/cards/anki/import",
    )

    async def dispatch(self, request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path.startswith("/api/"):
            if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
                return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme not in {"http", "https"} or parsed.netloc != request.headers.get("host", ""):
                    return JSONResponse({"detail": "请求来源不可信"}, status_code=403)
            elif config.CSRF_REQUIRE_ORIGIN:
                return JSONResponse({"detail": "请求缺少来源标识"}, status_code=403)
            if not request.url.path.startswith(self._UPLOAD_PATH_PREFIXES):
                content_length = request.headers.get("content-length")
                if content_length:
                    try:
                        declared = int(content_length)
                    except ValueError:
                        return JSONResponse({"detail": "请求体大小格式不正确"}, status_code=400)
                    if declared > self._MAX_API_BODY_BYTES:
                        return JSONResponse({"detail": "请求体过大"}, status_code=413)
                else:
                    # 无 Content-Length（分块传输）：包装 receive 计数，超限立即中止。
                    total = 0
                    original_receive = request._receive

                    async def counted_receive() -> dict:
                        nonlocal total
                        message = await original_receive()
                        if message.get("type") == "http.request":
                            total += len(message.get("body", b""))
                            if total > self._MAX_API_BODY_BYTES:
                                raise HTTPException(status_code=413, detail="请求体过大")
                        return message

                    request._receive = counted_receive
        response = await call_next(request)
        # SW 脚本在 /static/ 下但要控制整站：必须显式放宽允许作用域，
        # 否则 register(..., {scope: "/"}) 会被浏览器拒绝。
        if request.url.path == "/static/sw.js":
            response.headers.setdefault("Service-Worker-Allowed", "/")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'self'; object-src 'none'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "media-src 'self'; connect-src 'self'",
        )
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if request.url.path == "/" or request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if request.url.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    if config.EMAIL_VERIFICATION_REQUIRED:
        if not config.VERIFICATION_SECRET:
            logger.warning(
                "VERIFICATION_SECRET 未配置：验证码 HMAC 密钥可预测，生产环境必须设置"
            )
        if not config.RESEND_API_KEY:
            logger.warning("RESEND_API_KEY 未配置：注册/重置验证邮件无法发送")
    init_db()
    # 全量清理与历史数据同步挪到后台线程执行：用户量大时这些任务
    # 可能跑几十秒，留在启动路径会触发 systemd 启动超时被杀、
    # 进而重启循环（见 docs/服务器故障记录.md）。
    threading.Thread(target=_run_startup_cleanup, name="startup-cleanup", daemon=True).start()
    yield


def _run_startup_cleanup() -> None:
    """后台启动清理：只清理过期临时数据，不改写用户学习状态。"""
    try:
        from .db import run_startup_maintenance

        run_startup_maintenance()
    except Exception:
        logger.exception("启动清理任务失败（不影响服务，下次重启重试）")


app = FastAPI(
    title="vocabtool Web",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)


app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
app.include_router(api_router)


@app.get("/tts-audio/{filename}", include_in_schema=False)
def tts_audio_file(filename: str, request: Request):
    """提供 TTS 生成的 mp3；文件名即内容哈希，可永久缓存。"""
    from . import tts

    if not tts.is_audio_filename(filename):
        raise HTTPException(status_code=404, detail="音频不存在")
    path = tts.TTS_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="音频不存在")
    return FileResponse(
        path,
        media_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@app.get("/healthz", include_in_schema=False)
def healthz():
    """健康检查用独立连接，绝不占用业务连接池。

    业务池饱和/写锁竞争时（previous 实现共用 SessionLocal 池），
    healthz 会跟着阻塞超过看门狗上限，连续失败被误判重启。
    SQLite 用只读直连 + 1 秒超时。返回体附部署 commit（REVISION 文件，
    由 deploy.sh 写入），用于核对线上版本与回滚定位。
    """
    try:
        from sqlalchemy.engine import make_url

        url = make_url(config.DATABASE_URL)
        import sqlite3

        conn = sqlite3.connect(
            f"file:{url.database}?mode=ro", uri=True, timeout=1
        )
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
    except Exception:  # 健康接口不暴露数据库错误细节
        return JSONResponse({"ok": False}, status_code=503)
    payload: dict = {"ok": True}
    revision_file = APP_DIR.parent / "REVISION"
    try:
        revision = revision_file.read_text(encoding="utf-8").strip()
        if revision:
            payload["revision"] = revision[:64]
    except OSError:
        pass
    return payload


def _logged_in(request: Request) -> bool:
    from .auth import current_user

    db = SessionLocal()
    try:
        return current_user(request, db) is not None
    finally:
        db.close()


@app.get("/")
def index(request: Request):
    logged_in = _logged_in(request)
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={
            "request": request,
            "logged_in": logged_in,
            "email_verification_required": config.EMAIL_VERIFICATION_REQUIRED,
        },
    )


@app.get("/login")
def login_page(request: Request):
    if _logged_in(request):
        return RedirectResponse("/")
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "request": request,
            "email_verification_required": config.EMAIL_VERIFICATION_REQUIRED,
        },
    )


@app.get("/manifest.webmanifest")
def manifest():
    from fastapi.responses import JSONResponse

    return JSONResponse(
        {
            "name": "vocabtool",
            "short_name": "vocabtool",
            "description": "多用户英语阅读与词汇记忆",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#f5f9ff",
            "theme_color": "#0A70EE",
            "icons": [
                {
                    "src": static_url("icons/icon-192.png"),
                    "sizes": "192x192",
                    "type": "image/png",
                },
                {
                    "src": static_url("icons/icon-512.png"),
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": static_url("icons/icon-512.png"),
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
        }
    )
