from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from . import config
from .models import UserApiCredential


class ApiKeyError(RuntimeError):
    pass


@dataclass(frozen=True)
class AiProvider:
    label: str
    base_url: str
    model: str


@dataclass(frozen=True)
class UserAiCredential:
    provider: str
    api_key: str


AI_PROVIDERS: dict[str, AiProvider] = {
    "mimo": AiProvider("小米 MiMo", "https://api.xiaomimimo.com/v1", "mimo-v2-flash"),
    "deepseek": AiProvider("DeepSeek", "https://api.deepseek.com", "deepseek-v4-flash"),
    "openai": AiProvider("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
    "gemini": AiProvider(
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "gemini-2.5-flash",
    ),
    "anthropic": AiProvider(
        "Anthropic Claude", "https://api.anthropic.com/v1/", "claude-sonnet-4-6"
    ),
    "qwen": AiProvider(
        "通义千问",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus",
    ),
    "kimi": AiProvider("Kimi", "https://api.moonshot.cn/v1", "kimi-k3"),
    "glm": AiProvider("智谱 GLM", "https://open.bigmodel.cn/api/paas/v4", "glm-5.2"),
    "xai": AiProvider("xAI Grok", "https://api.x.ai/v1", "grok-4.3"),
}


def _cipher() -> Fernet:
    secret = config.API_KEY_ENCRYPTION_SECRET.strip()
    if not secret:
        raise ApiKeyError("服务器尚未配置 API Key 加密密钥")
    derived = hashlib.sha256(
        f"vocabflow:user-api-key:v1:{secret}".encode()
    ).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def normalize_provider(raw_provider: str) -> str:
    provider = str(raw_provider or "").strip().lower()
    if provider not in AI_PROVIDERS:
        raise ApiKeyError("不支持该 AI 服务商")
    return provider


def normalize_api_key(raw_key: str) -> str:
    key = str(raw_key or "").strip()
    if not re.fullmatch(r"[\x21-\x7e]{10,256}", key):
        raise ApiKeyError("请输入有效的 API Key")
    return key


def save_api_key(
    db: Session, user_id: int, raw_provider: str, raw_key: str
) -> UserApiCredential:
    provider = normalize_provider(raw_provider)
    key = normalize_api_key(raw_key)
    encrypted = _cipher().encrypt(key.encode()).decode()
    credential = db.get(UserApiCredential, user_id)
    if credential is None:
        credential = UserApiCredential(user_id=user_id)
        db.add(credential)
    credential.provider = provider
    credential.encrypted_key = encrypted
    credential.key_hint = key[-4:]
    db.commit()
    db.refresh(credential)
    return credential


def load_api_key(db: Session, user_id: int) -> UserAiCredential | None:
    credential = db.get(UserApiCredential, user_id)
    if credential is None:
        return None
    try:
        decrypted = _cipher().decrypt(credential.encrypted_key.encode()).decode()
    except (InvalidToken, UnicodeError) as exc:
        raise ApiKeyError("已保存的 API Key 无法解密，请重新保存") from exc
    return UserAiCredential(
        provider=normalize_provider(credential.provider),
        api_key=normalize_api_key(decrypted),
    )


def delete_api_key(db: Session, user_id: int) -> bool:
    credential = db.get(UserApiCredential, user_id)
    if credential is None:
        return False
    db.delete(credential)
    db.commit()
    return True


def credential_status(db: Session, user_id: int) -> dict[str, object]:
    credential = db.get(UserApiCredential, user_id)
    provider = credential.provider if credential else ""
    provider_config = AI_PROVIDERS.get(provider)
    return {
        "configured": credential is not None,
        "provider": provider,
        "provider_label": provider_config.label if provider_config else "",
        "key_hint": credential.key_hint if credential else "",
    }
