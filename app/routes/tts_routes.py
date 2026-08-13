from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import tts
from ..api_support import _anonymous_request_identity, _require_user, check_request_rate
from ..auth import current_user
from ..db import get_db

router = APIRouter(prefix="/tts", tags=["tts"])


class PrefetchIn(BaseModel):
    texts: list[str] = Field(default_factory=list, max_length=200)


class TtsIn(BaseModel):
    text: str = Field(default="", min_length=1, max_length=500)


@router.post("")
async def generate_tts(
    body: TtsIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """按文本生成/复用缓存音频，返回可播放 URL；登录用户按用户限流，
    游客（查词结果发音等）按来源身份限流。"""
    user = current_user(request, db)
    if user is None:
        if not check_request_rate(
            db,
            action="tts-generate",
            identity=_anonymous_request_identity(request),
            limit=200,
            window_minutes=60,
        ):
            raise HTTPException(status_code=429, detail="语音生成请求过多，请稍后再试或登录")
    elif not check_request_rate(
        db,
        action="tts-generate",
        identity=f"u{user.id}",
        limit=1200,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="语音生成请求过多，请稍后再试")
    url = await tts.audio_url_for_text(body.text)
    if not url:
        raise HTTPException(status_code=503, detail="语音生成失败，请稍后重试")
    return {"url": url}


@router.post("/prefetch")
async def prefetch_tts(
    body: PrefetchIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """后台预生成一批文本的音频，立即返回；仅限登录用户，按用户限流。"""
    user = _require_user(db, request)
    if not check_request_rate(
        db,
        action="tts-prefetch",
        identity=f"u{user.id}",
        limit=300,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="语音预生成请求过多，请稍后再试")
    scheduled = tts.schedule_prefetch(body.texts)
    return {"ok": True, "scheduled": scheduled}
