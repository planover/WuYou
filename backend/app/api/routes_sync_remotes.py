"""WuYou 设备间远程同步路由（pull / push / status）。

提供跨设备的用户数据同步能力：
- ``POST /api/sync/remotes/pull`` — 生成当前用户的完整快照
- ``POST /api/sync/remotes/push`` — 接收远程快照并合并到本地数据库
- ``POST /api/sync/remotes/status`` — 返回当前用户的同步数据摘要

同步数据范围（6 类）：
- settings（用户设置）
- tags（标签）
- mailbox_accounts（邮箱账户）
- mailbox_folders（文件夹映射）
- installed_plugins（已安装插件）
- content_items（日历/联系人/任务/笔记）

合并策略：
- settings / tags / plugins：匹配唯一键，存在则 UPDATE，不存在则 INSERT
- mailbox_accounts：按 email_address 匹配更新或新建
- mailbox_folders：按 imap_name 跨所有邮箱匹配
- content_items：按 id 匹配（覆盖模式）
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.core.database import Database, db
from app.core.security import utc_iso

router = APIRouter(prefix="/api/sync/remotes", tags=["sync-remotes"])


# 注意：此文件中的 build_full_snapshot / merge_snapshot 与
# services/sync/snapshot.py 中的实现有重复。HTTP API 版本操作请求 payload，
# snapshot.py 版本操作通用 dict。两份代码待统一。

# ── Snapshot helpers ──────────────────────────────────────────────────────

def build_full_snapshot(target_db: Database, user_id: int) -> dict:
    """Collect all user-configuration data into a full snapshot dict.

    Returns a dict with snapshot_id, created_at and ``data`` containing the
    six synced categories: settings, tags, mailbox_accounts, mailbox_folders,
    installed_plugins, content_items.
    """
    snapshot_id = uuid.uuid4().hex
    now = utc_iso()

    settings_rows = target_db.query_all(
        "SELECT key, value_json FROM settings WHERE user_id = ?",
        (user_id,),
    )
    tags_rows = target_db.query_all(
        "SELECT id, name, color, priority, created_at FROM tags WHERE user_id = ?",
        (user_id,),
    )
    mailbox_rows = target_db.query_all(
        """SELECT id, display_name, email_address, provider,
                  imap_host, imap_port, imap_ssl,
                  smtp_host, smtp_port, smtp_ssl,
                  auth_type, username, sync_enabled,
                  created_at, updated_at
           FROM mailbox_accounts WHERE user_id = ?""",
        (user_id,),
    )
    folder_rows = target_db.query_all(
        "SELECT id, mailbox_id, role, imap_name, display_name, attributes_json, enabled, created_at, updated_at FROM mailbox_folders WHERE user_id = ?",
        (user_id,),
    )
    plugin_rows = target_db.query_all(
        "SELECT plugin_id, name, version, type, category, manifest_json, enabled, installed_at FROM installed_plugins WHERE user_id = ?",
        (user_id,),
    )
    content_rows = target_db.query_all(
        "SELECT id, mailbox_id, kind, title, body, meta_json, created_at, updated_at FROM content_items WHERE user_id = ?",
        (user_id,),
    )

    snapshot = {
        "snapshot_id": snapshot_id,
        "created_at": now,
        "data": {
            "settings": [dict(row) for row in settings_rows],
            "tags": [dict(row) for row in tags_rows],
            "mailbox_accounts": [dict(row) for row in mailbox_rows],
            "mailbox_folders": [dict(row) for row in folder_rows],
            "installed_plugins": [dict(row) for row in plugin_rows],
            "content_items": [dict(row) for row in content_rows],
        },
    }

    target_db.execute(
        "INSERT INTO sync_snapshots(user_id, snapshot_id, created_at) VALUES (?, ?, ?)",
        (user_id, snapshot_id, now),
    )

    return snapshot


def merge_snapshot(target_db: Database, user_id: int, payload: dict) -> dict:
    """Merge a remote snapshot into the local database.

    Returns a dict with:
    - remote_snapshot_id
    - merged: counts per category
    - conflicts: list of conflict descriptions
    """
    remote_snapshot_id = payload.get("snapshot_id", "")
    data = payload.get("data", {})
    now = utc_iso()

    merged = {
        "settings": 0,
        "tags": 0,
        "mailbox_accounts": 0,
        "mailbox_folders": 0,
        "installed_plugins": 0,
        "content_items": 0,
    }
    conflicts: list[str] = []

    # ── Merge settings ──
    for item in data.get("settings", []):
        try:
            result = target_db.execute(
                """INSERT INTO settings(user_id, key, value_json, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, key) DO UPDATE SET
                       value_json = excluded.value_json,
                       updated_at = excluded.updated_at""",
                (user_id, item["key"], item["value_json"], item.get("updated_at", now)),
            )
            if result.rowcount > 0:
                merged["settings"] += 1
        except Exception as exc:
            conflicts.append(f"settings/{item.get('key', '?')}: {exc}")

    # ── Merge tags ──
    for item in data.get("tags", []):
        try:
            result = target_db.execute(
                """INSERT INTO tags(user_id, name, color, priority, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, name) DO UPDATE SET
                       color = excluded.color,
                       priority = excluded.priority""",
                (
                    user_id,
                    item["name"],
                    item.get("color", "#2f7cf6"),
                    item.get("priority", 0),
                    item.get("created_at", now),
                ),
            )
            if result.rowcount > 0:
                merged["tags"] += 1
        except Exception as exc:
            conflicts.append(f"tags/{item.get('name', '?')}: {exc}")

    # ── Merge mailbox_accounts ──
    for item in data.get("mailbox_accounts", []):
        try:
            email = item.get("email_address", "")
            existing = target_db.query_one(
                "SELECT id FROM mailbox_accounts WHERE user_id = ? AND email_address = ?",
                (user_id, email),
            )
            if existing:
                target_db.execute(
                    """UPDATE mailbox_accounts SET
                           display_name = ?, provider = ?,
                           imap_host = ?, imap_port = ?, imap_ssl = ?,
                           smtp_host = ?, smtp_port = ?, smtp_ssl = ?,
                           auth_type = ?, username = ?, encrypted_secret = ?,
                           sync_enabled = ?, updated_at = ?
                       WHERE user_id = ? AND email_address = ?""",
                    (
                        item.get("display_name", ""),
                        item.get("provider", "auto"),
                        item.get("imap_host", ""),
                        item.get("imap_port", 993),
                        item.get("imap_ssl", 1),
                        item.get("smtp_host", ""),
                        item.get("smtp_port", 465),
                        item.get("smtp_ssl", 1),
                        item.get("auth_type", "app_password"),
                        item.get("username", ""),
                        item.get("encrypted_secret", ""),
                        item.get("sync_enabled", 1),
                        now,
                        user_id,
                        email,
                    ),
                )
            else:
                target_db.execute(
                    """INSERT INTO mailbox_accounts(
                           user_id, display_name, email_address, provider,
                           imap_host, imap_port, imap_ssl,
                           smtp_host, smtp_port, smtp_ssl,
                           auth_type, username, encrypted_secret, sync_enabled,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        item.get("display_name", ""),
                        email,
                        item.get("provider", "auto"),
                        item.get("imap_host", ""),
                        item.get("imap_port", 993),
                        item.get("imap_ssl", 1),
                        item.get("smtp_host", ""),
                        item.get("smtp_port", 465),
                        item.get("smtp_ssl", 1),
                        item.get("auth_type", "app_password"),
                        item.get("username", ""),
                        item.get("encrypted_secret", ""),
                        item.get("sync_enabled", 1),
                        item.get("created_at", now),
                        now,
                    ),
                )
            merged["mailbox_accounts"] += 1
        except Exception as exc:
            conflicts.append(f"mailbox_accounts/{item.get('email_address', '?')}: {exc}")

    # ── Merge mailbox_folders ──
    for item in data.get("mailbox_folders", []):
        try:
            # 按 imap_name 跨同一用户的所有邮箱匹配文件夹（忽略跨设备的 mailbox_id 差异）
            existing = target_db.query_one(
                "SELECT id FROM mailbox_folders WHERE user_id = ? AND imap_name = ?",
                (user_id, item.get("imap_name", "")),
            )
            if existing:
                target_db.execute(
                    """UPDATE mailbox_folders SET
                           role = ?, display_name = ?, attributes_json = ?,
                           enabled = ?, updated_at = ?
                       WHERE user_id = ? AND imap_name = ?""",
                    (
                        item.get("role", "inbox"),
                        item.get("display_name", ""),
                        item.get("attributes_json", "[]"),
                        item.get("enabled", 1),
                        now,
                        user_id,
                        item.get("imap_name", ""),
                    ),
                )
            else:
                target_db.execute(
                    """INSERT INTO mailbox_folders(
                           user_id, mailbox_id, role, imap_name, display_name,
                           attributes_json, enabled, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        item.get("mailbox_id", 0),
                        item.get("role", "inbox"),
                        item.get("imap_name", ""),
                        item.get("display_name", ""),
                        item.get("attributes_json", "[]"),
                        item.get("enabled", 1),
                        item.get("created_at", now),
                        now,
                    ),
                )
            merged["mailbox_folders"] += 1
        except Exception as exc:
            conflicts.append(f"mailbox_folders/{item.get('imap_name', '?')}: {exc}")

    # ── Merge installed_plugins ──
    for item in data.get("installed_plugins", []):
        try:
            result = target_db.execute(
                """INSERT INTO installed_plugins(
                       user_id, plugin_id, name, version, type, category,
                       manifest_json, enabled, installed_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, plugin_id) DO UPDATE SET
                       name = excluded.name,
                       version = excluded.version,
                       type = excluded.type,
                       category = excluded.category,
                       manifest_json = excluded.manifest_json,
                       enabled = excluded.enabled,
                       installed_at = excluded.installed_at""",
                (
                    user_id,
                    item["plugin_id"],
                    item.get("name", ""),
                    item.get("version", ""),
                    item.get("type", ""),
                    item.get("category", ""),
                    item.get("manifest_json", "{}"),
                    item.get("enabled", 1),
                    item.get("installed_at", now),
                ),
            )
            if result.rowcount > 0:
                merged["installed_plugins"] += 1
        except Exception as exc:
            conflicts.append(f"installed_plugins/{item.get('plugin_id', '?')}: {exc}")

    # ── Merge content_items ──
    for item in data.get("content_items", []):
        try:
            existing = target_db.query_one(
                "SELECT id FROM content_items WHERE user_id = ? AND kind = ? AND title = ? AND id = ?",
                (user_id, item.get("kind", ""), item.get("title", ""), item.get("id", 0)),
            )
            if existing:
                target_db.execute(
                    """UPDATE content_items SET
                           body = ?, meta_json = ?, updated_at = ?
                       WHERE user_id = ? AND id = ?""",
                    (
                        item.get("body", ""),
                        item.get("meta_json", "{}"),
                        now,
                        user_id,
                        item.get("id", 0),
                    ),
                )
            else:
                target_db.execute(
                    """INSERT INTO content_items(
                           user_id, mailbox_id, kind, title, body, meta_json,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        item.get("mailbox_id"),
                        item.get("kind", ""),
                        item.get("title", ""),
                        item.get("body", ""),
                        item.get("meta_json", "{}"),
                        item.get("created_at", now),
                        now,
                    ),
                )
            merged["content_items"] += 1
        except Exception as exc:
            conflicts.append(f"content_items/{item.get('title', '?')}: {exc}")

    return {
        "remote_snapshot_id": remote_snapshot_id,
        "merged": merged,
        "conflicts": conflicts,
    }


