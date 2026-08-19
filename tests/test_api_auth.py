import datetime as dt

from app import config, email_verification
from app.auth import hash_password
from app.db import SessionLocal
from app.models import LoginThrottle, User
from app.models import Session as DbSession
from tests.conftest import register


def test_healthcheck_does_not_require_login(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_landing_js_bundle_is_served(client):
    response = client.get("/static/landing-v51.js")
    assert response.status_code == 200
    assert "text/javascript" in response.headers["content-type"]
    assert "function renderRealReview" in response.text
    assert "if (realReviewQueue.length === 0) renderRealReview();" in response.text
    assert 'fetch("/api/ai-credentials/deepseek"' in response.text
    assert 'accountApiKeyInput.value = "";' in response.text
    assert "})();" in response.text


def test_pwa_manifest_icons_and_service_worker(client):
    manifest = client.get("/manifest.webmanifest").json()
    assert manifest["icons"]
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200
    sw = client.get("/static/sw.js")
    assert sw.status_code == 200
    assert "vocabtool-shell-" in sw.text
    bundle = client.get("/static/landing-v51.js")
    assert "serviceWorker" in bundle.text
    assert "/static/sw.js" in bundle.text


def test_security_headers_docs_and_cross_site_write_guard(client):
    response = client.get("/")
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    blocked = client.post(
        "/api/login",
        json={"email": "nobody@example.com", "password": "password123"},
        headers={"Origin": "https://evil.example"},
    )
    assert blocked.status_code == 403


def test_home_page_has_guest_search_demo_and_login_link(client):
    page = client.get("/")
    assert page.status_code == 200
    assert '<form id="landing-search-form" class="landing-search">' in page.text
    assert 'landing-hint' not in page.text
    assert 'id="landing-search-clear"' in page.text
    assert 'id="landing-search-result"' in page.text
    assert '/static/landing-v51.js' in page.text
    assert 'id="mobile-profile-btn"' not in page.text
    assert 'id="account-menu-toggle"' not in page.text
    assert 'id="guest-demo-cards"' in page.text
    assert 'id="guest-demo-article"' in page.text
    assert 'id="real-review" hidden' in page.text
    assert "游客每人可体验" not in page.text
    assert 'action="/login"' not in page.text
    assert page.text.count('class="demo-card"') == 4
    assert "onclick=" not in page.text
    assert "data-audio=" in page.text
    assert "data-demo-rating=" in page.text
    assert "重来" in page.text and "困难" in page.text and "良好" in page.text
    assert "简单" in page.text
    assert "体验学习卡片" in page.text
    assert "体验 AI 短文" in page.text


def test_deepseek_key_is_encrypted_never_returned_and_user_isolated(client, monkeypatch):
    from app.models import UserApiCredential

    monkeypatch.setattr(config, "API_KEY_ENCRYPTION_SECRET", "test-encryption-secret")
    api_key = "sk-test-user-secret-1234567890"
    register(client, "key-owner@example.com")

    saved = client.put(
        "/api/ai-credentials/deepseek",
        json={"api_key": api_key},
    )
    assert saved.status_code == 200, saved.text
    assert api_key not in saved.text
    assert saved.json() == {
        "ok": True,
        "configured": True,
        "provider": "deepseek",
        "key_hint": "7890",
    }

    status = client.get("/api/ai-credentials/deepseek")
    assert status.status_code == 200
    assert api_key not in status.text
    assert status.json()["configured"] is True
    assert "encrypted_key" not in status.json()

    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == "key-owner@example.com").one()
        credential = db.get(UserApiCredential, owner.id)
        assert credential is not None
        assert api_key not in credential.encrypted_key
        assert credential.encrypted_key != api_key
    finally:
        db.close()

    client.post("/api/logout")
    register(client, "other-key-user@example.com")
    other_status = client.get("/api/ai-credentials/deepseek").json()
    assert other_status == {
        "configured": False,
        "provider": "deepseek",
        "key_hint": "",
    }


