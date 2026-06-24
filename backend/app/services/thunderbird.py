"""Thunderbird 配置文件解析 + mbox 导入引擎。

功能：
1. 解析 Thunderbird profile 的 ``prefs.js``，提取 IMAP/SMTP 账户配置
2. 扫描 ``ImapMail/`` 目录，解析 mbox 文件并导入为 WuYou 消息

主要入口：
- ``parse_prefs_js(content)`` — 解析 prefs.js 文本
- ``extract_accounts_from_prefs(prefs)`` — 从 prefs 字典提取账户列表
- ``parse_mbox(file_path)`` — 分割 mbox 文件为 RFC822 消息列表
- ``import_thunderbird_profile(profile_dir, db, user_id)`` — 完整导入流程
"""

from __future__ import annotations

import email
import json
import re
from email.message import EmailMessage
from email.policy import default as default_policy
from pathlib import Path
from typing import Any

from app.core.security import utc_iso
from app.services.mail_client import _decode_addresses, _extract_body
from app.services.sync.folder_discovery import classify_folder

# ── prefs.js parser ────────────────────────────────────────────────

_PREFS_RE = re.compile(r'user_pref\("([^"]+)",\s*("[^"]*"|[^)]+)\);')


def parse_prefs_js(content: str) -> dict[str, str]:
    """用正则 ``user_pref("KEY", VALUE)`` 提取所有 prefs 键值对，返回 flat dict。

    VALUE 可能是引号字符串、数字、布尔值，统一转为字符串存储。
    """
    result: dict[str, str] = {}
    for m in _PREFS_RE.finditer(content):
        key = m.group(1)
        raw_value = m.group(2).strip()
        # 去掉字符串引号
        if raw_value.startswith('"') and raw_value.endswith('"'):
            raw_value = raw_value[1:-1]
        result[key] = raw_value
    return result


# ── account extraction ─────────────────────────────────────────────

_SERVER_RE = re.compile(r"^mail\.server\.server(\d+)\.(.+)$")
_SMTP_RE = re.compile(r"^mail\.smtpserver\.smtp(\d+)\.(.+)$")
_IDENTITY_RE = re.compile(r"^mail\.identity\.id(\d+)\.(.+)$")
_ACCOUNT_SERVER_RE = re.compile(r"^mail\.account\.account(\d+)\.server$")
_ACCOUNT_IDENTITIES_RE = re.compile(r"^mail\.account\.account(\d+)\.identities$")


def _socket_type_to_ssl(socket_type: str) -> bool:
    """Thunderbird socketType 到 SSL 标志的映射。

    Thunderbird socketType 取值：
    - 0 = plain (无加密)
    - 1 = alwaysSTARTTLS
    - 2 = STARTTLS
    - 3 = SSL (直接加密连接)

    仅 3 视为 SSL，其余均需要 STARTTLS 协商。
    """
    try:
        return int(socket_type) == 3
    except (ValueError, TypeError):
        return False


def _smtp_try_ssl_to_ssl(try_ssl: str) -> bool:
    """Thunderbird SMTP try_ssl 到 SSL 标志的映射。

    try_ssl 取值：0=无 SSL, 1=TLS if available, 2=TLS, 3=SSL。
    仅 >=3 视为 SSL，其余走 STARTTLS。
    """
    try:
        return int(try_ssl) >= 3
    except (ValueError, TypeError):
        return False


