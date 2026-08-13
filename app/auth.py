from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets

from fastapi import Depends, Request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config
from .db import get_db, is_sqlite_busy_error, reserve_sqlite_write
from .models import LoginThrottle, RequestThrottle, User
from .models import Session as DbSession

_PBKDF2_ITERATIONS = 600_000
_LEGACY_PBKDF2_ITERATIONS = 240_000
_LOGIN_WINDOW_MINUTES = 15
_LOGIN_MAX_FAILURES = 5
_MAX_ACTIVE_SESSIONS = 10
_DUMMY_PASSWORD_HASH = (
    f"$pbkdf2-sha256${_PBKDF2_ITERATIONS}$"
    f"{'00' * 16}${'00' * 32}"
)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def valid_email(email: str) -> bool:
    email = normalize_email(email)
    if len(email) > 255 or email.count("@") != 1:
        return False
    local, domain = email.rsplit("@", 1)
    if not local or len(local) > 64 or any(char.isspace() for char in local):
        return False
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    labels = ascii_domain.split(".")
    return (
        len(labels) >= 2
        and len(ascii_domain) <= 253
        and all(
            label
            and len(label) <= 63
            and label[0].isalnum()
            and label[-1].isalnum()
            and all(char.isalnum() or char == "-" for char in label)
            for label in labels
        )
    )


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt_bytes = bytes.fromhex(salt) if salt else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, _PBKDF2_ITERATIONS
    )
    encoded = (
        f"$pbkdf2-sha256${_PBKDF2_ITERATIONS}"
        f"${salt_bytes.hex()}${digest.hex()}"
    )
    return encoded, ""


def verify_password(password: str, salt: str, expected: str) -> bool:
    if expected.startswith("$argon2id$"):
        # 兼容回退前短暂上线期间升级为 Argon2 的账号：
        # 只验证、不新建；登录成功后由 login_user 自动转回 PBKDF2。
        try:
            from pwdlib import PasswordHash

            return PasswordHash.recommended().verify(password, expected)
        except Exception:
            return False
    if expected.startswith("$pbkdf2-sha256$"):
        try:
            _, scheme, iterations, salt_hex, digest_hex = expected.split("$")
            if scheme != "pbkdf2-sha256":
                return False
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                bytes.fromhex(salt_hex),
                int(iterations),
            )
            return hmac.compare_digest(digest.hex(), digest_hex)
        except (ValueError, TypeError, OverflowError):
            return False
    # 兼容旧 PBKDF2 账户；成功登录后自动升级为带版本和参数的新格式。
    try:
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            _LEGACY_PBKDF2_ITERATIONS,
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), expected)


def _session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _login_locked(db: Session, email: str) -> bool:
    row = db.get(LoginThrottle, email)
    return bool(row and row.locked_until and row.locked_until > dt.datetime.now(dt.timezone.utc).replace(tzinfo=None))


def _record_login_failure(db: Session, email: str) -> None:
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    window = dt.timedelta(minutes=_LOGIN_WINDOW_MINUTES)
    for _attempt in range(4):
        row = db.get(LoginThrottle, email)
        if row is None:
            db.add(LoginThrottle(email=email, failures=1, window_started=now))
            try:
                db.commit()
                return
            except IntegrityError:
                db.rollback()
                continue
        old_failures = int(row.failures or 0)
        old_window_started = row.window_started
        if old_window_started < now - window:
            new_failures = 1
            new_window_started = now
            locked_until = None
        else:
            new_failures = old_failures + 1
            new_window_started = old_window_started
            locked_until = (
                now + window if new_failures >= _LOGIN_MAX_FAILURES else row.locked_until
            )
        result = db.execute(
            update(LoginThrottle)
            .where(
                LoginThrottle.email == email,
                LoginThrottle.failures == old_failures,
                LoginThrottle.window_started == old_window_started,
            )
            .values(
                failures=new_failures,
                window_started=new_window_started,
                locked_until=locked_until,
            )
        )
        if result.rowcount == 1:
            db.commit()
            return
        db.rollback()
    # 竞争持续存在时按一次完整失败窗口锁定，避免并发请求绕过保护。
    db.execute(
        update(LoginThrottle)
        .where(LoginThrottle.email == email)
        .values(failures=_LOGIN_MAX_FAILURES, locked_until=now + window)
    )
    db.commit()


def _extend_login_lock(db: Session, email: str) -> None:
    """锁定期间再试错密码时延长锁定，失败次数保持上限不再累加。"""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    window = dt.timedelta(minutes=_LOGIN_WINDOW_MINUTES)
    db.execute(
        update(LoginThrottle)
        .where(LoginThrottle.email == email)
        .values(failures=_LOGIN_MAX_FAILURES, locked_until=now + window)
    )
    db.commit()