def test_deepseek_key_can_be_deleted_without_returning_plaintext(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY_ENCRYPTION_SECRET", "test-encryption-secret")
    register(client, "delete-key@example.com")
    api_key = "sk-delete-this-secret-12345678"
    assert client.put(
        "/api/ai-credentials/deepseek", json={"api_key": api_key}
    ).status_code == 200

    deleted = client.delete("/api/ai-credentials/deepseek")
    assert deleted.status_code == 200
    assert api_key not in deleted.text
    assert deleted.json()["configured"] is False
    assert client.get("/api/ai-credentials/deepseek").json()["configured"] is False


def test_theme_is_applied_before_stylesheets_on_home_and_login(client):
    """主题必须在首帧前生效：theme-head.js 先于样式表加载，且页面不能依赖
    内联脚本（CSP script-src 'self' 会拦截内联脚本，夜间模式刷新会闪日间模式）。"""
    for path in ("/", "/login"):
        response = client.get(path)
        html = response.text
        initializer = html.index("theme-head.js")
        first_stylesheet = html.index('<link rel="stylesheet"')
        assert initializer < first_stylesheet
        assert '<meta name="color-scheme" content="dark light">' in html
        # 内联 <script> 会被 CSP script-src 'self' 拦截；模板只能引用外部脚本。
        assert "<script>" not in html
        assert "theme-flash-guard" not in html
        assert "visibility: hidden" not in html
        policy = response.headers.get("content-security-policy", "")
        script_src = next(
            (part for part in policy.split(";") if part.strip().startswith("script-src")),
            "",
        )
        assert "script-src 'self'" in script_src
        assert "unsafe-inline" not in script_src


def test_app_page_is_removed(client):
    response = client.get("/app", follow_redirects=False)
    assert response.status_code == 404
    manifest = client.get("/manifest.webmanifest").json()
    assert manifest["start_url"] == "/"


def test_home_page_stays_guest_like_when_logged_in(client):
    register(client, "home-page@example.com")
    page = client.get("/")
    assert page.status_code == 200
    assert '<form id="landing-search-form"' in page.text
    assert 'landing-hint' not in page.text
    assert 'id="account-menu-panel"' in page.text
    assert 'id="account-api-key-input" type="password"' in page.text
    assert "保存后不再显示明文" in page.text
    assert 'data-mobile-view="cards"' in page.text
    assert 'id="guest-demo-cards" hidden' in page.text
    assert 'id="real-review"' in page.text and 'id="real-review" hidden' not in page.text
    css = client.get("/static/style.css")
    assert '.landing-body[data-logged-in="true"] #landing-hero { display: none; }' not in css.text


def test_api_write_requires_origin_when_enabled(client, monkeypatch):
    from app import config as app_config

    monkeypatch.setattr(app_config, "CSRF_REQUIRE_ORIGIN", True)
    missing = client.post(
        "/api/login",
        json={"email": "nobody@example.com", "password": "password123"},
    )
    assert missing.status_code == 403
    assert "来源" in missing.json()["detail"]
    matched = client.post(
        "/api/login",
        json={"email": "nobody@example.com", "password": "password123"},
        headers={"Origin": "http://testserver"},
    )
    # Origin 合法后不再被 CSRF 拦截，进入正常登录校验（无此账号，返回 400）。
    assert matched.status_code == 400


def test_expired_sessions_are_removed_and_active_sessions_are_bounded(client):
    register(client, "session-cleanup@example.com")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "session-cleanup@example.com").one()
        db.query(DbSession).filter(DbSession.user_id == user.id).update(
            {DbSession.expires_at: dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(seconds=1)}
        )
        db.commit()
    finally:
        db.close()
    assert client.get("/api/me").status_code == 401
    for _ in range(12):
        assert client.post(
            "/api/login",
            json={"email": "session-cleanup@example.com", "password": "password123"},
        ).status_code == 200
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "session-cleanup@example.com").one()
        assert db.query(DbSession).filter(DbSession.user_id == user.id).count() <= 10
    finally:
        db.close()


def test_register_login_logout_me(client):
    register(client, "bob@example.com")
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["email"] == "bob@example.com"

    logout = client.post("/api/logout")
    assert logout.status_code == 200
    assert client.get("/api/me").status_code == 401

    login = client.post("/api/login", json={"email": "bob@example.com", "password": "password123"})
    assert login.status_code == 200
    assert client.get("/api/me").status_code == 200


