"""Mail, tags, search, and compose routes."""

from __future__ import annotations

import json
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

from app.api.deps import get_current_user, json_loads
from app.core.config import get_settings
from app.core.database import db
from app.core.security import decrypt_secret, utc_iso
from app.models import (
    MessageOut,
    ReplyRequest,
    ScheduledMailCreate,
    ScheduledMailOut,
    SendMailRequest,
    TagCreate,
    TagOut,
)
from app.services.mail_client import create_imap_folder, delete_imap_folder, rename_imap_folder, save_draft_to_imap, send_email

from collections import defaultdict

router = APIRouter(prefix="/api/mail", tags=["mail"])


def _message_out(row, tag_map: dict[int, list[dict]] | None = None) -> MessageOut:
    """将数据行转为 MessageOut。

    提供 tag_map 时直接从预加载的批量结果中取 tag（避免 N+1）。
    不提供时单独查询（仅用于单封邮件详情）。
    """
    message_id = row["id"]
    if tag_map is not None:
        tags = tag_map.get(message_id, [])
    else:
        tags = db.query_all(
            """
            SELECT tags.id, tags.name, tags.color, tags.priority
            FROM tags
            JOIN message_tags ON message_tags.tag_id = tags.id
            WHERE message_tags.message_id = ?
            ORDER BY tags.priority DESC, tags.name
            """,
            (message_id,),
        )
    return MessageOut(
        id=message_id,
        mailbox_id=row["mailbox_id"],
        folder=row["folder"],
        folder_role=row.get("folder_role", "inbox"),
        subject=row["subject"],
        sender=row["sender"],
        recipients=json_loads(row["recipients"], []),
        snippet=row["snippet"],
        body_text=row["body_text"],
        body_html=row["body_html"],
        attachments=json_loads(row["attachments_json"], []),
        unread=bool(row["unread"]),
        starred=bool(row["starred"]),
        has_attachments=bool(row["has_attachments"]),
        remote_content_allowed=bool(row["remote_content_allowed"]),
        thread_id=row["thread_id"] if "thread_id" in row.keys() else "",
        in_reply_to=row["in_reply_to"] if "in_reply_to" in row.keys() else "",
        received_at=row["received_at"],
        tags=tags if isinstance(tags, list) else [dict(item) for item in tags],
    )


def _batch_tag_map(message_ids: list[int], user_id: int) -> dict[int, list[dict]]:
    """一次查询拉取所有 message 的标签，按 message_id 分组返回。

    消除 inbox 列表页面的 N+1 查询问题：从每封邮件查一次
    变为只查一次 GROUP BY 查询。
    """
    if not message_ids:
        return {}
    placeholders = ",".join("?" for _ in message_ids)
    rows = db.query_all(
        f"""
        SELECT message_tags.message_id, tags.id, tags.name, tags.color, tags.priority
        FROM tags
        JOIN message_tags ON message_tags.tag_id = tags.id
        WHERE tags.user_id = ? AND message_tags.message_id IN ({placeholders})
        ORDER BY tags.priority DESC, tags.name
        """,
        [user_id] + message_ids,
    )
    tag_map: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        tag_map[r["message_id"]].append({
            "id": r["id"],
            "name": r["name"],
            "color": r["color"],
            "priority": r["priority"],
        })
    return tag_map


def _get_message(message_id: int, user_id: int):
    row = db.query_one("SELECT * FROM messages WHERE id = ? AND user_id = ?", (message_id, user_id))
    if not row:
        raise HTTPException(status_code=404, detail="邮件不存在。")
    return row