def extract_accounts_from_prefs(prefs: dict[str, str]) -> list[dict[str, Any]]:
    """从 prefs 中提取 mail.server.serverN.* 系列配置，按 N 分组，
    过滤 type=imap 的服务器，关联身份信息与 SMTP 配置。

    Returns:
        [{display_name, email, imap_host, imap_port, imap_ssl,
          smtp_host, smtp_port, smtp_ssl, username}]
    """
    # 1. 收集 serverN 配置 group
    server_groups: dict[int, dict[str, str]] = {}
    for k, v in prefs.items():
        m = _SERVER_RE.match(k)
        if m:
            n = int(m.group(1))
            attr = m.group(2)
            server_groups.setdefault(n, {})[attr] = v

    # 2. 收集 smtpN 配置 group
    smtp_groups: dict[int, dict[str, str]] = {}
    for k, v in prefs.items():
        m = _SMTP_RE.match(k)
        if m:
            n = int(m.group(1))
            attr = m.group(2)
            smtp_groups.setdefault(n, {})[attr] = v

    # 3. 收集 identity idN 配置 group
    identity_groups: dict[int, dict[str, str]] = {}
    for k, v in prefs.items():
        m = _IDENTITY_RE.match(k)
        if m:
            n = int(m.group(1))
            attr = m.group(2)
            if attr in ("useremail", "fullName", "smtpServer"):
                identity_groups.setdefault(n, {})[attr] = v

    # 4. accountN -> server 和 identities 映射
    account_to_server: dict[int, int] = {}
    account_to_identities: dict[int, list[int]] = {}
    for k, v in prefs.items():
        m = _ACCOUNT_SERVER_RE.match(k)
        if m:
            account_n = int(m.group(1))
            server_n = _parse_server_ref(v)
            if server_n is not None:
                account_to_server[account_n] = server_n
            continue
        m = _ACCOUNT_IDENTITIES_RE.match(k)
        if m:
            account_n = int(m.group(1))
            ids = [_parse_server_ref(x) for x in v.split(",")]
            account_to_identities[account_n] = [x for x in ids if x is not None]

    # 5. serverN -> accountN 反向映射 (只保留最大 account number 的映射)
    server_to_account: dict[int, int] = {}
    for acc_n, srv_n in account_to_server.items():
        if srv_n not in server_to_account or acc_n > server_to_account[srv_n]:
            server_to_account[srv_n] = acc_n

    # 6. 为每个 type=imap 的 server 构建账号信息
    accounts: list[dict[str, Any]] = []
    for server_n, cfg in sorted(server_groups.items()):
        if cfg.get("type", "").strip('"').lower() != "imap":
            continue

        # 找到对应的 account 和 identity
        acc_n = server_to_account.get(server_n)
        identity_n: int | None = None
        if acc_n is not None:
            id_list = account_to_identities.get(acc_n, [])
            identity_n = id_list[0] if id_list else None

        # IMAP 配置
        imap_host = cfg.get("hostname", "localhost")
        imap_port = int(cfg.get("port", "993"))
        imap_ssl = _socket_type_to_ssl(cfg.get("socketType", "3"))
        username = cfg.get("userName", "")
        display_name = cfg.get("name", username)
        email_addr = ""

        # SMTP 配置：从 identity 关联
        smtp_host = ""
        smtp_port = 465
        smtp_ssl = True
        if identity_n is not None:
            identity = identity_groups.get(identity_n, {})
            email_addr = identity.get("useremail", "")
            if not display_name or display_name == username:
                display_name = identity.get("fullName", display_name)
            smtp_ref = identity.get("smtpServer", "")
            smtp_n = _parse_server_ref(smtp_ref)
            if smtp_n is not None and smtp_n in smtp_groups:
                smtp_cfg = smtp_groups[smtp_n]
                smtp_host = smtp_cfg.get("hostname", "")
                smtp_port = int(smtp_cfg.get("port", "465"))
                smtp_ssl = _smtp_try_ssl_to_ssl(smtp_cfg.get("try_ssl", "3"))

        accounts.append({
            "display_name": display_name or email_addr or username,
            "email": email_addr or username,
            "imap_host": imap_host,
            "imap_port": imap_port,
            "imap_ssl": imap_ssl,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_ssl": smtp_ssl,
            "username": username,
        })

    return accounts


def _parse_server_ref(value: str) -> int | None:
    """从 ``"serverN"`` 或 ``"smtpN"`` 中提取数字 N。

    Args:
        value: 如 ``"server2"``、``"smtp1"``。

    Returns:
        数字 N，解析失败返回 None。
    """
    if not value:
        return None
    value = value.strip().strip('"')
    m = re.search(r"(\d+)$", value)
    return int(m.group(1)) if m else None