def test_bad_login_rejected(client):
    register(client, "carol@example.com")
    res = client.post("/api/login", json={"email": "carol@example.com", "password": "wrongpass"})
    assert res.status_code == 400


def test_existing_short_password_can_still_log_in(client):
    password_hash, salt = hash_password("oldpass")
    db = SessionLocal()
    try:
        db.add(
            User(
                email="legacy-short-password@example.com",
                password_hash=password_hash,
                salt=salt,
            )
        )
        db.commit()
    finally:
        db.close()

    page = client.get("/login")
    assert 'id="login-password"' in page.text
    assert 'id="login-password" type="password" required minlength=' not in page.text
    logged_in = client.post(
        "/api/login",
        json={
            "email": "legacy-short-password@example.com",
            "password": "oldpass",
        },
    )
    assert logged_in.status_code == 200
    assert client.get("/api/me").status_code == 200


def test_rollback_argon2_hash_still_logs_in_and_downgrades_to_pbkdf2(client):
    from pwdlib import PasswordHash

    password_hash = PasswordHash.recommended().hash("rollback-pass")
    db = SessionLocal()
    try:
        db.add(
            User(
                email="rollback-argon2@example.com",
                password_hash=password_hash,
                salt="",
            )
        )
        db.commit()
    finally:
        db.close()

    logged_in = client.post(
        "/api/login",
        json={
            "email": "rollback-argon2@example.com",
            "password": "rollback-pass",
        },
    )
    assert logged_in.status_code == 200
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "rollback-argon2@example.com").one()
        assert user.password_hash.startswith("$pbkdf2-sha256$")
    finally:
        db.close()


def test_http_login_cookie_omits_secure_when_flag_disabled(client, monkeypatch):
    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    response = register(client, "local-cookie@example.com")
    cookie_header = response.headers["set-cookie"].lower()
    assert "httponly" in cookie_header
    assert "samesite=lax" in cookie_header
    assert "secure" not in cookie_header
    assert client.get("/api/me").status_code == 200


def test_http_login_cookie_sets_secure_when_flag_enabled(client, monkeypatch):
    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    # Secure cookie 只在 https 请求中回传，因此用 https base_url 验证会话可用
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, base_url="https://testserver") as https_client:
        response = register(https_client, "secure-cookie@example.com")
        cookie_header = response.headers["set-cookie"].lower()
        assert "httponly" in cookie_header
        assert "samesite=lax" in cookie_header
        assert "secure" in cookie_header
        assert https_client.get("/api/me").status_code == 200


def test_session_token_is_hashed_in_database(client):
    register(client, "token-hash@example.com")
    cookie_token = client.cookies.get(config.COOKIE_NAME)
    db = SessionLocal()
    try:
        row = db.query(DbSession).order_by(DbSession.created_at.desc()).first()
        assert row is not None
        assert row.token != cookie_token
        assert len(row.token) == 64
    finally:
        db.close()


def test_login_throttles_repeated_wrong_passwords(client):
    register(client, "throttle@example.com")
    client.post("/api/logout")
    responses = [
        client.post(
            "/api/login",
            json={"email": "throttle@example.com", "password": "wrong-password"},
        )
        for _ in range(6)
    ]
    assert responses[-1].status_code == 429


def test_wrong_password_during_lock_extends_lock(client):
    register(client, "lock-extend@example.com")
    client.post("/api/logout")
    for _ in range(5):
        assert client.post(
            "/api/login",
            json={"email": "lock-extend@example.com", "password": "wrong-password"},
        ).status_code == 400
    assert client.post(
        "/api/login",
        json={"email": "lock-extend@example.com", "password": "wrong-password"},
    ).status_code == 429

    db = SessionLocal()
    try:
        row = db.get(LoginThrottle, "lock-extend@example.com")
        locked_before = row.locked_until
        assert locked_before is not None
    finally:
        db.close()

    retry = client.post(
        "/api/login",
        json={"email": "lock-extend@example.com", "password": "wrong-password"},
    )
    assert retry.status_code == 429

    db = SessionLocal()
    try:
        row = db.get(LoginThrottle, "lock-extend@example.com")
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        assert row.failures == 5
        assert row.locked_until > locked_before
        assert row.locked_until >= now + dt.timedelta(minutes=15) - dt.timedelta(seconds=2)
    finally:
        db.close()


