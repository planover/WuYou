"""Mailbox account routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.database import db
from app.core.security import encrypt_secret, utc_iso
from app.models import MailboxCreate, MailboxOut
from app.services.provider_catalog import discover_provider, list_providers
from app.services.sync.jobs import create_job
from app.services.thunderbird import import_thunderbird_profile


class ThunderbirdImportRequest(BaseModel):
    profile_path: str


class MailboxUpdateRequest(BaseModel):
    display_name: str | None = None
    email_address: EmailStr | None = None
    auth_type: str | None = None
    secret: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_ssl: bool | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_ssl: bool | None = None
    sync_enabled: bool | None = None
    signature_html: str | None = None
    signature_text: str | None = None
    auto_reply_enabled: bool | None = None
    auto_reply_subject: str | None = None
    auto_reply_body: str | None = None
    auto_reply_start: str | None = None
    auto_reply_end: str | None = None
    auto_reply_days: int | None = None

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _account_out(row) -> MailboxOut:
    return MailboxOut(
        id=row["id"],
        display_name=row["display_name"],
        email_address=row["email_address"],
        provider=row["provider"],
        imap_host=row["imap_host"],
        imap_port=row["imap_port"],
        imap_ssl=bool(row["imap_ssl"]),
        smtp_host=row["smtp_host"],
        smtp_port=row["smtp_port"],
        smtp_ssl=bool(row["smtp_ssl"]),
        auth_type=row["auth_type"],
        username=row["username"],
        sync_enabled=bool(row["sync_enabled"]),
        signature_html=row["signature_html"] if "signature_html" in row.keys() else "",
        signature_text=row["signature_text"] if "signature_text" in row.keys() else "",
        auto_reply_enabled=bool(row["auto_reply_enabled"]) if "auto_reply_enabled" in row.keys() else False,
        auto_reply_subject=row["auto_reply_subject"] if "auto_reply_subject" in row.keys() else "",
        auto_reply_body=row["auto_reply_body"] if "auto_reply_body" in row.keys() else "",
        auto_reply_start=row["auto_reply_start"] if "auto_reply_start" in row.keys() else None,
        auto_reply_end=row["auto_reply_end"] if "auto_reply_end" in row.keys() else None,
        auto_reply_days=int(row["auto_reply_days"] or 0) if "auto_reply_days" in row.keys() else 0,
        created_at=row["created_at"],
    )


def _get_account(account_id: int, user_id: int):
    row = db.query_one("SELECT * FROM mailbox_accounts WHERE id = ? AND user_id = ?", (account_id, user_id))
    if not row:
        raise HTTPException(status_code=404, detail="邮箱账户不存在。")
    return row


@router.get("", response_model=list[MailboxOut])
def list_accounts(current_user: dict = Depends(get_current_user)):
    rows = db.query_all("SELECT * FROM mailbox_accounts WHERE user_id = ? ORDER BY id DESC", (current_user["user_id"],))
    return [_account_out(row) for row in rows]


@router.get("/providers")
def providers():
    return {"providers": list_providers()}


@router.post("", response_model=MailboxOut)
def create_account(payload: MailboxCreate, current_user: dict = Depends(get_current_user)):
    provider = discover_provider(str(payload.email_address)) if payload.provider == "auto" else None
    provider_id = provider["id"] if provider else payload.provider
    imap_host = payload.imap_host or (provider["imap"]["host"] if provider else None)
    smtp_host = payload.smtp_host or (provider["smtp"]["host"] if provider else None)
    if not imap_host or not smtp_host:
        raise HTTPException(status_code=400, detail="暂未识别该邮箱服务商，请手动填写 IMAP/SMTP。")
    imap_port = payload.imap_port or (provider["imap"]["port"] if provider else 993)
    smtp_port = payload.smtp_port or (provider["smtp"]["port"] if provider else 465)
    imap_ssl = payload.imap_ssl if payload.imap_host or not provider else bool(provider["imap"]["ssl"])
    smtp_ssl = payload.smtp_ssl if payload.smtp_host or not provider else bool(provider["smtp"]["ssl"])
    now = utc_iso()
    cursor = db.execute(
        """
        INSERT INTO mailbox_accounts(
            user_id, display_name, email_address, provider, imap_host, imap_port, imap_ssl,
            smtp_host, smtp_port, smtp_ssl, auth_type, username, encrypted_secret, sync_enabled,
            signature_html, signature_text,
            auto_reply_enabled, auto_reply_subject, auto_reply_body, auto_reply_start, auto_reply_end, auto_reply_days,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            current_user["user_id"],
            payload.display_name,
            str(payload.email_address),
            provider_id,
            imap_host,
            imap_port,
            1 if imap_ssl else 0,
            smtp_host,
            smtp_port,
            1 if smtp_ssl else 0,
            payload.auth_type,
            payload.username or str(payload.email_address),
            encrypt_secret(payload.secret, get_settings().secret_key_path),
            payload.signature_html or "",
            payload.signature_text or "",
            1 if payload.auto_reply_enabled else 0,
            payload.auto_reply_subject or "",
            payload.auto_reply_body or "",
            payload.auto_reply_start,
            payload.auto_reply_end,
            payload.auto_reply_days or 0,
            now,
            now,
        ),
    )
    row = db.query_one("SELECT * FROM mailbox_accounts WHERE id = ?", (cursor.lastrowid,))
    return _account_out(row)


