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