def test_correct_password_during_lock_logs_in_and_clears_lock(client):
    register(client, "lock-clear@example.com")
    client.post("/api/logout")
    for _ in range(5):
        assert client.post(
            "/api/login",
            json={"email": "lock-clear@example.com", "password": "wrong-password"},
        ).status_code == 400
    assert client.post(
        "/api/login",
        json={"email": "lock-clear@example.com", "password": "wrong-password"},
    ).status_code == 429

    ok = client.post(
        "/api/login",
        json={"email": "lock-clear@example.com", "password": "password123"},
    )
    assert ok.status_code == 200
    assert client.get("/api/me").status_code == 200
    db = SessionLocal()
    try:
        assert db.get(LoginThrottle, "lock-clear@example.com") is None
    finally:
        db.close()


def test_real_email_verification_code_registration(client, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_VERIFICATION_REQUIRED", True)
    monkeypatch.setattr(config, "RESEND_API_KEY", "test-only-key")
    monkeypatch.setattr(config, "VERIFICATION_SECRET", "test-secret-with-enough-entropy")
    monkeypatch.setattr(config, "EMAIL_FROM", "VocabTool <verify@example.com>")
    monkeypatch.setattr(email_verification, "_send_email", lambda email, code: None)
    monkeypatch.setattr(email_verification.secrets, "randbelow", lambda _: 123456)

    sent = client.post(
        "/api/register/request-code", json={"email": "verified@example.com"}
    )
    assert sent.status_code == 200
    wrong = client.post(
        "/api/register",
        json={
            "email": "verified@example.com",
            "password": "password123",
            "code": "000000",
        },
    )
    assert wrong.status_code == 400
    created = client.post(
        "/api/register",
        json={
            "email": "verified@example.com",
            "password": "password123",
            "code": "123456",
        },
    )
    assert created.status_code == 200
    assert client.get("/api/me").json()["email"] == "verified@example.com"


def test_password_reset_uses_email_code_and_revokes_sessions(client, monkeypatch):
    register(client, "reset@example.com", "old-password123")
    monkeypatch.setattr(config, "RESEND_API_KEY", "test-only-key")
    monkeypatch.setattr(config, "VERIFICATION_SECRET", "test-secret-with-enough-entropy")
    monkeypatch.setattr(config, "EMAIL_FROM", "VocabTool <verify@example.com>")
    sent = []
    monkeypatch.setattr(
        email_verification,
        "_send_password_reset_email",
        lambda email, code: sent.append((email, code)),
    )
    monkeypatch.setattr(email_verification.secrets, "randbelow", lambda _: 654321)

    missing = client.post(
        "/api/password-reset/request-code",
        json={"email": "missing@example.com"},
    )
    requested = client.post(
        "/api/password-reset/request-code",
        json={"email": "reset@example.com"},
    )
    assert missing.status_code == 200
    assert requested.status_code == 200
    assert missing.json()["message"] == requested.json()["message"]
    assert sent == [("reset@example.com", "654321")]

    wrong = client.post(
        "/api/password-reset",
        json={
            "email": "reset@example.com",
            "password": "new-password123",
            "code": "000000",
        },
    )
    assert wrong.status_code == 400
    assert client.get("/api/me").status_code == 200

    reset = client.post(
        "/api/password-reset",
        json={
            "email": "reset@example.com",
            "password": "new-password123",
            "code": "654321",
        },
    )
    assert reset.status_code == 200
    assert client.get("/api/me").status_code == 401
    assert client.post(
        "/api/login",
        json={"email": "reset@example.com", "password": "old-password123"},
    ).status_code == 400
    assert client.post(
        "/api/login",
        json={"email": "reset@example.com", "password": "new-password123"},
    ).status_code == 200
    assert client.post(
        "/api/password-reset",
        json={
            "email": "reset@example.com",
            "password": "another-password123",
            "code": "654321",
        },
    ).status_code == 400


def test_new_registration_code_consumes_previous_one(client, monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "test-only-key")
    monkeypatch.setattr(config, "VERIFICATION_SECRET", "test-secret-with-enough-entropy")
    monkeypatch.setattr(config, "EMAIL_FROM", "VocabTool <verify@example.com>")
    monkeypatch.setattr(email_verification, "_send_email", lambda email, code: None)
    codes = iter([111111, 222222])
    monkeypatch.setattr(email_verification.secrets, "randbelow", lambda _: next(codes))

    from app.db import SessionLocal
    from app.models import EmailVerification

    db = SessionLocal()
    try:
        assert email_verification.request_code(db, "renew@example.com") is None
        row = db.query(EmailVerification).filter(
            EmailVerification.email == "renew@example.com"
        ).one()
        row.created_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(minutes=5)
        db.commit()
        assert email_verification.request_code(db, "renew@example.com") is None

        rows = (
            db.query(EmailVerification)
            .filter(EmailVerification.email == "renew@example.com")
            .order_by(EmailVerification.created_at.asc())
            .all()
        )
        assert rows[0].consumed_at is not None
        assert rows[1].consumed_at is None
    finally:
        db.close()


def test_new_password_reset_code_consumes_previous_one(client, monkeypatch):
    register(client, "renew-reset@example.com")
    monkeypatch.setattr(config, "RESEND_API_KEY", "test-only-key")
    monkeypatch.setattr(config, "VERIFICATION_SECRET", "test-secret-with-enough-entropy")
    monkeypatch.setattr(config, "EMAIL_FROM", "VocabTool <verify@example.com>")
    monkeypatch.setattr(
        email_verification, "_send_password_reset_email", lambda email, code: None
    )
    codes = iter([111111, 222222])
    monkeypatch.setattr(email_verification.secrets, "randbelow", lambda _: next(codes))

    from app.db import SessionLocal
    from app.models import PasswordResetVerification

    db = SessionLocal()
    try:
        assert email_verification.request_password_reset_code(db, "renew-reset@example.com") is None
        row = db.query(PasswordResetVerification).one()
        row.created_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(minutes=5)
        db.commit()
        assert email_verification.request_password_reset_code(db, "renew-reset@example.com") is None

        rows = (
            db.query(PasswordResetVerification)
            .order_by(PasswordResetVerification.created_at.asc())
            .all()
        )
        assert rows[0].consumed_at is not None
        assert rows[1].consumed_at is None
    finally:
        db.close()


def test_password_reset_endpoint_is_rate_limited(client):
    register(client, "ratelimit-reset@example.com")
    for _ in range(100):
        response = client.post(
            "/api/password-reset",
            json={
                "email": "ratelimit-reset@example.com",
                "password": "new-password123",
                "code": "000000",
            },
        )
        assert response.status_code == 400
    response = client.post(
        "/api/password-reset",
        json={
            "email": "ratelimit-reset@example.com",
            "password": "new-password123",
            "code": "000000",
        },
    )
    assert response.status_code == 429


def test_password_reset_page_is_available(client):
    page = client.get("/login")
    assert page.status_code == 200
    assert 'id="show-reset"' in page.text
    assert 'id="form-reset"' in page.text
    assert 'id="send-reset-code"' in page.text


def test_landing_groups_target_sources_and_offers_one_ngsl_filter(client):
    page = client.get("/")
    assert page.status_code == 200
    assert '<optgroup label="自己提供内容">' in page.text
    assert '<optgroup label="系统词库">' in page.text
    assert '<optgroup label="AI 与口语">' in page.text
    assert '<option value="corpus">从文章或文本提取</option>' in page.text
    assert '<option value="wordlist">粘贴单词或短语</option>' in page.text
    assert '<option value="file">从文件提取</option>' in page.text
    assert '<option value="builtin">选择内置词表</option>' in page.text
    assert '<option value="ngsl">按 NGSL 排名选词</option>' in page.text
    assert '<option value="saved">从生词库选择</option>' in page.text
    assert '<option value="topic">AI 按主题生成词表</option>' in page.text
    assert '<option value="needs">选择口语表达素材</option>' in page.text
    assert 'id="real-card-list-fields"' in page.text
    assert 'id="real-card-list-id"' in page.text
    assert page.text.count('id="real-card-ngsl-filter"') == 1
    assert page.text.count('id="real-card-ngsl-filter-fields"') == 1
    assert 'id="real-card-list-filter"' not in page.text


def test_static_assets_have_content_hash_version(client, monkeypatch):
    """静态资源 ?v= 必须是内容哈希：文件内容一变，版本号自动变，杜绝缓存问题。"""
    import app.main as main

    page = client.get("/")
    assert page.status_code == 200

    # 版本号形如 v=h<sha256前8位>，且能定位到文件
    script_src = [
        line.strip() for line in page.text.splitlines()
        if "landing-v51.js?v=" in line
    ]
    assert script_src, "页面未引用 landing-v51.js 带哈希版本号"
    assert "?v=h" in script_src[0], f"版本号不是内容哈希: {script_src[0]}"

    # 同内容哈希稳定，不同内容哈希不同
    css_src = [
        line.strip() for line in page.text.splitlines()
        if "style.css?v=" in line
    ]
    assert css_src
    assert main.static_url("style.css") == main.static_url("style.css")
    assert main.static_url("style.css") != main.static_url("landing-v51.js")

    # 文件不存在时返回固定占位版本，不抛错
    assert main.static_url("no-such-file.js")


def test_user_isolation(client):
    from app.models import Card

    register(client, "eve@example.com")
    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.email == "eve@example.com").one()
        card = Card(
            user_id=owner.id,
            word="private",
            card_type="general",
            front="private",
            back="private",
        )
        db.add(card)
        db.commit()
        card_id = card.id
    finally:
        db.close()

    register(client, "frank@example.com")
    assert client.get("/api/cards/browse", params={"q": "private"}).json()["total"] == 0
    assert client.delete(f"/api/cards/{card_id}").status_code == 404


