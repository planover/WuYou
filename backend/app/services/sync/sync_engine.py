"""WuYou 邮件同步引擎核心原语。

提供 IMAP 邮箱多文件夹增量同步的完整流程：
- ``build_uid_range`` — UID 增量范围表达式（纯函数）
- ``_parse_list_line`` — IMAP LIST 响应行解析
- ``ensure_folders`` — 确保 mailbox_folders 映射存在
- ``sync_mailbox`` — 单邮箱多文件夹增量同步
- ``run_mailbox_sync`` — 同步任务入口（解析 job、委托 sync_mailbox）

同步流程：
1. 解析 job 中的 folder_roles
2. 调用 ensure_folders 发现并注册 IMAP 文件夹
3. 过滤启用的文件夹，读取 mailbox_folder_state 中的 last_uid
4. 连接 IMAP 获取 UIDVALIDITY（变化则重置游标）
5. UID SEARCH 拉取增量 → 分批 FETCH → 解析邮件 → 批量 INSERT OR IGNORE
6. 更新 mailbox_folder_state
"""

from __future__ import annotations

import imaplib
import json
import logging
import re
import ssl
from pathlib import Path
from typing import Any, Iterable

from app.core.database import Database
from app.core.security import decrypt_secret, utc_iso
from app.core.config import get_settings, Settings
from app.services.sync.constants import DEFAULT_ROLES, ROLE_CUSTOM, ROLE_INBOX
from app.services.sync.folder_discovery import classify_folder
from app.services.telemetry import track

logger = logging.getLogger(__name__)


def build_uid_range(last_uid: int) -> str:
    """构建 IMAP UID 增量拉取的 range 表达式。

    IMAP UID 范围表达式通常使用 "start:*" 表示从某个 UID 开始直到最新。
    这里的 start 需要是 last_uid 的下一个 UID（即 last_uid + 1）。
    """

    start_uid = int(last_uid) + 1
    if start_uid < 1:
        start_uid = 1
    return f"{start_uid}:*"


def _open_imap(account: dict[str, Any], secret: str) -> imaplib.IMAP4:
    """建立 IMAP 连接并登录。

    根据 account 配置自动选择 SSL 直连或 STARTTLS 升级。
    oauth2/sms_code 类型的账户直接拒绝（需服务商网关支持）。

    Args:
        account: mailbox_accounts 行 dict。
        secret: 解密的邮箱密码。

    Returns:
        已登录的 IMAP4 连接对象。

    Raises:
        RuntimeError: 不支持的登录方式。
    """
    if account["auth_type"] in {"oauth2", "sms_code"}:
        raise RuntimeError("该登录方式需要接入服务商 OAuth2/验证码网关后才能同步。")

    if account["imap_ssl"]:
        client: imaplib.IMAP4 = imaplib.IMAP4_SSL(account["imap_host"], int(account["imap_port"]))
    else:
        client = imaplib.IMAP4(account["imap_host"], int(account["imap_port"]))
        client.starttls(ssl.create_default_context())
    client.login(account["username"], secret)
    return client


_LIST_LINE_RE = re.compile(
    r"^\((?P<flags>[^)]*)\)\s+(?P<delim>NIL|\"[^\"]*\")\s+(?P<name>.*)$"
)
"""IMAP LIST 响应的正则表达式，提取 flags 和 folder name。"""


def _parse_list_line(line: bytes) -> tuple[str, list[str]] | None:
    """解析 IMAP LIST 的单行输出，返回 (folder_name, flags)."""

    try:
        text = line.decode("utf-8", errors="ignore").strip()
    except Exception:
        return None
    if not text:
        return None

    match = _LIST_LINE_RE.match(text)
    if not match:
        # 兜底：尽可能从最后一个 token 提取 name
        parts = text.split(" ", 2)
        if not parts:
            return None
        name = parts[-1].strip()
        if name.startswith('"') and name.endswith('"') and len(name) >= 2:
            name = name[1:-1]
        return name, []

    flags_part = match.group("flags").strip()
    flags = [item for item in flags_part.split() if item] if flags_part else []
    name = match.group("name").strip()
    if name.startswith('"') and name.endswith('"') and len(name) >= 2:
        name = name[1:-1]
    return name, flags