# ── mbox parser ────────────────────────────────────────────────────

_MBOX_SEP_RE = re.compile(rb"\nFrom .*@.* .*\n")


def parse_mbox(file_path: Path) -> list[bytes]:
    """用正则 ``\\nFrom .*@.* .*\\n`` 分割 mbox 文件。

    在数据前补一个换行确保第一封邮件也能被分隔符匹配到。
    去除可能为空的第一个片段和纯空白片段。

    Args:
        file_path: mbox 文件路径。

    Returns:
        每封 RFC822 邮件的 raw bytes 列表。
    """
    raw = file_path.read_bytes()
    # 在开头补一个换行，确保第一封邮件也能被分隔符匹配到
    raw = b"\n" + raw
    parts = _MBOX_SEP_RE.split(raw)
    # 去掉可能为空的第一个片段
    if parts and parts[0].strip() == b"":
        parts = parts[1:]
    return [p for p in parts if p.strip()]


# ── full import engine ─────────────────────────────────────────────


def import_thunderbird_profile(profile_dir: Path, db: Any, user_id: int) -> dict[str, Any]:
    """完整 Thunderbird 导入流程。

    1. 读 prefs.js -> 解析 -> 提取账号 -> 写入 mailbox_accounts 表
    2. 扫描 ImapMail/{server}/ 下所有 mbox 文件 -> 解析 -> 写入 messages 表
    3. 返回导入报告

    Args:
        profile_dir: Thunderbird profile 目录路径
        db: Database 实例 (app.core.database.Database)
        user_id: 用户 ID

    Returns:
        {accounts_parsed, accounts_created, folders_imported, messages_imported}
    """
    now = utc_iso()
    profile_dir = Path(profile_dir)
    prefs_path = profile_dir / "prefs.js"

    # ── Step 1: 解析 prefs.js ──
    if not prefs_path.exists():
        raise FileNotFoundError(f"Thunderbird prefs.js not found at {prefs_path}")

    prefs_content = prefs_path.read_text(encoding="utf-8", errors="replace")
    prefs = parse_prefs_js(prefs_content)
    accounts = extract_accounts_from_prefs(prefs)

    # ── Step 2: 写入 mailbox_accounts ──
    created_ids: dict[int, int] = {}  # index -> mailbox_id
    for idx, acc in enumerate(accounts):
        db.execute(
            """INSERT INTO mailbox_accounts
               (user_id, display_name, email_address, provider,
                imap_host, imap_port, imap_ssl,
                smtp_host, smtp_port, smtp_ssl,
                auth_type, username, encrypted_secret,
                sync_enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                acc["display_name"],
                acc["email"],
                "thunderbird",
                acc["imap_host"],
                acc["imap_port"],
                1 if acc["imap_ssl"] else 0,
                acc["smtp_host"],
                acc["smtp_port"],
                1 if acc["smtp_ssl"] else 0,
                "password",
                acc["username"],
                "",  # encrypted_secret 空串，需用户手动填密码
                0,  # sync_enabled=0
                now,
                now,
            ),
        )
        row = db.query_one("SELECT last_insert_rowid()")
        if row:
            created_ids[idx] = row[0]

    # ── Step 3: 扫描 ImapMail 目录导入邮件 ──
    imapmail_dir = profile_dir / "ImapMail"
    folders_imported = 0
    messages_imported = 0

    if imapmail_dir.exists():
        for idx, acc in enumerate(accounts):
            imap_host = acc["imap_host"]
            server_dir = imapmail_dir / imap_host
            if not server_dir.is_dir():
                # 尝试匹配包含 hostname 的目录
                candidates = [
                    d
                    for d in imapmail_dir.iterdir()
                    if d.is_dir() and imap_host in d.name
                ]
                if candidates:
                    server_dir = candidates[0]
                else:
                    continue

            mailbox_id = created_ids.get(idx)
            if mailbox_id is None:
                continue

            # 递归扫描 mbox 文件
            for mbox_path, folder_name in _list_mbox_files(server_dir):
                folders_imported += 1
                raw_messages = parse_mbox(mbox_path)
                if not raw_messages:
                    continue

                folder_role = classify_folder(folder_name, [])
                rows: list[tuple] = []

                for raw_msg in raw_messages:
                    try:
                        parsed = email.message_from_bytes(raw_msg, policy=default_policy)
                    except Exception:
                        continue

                    # 使用 Message-ID 作为 external_id，缺失则跳过
                    external_id = parsed.get("Message-ID")
                    if not external_id:
                        external_id = parsed.get("Message-Id") or ""
                    if not external_id:
                        # 用 subject + date + from 组合做 fallback id
                        subject = parsed.get("Subject") or ""
                        date = parsed.get("Date") or ""
                        sender = parsed.get("From") or ""
                        external_id = f"mbox://{folder_name}/{hash(subject + date + sender)}"
                    external_id = str(external_id).strip()

                    body_text, body_html, attachments = _extract_body(parsed)
                    snippet = (body_text or body_html).replace("\n", " ").strip()[:240]
                    subject = str(parsed.get("Subject") or "(无主题)")
                    sender = str(parsed.get("From") or "")
                    recipients = json.dumps(_decode_addresses(parsed.get("To")), ensure_ascii=False)
                    raw_headers = json.dumps(dict(parsed.items()), ensure_ascii=False)
                    attachments_json = json.dumps(attachments, ensure_ascii=False)
                    received_at = str(parsed.get("Date") or now)
                    has_attachments = 1 if attachments else 0

                    rows.append((
                        user_id,
                        mailbox_id,
                        external_id,
                        folder_name,          # folder
                        folder_role,           # folder_role
                        folder_name,           # imap_folder
                        subject,
                        sender,
                        recipients,
                        snippet,
                        body_text,
                        body_html,
                        raw_headers,
                        attachments_json,
                        0,                     # unread (已导入视为已读)
                        0,                     # starred
                        has_attachments,
                        0,                     # remote_content_allowed
                        received_at,
                        now,                   # created_at
                        now,                   # updated_at
                    ))

                if rows:
                    before = db.query_one("SELECT COUNT(*) as cnt FROM messages")["cnt"]
                    db.executemany(
                        """INSERT OR IGNORE INTO messages
                           (user_id, mailbox_id, external_id,
                            folder, folder_role, imap_folder,
                            subject, sender, recipients,
                            snippet, body_text, body_html,
                            raw_headers, attachments_json,
                            unread, starred, has_attachments,
                            remote_content_allowed, received_at,
                            created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        rows,
                    )
                    after = db.query_one("SELECT COUNT(*) as cnt FROM messages")["cnt"]
                    messages_imported += max(0, after - before)

    return {
        "accounts_parsed": len(accounts),
        "accounts_created": len(created_ids),
        "folders_imported": folders_imported,
        "messages_imported": messages_imported,
    }


def _list_mbox_files(server_dir: Path, prefix: str = "") -> list[tuple[Path, str]]:
    """递归列出 server_dir 下所有 mbox 文件，排除 .msf 索引文件和 .sbd 子目录结构。

    Returns:
        [(file_path, folder_name), ...]  folder_name 为去除 .sbd 层级后的文件夹路径。
    """
    result: list[tuple[Path, str]] = []
    if not server_dir.is_dir():
        return result

    for entry in sorted(server_dir.iterdir()):
        name = entry.name
        if name.endswith(".msf"):
            continue
        if entry.is_file():
            folder_name = f"{prefix}/{name}" if prefix else name
            result.append((entry, folder_name))
        elif entry.is_dir():
            # .sbd 目录表示其父级是一个含子文件夹的 IMAP 文件夹
            if name.endswith(".sbd"):
                parent_name = name[:-4]  # 去掉 .sbd
                new_prefix = f"{prefix}/{parent_name}" if prefix else parent_name
            else:
                new_prefix = f"{prefix}/{name}" if prefix else name
            result.extend(_list_mbox_files(entry, prefix=new_prefix))

    return result