def test_api_rejects_oversized_json_body(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.SecurityHeadersMiddleware, "_MAX_API_BODY_BYTES", 10_000)
    oversized = client.post(
        "/api/login", content=b'{"email":"a@b.com","password":"' + b"x" * 20_000 + b'"}'
    )
    assert oversized.status_code == 413
    normal = client.post(
        "/api/login",
        json={"email": "nobody@example.com", "password": "password123"},
    )
    assert normal.status_code == 400


def test_storage_bytes_counts_rows_with_null_columns():
    from sqlalchemy import insert

    from app.api_support import _sum_storage_bytes
    from app.models import Card

    db = SessionLocal()
    try:
        user = User(email="storage-null@example.com", password_hash="x", salt="y")
        db.add(user)
        db.commit()
        db.refresh(user)
        db.execute(
            insert(Card).values(
                user_id=user.id,
                word="alpha",
                card_type="general",
                front="front",
                back="back",
                context=None,
            )
        )
        db.execute(
            insert(Card).values(
                user_id=user.id,
                word="beta",
                card_type="general",
                front="f",
                back="b",
                context="ctx",
            )
        )
        db.commit()
        total = _sum_storage_bytes(
            db,
            Card.word,
            Card.front,
            Card.back,
            Card.context,
            filters=[(Card.user_id, user.id)],
        )
        per_row = _sum_storage_bytes(
            db,
            Card.word,
            Card.front,
            Card.back,
            Card.context,
            extra_per_row=16,
            filters=[(Card.user_id, user.id)],
        )
    finally:
        db.close()

    expected = sum(
        len(value.encode("utf-8"))
        for value in ("alpha", "front", "back", "", "beta", "f", "b", "ctx")
    )
    assert total == expected
    assert per_row == expected + 16 * 2


