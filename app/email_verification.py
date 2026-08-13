from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import uuid

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config
from .auth import hash_password, normalize_email, valid_email
from .models import (
    EmailNoticeThrottle,
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
    # 先落库本次请求的限流行再发信，发信期间的并发请求会被 60 秒限流拒绝。
    db.add(row)
    db.commit()
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
            db.delete(row)
            db.commit()
            return "验证码暂时无法发送，请稍后重试"
    else:
        try:
            _send_email(email, code)
        except Exception:  # 不向浏览器暴露邮件服务商或 Key 相关细节
            db.delete(row)
            db.commit()
            return "验证码暂时无法发送，请稍后重试"
    # 新码生效时作废旧码，避免旧码残留可被继续试探。
    db.query(EmailVerification).filter(
        EmailVerification.email == email,
        EmailVerification.consumed_at.is_(None),
        EmailVerification.id != row.id,
    ).update({EmailVerification.consumed_at: now})
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
    db.commit()
    return None


def _notice_email_throttled(db: Session, email: str, now: dt.datetime) -> bool:
    """提醒邮件按邮箱节流：60 秒内最多 1 封、每小时最多 5 封。

    返回 True 表示应跳过本次发送。并发首次请求由唯一约束兜底：
    插入失败的一方保守跳过，宁可少发一封提醒邮件。
    """
    row = db.get(EmailNoticeThrottle, email)
    if row is not None:
        if row.window_started < now - dt.timedelta(hours=1):
            row.attempts = 1
            row.window_started = now
            db.commit()
            return False
        if int(row.attempts or 0) >= 5:
            return True
        if row.window_started > now - dt.timedelta(seconds=60):
            return True
        row.attempts = int(row.attempts or 0) + 1
        db.commit()
        return False
    try:
        with db.begin_nested():
            db.add(EmailNoticeThrottle(email=email, attempts=1, window_started=now))
        db.commit()
        return False
    except IntegrityError:
        db.rollback()
        return True


def request_password_reset_code(db: Session, email: str) -> str | None:
    """请求密码重置码；对有效邮箱统一成功响应，避免枚举注册账号。"""
    email = _normalize_email(email)
    if not configured():
        return "邮件验证尚未配置，请联系管理员"
    if not valid_email(email):
        return "邮箱格式不正确"

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    user = db.query(User).filter(User.email == email).first()
    if not user:
        # 无账号邮箱也发一封提醒邮件：响应时间与有账号时一致，
        # 避免通过耗时判断邮箱是否注册；提醒邮件本身同样按邮箱节流，
        # 防止借邮件服务轰炸任意第三方邮箱。
        if _notice_email_throttled(db, email, now):
            return None
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
    # 先落库本次请求的限流行再发信，发信期间的并发请求会被 60 秒限流拒绝
    # （与注册码路径一致）；发信失败时删除该行，避免留下发不出的验证码。
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    try:
        _send_password_reset_email(email, code)
    except Exception:
        # 仍返回统一成功响应，避免通过邮件服务响应判断账号是否存在。
        db.delete(row)
        db.commit()
        return None
    # 新码生效时作废旧码，避免旧码残留可被继续试探。
    db.query(PasswordResetVerification).filter(
        PasswordResetVerification.user_id == user.id,
        PasswordResetVerification.consumed_at.is_(None),
        PasswordResetVerification.id != row.id,
    ).update({PasswordResetVerification.consumed_at: now})
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