@router.get("/inbox", response_model=list[MessageOut])
def inbox(
    status: str = Query(default="all", pattern="^(all|unread|read)$"),
    q: str = "",
    tag_id: int | None = None,
    folder_role: str = Query(default="all", pattern="^(all|inbox|sent|trash|archive|junk|custom)$"),
    current_user: dict = Depends(get_current_user),
):
    where = ["messages.user_id = ?"]
    params: list = [current_user["user_id"]]
    if status == "unread":
        where.append("messages.unread = 1")
    elif status == "read":
        where.append("messages.unread = 0")
    if q:
        where.append("(messages.subject LIKE ? OR messages.sender LIKE ? OR messages.body_text LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])
    if tag_id:
        where.append("messages.id IN (SELECT message_id FROM message_tags WHERE tag_id = ?)")
        params.append(tag_id)
    if folder_role != "all":
        where.append("messages.folder_role = ?")
        params.append(folder_role)
    sql = f"""
        SELECT messages.*
        FROM messages
        WHERE {' AND '.join(where)}
        ORDER BY unread DESC, received_at DESC
        LIMIT 200
    """
    rows = db.query_all(sql, params)
    tag_map = _batch_tag_map([r["id"] for r in rows], current_user["user_id"])
    return [_message_out(row, tag_map) for row in rows]


@router.get("/folders")
def list_folders(current_user: dict = Depends(get_current_user)):
    """Return DISTINCT folder_role values with message counts and folder IDs for the current user."""
    rows = db.query_all(
        """
        SELECT m.folder_role, COUNT(*) AS count, mf.id AS folder_id, mf.mailbox_id, mf.imap_name, mf.display_name
        FROM messages m
        LEFT JOIN mailbox_folders mf ON mf.role = m.folder_role AND mf.user_id = m.user_id
        WHERE m.user_id = ?
        GROUP BY m.folder_role
        ORDER BY
            CASE m.folder_role
                WHEN 'inbox' THEN 1
                WHEN 'sent' THEN 2
                WHEN 'trash' THEN 3
                WHEN 'archive' THEN 4
                WHEN 'junk' THEN 5
                ELSE 6
            END
        """,
        (current_user["user_id"],),
    )
    return [dict(row) for row in rows]


class CreateFolderRequest(BaseModel):
    mailbox_id: int
    folder_name: str
    parent_folder: str | None = None


class RenameFolderRequest(BaseModel):
    new_name: str


@router.post("/folders")
def create_folder(payload: CreateFolderRequest, current_user: dict = Depends(get_current_user)):
    account = db.query_one(
        "SELECT * FROM mailbox_accounts WHERE id = ? AND user_id = ?",
        (payload.mailbox_id, current_user["user_id"]),
    )
    if not account:
        raise HTTPException(status_code=404, detail="邮箱账户不存在。")

    folder_name = payload.folder_name.strip()
    if not folder_name:
        raise HTTPException(status_code=400, detail="文件夹名称不能为空。")

    if payload.parent_folder:
        imap_name = f"{payload.parent_folder}/{folder_name}"
    else:
        imap_name = folder_name

    try:
        create_imap_folder(
            dict(account),
            decrypt_secret(account["encrypted_secret"], get_settings().secret_key_path),
            imap_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"创建文件夹失败：{exc}") from exc

    now = utc_iso()
    cursor = db.execute(
        """INSERT INTO mailbox_folders(user_id, mailbox_id, role, imap_name, display_name, created_at, updated_at)
           VALUES (?, ?, 'custom', ?, ?, ?, ?)""",
        (current_user["user_id"], payload.mailbox_id, imap_name, folder_name, now, now),
    )
    return {
        "id": cursor.lastrowid,
        "mailbox_id": payload.mailbox_id,
        "role": "custom",
        "imap_name": imap_name,
        "display_name": folder_name,
    }


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: int, current_user: dict = Depends(get_current_user)):
    folder = db.query_one(
        "SELECT * FROM mailbox_folders WHERE id = ? AND user_id = ?",
        (folder_id, current_user["user_id"]),
    )
    if not folder:
        raise HTTPException(status_code=404, detail="文件夹不存在。")

    account = db.query_one(
        "SELECT * FROM mailbox_accounts WHERE id = ? AND user_id = ?",
        (folder["mailbox_id"], current_user["user_id"]),
    )
    if not account:
        raise HTTPException(status_code=404, detail="邮箱账户不存在。")

    try:
        delete_imap_folder(
            dict(account),
            decrypt_secret(account["encrypted_secret"], get_settings().secret_key_path),
            folder["imap_name"],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"删除文件夹失败：{exc}") from exc

    db.execute("DELETE FROM mailbox_folder_state WHERE folder_id = ?", (folder_id,))
    db.execute("DELETE FROM mailbox_folders WHERE id = ?", (folder_id,))
    return {"message": "文件夹已删除。"}