def register_user(
    db: Session, email: str, password: str
) -> tuple[User | None, str | None]:
    email = normalize_email(email)
    if not valid_email(email):
        return None, "邮箱格式不正确"
    if len(password) < 10:
        return None, "密码至少 10 位"
    if db.query(User).filter(User.email == email).first():
        return None, "该邮箱已注册"
    pw_hash, salt = hash_password(password)
    user = User(email=email, password_hash=pw_hash, salt=salt)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None, "该邮箱已注册"
    db.refresh(user)
    return user, None


def login_user(
    db: Session, email: str, password: str
) -> tuple[User | None, str | None]:
    email = normalize_email(email)
    user = db.query(User).filter(User.email == email).first()
    locked = _login_locked(db, email)
    if locked:
        # 锁定期间仍校验密码：本人输入正确密码可正常登录并解除锁定，
        # 避免知道邮箱的攻击者用持续失败把真正用户挡在门外；
        # 错误密码会延长锁定，使暴力破解持续付出代价。
        password_ok = verify_password(
            password,
            user.salt if user else "",
            user.password_hash if user else _DUMMY_PASSWORD_HASH,
        )
        if user and password_ok:
            throttle = db.get(LoginThrottle, email)
            if throttle:
                db.delete(throttle)
            if not user.password_hash.startswith("$pbkdf2-sha256$"):
                user.password_hash, user.salt = hash_password(password)
            db.commit()
            return user, None
        _extend_login_lock(db, email)
        return None, "登录尝试过多，请稍后再试"
    password_ok = verify_password(
        password,
        user.salt if user else "",
        user.password_hash if user else _DUMMY_PASSWORD_HASH,
    )
    if not user or not password_ok:
        _record_login_failure(db, email)
        return None, "邮箱或密码错误"
    throttle = db.get(LoginThrottle, email)
    if throttle:
        db.delete(throttle)
    if not user.password_hash.startswith("$pbkdf2-sha256$"):
        user.password_hash, user.salt = hash_password(password)
    db.commit()
    return user, None


def create_session(db: Session, user: User) -> str:
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    db.query(DbSession).filter(
        DbSession.user_id == user.id,
        DbSession.expires_at <= now,
    ).delete(synchronize_session=False)
    active = (
        db.query(DbSession)
        .filter(DbSession.user_id == user.id)
        .order_by(DbSession.created_at.desc())
        .all()
    )
    for old_session in active[_MAX_ACTIVE_SESSIONS - 1 :]:
        db.delete(old_session)
    token = secrets.token_urlsafe(32)
    session = DbSession(
        token=_session_token_hash(token),
        user_id=user.id,
        expires_at=now + dt.timedelta(days=config.SESSION_TTL_DAYS),
    )
    db.add(session)
    db.commit()
    return token


def delete_session(db: Session, token: str) -> None:
    hashed = _session_token_hash(token)
    db.query(DbSession).filter(DbSession.token.in_([hashed, token])).delete()
    db.commit()


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(config.COOKIE_NAME)
    if not token:
        return None
    hashed = _session_token_hash(token)
    session = db.query(DbSession).filter(DbSession.token.in_([hashed, token])).first()
    if not session:
        return None
    if session.expires_at < dt.datetime.now(dt.timezone.utc).replace(tzinfo=None):
        db.delete(session)
        db.commit()
        return None
    if session.token == token:
        session.token = hashed
        db.commit()
    return db.get(User, session.user_id)


def check_request_rate(
    db: Session,
    *,
    action: str,
    identity: str,
    limit: int,
    window_minutes: int,
    need: int = 1,
) -> bool:
    """按 (action, identity) 做固定窗口计数；limit <= 0 表示不限制。

    key 只保存 SHA-256 摘要，避免在限流表里留下邮箱/IP 明文。
    阈值按“正常使用不会触发”的高值配置，仅挡住异常滥用。
    """
    if limit <= 0 or need <= 0:
        return True
    key = hashlib.sha256(f"{action}:{identity}".encode()).hexdigest()
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    window = dt.timedelta(minutes=window_minutes)
    try:
        reserve_sqlite_write(db)
    except Exception as exc:
        db.rollback()
        if not is_sqlite_busy_error(exc):
            raise
        return False
    for _attempt in range(4):
        row = db.get(RequestThrottle, key)
        if row is None:
            db.add(RequestThrottle(key=key, attempts=need, window_started=now))
            try:
                db.commit()
            except IntegrityError:
                # 并发请求同时创建同一 key：回滚后重读一次。
                db.rollback()
                continue
            return True
        old_attempts = int(row.attempts or 0)
        old_window_started = row.window_started
        if old_window_started < now - window:
            new_attempts = need
            new_window_started = now
        else:
            new_attempts = old_attempts + need
            new_window_started = old_window_started
        if new_attempts > limit:
            return False
        result = db.execute(
            update(RequestThrottle)
            .where(
                RequestThrottle.key == key,
                RequestThrottle.attempts == old_attempts,
                RequestThrottle.window_started == old_window_started,
            )
            .values(attempts=new_attempts, window_started=new_window_started)
        )
        if result.rowcount == 1:
            db.commit()
            return True
        db.rollback()
    # 极端并发下宁可让客户端短暂重试，也不绕过滥用保护。
    return False
