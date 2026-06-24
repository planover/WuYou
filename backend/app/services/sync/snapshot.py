"""Snapshot engine -- full snapshot builder and merge logic.

Task 2: 快照引擎提供两个纯函数：
- build_full_snapshot：生成全量快照
- merge_snapshot：逐表合并，按 updated_at 比较
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.database import Database
from app.core.security import utc_iso


# ──────────────────────────────────────────────
#  build_full_snapshot
# ──────────────────────────────────────────────

def build_full_snapshot(db: Database, user_id: int) -> dict[str, Any]:
    """生成全量快照，返回 7 个顶层键。

    Keys: snapshot_id, settings, tags, mailbox_accounts, folder_mappings,
          installed_plugins, content_items
    """
    snapshot_id = str(uuid.uuid4())

    settings = [dict(row) for row in db.query_all(
        "SELECT * FROM settings WHERE user_id = ?", (user_id,)
    )]

    tags = [dict(row) for row in db.query_all(
        "SELECT * FROM tags WHERE user_id = ?", (user_id,)
    )]

    # 显式列出所有列，排除 encrypted_secret
    mailbox_accounts = [dict(row) for row in db.query_all(
        """SELECT id, user_id, display_name, email_address, provider,
                  imap_host, imap_port, imap_ssl, smtp_host, smtp_port,
                  smtp_ssl, auth_type, username, sync_enabled,
                  created_at, updated_at
           FROM mailbox_accounts WHERE user_id = ?""",
        (user_id,),
    )]

    folder_mappings = [dict(row) for row in db.query_all(
        "SELECT * FROM mailbox_folders WHERE user_id = ?", (user_id,),
    )]

    installed_plugins = [dict(row) for row in db.query_all(
        "SELECT * FROM installed_plugins WHERE user_id = ?", (user_id,),
    )]

    content_items = [dict(row) for row in db.query_all(
        "SELECT * FROM content_items WHERE user_id = ?", (user_id,),
    )]

    # 插入 snapshot 记录
    now = utc_iso()
    db.execute(
        "INSERT INTO sync_snapshots(user_id, snapshot_id, created_at) VALUES (?, ?, ?)",
        (user_id, snapshot_id, now),
    )

    return {
        "snapshot_id": snapshot_id,
        "settings": settings,
        "tags": tags,
        "mailbox_accounts": mailbox_accounts,
        "folder_mappings": folder_mappings,
        "installed_plugins": installed_plugins,
        "content_items": content_items,
    }


# ──────────────────────────────────────────────
#  merge_snapshot
# ──────────────────────────────────────────────

def merge_snapshot(db: Database, user_id: int, incoming: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """逐表合并传入快照，按 updated_at 比较。

    远程新则覆盖本地，远程旧则记录 conflict。

    Returns:
        (summary, conflicts)
        summary = {category: {updated, inserted, conflicts}, ...}
        conflicts = [{category, key_info, local_ts, remote_ts}, ...]
    """
    summary: dict[str, dict[str, int]] = {}
    conflicts: list[dict[str, Any]] = []

    def _init_category(cat: str) -> None:
        if cat not in summary:
            summary[cat] = {"updated": 0, "inserted": 0, "conflicts": 0}

    def _resolve(
        cat: str,
        local_ts: str | None,
        remote_ts: str | None,
    ) -> str:
        """比较两个 ISO 时间字符串，返回 "update" / "conflict" / "insert"。

        规则：remote_ts > local_ts → "update"；否则 → "conflict"。
        如果 local_ts 为 None，则视为 "insert"（本地不存在该行）。
        """
        if local_ts is None:
            return "insert"
        # 按字符串字典序比较 ISO 格式
        if remote_ts is not None and remote_ts > local_ts:
            return "update"
        return "conflict"

    # ── settings ──
    _init_category("settings")
    incoming_settings = incoming.get("settings", []) or []
    for remote in incoming_settings:
        local = db.query_one(
            "SELECT * FROM settings WHERE user_id = ? AND key = ?",
            (user_id, remote["key"]),
        )
        action = _resolve("settings", local["updated_at"] if local else None, remote.get("updated_at"))
        if action == "insert":
            db.execute(
                "INSERT INTO settings(user_id, key, value_json, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, remote["key"], remote["value_json"], remote["updated_at"]),
            )
            summary["settings"]["inserted"] += 1
        elif action == "update":
            db.execute(
                "UPDATE settings SET value_json = ?, updated_at = ? WHERE user_id = ? AND key = ?",
                (remote["value_json"], remote["updated_at"], user_id, remote["key"]),
            )
            summary["settings"]["updated"] += 1
        else:
            conflicts.append({
                "category": "settings",
                "key": remote["key"],
                "local_updated_at": local["updated_at"] if local else None,
                "remote_updated_at": remote.get("updated_at"),
            })
            summary["settings"]["conflicts"] += 1

    # ── tags ──
    _init_category("tags")
    incoming_tags = incoming.get("tags", []) or []
    for remote in incoming_tags:
        local = db.query_one(
            "SELECT * FROM tags WHERE user_id = ? AND name = ?",
            (user_id, remote["name"]),
        )
        # tags 表没有 updated_at，用 created_at 比较
        action = _resolve("tags", local["created_at"] if local else None, remote.get("created_at"))
        if action == "insert":
            db.execute(
                """INSERT INTO tags(user_id, name, color, priority, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, remote["name"], remote["color"], remote["priority"], remote["created_at"]),
            )
            summary["tags"]["inserted"] += 1
        elif action == "update":
            db.execute(
                "UPDATE tags SET color = ?, priority = ?, created_at = ? WHERE user_id = ? AND name = ?",
                (remote["color"], remote["priority"], remote["created_at"], user_id, remote["name"]),
            )
            summary["tags"]["updated"] += 1
        else:
            conflicts.append({
                "category": "tags",
                "name": remote["name"],
                "local_ts": local["created_at"] if local else None,
                "remote_ts": remote.get("created_at"),
            })
            summary["tags"]["conflicts"] += 1

    # ── mailbox_accounts ──
    _init_category("mailbox_accounts")
    incoming_accounts = incoming.get("mailbox_accounts", []) or []
    for remote in incoming_accounts:
        local = db.query_one(
            "SELECT * FROM mailbox_accounts WHERE user_id = ? AND email_address = ?",
            (user_id, remote["email_address"]),
        )
        action = _resolve("mailbox_accounts", local["updated_at"] if local else None, remote.get("updated_at"))
        if action == "insert":
            # encrypted_secret 写空串
            db.execute(
                """INSERT INTO mailbox_accounts(user_id, display_name, email_address, provider,
                    imap_host, imap_port, imap_ssl, smtp_host, smtp_port, smtp_ssl,
                    auth_type, username, encrypted_secret, sync_enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    remote["display_name"],
                    remote["email_address"],
                    remote["provider"],
                    remote["imap_host"],
                    remote["imap_port"],
                    remote["imap_ssl"],
                    remote["smtp_host"],
                    remote["smtp_port"],
                    remote["smtp_ssl"],
                    remote["auth_type"],
                    remote["username"],
                    "",  # encrypted_secret 写空串
                    remote["sync_enabled"],
                    remote["created_at"],
                    remote["updated_at"],
                ),
            )
            summary["mailbox_accounts"]["inserted"] += 1
        elif action == "update":
            db.execute(
                """UPDATE mailbox_accounts
                   SET display_name = ?, email_address = ?, provider = ?,
                       imap_host = ?, imap_port = ?, imap_ssl = ?,
                       smtp_host = ?, smtp_port = ?, smtp_ssl = ?,
                       auth_type = ?, username = ?, sync_enabled = ?,
                       created_at = ?, updated_at = ?
                   WHERE user_id = ? AND email_address = ?""",
                (
                    remote["display_name"],
                    remote["email_address"],
                    remote["provider"],
                    remote["imap_host"],
                    remote["imap_port"],
                    remote["imap_ssl"],
                    remote["smtp_host"],
                    remote["smtp_port"],
                    remote["smtp_ssl"],
                    remote["auth_type"],
                    remote["username"],
                    remote["sync_enabled"],
                    remote["created_at"],
                    remote["updated_at"],
                    user_id,
                    remote["email_address"],
                ),
            )
            summary["mailbox_accounts"]["updated"] += 1
        else:
            conflicts.append({
                "category": "mailbox_accounts",
                "email_address": remote["email_address"],
                "local_updated_at": local["updated_at"] if local else None,
                "remote_updated_at": remote.get("updated_at"),
            })
            summary["mailbox_accounts"]["conflicts"] += 1

    # ── folder_mappings ──
    _init_category("folder_mappings")
    incoming_folders = incoming.get("folder_mappings", []) or []
    for remote in incoming_folders:
        remote_mailbox_email = remote.get("mailbox_email", "")
        # JOIN mailbox_accounts 查找 mailbox_id
        local = db.query_one(
            """SELECT mf.* FROM mailbox_folders mf
               JOIN mailbox_accounts ma ON ma.id = mf.mailbox_id
               WHERE mf.user_id = ? AND ma.email_address = ? AND mf.imap_name = ?""",
            (user_id, remote_mailbox_email, remote["imap_name"]),
        )
        action = _resolve("folder_mappings", local["updated_at"] if local else None, remote.get("updated_at"))
        if action == "insert":
            # 需要拿到 mailbox_id
            mailbox_row = db.query_one(
                "SELECT id FROM mailbox_accounts WHERE user_id = ? AND email_address = ?",
                (user_id, remote_mailbox_email),
            )
            if mailbox_row is None:
                continue  # 没有对应的邮箱账户，跳过
            mailbox_id = int(mailbox_row["id"])
            db.execute(
                """INSERT INTO mailbox_folders(user_id, mailbox_id, role, imap_name,
                    display_name, attributes_json, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    mailbox_id,
                    remote["role"],
                    remote["imap_name"],
                    remote.get("display_name", ""),
                    remote.get("attributes_json", "[]"),
                    remote.get("enabled", 1),
                    remote["created_at"],
                    remote["updated_at"],
                ),
            )
            summary["folder_mappings"]["inserted"] += 1
        elif action == "update":
            db.execute(
                """UPDATE mailbox_folders
                   SET role = ?, display_name = ?, attributes_json = ?,
                       enabled = ?, created_at = ?, updated_at = ?
                   WHERE user_id = ? AND mailbox_id = ? AND imap_name = ?""",
                (
                    remote["role"],
                    remote.get("display_name", ""),
                    remote.get("attributes_json", "[]"),
                    remote.get("enabled", 1),
                    remote["created_at"],
                    remote["updated_at"],
                    user_id,
                    int(local["mailbox_id"]) if local else 0,
                    remote["imap_name"],
                ),
            )
            summary["folder_mappings"]["updated"] += 1
        else:
            conflicts.append({
                "category": "folder_mappings",
                "mailbox_email": remote_mailbox_email,
                "imap_name": remote["imap_name"],
                "local_updated_at": local["updated_at"] if local else None,
                "remote_updated_at": remote.get("updated_at"),
            })
            summary["folder_mappings"]["conflicts"] += 1

    # ── installed_plugins ──
    _init_category("installed_plugins")
    incoming_plugins = incoming.get("installed_plugins", []) or []
    for remote in incoming_plugins:
        local = db.query_one(
            "SELECT * FROM installed_plugins WHERE user_id = ? AND plugin_id = ?",
            (user_id, remote["plugin_id"]),
        )
        if local is None:
            # 不存在则 insert
            db.execute(
                """INSERT INTO installed_plugins(user_id, plugin_id, name, version,
                    type, category, manifest_json, installed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    remote["plugin_id"],
                    remote["name"],
                    remote["version"],
                    remote["type"],
                    remote["category"],
                    remote["manifest_json"],
                    remote.get("installed_at", utc_iso()),
                ),
            )
            summary["installed_plugins"]["inserted"] += 1
        else:
            # 已存在，跳过（没有 updated_at 字段，不做时间比较）
            summary["installed_plugins"]["conflicts"] += 0

    # ── content_items ──
    _init_category("content_items")
    incoming_items = incoming.get("content_items", []) or []
    for remote in incoming_items:
        local = db.query_one(
            "SELECT * FROM content_items WHERE user_id = ? AND kind = ? AND title = ?",
            (user_id, remote["kind"], remote["title"]),
        )
        action = _resolve("content_items", local["updated_at"] if local else None, remote.get("updated_at"))
        if action == "insert":
            db.execute(
                """INSERT INTO content_items(user_id, mailbox_id, kind, title,
                    body, meta_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    remote.get("mailbox_id"),
                    remote["kind"],
                    remote["title"],
                    remote.get("body", ""),
                    remote.get("meta_json", "{}"),
                    remote["created_at"],
                    remote["updated_at"],
                ),
            )
            summary["content_items"]["inserted"] += 1
        elif action == "update":
            db.execute(
                """UPDATE content_items
                   SET mailbox_id = ?, body = ?, meta_json = ?,
                       created_at = ?, updated_at = ?
                   WHERE user_id = ? AND kind = ? AND title = ?""",
                (
                    remote.get("mailbox_id"),
                    remote.get("body", ""),
                    remote.get("meta_json", "{}"),
                    remote["created_at"],
                    remote["updated_at"],
                    user_id,
                    remote["kind"],
                    remote["title"],
                ),
            )
            summary["content_items"]["updated"] += 1
        else:
            conflicts.append({
                "category": "content_items",
                "kind": remote["kind"],
                "title": remote["title"],
                "local_updated_at": local["updated_at"] if local else None,
                "remote_updated_at": remote.get("updated_at"),
            })
            summary["content_items"]["conflicts"] += 1

    return summary, conflicts
