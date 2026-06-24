"""Community share API -- submit theme / language-pack / extension."""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.core.database import db
from app.core.security import utc_iso

router = APIRouter(prefix="/api/share", tags=["share"])

ALLOWED_TYPES = {"theme", "language-pack", "extension"}


# ── request model ───────────────────────────────────────────────────────

class ShareRequest(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    item_id: str = Field(min_length=1, max_length=128)
    manifest: dict = Field(default_factory=dict)


# ── helpers ──────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    d = dict(row)
    d["manifest"] = json.loads(d.pop("manifest_json", "{}"))
    return d


# ── routes ───────────────────────────────────────────────────────────────

@router.post("")
def submit_share(payload: ShareRequest, current_user: dict = Depends(get_current_user)):
    """Submit a community share item.

    ``type`` must be one of ``theme``, ``language-pack``, ``extension``.
    Duplicate submissions for the same ``type`` + ``item_id`` by the same
    user are rejected with 409.
    """
    if payload.type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="type 必须是 theme、language-pack 或 extension。")
    try:
        db.execute(
            """
            INSERT INTO shared_items (user_id, type, item_id, manifest_json, status, submitted_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (
                current_user["user_id"],
                payload.type,
                payload.item_id,
                json.dumps(payload.manifest, ensure_ascii=False),
                utc_iso(),
            ),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="您已提交过该类型的同一项目，请勿重复提交。",
        )

    return {"message": "分享已提交，等待审核。"}


@router.get("/submissions")
def list_submissions(current_user: dict = Depends(get_current_user)):
    """Return all shared_items submitted by the current user."""
    rows = db.query_all(
        "SELECT id, type, item_id, manifest_json, status, submitted_at FROM shared_items WHERE user_id = ? ORDER BY submitted_at DESC",
        (current_user["user_id"],),
    )
    return {"submissions": [_row_to_dict(r) for r in rows]}
