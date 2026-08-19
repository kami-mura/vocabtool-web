from __future__ import annotations

import base64
import hashlib
import re

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from . import config
from .models import UserApiCredential


class ApiKeyError(RuntimeError):
    pass


def _cipher() -> Fernet:
    secret = config.API_KEY_ENCRYPTION_SECRET.strip()
    if not secret:
        raise ApiKeyError("服务器尚未配置 API Key 加密密钥")
    derived = hashlib.sha256(
        f"vocabflow:user-api-key:v1:{secret}".encode()
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def normalize_deepseek_key(raw_key: str) -> str:
    key = str(raw_key or "").strip()
    if not re.fullmatch(r"sk-[A-Za-z0-9_-]{8,253}", key):
        raise ApiKeyError("请输入有效的 DeepSeek API Key")
    return key


def save_deepseek_key(db: Session, user_id: int, raw_key: str) -> UserApiCredential:
    key = normalize_deepseek_key(raw_key)
    encrypted = _cipher().encrypt(key.encode()).decode()
    credential = db.get(UserApiCredential, user_id)
    if credential is None:
        credential = UserApiCredential(user_id=user_id)
        db.add(credential)
    credential.provider = "deepseek"
    credential.encrypted_key = encrypted
    credential.key_hint = key[-4:]
    db.commit()
    db.refresh(credential)
    return credential


def load_deepseek_key(db: Session, user_id: int) -> str | None:
    credential = db.get(UserApiCredential, user_id)
    if credential is None:
        return None
    try:
        decrypted = _cipher().decrypt(credential.encrypted_key.encode()).decode()
    except (InvalidToken, UnicodeError) as exc:
        raise ApiKeyError("已保存的 API Key 无法解密，请重新保存") from exc
    return normalize_deepseek_key(decrypted)


def delete_deepseek_key(db: Session, user_id: int) -> bool:
    credential = db.get(UserApiCredential, user_id)
    if credential is None:
        return False
    db.delete(credential)
    db.commit()
    return True


def credential_status(db: Session, user_id: int) -> dict[str, object]:
    credential = db.get(UserApiCredential, user_id)
    return {
        "configured": credential is not None,
        "provider": "deepseek",
        "key_hint": credential.key_hint if credential else "",
    }
