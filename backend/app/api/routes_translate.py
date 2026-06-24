"""Translation routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.models import TranslationRequest, TranslationResponse
from app.services.telemetry import track
from app.services.translation import PROVIDER_OPTIONS, translate

router = APIRouter(prefix="/api/translate", tags=["translate"])


@router.get("/providers")
def providers(current_user: dict = Depends(get_current_user)):
    return {"providers": PROVIDER_OPTIONS}


@router.post("", response_model=TranslationResponse)
async def translate_text(payload: TranslationRequest, current_user: dict = Depends(get_current_user)):
    try:
        result = await translate(payload, get_settings().request_timeout_seconds)
        track("translation_used", provider=result.provider)
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

