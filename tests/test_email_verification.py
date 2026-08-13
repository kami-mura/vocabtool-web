from app import config, email_verification
from app.db import SessionLocal
from app.models import EmailVerification


def _configure_email(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "test-only-key")
    monkeypatch.setattr(config, "VERIFICATION_SECRET", "test-secret-with-enough-entropy")
    monkeypatch.setattr(config, "EMAIL_FROM", "VocabTool <verify@example.com>")


def test_rate_limit_row_is_committed_before_email_send(monkeypatch):
    _configure_email(monkeypatch)
    sent_codes = []
    inner_errors = []

    def fake_send(email, code):
        sent_codes.append(code)
        if len(sent_codes) == 1:
            inner = SessionLocal()
            try:
                inner_errors.append(email_verification.request_code(inner, email))
            finally:
                inner.close()

    monkeypatch.setattr(email_verification, "_send_email", fake_send)

    db = SessionLocal()
    try:
        error = email_verification.request_code(db, "race@example.com")
    finally:
        db.close()
    assert error is None
    assert len(sent_codes) == 1
    assert inner_errors == ["验证码发送过于频繁，请 60 秒后再试"]


def test_failed_send_does_not_consume_rate_limit_quota(monkeypatch):
    _configure_email(monkeypatch)
    calls = []

    def flaky_send(email, code):
        calls.append(code)
        if len(calls) == 1:
            raise RuntimeError("smtp down")

    monkeypatch.setattr(email_verification, "_send_email", flaky_send)

    db = SessionLocal()
    try:
        error = email_verification.request_code(db, "flaky@example.com")
        assert error == "验证码暂时无法发送，请稍后重试"
        rows = (
            db.query(EmailVerification)
            .filter(EmailVerification.email == "flaky@example.com")
            .all()
        )
        assert rows == []
        ok = email_verification.request_code(db, "flaky@example.com")
        assert ok is None
    finally:
        db.close()
    assert len(calls) == 2


def test_code_consumed_once_register_validation_fails_after_verify(monkeypatch):
    from app.auth import register_user

    _configure_email(monkeypatch)
    monkeypatch.setattr(email_verification, "_send_email", lambda email, code: None)
    monkeypatch.setattr(email_verification.secrets, "randbelow", lambda _: 123456)

    db = SessionLocal()
    try:
        assert email_verification.request_code(db, "one-shot@example.com") is None
        assert (
            email_verification.verify_code(db, "one-shot@example.com", "123456")
            is None
        )
        user, error = register_user(db, "one-shot@example.com", "short123")
        assert user is None and error == "密码至少 10 位"
        assert (
            email_verification.verify_code(db, "one-shot@example.com", "123456")
            == "验证码不存在或已过期，请重新获取"
        )
    finally:
        db.close()


def test_verification_code_cannot_be_reused_after_failed_registration(
    client, monkeypatch
):
    from tests.conftest import register

    register(client, "one-shot@example.com")
    monkeypatch.setattr(config, "EMAIL_VERIFICATION_REQUIRED", True)
    _configure_email(monkeypatch)
    monkeypatch.setattr(
        email_verification, "_send_account_notice_email", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(email_verification.secrets, "randbelow", lambda _: 123456)

    sent = client.post(
        "/api/register/request-code", json={"email": "one-shot@example.com"}
    )
    assert sent.status_code == 200

    duplicate = client.post(
        "/api/register",
        json={
            "email": "one-shot@example.com",
            "password": "password123",
            "code": "123456",
        },
    )
    assert duplicate.status_code == 400
    assert "该邮箱已注册" in duplicate.json()["detail"]

    retry = client.post(
        "/api/register",
        json={
            "email": "one-shot@example.com",
            "password": "password456",
            "code": "123456",
        },
    )
    assert retry.status_code == 400
    assert "不存在或已过期" in retry.json()["detail"]


def test_password_reset_code_row_committed_before_send(monkeypatch):
    """重置码先落库再发信：发信期间的并发请求被 60 秒节流拒绝。"""
    _configure_email(monkeypatch)
    sent_codes = []
    inner_errors = []

    def fake_send(email, code):
        sent_codes.append(code)
        if len(sent_codes) == 1:
            inner = SessionLocal()
            try:
                inner_errors.append(
                    email_verification.request_password_reset_code(inner, email)
                )
            finally:
                inner.close()

    monkeypatch.setattr(
        email_verification, "_send_password_reset_email", fake_send
    )

    from app.auth import register_user

    db = SessionLocal()
    try:
        user, error = register_user(db, "reset-race@example.com", "password123")
        assert user is not None and error is None
        result = email_verification.request_password_reset_code(
            db, "reset-race@example.com"
        )
    finally:
        db.close()
    assert result is None
    assert len(sent_codes) == 1
    assert inner_errors == [None]  # 并发请求被节流，不发第二封邮件


def test_password_reset_failed_send_removes_row(monkeypatch):
    """重置邮件发送失败时删除已落库的验证码行，不给用户留下发不出的码。"""
    _configure_email(monkeypatch)

    def failing_send(email, code):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(
        email_verification, "_send_password_reset_email", failing_send
    )

    from app.auth import register_user
    from app.models import PasswordResetVerification

    db = SessionLocal()
    try:
        user, error = register_user(db, "reset-flaky@example.com", "password123")
        assert user is not None
        assert (
            email_verification.request_password_reset_code(
                db, "reset-flaky@example.com"
            )
            is None
        )
        rows = (
            db.query(PasswordResetVerification)
            .filter(PasswordResetVerification.user_id == user.id)
            .all()
        )
        assert rows == []
    finally:
        db.close()


def test_unregistered_notice_email_is_throttled_per_address(monkeypatch):
    """未注册邮箱的提醒邮件按邮箱节流：60 秒内第二封、一小时内第 6 封都不发。"""
    _configure_email(monkeypatch)
    sent = []

    def fake_send(email, subject, text):
        sent.append(email)

    monkeypatch.setattr(
        email_verification, "_send_account_notice_email", fake_send
    )

    db = SessionLocal()
    try:
        assert (
            email_verification.request_password_reset_code(
                db, "nobody@example.com"
            )
            is None
        )
        # 60 秒内重复请求：跳过发送。
        assert (
            email_verification.request_password_reset_code(
                db, "nobody@example.com"
            )
            is None
        )
        assert sent == ["nobody@example.com"]

        from app.models import EmailNoticeThrottle

        throttle = db.get(EmailNoticeThrottle, "nobody@example.com")
        assert throttle is not None
        # 一小时窗口内最多 5 封：把计数直接推到上限后应拒绝。
        import datetime as dt

        throttle.attempts = 5
        throttle.window_started = dt.datetime.now(dt.timezone.utc).replace(
            tzinfo=None
        )
        db.commit()
        assert (
            email_verification.request_password_reset_code(
                db, "nobody@example.com"
            )
            is None
        )
        assert sent == ["nobody@example.com"]
        # 窗口过期后恢复发送。
        throttle.window_started = dt.datetime.now(dt.timezone.utc).replace(
            tzinfo=None
        ) - dt.timedelta(hours=2)
        db.commit()
        assert (
            email_verification.request_password_reset_code(
                db, "nobody@example.com"
            )
            is None
        )
        assert sent == ["nobody@example.com", "nobody@example.com"]
    finally:
        db.close()
