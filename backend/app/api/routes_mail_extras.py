"""Mail rules, templates, contact groups, and thread routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user, json_loads
from app.core.database import db
from app.core.security import utc_iso
from app.models import (
    ContactGroupCreate,
    ContactGroupOut,
    MailRuleCreate,
    MailRuleOut,
    MessageOut,
    TemplateCreate,
    TemplateOut,
    ThreadOut,
)

router = APIRouter(prefix="/api/mail", tags=["mail-extras"])


# ── helpers ──

def _thread_id_from_subject(subject: str) -> str:
    """Derive a normalized thread-id from subject by stripping Re:/Fwd: prefixes."""
    cleaned = subject.strip()
    while cleaned.lower().startswith(("re:", "fwd:", "aw:")):
        idx = cleaned.find(":") + 1
        cleaned = cleaned[idx:].strip()
    return cleaned.lower().strip()


# ── MAIL RULES ──

@router.get("/rules", response_model=list[MailRuleOut])
def list_rules(current_user: dict = Depends(get_current_user)):
    rows = db.query_all(
        "SELECT * FROM mail_rules WHERE user_id = ? ORDER BY priority DESC, id",
        (current_user["user_id"],),
    )
    return [
        MailRuleOut(
            id=r["id"], name=r["name"], enabled=bool(r["enabled"]),
            condition_field=r["condition_field"], condition_op=r["condition_op"],
            condition_value=r["condition_value"], action_type=r["action_type"],
            action_value=r["action_value"], priority=r["priority"],
        )
        for r in rows
    ]


@router.post("/rules", response_model=MailRuleOut)
def create_rule(payload: MailRuleCreate, current_user: dict = Depends(get_current_user)):
    now = utc_iso()
    cursor = db.execute(
        """INSERT INTO mail_rules(user_id, name, enabled, condition_field, condition_op,
           condition_value, action_type, action_value, priority, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            current_user["user_id"], payload.name, 1 if payload.enabled else 0,
            payload.condition_field, payload.condition_op, payload.condition_value,
            payload.action_type, payload.action_value, payload.priority,
            now, now,
        ),
    )
    row = db.query_one("SELECT * FROM mail_rules WHERE id = ?", (cursor.lastrowid,))
    return MailRuleOut(
        id=row["id"], name=row["name"], enabled=bool(row["enabled"]),
        condition_field=row["condition_field"], condition_op=row["condition_op"],
        condition_value=row["condition_value"], action_type=row["action_type"],
        action_value=row["action_value"], priority=row["priority"],
    )


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, current_user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT id FROM mail_rules WHERE id = ? AND user_id = ?", (rule_id, current_user["user_id"]))
    if not row:
        raise HTTPException(status_code=404, detail="规则不存在。")
    db.execute("DELETE FROM mail_rules WHERE id = ?", (rule_id,))
    return {"message": "规则已删除。"}


