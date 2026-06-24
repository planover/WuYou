"""Telemetry administration routes (opt-in, no PII)."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.database import db
from app.services.telemetry import flush, get_stats

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])


@router.get("/stats")
def telemetry_stats(current_user: dict = Depends(get_current_user)):
    """Return event counts for the last 7 days.

    Response: ``{"events": N, "top_events": [{"event": "...", "count": N}, ...]}``
    """
    return get_stats(db)


@router.post("/flush")
def telemetry_flush(background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    """Manually flush the pending telemetry event queue (admin trigger)."""
    settings = get_settings()
    # Run flush in a background task so the HTTP response is not blocked
    background_tasks.add_task(flush, db, settings)
    return {"message": "Flush triggered."}