def ensure_folders(db: Database, mailbox_row: dict[str, Any] | Any, secret: str) -> list[dict[str, Any]]:
    """确保 mailbox_folders 已建立映射。

    - 若该 mailbox 未建立映射：连接 IMAP 执行 LIST → classify_folder() → 写入 mailbox_folders
    - 必须保证至少写入 inbox(INBOX)
    - 对 custom 默认 enabled=0
    """

    mailbox = dict(mailbox_row)
    existing = db.query_all(
        "SELECT * FROM mailbox_folders WHERE user_id = ? AND mailbox_id = ? ORDER BY id ASC",
        (int(mailbox["user_id"]), int(mailbox["id"])),
    )
    if existing:
        return [dict(row) for row in existing]

    now = utc_iso()
    inserted_rows: list[tuple] = []
    seen_names: set[str] = set()
    client: imaplib.IMAP4 | None = None
    try:
        client = _open_imap(mailbox, secret)
        status, data = client.list()
        if status == "OK" and data:
            for item in data:
                if not item:
                    continue
                parsed = _parse_list_line(item)
                if not parsed:
                    continue
                imap_name, flags = parsed
                if not imap_name:
                    continue
                if imap_name.upper() == "INBOX":
                    role = ROLE_INBOX
                else:
                    role = classify_folder(imap_name=imap_name, flags=flags)
                enabled = 0 if role == ROLE_CUSTOM else 1
                if imap_name in seen_names:
                    continue
                seen_names.add(imap_name)
                inserted_rows.append(
                    (
                        int(mailbox["user_id"]),
                        int(mailbox["id"]),
                        str(role),
                        str(imap_name),
                        str(imap_name),
                        json.dumps(flags or [], ensure_ascii=False),
                        int(enabled),
                        now,
                        now,
                    )
                )
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass

    # 确保至少有 INBOX
    if "INBOX" not in {name.upper() for name in seen_names}:
        inserted_rows.append(
            (
                int(mailbox["user_id"]),
                int(mailbox["id"]),
                ROLE_INBOX,
                "INBOX",
                "INBOX",
                "[]",
                1,
                now,
                now,
            )
        )

    if inserted_rows:
        db.executemany(
            """
            INSERT OR IGNORE INTO mailbox_folders(
              user_id, mailbox_id, role, imap_name, display_name, attributes_json, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            inserted_rows,
        )

    rows = db.query_all(
        "SELECT * FROM mailbox_folders WHERE user_id = ? AND mailbox_id = ? ORDER BY id ASC",
        (int(mailbox["user_id"]), int(mailbox["id"])),
    )
    return [dict(row) for row in rows]


def _iter_enabled_folders(
    folders: Iterable[dict[str, Any]],
    role: str,
) -> list[dict[str, Any]]:
    """过滤出指定 role 且 enabled=1 的文件夹列表。

    Args:
        folders: 所有文件夹 row 列表。
        role: 要匹配的角色（如 "inbox"）。

    Returns:
        匹配的文件夹 row 列表。
    """
    return [
        f for f in folders
        if f.get("role") == role and int(f.get("enabled", 0)) == 1
    ]


def sync_mailbox(
    db: Database,
    mailbox_row: dict[str, Any] | Any,
    secret: str,
    folder_roles: list[str] | None,
    attachment_root,
) -> dict[str, Any]:
    """同步一个 mailbox 的多个文件夹（仅同步 enabled=1 的映射）。

    Returns:
        stats dict: {fetched, inserted, folders:{role:[...]}}
    """

    mailbox = dict(mailbox_row)
    roles = folder_roles or list(DEFAULT_ROLES)
    folder_rows = ensure_folders(db, mailbox, secret)

    folders_to_sync: list[tuple[str, dict[str, Any]]] = []
    for role in roles:
        if role == ROLE_CUSTOM:
            for row in _iter_enabled_folders(folder_rows, role=ROLE_CUSTOM):
                folders_to_sync.append((ROLE_CUSTOM, row))
            continue
        enabled = _iter_enabled_folders(folder_rows, role=role)
        if enabled:
            folders_to_sync.append((role, enabled[0]))

    now = utc_iso()
    stats: dict[str, Any] = {"fetched": 0, "inserted": 0, "folders": {}}

    # 延迟导入，避免与 mail_client.py 循环依赖（mail_client 会 import build_uid_range）
    from app.services.mail_client import sync_folder_incremental

    for role, folder_row in folders_to_sync:
        imap_name = str(folder_row["imap_name"])
        folder_id = int(folder_row["id"])
        per_folder = {"imap_folder": imap_name, "fetched": 0, "inserted": 0}

        state = db.query_one(
            """
            SELECT * FROM mailbox_folder_state
            WHERE user_id = ? AND mailbox_id = ? AND imap_name = ?
            """,
            (int(mailbox["user_id"]), int(mailbox["id"]), imap_name),
        )
        if not state:
            db.execute(
                """
                INSERT INTO mailbox_folder_state(
                  user_id, mailbox_id, folder_id, imap_name, uidvalidity, last_uid, last_sync_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, 0, NULL, ?, ?)
                """,
                (int(mailbox["user_id"]), int(mailbox["id"]), folder_id, imap_name, now, now),
            )
            state = db.query_one(
                """
                SELECT * FROM mailbox_folder_state
                WHERE user_id = ? AND mailbox_id = ? AND imap_name = ?
                """,
                (int(mailbox["user_id"]), int(mailbox["id"]), imap_name),
            )

        last_uid = int(state["last_uid"] or 0) if state else 0
        old_uidvalidity = int(state["uidvalidity"]) if state and state["uidvalidity"] is not None else None

        # 先读 IMAP UIDVALIDITY，避免 UIDVALIDITY 变化时拉取两次
        cur_uidvalidity: int | None = None
        imap_client: imaplib.IMAP4 | None = None
        try:
            imap_client = _open_imap(mailbox, secret)
            imap_client.select(imap_name)
            resp = imap_client.response("UIDVALIDITY")
            if resp and resp[1]:
                raw = resp[1][0]
                text = raw.decode("ascii", errors="ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
                mm = re.search(r"(\d+)", text)
                if mm:
                    cur_uidvalidity = int(mm.group(1))
        except Exception:
            pass
        finally:
            if imap_client is not None:
                try:
                    imap_client.logout()
                except Exception:
                    pass

        effective_last_uid = 0 if (cur_uidvalidity is not None and old_uidvalidity is not None and cur_uidvalidity != old_uidvalidity) else last_uid

        messages, new_last_uid, uidvalidity = sync_folder_incremental(
            mailbox,
            secret,
            imap_folder=imap_name,
            last_uid=effective_last_uid,
            attachment_root=attachment_root,
        )

        per_folder["fetched"] = len(messages)
        stats["fetched"] += len(messages)

        # ── 批量入库：所有 message 参数一次性 executemany，单次 COMMIT 提升性能
        # 注意：此处绕过 Database 的 lock 方法直接获取底层连接，适用于仅在同步线程中调用的场景
        inserted = 0
        message_rows = []
        for message in messages:
            message["folder_role"] = role
            message["imap_folder"] = imap_name
            message["folder"] = imap_name
            message_rows.append(
                (
                    int(mailbox["user_id"]),
                    int(mailbox["id"]),
                    message["external_id"],
                    message["folder"],
                    message["folder_role"],
                    message["imap_folder"],
                    message["subject"],
                    message["sender"],
                    message["recipients"],
                    message["snippet"],
                    message["body_text"],
                    message["body_html"],
                    message["raw_headers"],
                    message["attachments_json"],
                    int(message.get("unread", 1)),
                    int(message.get("starred", 0)),
                    int(message.get("has_attachments", 0)),
                    int(message.get("remote_content_allowed", 0)),
                    message["received_at"],
                    now,
                    now,
                )
            )

        if message_rows:
            # 用原始 sqlite3 连接做 executemany + 单次 COMMIT
            from app.core.database import db as module_db
            conn = module_db.connect()
            try:
                conn.cursor().executemany(
                    """
                    INSERT OR IGNORE INTO messages(
                        user_id, mailbox_id, external_id, folder, folder_role, imap_folder,
                        subject, sender, recipients, snippet, body_text, body_html, raw_headers,
                        attachments_json, unread, starred, has_attachments, remote_content_allowed,
                        received_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    message_rows,
                )
                conn.commit()
                inserted = conn.cursor().rowcount
            except Exception:
                conn.rollback()
                raise

        per_folder["inserted"] = inserted
        stats["inserted"] += inserted
        stats["folders"].setdefault(role, []).append(per_folder)

        db.execute(
            """
            UPDATE mailbox_folder_state
            SET folder_id = ?, uidvalidity = COALESCE(?, uidvalidity), last_uid = ?, last_sync_at = ?, updated_at = ?
            WHERE user_id = ? AND mailbox_id = ? AND imap_name = ?
            """,
            (
                folder_id,
                uidvalidity,
                int(new_last_uid),
                now,
                now,
                int(mailbox["user_id"]),
                int(mailbox["id"]),
                imap_name,
            ),
        )

    return stats


def run_mailbox_sync(
    db: Database,
    settings: Settings | None,
    job: dict[str, Any],
    account: dict[str, Any],
    secret: str,
) -> dict[str, Any]:
    """多文件夹同步入口（Task 6）。

    流程：
    1. 从 job 解析 folder_roles
    2. 从 settings 构建 attachment_root
    3. 调用 folder_discovery（ensure_folders）发现并写入 mailbox_folders
    4. 按 role 过滤出要同步的文件夹（默认 DEFAULT_ROLES 五个）
    5. 对每个启用的文件夹执行增量同步：
       - 读取 mailbox_folder_state 的 last_uid / uidvalidity
       - 连接 IMAP SELECT 获取 UIDVALIDITY（变化则重置游标从头拉）
       - UID SEARCH UID {last_uid+1}:* 拉增量
       - 分批 UID FETCH 解析邮件
       - INSERT OR IGNORE 入库（含 folder_role / imap_folder 列）
       - 更新 mailbox_folder_state
    6. 返回 stats 字典
    """
    # 1. 解析 folder_roles
    folder_roles_raw = job.get("folder_roles_json", "[]") or "[]"
    try:
        folder_roles: list[str] = json.loads(folder_roles_raw)
    except (json.JSONDecodeError, TypeError):
        folder_roles = []
    if not folder_roles:
        folder_roles = list(DEFAULT_ROLES)

    # 2. 构建 attachment_root
    if settings is not None:
        attachment_root: Path | None = settings.data_dir / "attachments"
    else:
        attachment_root = get_settings().data_dir / "attachments"

    logger.info(
        "开始同步 mailbox_id=%s user_id=%s folders=%s",
        account.get("id"),
        account.get("user_id"),
        folder_roles,
    )

    # 3-5. 委托 sync_mailbox 完成全部文件夹发现与增量同步
    stats = sync_mailbox(
        db=db,
        mailbox_row=account,
        secret=secret,
        folder_roles=folder_roles,
        attachment_root=attachment_root,
    )

    logger.info(
        "同步完成 mailbox_id=%s 拉取=%s 入库=%s",
        account.get("id"),
        stats.get("fetched", 0),
        stats.get("inserted", 0),
    )

    mailbox_count = sum(
        1 for r in db.query_all(
            "SELECT id FROM mailbox_accounts WHERE user_id = ?",
            (account.get("user_id"),),
        )
    )
    track("mail_synced", mailbox_count=mailbox_count)

    return stats