@router.put("/rules/{rule_id}", response_model=MailRuleOut)
def update_rule(rule_id: int, payload: MailRuleCreate, current_user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT id FROM mail_rules WHERE id = ? AND user_id = ?", (rule_id, current_user["user_id"]))
    if not row:
        raise HTTPException(status_code=404, detail="规则不存在。")
    now = utc_iso()
    db.execute(
        """UPDATE mail_rules SET name=?, enabled=?, condition_field=?, condition_op=?,
           condition_value=?, action_type=?, action_value=?, priority=?, updated_at=?
           WHERE id=?""",
        (
            payload.name, 1 if payload.enabled else 0, payload.condition_field,
            payload.condition_op, payload.condition_value, payload.action_type,
            payload.action_value, payload.priority, now, rule_id,
        ),
    )
    row = db.query_one("SELECT * FROM mail_rules WHERE id = ?", (rule_id,))
    return MailRuleOut(
        id=row["id"], name=row["name"], enabled=bool(row["enabled"]),
        condition_field=row["condition_field"], condition_op=row["condition_op"],
        condition_value=row["condition_value"], action_type=row["action_type"],
        action_value=row["action_value"], priority=row["priority"],
    )


# ── MESSAGE TEMPLATES ──

@router.get("/templates", response_model=list[TemplateOut])
def list_templates(current_user: dict = Depends(get_current_user)):
    rows = db.query_all(
        "SELECT * FROM message_templates WHERE user_id = ? ORDER BY name",
        (current_user["user_id"],),
    )
    return [TemplateOut(id=r["id"], name=r["name"], subject=r["subject"], body_text=r["body_text"], body_html=r["body_html"], format=r["format"]) for r in rows]


@router.post("/templates", response_model=TemplateOut)
def create_template(payload: TemplateCreate, current_user: dict = Depends(get_current_user)):
    now = utc_iso()
    cursor = db.execute(
        "INSERT INTO message_templates(user_id, name, subject, body_text, body_html, format, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (current_user["user_id"], payload.name, payload.subject, payload.body_text, payload.body_html, payload.format, now, now),
    )
    row = db.query_one("SELECT * FROM message_templates WHERE id = ?", (cursor.lastrowid,))
    return TemplateOut(id=row["id"], name=row["name"], subject=row["subject"], body_text=row["body_text"], body_html=row["body_html"], format=row["format"])


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, current_user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT id FROM message_templates WHERE id = ? AND user_id = ?", (template_id, current_user["user_id"]))
    if not row:
        raise HTTPException(status_code=404, detail="模板不存在。")
    db.execute("DELETE FROM message_templates WHERE id = ?", (template_id,))
    return {"message": "模板已删除。"}


# ── CONTACT GROUPS ──

@router.get("/contact-groups", response_model=list[ContactGroupOut])
def list_contact_groups(current_user: dict = Depends(get_current_user)):
    groups = db.query_all("SELECT * FROM contact_groups WHERE user_id = ? ORDER BY name", (current_user["user_id"],))
    result = []
    for g in groups:
        members = db.query_all(
            "SELECT contact_id FROM contact_group_members WHERE group_id = ?", (g["id"],)
        )
        result.append(ContactGroupOut(
            id=g["id"], name=g["name"],
            contact_ids=[m["contact_id"] for m in members],
            created_at=g["created_at"],
        ))
    return result


@router.post("/contact-groups", response_model=ContactGroupOut)
def create_contact_group(payload: ContactGroupCreate, current_user: dict = Depends(get_current_user)):
    now = utc_iso()
    cursor = db.execute(
        "INSERT INTO contact_groups(user_id, name, created_at) VALUES (?, ?, ?)",
        (current_user["user_id"], payload.name, now),
    )
    gid = cursor.lastrowid
    for cid in payload.contact_ids:
        row = db.query_one("SELECT id FROM content_items WHERE id = ? AND user_id = ? AND kind = 'contact'", (cid, current_user["user_id"]))
        if row:
            db.execute("INSERT OR IGNORE INTO contact_group_members(group_id, contact_id) VALUES (?, ?)", (gid, cid))
    return ContactGroupOut(id=gid, name=payload.name, contact_ids=payload.contact_ids, created_at=now)


@router.delete("/contact-groups/{group_id}")
def delete_contact_group(group_id: int, current_user: dict = Depends(get_current_user)):
    row = db.query_one("SELECT id FROM contact_groups WHERE id = ? AND user_id = ?", (group_id, current_user["user_id"]))
    if not row:
        raise HTTPException(status_code=404, detail="联系人群组不存在。")
    db.execute("DELETE FROM contact_groups WHERE id = ?", (group_id,))
    return {"message": "联系人群组已删除。"}


# ── THREAD VIEW ──

@router.get("/threads", response_model=list[ThreadOut])
def list_threads(folder_role: str = "inbox", current_user: dict = Depends(get_current_user)):
    rows = db.query_all(
        """SELECT * FROM messages WHERE user_id = ? AND folder_role = ?
           ORDER BY received_at DESC LIMIT 400""",
        (current_user["user_id"], folder_role),
    )
    threads: dict[str, dict] = {}
    for r in rows:
        row = dict(r)
        tid = row["thread_id"] or _thread_id_from_subject(row["subject"])
        if tid not in threads:
            threads[tid] = {
                "thread_id": tid,
                "subject": row["subject"],
                "messages": [],
                "latest_at": row["received_at"],
                "count": 0,
            }
        threads[tid]["messages"].append(MessageOut(
            id=row["id"], mailbox_id=row["mailbox_id"], folder=row["folder"],
            subject=row["subject"], sender=row["sender"],
            recipients=json_loads(row["recipients"], []),
            snippet=row["snippet"], body_text=row["body_text"],
            body_html=row["body_html"],
            attachments=json_loads(row["attachments_json"], []),
            unread=bool(row["unread"]), starred=bool(row["starred"]),
            has_attachments=bool(row["has_attachments"]),
            remote_content_allowed=bool(row["remote_content_allowed"]),
            received_at=row["received_at"], tags=[],
            thread_id=row.get("thread_id", ""),
            in_reply_to=row.get("in_reply_to", ""),
        ))
        threads[tid]["count"] += 1
        if row["received_at"] > threads[tid]["latest_at"]:
            threads[tid]["latest_at"] = row["received_at"]

    result = sorted(threads.values(), key=lambda t: t["latest_at"], reverse=True)
    return [ThreadOut(**t) for t in result]
