from __future__ import annotations

from fastapi import APIRouter

from .routes.auth_routes import router as auth_router
from .routes.card_routes import router as card_router
from .routes.dashboard_routes import router as dashboard_router
from .routes.lookup_routes import router as lookup_router
from .routes.tts_routes import router as tts_router
from .routes.word_routes import router as word_router

router = APIRouter(prefix="/api")
router.include_router(auth_router)
router.include_router(card_router)
router.include_router(word_router)
router.include_router(lookup_router)
router.include_router(tts_router)
router.include_router(dashboard_router)
