"""System management routes: health, hot-reload."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user

from app.core.config import get_settings

router = APIRouter(prefix="/api/system", tags=["system"])

_START_TIME = time.time()


@router.get("/health")
def health() -> dict:
    """Return system health info: version, uptime, memory usage."""
    settings = get_settings()
    uptime_seconds = time.time() - _START_TIME
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        mem_info = proc.memory_info()
        memory_mb = round(mem_info.rss / (1024 * 1024), 2)
    except ImportError:
        memory_mb = -1

    return {
        "status": "ok",
        "app": settings.app_name,
        "version": "1.0.1",
        "uptime_seconds": round(uptime_seconds, 1),
        "memory_mb": memory_mb,
    }


@router.post("/reload")
def reload(current_user: dict = Depends(get_current_user)) -> dict:
    """Manually trigger hot-reload of static assets and plugins."""
    from app.services.hot_reload import reload_plugins, reload_static_assets

    reload_static_assets()
    reload_plugins()
    return {"message": "热更新已完成。", "status": "ok"}