def test_check_request_rate_blocks_after_limit_and_resets():
    from app.auth import check_request_rate
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        assert all(
            check_request_rate(db, action="test-rate", identity="x", limit=3, window_minutes=15)
            for _ in range(3)
        )
        assert not check_request_rate(
            db, action="test-rate", identity="x", limit=3, window_minutes=15
        )
        # 不同身份互不影响；limit=0 表示不限制。
        assert check_request_rate(
            db, action="test-rate", identity="y", limit=3, window_minutes=15
        )
        assert check_request_rate(
            db, action="test-rate", identity="x", limit=0, window_minutes=15
        )
    finally:
        db.close()


def test_check_request_rate_concurrent_writes_do_not_raise():
    import hashlib
    import threading

    from app.auth import check_request_rate
    from app.db import SessionLocal
    from app.models import RequestThrottle

    results = []
    errors = []

    def worker():
        db = SessionLocal()
        try:
            results.append(
                check_request_rate(
                    db, action="race", identity="z", limit=3, window_minutes=15
                )
            )
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert sum(results) == 3
    db = SessionLocal()
    try:
        key = hashlib.sha256(b"race:z").hexdigest()
        row = db.get(RequestThrottle, key)
        assert row is not None
        assert row.attempts == 3
    finally:
        db.close()


