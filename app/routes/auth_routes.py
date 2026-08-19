from __future__ import annotations

from fastapi import APIRouter

from .. import api_keys
from ..api_support import (
    ALLOWED_READING_FONTS,
    Depends,
    HTTPException,
    JSONResponse,
    ReadingDisplayPreference,
    Request,
    Session,
    _anonymous_request_identity,
    _cookie_secure,
    _reading_display_preference,
    _require_user,
    _set_auth_cookie,
    _user_storage_bytes,
    check_request_rate,
    config,
    create_session,
    current_user,
    delete_session,
    dt,
    email_verification,
    get_db,
    login_user,
    register_user,
)
from ..schemas import (
    ApiKeyIn,
    LegacyApiKeyIn,
    LoginIn,
    PasswordResetIn,
    ReadingDisplayIn,
    RegisterIn,
    VerificationCodeIn,
)

router = APIRouter()

# ---------- 认证 ----------


@router.post("/register/request-code")
def request_registration_code(
    body: VerificationCodeIn, request: Request, db: Session = Depends(get_db)
):
    if not config.EMAIL_VERIFICATION_REQUIRED:
        return {"ok": True, "verification_required": False}
    if not check_request_rate(
        db,
        action="registration-code",
        identity=_anonymous_request_identity(request),
        limit=100,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="验证码请求过多，请稍后再试")
    error = email_verification.request_code(db, body.email)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {
        "ok": True,
        "verification_required": True,
        "message": "如果邮箱可以注册，验证码将发送到该邮箱",
    }


@router.post("/register")
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    if config.EMAIL_VERIFICATION_REQUIRED:
        if len(body.code.strip()) != 6 or not body.code.strip().isdigit():
            raise HTTPException(status_code=400, detail="请输入 6 位邮箱验证码")
        verification_error = email_verification.verify_code(
            db, body.email, body.code
        )
        if verification_error:
            raise HTTPException(status_code=400, detail=verification_error)
    user, error = register_user(db, body.email, body.password)
    if error:
        raise HTTPException(status_code=400, detail=error)
    token = create_session(db, user)
    response = JSONResponse({"ok": True, "email": user.email})
    _set_auth_cookie(response, token, request)
    return response


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    if not check_request_rate(
        db,
        action="login",
        identity=_anonymous_request_identity(request),
        limit=300,
        window_minutes=15,
    ):
        raise HTTPException(status_code=429, detail="登录请求过多，请稍后再试")
    user, error = login_user(db, body.email, body.password)
    if error:
        status_code = 429 if "尝试过多" in error else 400
        raise HTTPException(status_code=status_code, detail=error)
    token = create_session(db, user)
    response = JSONResponse({"ok": True, "email": user.email})
    _set_auth_cookie(response, token, request)
    return response


@router.post("/password-reset/request-code")
def request_password_reset_code(
    body: VerificationCodeIn, request: Request, db: Session = Depends(get_db)
):
    if not check_request_rate(
        db,
        action="password-reset-code",
        identity=_anonymous_request_identity(request),
        limit=100,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="验证码请求过多，请稍后再试")
    error = email_verification.request_password_reset_code(db, body.email)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {
        "ok": True,
        "message": "如果该邮箱已注册，密码重置验证码将发送到该邮箱",
    }


@router.post("/password-reset")
def password_reset(
    body: PasswordResetIn, request: Request, db: Session = Depends(get_db)
):
    if not check_request_rate(
        db,
        action="password-reset",
        identity=_anonymous_request_identity(request),
        limit=100,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="验证码尝试过多，请稍后再试")
    if not body.code.isdigit():
        raise HTTPException(status_code=400, detail="请输入 6 位邮箱验证码")
    error = email_verification.reset_password(
        db, body.email, body.code, body.password
    )
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {"ok": True, "message": "密码已重置，请重新登录"}


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(config.COOKIE_NAME)
    if token:
        delete_session(db, token)
    response = JSONResponse({"ok": True})
    response.delete_cookie(
        config.COOKIE_NAME,
        path="/",
        secure=_cookie_secure(request),
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return {"email": user.email, "user_id": user.id}


@router.get("/ai-credentials")
def get_ai_credential(request: Request, db: Session = Depends(get_db)):
    user = _require_user(db, request)
    return api_keys.credential_status(db, user.id)


@router.put("/ai-credentials")
def save_ai_credential(
    body: ApiKeyIn, request: Request, db: Session = Depends(get_db)
):
    user = _require_user(db, request)
    if not check_request_rate(
        db,
        action="save-ai-key",
        identity=f"u{user.id}",
        limit=20,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="API Key 更新过于频繁，请稍后再试")
    try:
        credential = api_keys.save_api_key(db, user.id, body.provider, body.api_key)
    except api_keys.ApiKeyError as exc:
        status = 503 if "服务器尚未配置" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {
        "ok": True,
        "configured": True,
        "provider": credential.provider,
        "provider_label": api_keys.AI_PROVIDERS[credential.provider].label,
        "key_hint": credential.key_hint,
    }


@router.delete("/ai-credentials")
def delete_ai_credential(request: Request, db: Session = Depends(get_db)):
    user = _require_user(db, request)
    deleted = api_keys.delete_api_key(db, user.id)
    return {"ok": True, "deleted": deleted, "configured": False}


# 兼容已经被浏览器/PWA 缓存的上一版前端；新前端只使用通用端点。
@router.get("/ai-credentials/deepseek")
def get_legacy_deepseek_credential(
    request: Request, db: Session = Depends(get_db)
):
    return get_ai_credential(request, db)


@router.put("/ai-credentials/deepseek")
def save_legacy_deepseek_credential(
    body: LegacyApiKeyIn, request: Request, db: Session = Depends(get_db)
):
    return save_ai_credential(
        ApiKeyIn(provider="deepseek", api_key=body.api_key), request, db
    )


@router.delete("/ai-credentials/deepseek")
def delete_legacy_deepseek_credential(
    request: Request, db: Session = Depends(get_db)
):
    return delete_ai_credential(request, db)


@router.get("/storage")
def storage_usage(request: Request, db: Session = Depends(get_db)):
    user = _require_user(db, request)
    used = _user_storage_bytes(db, user.id)
    limit = config.USER_STORAGE_QUOTA_BYTES
    return {
        "used_bytes": used,
        "limit_bytes": limit,
        "used_mb": round(used / 1024 / 1024, 2),
        "limit_mb": round(limit / 1024 / 1024),
        "percent": round(used / limit * 100, 1) if limit else 100.0,
        "single_file_limit_mb": round(config.MAX_UPLOAD_BYTES / 1024 / 1024),
    }


def _reading_display_dict(preference: ReadingDisplayPreference) -> dict:
    return {
        "font_family": preference.font_family,
        "font_size": int(preference.font_size or 17),
        "page_margin": int(preference.page_margin or 36),
    }


@router.get("/reading/display-preference")
def get_reading_display_preference(
    request: Request, db: Session = Depends(get_db)
):
    user = _require_user(db, request)
    return _reading_display_dict(_reading_display_preference(db, user))


@router.put("/reading/display-preference")
def update_reading_display_preference(
    body: ReadingDisplayIn,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_user(db, request)
    if body.font_family not in ALLOWED_READING_FONTS:
        raise HTTPException(status_code=400, detail="不支持的阅读字体")
    preference = _reading_display_preference(db, user)
    preference.font_family = body.font_family
    preference.font_size = body.font_size
    preference.page_margin = body.page_margin
    preference.updated_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(preference)
    return {"ok": True, **_reading_display_dict(preference)}
