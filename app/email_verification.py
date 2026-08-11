from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import uuid

import httpx
from sqlalchemy.orm import Session

from . import config
from .auth import hash_password, normalize_email, valid_email
from .models import (
    EmailVerification,
    LoginThrottle,
    PasswordResetVerification,
    User,
)
from .models import (
    Session as DbSession,
)


def _normalize_email(email: str) -> str:
    return normalize_email(email)


def _code_hash(email: str, code: str) -> str:
    message = f"{_normalize_email(email)}:{code}".encode()
    return hmac.new(
        config.VERIFICATION_SECRET.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


def _password_reset_code_hash(email: str, code: str) -> str:
    message = f"password-reset:{_normalize_email(email)}:{code}".encode()
    return hmac.new(
        config.VERIFICATION_SECRET.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


def configured() -> bool:
    return bool(
        config.RESEND_API_KEY
        and config.VERIFICATION_SECRET
        and "@" in config.EMAIL_FROM
    )


def _send_email(email: str, code: str) -> None:
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {config.RESEND_API_KEY}",
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4()),
        },
        json={
            "from": config.EMAIL_FROM,
            "to": [email],
            "subject": "VocabTool 注册验证码",
            "text": (
                f"你的 VocabTool 注册验证码是：{code}\n\n"
                f"验证码 {config.VERIFICATION_CODE_TTL_MINUTES} 分钟内有效。"
                "如果不是你本人操作，请忽略这封邮件。"
            ),
        },
        timeout=10.0,
    )
    response.raise_for_status()


def _send_password_reset_email(email: str, code: str) -> None:
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {config.RESEND_API_KEY}",
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4()),
        },
        json={
            "from": config.EMAIL_FROM,
            "to": [email],
            "subject": "VocabTool 密码重置验证码",
            "text": (
                f"你的 VocabTool 密码重置验证码是：{code}\n\n"
                f"验证码 {config.VERIFICATION_CODE_TTL_MINUTES} 分钟内有效。"
                "如果不是你本人操作，请忽略这封邮件，你的密码不会改变。"
            ),
        },
        timeout=10.0,
    )
    response.raise_for_status()


def _send_account_notice_email(email: str, subject: str, text: str) -> None:
    """发送不含验证码的提醒邮件；用于已注册邮箱的注册尝试等场景。

    发送行为与正常验证码邮件一致，使请求耗时相同，避免响应时间旁路
    泄露邮箱是否已注册。
    """
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {config.RESEND_API_KEY}",
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid.uuid4()),
        },
        json={
            "from": config.EMAIL_FROM,
            "to": [email],
            "subject": subject,
            "text": text,
        },
        timeout=10.0,
    )
    response.raise_for_status()


def request_code(db: Session, email: str) -> str | None:
    """发送注册验证码。返回错误信息；成功返回 None。"""
    email = _normalize_email(email)
    if not configured():
        return "邮件验证尚未配置，请联系管理员"
    if not valid_email(email):
        return "邮箱格式不正确"

    # 限流判断放在“是否已注册”之前，且已注册邮箱同样写入验证码表，
    # 两种邮箱的响应与限流行为完全一致，无法据此枚举账号。
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    latest = (
        db.query(EmailVerification)
        .filter(EmailVerification.email == email)
        .order_by(EmailVerification.created_at.desc())
        .first()
    )
    if latest and latest.created_at > now - dt.timedelta(seconds=60):
        return "验证码发送过于频繁，请 60 秒后再试"
    sent_last_hour = (
        db.query(EmailVerification)
        .filter(
            EmailVerification.email == email,
            EmailVerification.created_at >= now - dt.timedelta(hours=1),
        )
        .count()
    )
    if sent_last_hour >= 5:
        return "该邮箱验证码请求过多，请稍后再试"

    already_registered = db.query(User).filter(User.email == email).first() is not None
    code = f"{secrets.randbelow(1_000_000):06d}"
    row = EmailVerification(
        email=email,
        code_hash=_code_hash(email, code),
        expires_at=now
        + dt.timedelta(minutes=config.VERIFICATION_CODE_TTL_MINUTES),
    )
    if already_registered:
        # 不发送可用的注册验证码，改为发提醒邮件：耗时相同、响应相同，
        # 且邮箱主人能得知有人正在试探注册。
        try:
            _send_account_notice_email(
                email,
                "VocabTool：该邮箱已注册",
                "有人尝试使用此邮箱注册 VocabTool，但该邮箱已有账号。\n"
                "如果不是你本人操作，请忽略这封邮件，无需处理。",
            )
        except Exception:  # 不向浏览器暴露邮件服务商或 Key 相关细节
            return "验证码暂时无法发送，请稍后重试"
    else:
        try:
            _send_email(email, code)
        except Exception:  # 不向浏览器暴露邮件服务商或 Key 相关细节
            return "验证码暂时无法发送，请稍后重试"
    # 新码生效时作废旧码，避免旧码残留可被继续试探。
    db.query(EmailVerification).filter(
        EmailVerification.email == email,
        EmailVerification.consumed_at.is_(None),
    ).update({EmailVerification.consumed_at: now})
    db.add(row)
    db.commit()
    return None