@router.put("/folders/{folder_id}")
def rename_folder(folder_id: int, payload: RenameFolderRequest, current_user: dict = Depends(get_current_user)):
    folder = db.query_one(
        "SELECT * FROM mailbox_folders WHERE id = ? AND user_id = ?",
        (folder_id, current_user["user_id"]),
    )
    if not folder:
        raise HTTPException(status_code=404, detail="文件夹不存在。")

    new_name = payload.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="文件夹名称不能为空。")

    account = db.query_one(
        "SELECT * FROM mailbox_accounts WHERE id = ? AND user_id = ?",
        (folder["mailbox_id"], current_user["user_id"]),
    )
    if not account:
        raise HTTPException(status_code=404, detail="邮箱账户不存在。")

    old_imap = folder["imap_name"]
    if "/" in old_imap:
        new_imap = "/".join(old_imap.split("/")[:-1] + [new_name])
    else:
        new_imap = new_name

    try:
        rename_imap_folder(
            dict(account),
            decrypt_secret(account["encrypted_secret"], get_settings().secret_key_path),
            old_imap,
            new_imap,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"重命名文件夹失败：{exc}") from exc

    now = utc_iso()
    db.execute(
        "UPDATE mailbox_folders SET imap_name = ?, display_name = ?, updated_at = ? WHERE id = ?",
        (new_imap, new_name, now, folder_id),
    )
    return {"message": "文件夹已重命名。", "imap_name": new_imap, "display_name": new_name}


@router.get("/messages/{message_id}", response_model=MessageOut)
def get_message(message_id: int, current_user: dict = Depends(get_current_user)):
    return _message_out(_get_message(message_id, current_user["user_id"]))


@router.post("/messages/{message_id}/read")
def mark_read(message_id: int, unread: bool = False, current_user: dict = Depends(get_current_user)):
    _get_message(message_id, current_user["user_id"])
    db.execute("UPDATE messages SET unread = ?, updated_at = ? WHERE id = ?", (1 if unread else 0, utc_iso(), message_id))
    return {"message": "邮件状态已更新。"}


@router.post("/messages/{message_id}/remote-content")
def allow_remote_content(message_id: int, allowed: bool, current_user: dict = Depends(get_current_user)):
    _get_message(message_id, current_user["user_id"])
    db.execute(
        "UPDATE messages SET remote_content_allowed = ?, updated_at = ? WHERE id = ?",
        (1 if allowed else 0, utc_iso(), message_id),
    )
    return {"message": "远程内容加载设置已更新。"}


@router.get("/tags", response_model=list[TagOut])
def list_tags(current_user: dict = Depends(get_current_user)):
    rows = db.query_all(
        "SELECT id, name, color, priority FROM tags WHERE user_id = ? ORDER BY priority DESC, name",
        (current_user["user_id"],),
    )
    return [TagOut(**dict(row)) for row in rows]


@router.post("/tags", response_model=TagOut)
def create_tag(payload: TagCreate, current_user: dict = Depends(get_current_user)):
    cursor = db.execute(
        "INSERT INTO tags(user_id, name, color, priority, created_at) VALUES (?, ?, ?, ?, ?)",
        (current_user["user_id"], payload.name, payload.color, payload.priority, utc_iso()),
    )
    row = db.query_one("SELECT id, name, color, priority FROM tags WHERE id = ?", (cursor.lastrowid,))
    return TagOut(**dict(row))


@router.post("/messages/{message_id}/tags/{tag_id}")
def toggle_tag(message_id: int, tag_id: int, enabled: bool = True, current_user: dict = Depends(get_current_user)):
    _get_message(message_id, current_user["user_id"])
    tag = db.query_one("SELECT * FROM tags WHERE id = ? AND user_id = ?", (tag_id, current_user["user_id"]))
    if not tag:
        raise HTTPException(status_code=404, detail="标签不存在。")
    if enabled:
        db.execute("INSERT OR IGNORE INTO message_tags(message_id, tag_id) VALUES (?, ?)", (message_id, tag_id))
    else:
        db.execute("DELETE FROM message_tags WHERE message_id = ? AND tag_id = ?", (message_id, tag_id))
    return {"message": "标签已更新。"}


