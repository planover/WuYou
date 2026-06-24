"""Sync job routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.database import db
from app.services.sync.jobs import create_job

router = APIRouter(prefix="/api/sync", tags=["sync"])


def _job_out(row) -> dict:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "mailbox_id": row["mailbox_id"],
        "trigger": row["trigger"],
        "status": row["status"],
        "folder_roles_json": row["folder_roles_json"],
        "stats_json": row["stats_json"],
        "error": row["error"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.post("/jobs")
def create_sync_job(
    payload: dict,
    current_user: dict = Depends(get_current_user),
):
    """Create a sync job for a mailbox."""
    mailbox_id = payload.get("mailbox_id")
    if not mailbox_id:
        raise HTTPException(status_code=400, detail="mailbox_id 不能为空。")

    # Validate the mailbox belongs to the current user
    mailbox = db.query_one(
        "SELECT id FROM mailbox_accounts WHERE id = ? AND user_id = ?",
        (int(mailbox_id), current_user["user_id"]),
    )
    if not mailbox:
        raise HTTPException(status_code=404, detail="邮箱账户不存在。")

    folder_roles = payload.get("folder_roles")
    if folder_roles and isinstance(folder_roles, list) and len(folder_roles) > 0:
        roles = [str(r) for r in folder_roles]
    else:
        roles = list(get_settings().sync_folders_default)

    job_id = create_job(
        db,
        int(current_user["user_id"]),
        int(mailbox_id),
        "manual",
        roles,
    )
    return {"job_id": job_id, "message": "已加入同步队列"}


@router.get("/jobs")
def list_sync_jobs(
    mailbox_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List sync jobs for the current user, with optional filters."""
    query = "SELECT * FROM sync_jobs WHERE user_id = ?"
    params: list = [current_user["user_id"]]

    if mailbox_id is not None:
        query += " AND mailbox_id = ?"
        params.append(int(mailbox_id))
    if status is not None:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))

    rows = db.query_all(query, tuple(params))
    return [_job_out(row) for row in rows]


@router.get("/jobs/{job_id}")
def get_sync_job(
    job_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Get a single sync job's details (status, stats, error, timestamps)."""
    row = db.query_one(
        "SELECT * FROM sync_jobs WHERE id = ? AND user_id = ?",
        (job_id, current_user["user_id"]),
    )
    if not row:
        raise HTTPException(status_code=404, detail="同步任务不存在。")
    return _job_out(row)