def verify_code(db: Session, email: str, code: str) -> str | None:
    email = _normalize_email(email)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    row = (
        db.query(EmailVerification)
        .filter(
            EmailVerification.email == email,
            EmailVerification.consumed_at.is_(None),
        )
        .order_by(EmailVerification.created_at.desc())
        .first()
    )
    if not row or row.expires_at < now:
        return "验证码不存在或已过期，请重新获取"
    if row.attempts >= 5:
        return "验证码尝试次数过多，请重新获取"
    if not hmac.compare_digest(row.code_hash, _code_hash(email, code.strip())):
        row.attempts += 1
        db.commit()
        return "验证码错误"
    row.consumed_at = now
    return None


def request_password_reset_code(db: Session, email: str) -> str | None:
    """请求密码重置码；对有效邮箱统一成功响应，避免枚举注册账号。"""
    email = _normalize_email(email)
    if not configured():
        return "邮件验证尚未配置，请联系管理员"
    if not valid_email(email):
        return "邮箱格式不正确"

    user = db.query(User).filter(User.email == email).first()
    if not user:
        # 无账号邮箱也发一封提醒邮件：响应时间与有账号时一致，
        # 避免通过耗时判断邮箱是否注册。
        try:
            _send_account_notice_email(
                email,
                "VocabTool：该邮箱未注册",
                "有人尝试重置此邮箱的 VocabTool 密码，但该邮箱没有账号。\n"
                "如果不是你本人操作，请忽略这封邮件。",
            )
        except Exception:
            # 仍返回统一成功响应，避免通过邮件发送状态判断账号是否存在。
            return None
        return None

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    latest = (
        db.query(PasswordResetVerification)
        .filter(PasswordResetVerification.user_id == user.id)
        .order_by(PasswordResetVerification.created_at.desc())
        .first()
    )
    if latest and latest.created_at > now - dt.timedelta(seconds=60):
        return None
    sent_last_hour = (
        db.query(PasswordResetVerification)
        .filter(
            PasswordResetVerification.user_id == user.id,
            PasswordResetVerification.created_at >= now - dt.timedelta(hours=1),
        )
        .count()
    )
    if sent_last_hour >= 5:
        return None

    code = f"{secrets.randbelow(1_000_000):06d}"
    row = PasswordResetVerification(
        user_id=user.id,
        code_hash=_password_reset_code_hash(email, code),
        expires_at=now
        + dt.timedelta(minutes=config.VERIFICATION_CODE_TTL_MINUTES),
    )
    try:
        _send_password_reset_email(email, code)
    except Exception:
        # 仍返回统一成功响应，避免通过邮件服务响应判断账号是否存在。
        return None
    # 新码生效时作废旧码，避免旧码残留可被继续试探。
    db.query(PasswordResetVerification).filter(
        PasswordResetVerification.user_id == user.id,
        PasswordResetVerification.consumed_at.is_(None),
    ).update({PasswordResetVerification.consumed_at: now})
    db.add(row)
    db.commit()
    return None


def reset_password(
    db: Session, email: str, code: str, new_password: str
) -> str | None:
    email = _normalize_email(email)
    if len(new_password) < 10:
        return "密码至少 10 位"
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return "验证码无效或已过期，请重新获取"

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    row = (
        db.query(PasswordResetVerification)
        .filter(
            PasswordResetVerification.user_id == user.id,
            PasswordResetVerification.consumed_at.is_(None),
        )
        .order_by(PasswordResetVerification.created_at.desc())
        .first()
    )
    if not row or row.expires_at < now or row.attempts >= 5:
        return "验证码无效或已过期，请重新获取"
    if not hmac.compare_digest(
        row.code_hash, _password_reset_code_hash(email, code.strip())
    ):
        row.attempts += 1
        db.commit()
        return "验证码错误"

    user.password_hash, user.salt = hash_password(new_password)
    row.consumed_at = now
    db.query(DbSession).filter(DbSession.user_id == user.id).delete(
        synchronize_session=False
    )
    throttle = db.get(LoginThrottle, email)
    if throttle:
        db.delete(throttle)
    db.commit()
    return None
