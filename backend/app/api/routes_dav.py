"""WuYou DAV（CalDAV / CardDAV）账户管理与同步路由。

支持的协议：
- ``caldav`` — 通过 CalDAV 同步日历事件
- ``carddav`` — 通过 CardDAV 同步联系人
- ``google_tasks`` — 通过 Google Tasks API 同步任务
- ``ms_graph`` — 通过 Microsoft Graph API 同步日历/联系人

端点：
- ``GET /api/dav/accounts`` — 列出当前用户的 DAV 账户
- ``POST /api/dav/accounts`` — 创建 DAV 账户（密码自动 Fernet 加密存储）
- ``DELETE /api/dav/accounts/{id}`` — 删除 DAV 账户
- ``POST /api/dav/accounts/{id}/sync`` — 触发 DAV 账户同步
- ``POST /api/dav/discover`` — 通过邮箱地址发现 CalDAV/CardDAV URL
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.database import db
from app.core.security import decrypt_secret, encrypt_secret, utc_iso
from app.services.dav.caldav import sync_caldav
from app.services.dav.carddav import sync_carddav
from app.services.dav.discovery import discover_caldav_url, discover_carddav_url
from app.services.dav.google_tasks import sync_google_tasks
from app.services.dav.ms_graph import sync_ms_graph

router = APIRouter(prefix="/api/dav", tags=["dav"])


# ── Pydantic request models ─────────────────────────────────────────────

class DavAccountCreate(BaseModel):
    kind: Literal["calendar", "contacts", "tasks"]
    protocol: str = Field(min_length=1, max_length=32)
    url: str = Field(min_length=1, max_length=2048)
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=2048)
    mailbox_account_id: int | None = None


class DavDiscoverRequest(BaseModel):
    email: str = Field(min_length=3, max_length=256)


# ── Helper: row -> public dict (exclude encrypted_password) ──────────────

def _account_out(row) -> dict:
    """将 dav_accounts 数据库行转为前端安全 dict（排除 encrypted_password）。"""
    keys = row.keys()
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "mailbox_account_id": row["mailbox_account_id"],
        "kind": row["kind"],
        "protocol": row["protocol"],
        "url": row["url"],
        "username": row["username"],
        "sync_enabled": bool(row["sync_enabled"]),
        "last_sync_at": row["last_sync_at"],
        "last_sync_status": row["last_sync_status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _get_dav_account(account_id: int, user_id: int):
    """按 ID + user_id 查找 DAV 账户，不存在则抛出 404。

    Args:
        account_id: DAV 账户 ID。
        user_id: 当前用户 ID。

    Returns:
        匹配的 sqlite3.Row。

    Raises:
        HTTPException(404): 账户不存在。
    """
    row = db.query_one(
        "SELECT * FROM dav_accounts WHERE id = ? AND user_id = ?",
        (account_id, user_id),
    )
    if not row:
        raise HTTPException(status_code=404, detail="DAV 账户不存在。")
    return row


# ── Endpoints ───────────────────────────────────────────────────────────

@router.get("/accounts")
def list_accounts(current_user: dict = Depends(get_current_user)):
    """列出当前用户的所有 DAV 账户（按 ID 倒序）。"""
    rows = db.query_all(
        "SELECT * FROM dav_accounts WHERE user_id = ? ORDER BY id DESC",
        (current_user["user_id"],),
    )
    return [_account_out(row) for row in rows]


@router.post("/accounts")
def create_account(
    payload: DavAccountCreate,
    current_user: dict = Depends(get_current_user),
):
    """创建新的 DAV 账户。密码通过 Fernet 加密后存储，永不返回明文。"""
    now = utc_iso()
    encrypted = encrypt_secret(payload.password, get_settings().secret_key_path)
    cursor = db.execute(
        """
        INSERT INTO dav_accounts(
            user_id, mailbox_account_id, kind, protocol, url,
            username, encrypted_password, sync_enabled,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            current_user["user_id"],
            payload.mailbox_account_id,
            payload.kind,
            payload.protocol,
            payload.url,
            payload.username,
            encrypted,
            now,
            now,
        ),
    )
    row = db.query_one("SELECT * FROM dav_accounts WHERE id = ?", (cursor.lastrowid,))
    return _account_out(row)


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: int,
    current_user: dict = Depends(get_current_user),
):
    """删除指定 DAV 账户（含归属权校验）。"""
    _get_dav_account(account_id, current_user["user_id"])
    db.execute(
        "DELETE FROM dav_accounts WHERE id = ? AND user_id = ?",
        (account_id, current_user["user_id"]),
    )
    return {"message": "DAV 账户已删除。"}


@router.post("/accounts/{account_id}/sync")
async def sync_account(
    account_id: int,
    current_user: dict = Depends(get_current_user),
):
    row = _get_dav_account(account_id, current_user["user_id"])
    account = dict(row)
    protocol = (account.get("protocol") or "").lower()
    user_id = int(current_user["user_id"])

    if protocol == "caldav":
        password = decrypt_secret(
            account["encrypted_password"], get_settings().secret_key_path
        )
        result = await sync_caldav(db, user_id, account, password)
    elif protocol == "carddav":
        password = decrypt_secret(
            account["encrypted_password"], get_settings().secret_key_path
        )
        result = await sync_carddav(db, user_id, account, password)
    elif protocol == "google_tasks":
        result = await sync_google_tasks(
            db, user_id, account, account["encrypted_password"]
        )
    elif protocol == "ms_graph":
        result = await sync_ms_graph(
            db, user_id, account, account["encrypted_password"]
        )
    else:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的 DAV 协议: {protocol}",
        )

    return result


@router.post("/discover")
async def discover(payload: DavDiscoverRequest):
    """通过邮箱地址自动发现 CalDAV 和 CardDAV 的服务端点 URL。"""
    email = payload.email.strip()
    caldav_url = await discover_caldav_url(email)
    carddav_url = await discover_carddav_url(email)
    return {
        "email": email,
        "caldav_url": caldav_url,
        "carddav_url": carddav_url,
    }