@router.post("/attachments")
async def upload_attachment(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    upload_dir = os.path.join(get_settings().data_dir, "attachments", str(current_user["user_id"]))
    os.makedirs(upload_dir, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}_{file.filename or 'attachment'}"
    file_path = os.path.join(upload_dir, safe_name)
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)
    cursor = db.execute(
        "INSERT INTO attachments(user_id, filename, original_name, size_bytes, content_type, file_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            current_user["user_id"],
            safe_name,
            file.filename or "attachment",
            len(content),
            file.content_type or "application/octet-stream",
            file_path,
            utc_iso(),
        ),
    )
    return {"id": cursor.lastrowid, "filename": file.filename, "size": len(content)}


@router.post("/send")
def send(payload: SendMailRequest, current_user: dict = Depends(get_current_user)):
    account = db.query_one(
        "SELECT * FROM mailbox_accounts WHERE id = ? AND user_id = ?",
        (payload.mailbox_id, current_user["user_id"]),
    )
    if not account:
        raise HTTPException(status_code=404, detail="发件邮箱不存在。")
    attachments_data = []
    if payload.attachment_ids:
        rows = db.query_all(
            "SELECT * FROM attachments WHERE id IN ({seq}) AND user_id = ?".format(
                seq=",".join("?" * len(payload.attachment_ids))
            ),
            [*payload.attachment_ids, current_user["user_id"]],
        )
        attachments_data = [dict(row) for row in rows]
    try:
        result = send_email(dict(account), decrypt_secret(account["encrypted_secret"], get_settings().secret_key_path), payload, attachments_data)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"发信失败：{exc}") from exc
    return result