def _summary(target_db: Database, user_id: int) -> dict:
    """构建用户同步数据的轻量摘要（各表条目计数 + 最近快照 ID）。

    用于 ``/status`` 端点快速返回概要，不传输实际数据。
    """
    settings_count = len(
        target_db.query_all("SELECT 1 FROM settings WHERE user_id = ?", (user_id,))
    )
    tags_count = len(
        target_db.query_all("SELECT 1 FROM tags WHERE user_id = ?", (user_id,))
    )
    mailbox_count = len(
        target_db.query_all("SELECT 1 FROM mailbox_accounts WHERE user_id = ?", (user_id,))
    )
    folder_mapping_count = len(
        target_db.query_all("SELECT 1 FROM mailbox_folders WHERE user_id = ?", (user_id,))
    )
    plugins_count = len(
        target_db.query_all("SELECT 1 FROM installed_plugins WHERE user_id = ?", (user_id,))
    )
    content_items_count = len(
        target_db.query_all("SELECT 1 FROM content_items WHERE user_id = ?", (user_id,))
    )

    latest = target_db.query_one(
        "SELECT snapshot_id FROM sync_snapshots WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )

    return {
        "snapshot_id": latest["snapshot_id"] if latest else None,
        "summary": {
            "settings_count": settings_count,
            "tags_count": tags_count,
            "mailbox_count": mailbox_count,
            "folder_mapping_count": folder_mapping_count,
            "plugins_count": plugins_count,
            "content_items_count": content_items_count,
        },
    }


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.post("/pull")
def pull(
    payload: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """生成当前用户的完整数据快照并持久化快照记录。

    接受可选的 ``last_known_snapshot_id`` 为未来增量同步预留；v1 始终返回全量快照。
    """
    user_id = int(current_user["user_id"])
    last_known = payload.get("last_known_snapshot_id")
    # future: if last_known is provided we could attempt a delta; for now
    # always build a full snapshot.
    _ = last_known
    snapshot = build_full_snapshot(db, user_id)
    return snapshot


@router.post("/push")
def push(
    payload: dict,
    current_user: dict = Depends(get_current_user),
):
    """接收远程快照并合并到本地数据库。

    对六类数据分别执行 INSERT OR UPDATE 合并策略，返回合并计数和冲突列表。
    """
    user_id = int(current_user["user_id"])

    if not isinstance(payload, dict) or "data" not in payload:
        raise HTTPException(status_code=400, detail="payload 必须包含 data 字段。")

    result = merge_snapshot(db, user_id, payload)
    return result


@router.post("/status")
def status(current_user: dict = Depends(get_current_user)):
    """返回当前用户各同步表条目计数及最新快照 ID 的轻量摘要。"""
    user_id = int(current_user["user_id"])
    return _summary(db, user_id)