@router.delete("/{account_id}")
def delete_account(account_id: int, current_user: dict = Depends(get_current_user)):
    _get_account(account_id, current_user["user_id"])
    db.execute("DELETE FROM mailbox_accounts WHERE id = ? AND user_id = ?", (account_id, current_user["user_id"]))
    return {"message": "邮箱账户已删除。"}


@router.put("/{account_id}")
def update_account(account_id: int, payload: MailboxUpdateRequest, current_user: dict = Depends(get_current_user)):
    account = _get_account(account_id, current_user["user_id"])
    provider = None
    if payload.email_address:
        provider = discover_provider(str(payload.email_address))
    updates = {}
    if payload.display_name is not None:
        updates["display_name"] = payload.display_name
    if payload.email_address is not None:
        updates["email_address"] = str(payload.email_address)
        updates["username"] = str(payload.email_address)
        if provider:
            updates["provider"] = provider["id"]
            updates["imap_host"] = provider["imap"]["host"]
            updates["imap_port"] = provider["imap"]["port"]
            updates["imap_ssl"] = 1 if provider["imap"]["ssl"] else 0
            updates["smtp_host"] = provider["smtp"]["host"]
            updates["smtp_port"] = provider["smtp"]["port"]
            updates["smtp_ssl"] = 1 if provider["smtp"]["ssl"] else 0
    if payload.auth_type is not None:
        updates["auth_type"] = payload.auth_type
    if payload.secret is not None:
        updates["encrypted_secret"] = encrypt_secret(payload.secret, get_settings().secret_key_path)
    if payload.imap_host is not None:
        updates["imap_host"] = payload.imap_host
    if payload.imap_port is not None:
        updates["imap_port"] = payload.imap_port
    if payload.imap_ssl is not None:
        updates["imap_ssl"] = 1 if payload.imap_ssl else 0
    if payload.smtp_host is not None:
        updates["smtp_host"] = payload.smtp_host
    if payload.smtp_port is not None:
        updates["smtp_port"] = payload.smtp_port
    if payload.smtp_ssl is not None:
        updates["smtp_ssl"] = 1 if payload.smtp_ssl else 0
    if payload.sync_enabled is not None:
        updates["sync_enabled"] = 1 if payload.sync_enabled else 0
    if payload.signature_html is not None:
        updates["signature_html"] = payload.signature_html
    if payload.signature_text is not None:
        updates["signature_text"] = payload.signature_text
    if payload.auto_reply_enabled is not None:
        updates["auto_reply_enabled"] = 1 if payload.auto_reply_enabled else 0
    if payload.auto_reply_subject is not None:
        updates["auto_reply_subject"] = payload.auto_reply_subject
    if payload.auto_reply_body is not None:
        updates["auto_reply_body"] = payload.auto_reply_body
    if payload.auto_reply_start is not None:
        updates["auto_reply_start"] = payload.auto_reply_start if payload.auto_reply_start else None
    if payload.auto_reply_end is not None:
        updates["auto_reply_end"] = payload.auto_reply_end if payload.auto_reply_end else None
    if payload.auto_reply_days is not None:
        updates["auto_reply_days"] = payload.auto_reply_days or 0
    if not updates:
        return _account_out(account)
    updates["updated_at"] = utc_iso()
    set_clauses = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values())
    values.append(account_id)
    db.execute(f"UPDATE mailbox_accounts SET {set_clauses} WHERE id = ?", tuple(values))
    updated = db.query_one("SELECT * FROM mailbox_accounts WHERE id = ?", (account_id,))
    return _account_out(updated)


@router.post("/{account_id}/sync")
def sync_account(account_id: int, current_user: dict = Depends(get_current_user)):
    _get_account(account_id, current_user["user_id"])
    job_id = create_job(
        db,
        int(current_user["user_id"]),
        int(account_id),
        "manual",
        list(get_settings().sync_folders_default),
    )
    return {"job_id": job_id, "message": "已加入同步队列，可在同步任务中查看进度"}


@router.post("/thunderbird/import")
def import_thunderbird(payload: ThunderbirdImportRequest, current_user: dict = Depends(get_current_user)):
    path = Path(payload.profile_path)
    if not path.exists():
        raise HTTPException(status_code=400, detail="Thunderbird 配置目录不存在。")
    report = import_thunderbird_profile(path, db, current_user["user_id"])
    return {"message": "Thunderbird 数据导入完成。", "report": report}