@router.post("/messages/{message_id}/star")
def toggle_star(message_id: int, current_user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT id, starred FROM messages WHERE id = ? AND user_id = ?", (message_id, current_user["user_id"]))
    if not row:
        raise HTTPException(status_code=404, detail="邮件不存在。")
    new_val = 0 if row["starred"] else 1
    db.execute("UPDATE messages SET starred = ? WHERE id = ?", (new_val, message_id))
    return {"starred": bool(new_val)}


@router.get("/messages/{message_id}/reply")
def get_reply_template(message_id: int, mode: str = Query("reply"), current_user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT * FROM messages WHERE id = ? AND user_id = ?", (message_id, current_user["user_id"]))
    if not row:
        raise HTTPException(status_code=404, detail="邮件不存在。")
    sender = row["sender"]
    subject = row["subject"]
    body = row["body_text"] or row["snippet"]
    recipients_raw = json_loads(row["recipients"]) if isinstance(row["recipients"], str) else row["recipients"]
    recipients_list = recipients_raw if isinstance(recipients_raw, list) else []
    quoted = "\n".join(f"> {line}" for line in body.split("\n")[:20])
    if mode == "reply":
        to_list = [sender]
        cc_list = []
        prefix = "Re: " if not subject.startswith("Re:") else ""
    elif mode == "reply_all":
        to_list = [sender]
        my_addrs = {a.get("email_address", "") for a in (current_user.get("accounts") or [])}
        cc_list = [r for r in recipients_list if r not in to_list and r not in my_addrs]
        prefix = "Re: " if not subject.startswith("Re:") else ""
    elif mode == "forward":
        to_list = []
        cc_list = []
        prefix = "Fwd: " if not subject.startswith("Fwd:") else ""
        quoted = f"-------- 原始邮件 --------\n发件人: {sender}\n日期: {row['received_at']}\n主题: {subject}\n\n{body}"
    else:
        raise HTTPException(status_code=400, detail="未知回复模式。")
    return {
        "to": to_list,
        "cc": cc_list,
        "subject": f"{prefix}{subject}",
        "body": f"\n\n{quoted}",
        "in_reply_to": message_id,
    }


@router.post("/schedule")
def create_scheduled_mail(payload: ScheduledMailCreate, current_user: dict = Depends(get_current_user)):
    account = db.query_one("SELECT id FROM mailbox_accounts WHERE id = ? AND user_id = ?", (payload.mailbox_id, current_user["user_id"]))
    if not account:
        raise HTTPException(status_code=404, detail="发件邮箱不存在。")
    now = utc_iso()
    cursor = db.execute(
        """
        INSERT INTO scheduled_messages(
            user_id, mailbox_id, recipients_json, cc_json, bcc_json,
            subject, body_text, body_html, format, attachment_ids_json,
            scheduled_at, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (
            current_user["user_id"],
            payload.mailbox_id,
            json.dumps([str(r) for r in payload.recipients]),
            json.dumps([str(r) for r in payload.cc]),
            json.dumps([str(r) for r in payload.bcc]),
            payload.subject,
            payload.body,
            payload.body if payload.format == "html" else "",
            payload.format,
            json.dumps(payload.attachment_ids),
            payload.scheduled_at,
            now,
            now,
        ),
    )
    return {"id": cursor.lastrowid, "message": "定时邮件已创建。"}


@router.get("/scheduled", response_model=list[ScheduledMailOut])
def list_scheduled_mails(current_user: dict = Depends(get_current_user)):
    rows = db.query_all(
        "SELECT * FROM scheduled_messages WHERE user_id = ? ORDER BY scheduled_at DESC",
        (current_user["user_id"],),
    )
    return [ScheduledMailOut(
        id=r["id"], mailbox_id=r["mailbox_id"], recipients=r["recipients_json"],
        cc=r["cc_json"], subject=r["subject"], body_text=r["body_text"],
        scheduled_at=r["scheduled_at"], status=r["status"],
        error=r["error"], sent_at=r["sent_at"], created_at=r["created_at"],
    ) for r in rows]


@router.delete("/scheduled/{scheduled_id}")
def delete_scheduled_mail(scheduled_id: int, current_user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT id FROM scheduled_messages WHERE id = ? AND user_id = ? AND status = 'pending'", (scheduled_id, current_user["user_id"]))
    if not row:
        raise HTTPException(status_code=404, detail="定时邮件不存在或已发送。")
    db.execute("DELETE FROM scheduled_messages WHERE id = ?", (scheduled_id,))
    return {"message": "定时邮件已取消。"}


@router.post("/draft")
def save_draft(payload: SendMailRequest, current_user: dict = Depends(get_current_user)):
    account = db.query_one("SELECT * FROM mailbox_accounts WHERE id = ? AND user_id = ?", (payload.mailbox_id, current_user["user_id"]))
    if not account:
        raise HTTPException(status_code=404, detail="发件账户不存在。")
    from app.services.mail_client import _build_raw_email
    msg_bytes = _build_raw_email(dict(account), payload)
    try:
        secret = decrypt_secret(account["encrypted_secret"], get_settings().secret_key_path)
        save_draft_to_imap(dict(account), secret, msg_bytes)
    except Exception:
        pass
    now = utc_iso()
    cursor = db.execute(
        "INSERT INTO drafts(user_id, mailbox_id, recipients_json, cc_json, bcc_json, subject, body_text, body_html, format, imap_folder, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (current_user["user_id"], payload.mailbox_id,
         json.dumps([str(r) for r in payload.recipients]),
         json.dumps([str(r) for r in payload.cc]),
         json.dumps([str(r) for r in payload.bcc]),
         payload.subject, payload.body, "", payload.format, "Drafts", now, now))
    return {"id": cursor.lastrowid, "message": "草稿已保存。"}


@router.get("/drafts")
def list_drafts(current_user: dict = Depends(get_current_user)):
    rows = db.query_all("SELECT * FROM drafts WHERE user_id = ? ORDER BY updated_at DESC", (current_user["user_id"],))
    return [{"id": r["id"], "mailbox_id": r["mailbox_id"], "recipients": json_loads(r["recipients_json"], []),
             "subject": r["subject"], "body_text": r["body_text"], "format": r["format"],
             "created_at": r["created_at"]} for r in rows]


@router.delete("/draft/{draft_id}")
def delete_draft(draft_id: int, current_user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT id FROM drafts WHERE id = ? AND user_id = ?", (draft_id, current_user["user_id"]))
    if not row:
        raise HTTPException(status_code=404, detail="草稿不存在。")
    db.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))
    return {"message": "草稿已删除。"}


@router.get("/unread")
def unread_summary(current_user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT COUNT(*) AS count FROM messages WHERE user_id = ? AND unread = 1", (current_user["user_id"],))
    return {"unread": row["count"]}


@router.post("/messages/{message_id}/junk")
def toggle_junk(message_id: int, current_user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT id, folder_role FROM messages WHERE id = ? AND user_id = ?", (message_id, current_user["user_id"]))
    if not row:
        raise HTTPException(status_code=404, detail="邮件不存在。")
    if row["folder_role"] == "junk":
        db.execute("UPDATE messages SET folder_role = ?, folder = ? WHERE id = ?", ("inbox", "inbox", message_id))
        return {"junk": False}
    else:
        db.execute("UPDATE messages SET folder_role = ?, folder = ? WHERE id = ?", ("junk", "junk", message_id))
        return {"junk": True}


@router.post("/messages/{message_id}/archive")
def archive_message(message_id: int, current_user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT id, folder_role FROM messages WHERE id = ? AND user_id = ?", (message_id, current_user["user_id"]))
    if not row:
        raise HTTPException(status_code=404, detail="邮件不存在。")
    if row["folder_role"] == "archive":
        db.execute("UPDATE messages SET folder_role = ?, folder = ? WHERE id = ?", ("inbox", "inbox", message_id))
        return {"archived": False}
    else:
        db.execute("UPDATE messages SET folder_role = ?, folder = ? WHERE id = ?", ("archive", "archive", message_id))
        return {"archived": True}


@router.get("/search")
def search(q: str, current_user: dict = Depends(get_current_user)):
    like = f"%{q}%"
    messages = db.query_all(
        """
        SELECT id, 'message' AS kind, subject AS title, snippet AS body, received_at AS updated_at
        FROM messages
        WHERE user_id = ? AND (subject LIKE ? OR sender LIKE ? OR body_text LIKE ?)
        ORDER BY received_at DESC LIMIT 80
        """,
        (current_user["user_id"], like, like, like),
    )
    content = db.query_all(
        """
        SELECT id, kind, title, body, updated_at
        FROM content_items
        WHERE user_id = ? AND (title LIKE ? OR body LIKE ?)
        ORDER BY updated_at DESC LIMIT 80
        """,
        (current_user["user_id"], like, like),
    )
    return {"results": [dict(row) for row in messages + content]}


@router.get("/saved-searches")
def list_saved_searches(current_user: dict = Depends(get_current_user)):
    rows = db.query_all("SELECT * FROM saved_searches WHERE user_id = ? ORDER BY name", (current_user["user_id"],))
    return [{"id": r["id"], "name": r["name"], "query": json_loads(r["query_json"], {})} for r in rows]


@router.post("/saved-searches")
def create_saved_search(payload: dict, current_user: dict = Depends(get_current_user)):
    now = utc_iso()
    cursor = db.execute(
        "INSERT INTO saved_searches(user_id, name, query_json, created_at) VALUES (?, ?, ?, ?)",
        (current_user["user_id"], payload["name"], json.dumps(payload["query"]), now))
    return {"id": cursor.lastrowid, "message": "已保存搜索。"}


@router.delete("/saved-searches/{search_id}")
def delete_saved_search(search_id: int, current_user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT id FROM saved_searches WHERE id = ? AND user_id = ?", (search_id, current_user["user_id"]))
    if not row:
        raise HTTPException(status_code=404, detail="搜索不存在。")
    db.execute("DELETE FROM saved_searches WHERE id = ?", (search_id,))
    return {"message": "已删除搜索。"}

