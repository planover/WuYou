"""FastAPI dependencies."""

from __future__ import annotations

import json

from fastapi import Depends, Header, HTTPException, status

from app.core.database import db
from app.core.security import hash_token, parse_utc, now_utc


def row_to_public_user(row) -> dict:
    keys = row.keys() if hasattr(row, "keys") else row
    return {
        "id": row["user_id"] if "user_id" in keys else row["id"],
        "username": row["username"],
        "email": row["email"],
        "phone": row["phone"],
    }


def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录。")
    token = authorization.removeprefix("Bearer ").strip()
    session = db.query_one(
        """
        SELECT sessions.*, users.username, users.email, users.phone
        FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.token_hash = ?
        """,
        (hash_token(token),),
    )
    if not session or parse_utc(session["expires_at"]) <= now_utc():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录。")
    return dict(session)


def json_loads(value: str, fallback):
    try:
        return json.loads(value)
    except Exception:
        return fallback