def test_anonymous_identity_trusts_x_forwarded_for_from_trusted_proxy(monkeypatch):
    from types import SimpleNamespace

    from starlette.datastructures import Headers

    from app import config as app_config
    from app.api_support import _anonymous_request_identity

    direct = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers=Headers({"X-Forwarded-For": "203.0.113.5"}),
    )
    assert _anonymous_request_identity(direct) == "203.0.113.5"

    chained = SimpleNamespace(
        client=SimpleNamespace(host="10.0.0.7"),
        headers=Headers({"X-Forwarded-For": "1.2.3.4, 10.0.0.5"}),
    )
    # 私网对等方默认不再是可信代理：直接用 peer，不采信可伪造的转发头。
    assert _anonymous_request_identity(chained) == "10.0.0.7"
    monkeypatch.setattr(app_config, "TRUSTED_PROXY_IPS", "loopback,private")
    assert _anonymous_request_identity(chained) == "10.0.0.5"
    monkeypatch.setattr(app_config, "TRUSTED_PROXY_IPS", "")

    cloudflare = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers=Headers(
            {"CF-Connecting-IP": "203.0.113.9", "X-Forwarded-For": "1.2.3.4, 10.0.0.5"}
        ),
    )
    # CF-Connecting-IP 默认不读：普通反代会原样转发客户端伪造的该头。
    assert _anonymous_request_identity(cloudflare) == "10.0.0.5"
    monkeypatch.setattr(app_config, "TRUST_CF_CONNECTING_IP", True)
    assert _anonymous_request_identity(cloudflare) == "203.0.113.9"
    monkeypatch.setattr(app_config, "TRUST_CF_CONNECTING_IP", False)


def test_anonymous_identity_ignores_forwarded_headers_from_untrusted_peer():
    from types import SimpleNamespace

    from starlette.datastructures import Headers

    from app.api_support import _anonymous_request_identity

    public = SimpleNamespace(
        client=SimpleNamespace(host="8.8.8.8"),
        headers=Headers({"X-Forwarded-For": "203.0.113.5"}),
    )
    assert _anonymous_request_identity(public) == "8.8.8.8"


def test_startup_pruning_removes_old_ephemeral_rows():
    from app.db import _prune_ephemeral_rows, _prune_shared_caches
    from app.models import LoginThrottle

    db = SessionLocal()
    try:
        old = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=30)
        db.add(LoginThrottle(email="stale@example.com", failures=1, window_started=old))
        db.commit()
    finally:
        db.close()
    _prune_ephemeral_rows()
    _prune_shared_caches()
    db = SessionLocal()
    try:
        assert db.get(LoginThrottle, "stale@example.com") is None
    finally:
        db.close()


def test_startup_pruning_removes_expired_sessions_and_stale_review_requests():
    from app.db import _prune_ephemeral_rows
    from app.models import Card, ReviewRequest

    db = SessionLocal()
    try:
        user = User(email="prune-all@example.com", password_hash="x", salt="y")
        db.add(user)
        db.commit()
        db.refresh(user)
        card = Card(
            user_id=user.id,
            word="prune",
            card_type="general",
            front="prune",
            back="prune",
        )
        db.add(card)
        db.commit()
        db.refresh(card)
        old = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=100)
        db.add_all(
            [
                DbSession(
                    token="stale-session-token",
                    user_id=user.id,
                    expires_at=old,
                    created_at=old,
                ),
                ReviewRequest(
                    action_id="stale-review-action",
                    user_id=user.id,
                    card_id=card.id,
                    created_at=old,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    _prune_ephemeral_rows()

    db = SessionLocal()
    try:
        assert db.get(DbSession, "stale-session-token") is None
        assert db.get(ReviewRequest, "stale-review-action") is None
    finally:
        db.close()
