"""Sync peers management API -- CRUD for device-to-device sync peer records."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.database import db
from app.core.security import utc_iso
from app.services.sync.remote_client import run_remote_sync_cycle

router = APIRouter(prefix="/api/sync/peers", tags=["sync-peers"])

remote_router = APIRouter(prefix="/api/sync/remote", tags=["sync-remote"])


# ── GET /api/sync/peers ─────────────────────────────────────────────────

@router.get("")
def list_peers(current_user: dict = Depends(get_current_user)):
    """Return all sync_peers belonging to the current user."""
    user_id = int(current_user["user_id"])
    rows = db.query_all(
        "SELECT id, label, url, remote_username, enabled, last_sync_at, last_status, created_at, updated_at "
        "FROM sync_peers WHERE user_id = ? ORDER BY id ASC",
        (user_id,),
    )
    return [dict(row) for row in rows]


# ── POST /api/sync/peers ────────────────────────────────────────────────

@router.post("")
def create_peer(
    payload: dict,
    current_user: dict = Depends(get_current_user),
):
    """Create a new sync_peer record.

    Required fields: ``url``, ``remote_username``.
    Optional field: ``label`` (defaults to ``远程设备``).
    """
    user_id = int(current_user["user_id"])
    now = utc_iso()

    url = (payload.get("url") or "").strip()
    remote_username = (payload.get("remote_username") or "").strip()
    label = (payload.get("label") or "远程设备").strip()

    if not url:
        raise HTTPException(status_code=400, detail="url 不能为空。")
    if not remote_username:
        raise HTTPException(status_code=400, detail="remote_username 不能为空。")

    peer_id = db.execute(
        """INSERT INTO sync_peers(user_id, label, url, remote_username, enabled, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, ?, ?)""",
        (user_id, label, url, remote_username, now, now),
    ).lastrowid

    row = db.query_one("SELECT * FROM sync_peers WHERE id = ?", (peer_id,))
    return dict(row)


# ── DELETE /api/sync/peers/{peer_id} ────────────────────────────────────

@router.delete("/{peer_id}")
def delete_peer(
    peer_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Delete a sync_peer after verifying it belongs to the current user."""
    user_id = int(current_user["user_id"])

    peer = db.query_one(
        "SELECT id, user_id FROM sync_peers WHERE id = ?",
        (peer_id,),
    )
    if peer is None:
        raise HTTPException(status_code=404, detail="同步对等端不存在。")
    if int(peer["user_id"]) != user_id:
        raise HTTPException(status_code=403, detail="无权删除此同步对等端。")

    db.execute("DELETE FROM sync_peers WHERE id = ?", (peer_id,))
    return {"message": "已删除", "peer_id": peer_id}


# ── POST /api/sync/remote/now ─────────────────────────────────────────────

@remote_router.post("/now")
def sync_remote_now(
    payload: dict,
    current_user: dict = Depends(get_current_user),
):
    """Trigger a remote sync cycle synchronously within the current request.

    Accepts ``{"action": "push"|"pull"|"full"}`` and calls
    :func:`run_remote_sync_cycle` using ``asyncio.run()``.
    Returns ``{"message": "同步已完成。", "action": "..."}``.
    """
    action = (payload.get("action") or "full").strip().lower()
    if action not in ("push", "pull", "full"):
        raise HTTPException(status_code=400, detail="action 必须为 push、pull 或 full。")

    user_id = int(current_user["user_id"])

    # v1 MVP: action (push/pull/full) reserved for future delta sync; always full sync for now
    settings = get_settings()
    asyncio.run(run_remote_sync_cycle(db, settings, user_id=user_id))

    return {"message": "同步已完成。", "action": action}
