"""WuYou SQLite 数据层。

提供 ``Database`` 类封装一个 SQLite 连接，支持：
- 线程安全：所有写/读操作通过 ``threading.RLock`` 保护
- WAL 模式：读写并发性能更高
- 自动建表/迁移：``init()`` 执行 SCHEMA + 增量列迁移 + 创建索引
- 统一的 query/execute 接口：``query_one``、``query_all``、``execute``、``executemany``

模块级 ``db`` 实例由 ``get_settings().database_path`` 创建，所有 API 路由和
service 通过 ``from app.core.database import db`` 共享同一连接。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .config import get_settings


class Database:
    """轻量级 SQLite 数据访问层。

    所有公开方法都通过 ``threading.RLock`` 保证线程安全。
    连接使用 WAL 模式 + NORMAL synchronous，兼顾并发性能与数据安全性。

    Attributes:
        path: SQLite 数据库文件路径。
    """

    def __init__(self, path: Path):
        """初始化 Database 实例（不会立即连接，延迟到首次 connect()）。

        Args:
            path: SQLite 数据库文件路径。
        """
        self.path = path
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        """返回底层 sqlite3 连接（延迟初始化，线程不安全调用者需自行加锁）。

        首次调用时创建父目录、打开连接、配置 PRAGMA（foreign_keys、WAL、
        NORMAL synchronous、5000ms busy_timeout）。

        Returns:
            配置好 row_factory=sqlite3.Row 的连接对象。
        """
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.path, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = NORMAL")
            self._connection.execute("PRAGMA busy_timeout = 5000")
        return self._connection

    def init(self) -> None:
        """初始化数据库：建表、增量列迁移、创建性能索引。

        幂等操作——多次调用不会破坏已有数据。新数据库会全量建表，旧数据库
        仅执行缺失列的 ALTER TABLE 迁移和缺失索引的 CREATE INDEX IF NOT EXISTS。
        """
        with self._lock:
            connection = self.connect()
            connection.executescript(SCHEMA)
            # Lightweight migrations for older databases:
            # `CREATE TABLE IF NOT EXISTS` won't update existing table schemas.
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(messages)").fetchall()
            }
            if "folder_role" not in columns:
                connection.execute(
                    "ALTER TABLE messages ADD COLUMN folder_role TEXT NOT NULL DEFAULT 'inbox'"
                )
            if "imap_folder" not in columns:
                connection.execute(
                    "ALTER TABLE messages ADD COLUMN imap_folder TEXT NOT NULL DEFAULT 'INBOX'"
                )
            if "thread_id" not in columns:
                connection.execute(
                    "ALTER TABLE messages ADD COLUMN thread_id TEXT NOT NULL DEFAULT ''"
                )
            if "in_reply_to" not in columns:
                connection.execute(
                    "ALTER TABLE messages ADD COLUMN in_reply_to TEXT NOT NULL DEFAULT ''"
                )

            folder_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(mailbox_folders)").fetchall()
            }
            if "enabled" not in folder_columns:
                connection.execute(
                    "ALTER TABLE mailbox_folders ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
                )

            job_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(sync_jobs)").fetchall()
            }
            if "trigger" not in job_columns:
                connection.execute(
                    "ALTER TABLE sync_jobs ADD COLUMN trigger TEXT NOT NULL DEFAULT 'manual'"
                )
            if "folder_roles_json" not in job_columns:
                connection.execute(
                    "ALTER TABLE sync_jobs ADD COLUMN folder_roles_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "stats_json" not in job_columns:
                connection.execute(
                    "ALTER TABLE sync_jobs ADD COLUMN stats_json TEXT NOT NULL DEFAULT '{}'"
                )

            plugin_cols = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(installed_plugins)").fetchall()
            }
            if "enabled" not in plugin_cols:
                connection.execute(
                    "ALTER TABLE installed_plugins ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
                )

            acct_cols = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(mailbox_accounts)").fetchall()
            }
            for col, defn in [
                ("signature_html", "TEXT NOT NULL DEFAULT ''"),
                ("signature_text", "TEXT NOT NULL DEFAULT ''"),
                ("auto_reply_enabled", "INTEGER NOT NULL DEFAULT 0"),
                ("auto_reply_subject", "TEXT NOT NULL DEFAULT ''"),
                ("auto_reply_body", "TEXT NOT NULL DEFAULT ''"),
                ("auto_reply_start", "TEXT"),
                ("auto_reply_end", "TEXT"),
                ("auto_reply_days", "INTEGER NOT NULL DEFAULT 0"),
            ]:
                if col not in acct_cols:
                    connection.execute(f"ALTER TABLE mailbox_accounts ADD COLUMN {col} {defn}")

            # ── 性能索引：确保核心查询走索引而非全表扫描 ──
            _ensure_indexes(connection)
            connection.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        """执行写 SQL（INSERT/UPDATE/DELETE），自动提交。

        Args:
            sql: SQL 语句，使用 ``?`` 占位符。
            params: 参数元组。

        Returns:
            sqlite3.Cursor（可读取 rowcount、lastrowid）。
        """
        with self._lock:
            cursor = self.connect().execute(sql, tuple(params))
            self.connect().commit()
            return cursor

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        """批量执行写 SQL（多行 INSERT），自动提交。

        Args:
            sql: SQL 语句，使用 ``?`` 占位符。
            rows: 参数列表，每个元素是一个参数元组。
        """
        with self._lock:
            self.connect().executemany(sql, rows)
            self.connect().commit()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        """执行读 SQL 并返回单行结果。

        Args:
            sql: SQL 语句，使用 ``?`` 占位符。
            params: 参数元组。

        Returns:
            sqlite3.Row 或 None（无匹配时）。
        """
        with self._lock:
            return self.connect().execute(sql, tuple(params)).fetchone()

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        """执行读 SQL 并返回全部匹配行。

        Args:
            sql: SQL 语句，使用 ``?`` 占位符。
            params: 参数元组。

        Returns:
            sqlite3.Row 列表（无匹配时返回空列表）。
        """
        with self._lock:
            return self.connect().execute(sql, tuple(params)).fetchall()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    email TEXT UNIQUE,
    phone TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,
    target TEXT NOT NULL,
    purpose TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mailbox_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    email_address TEXT NOT NULL,
    provider TEXT NOT NULL,
    imap_host TEXT NOT NULL,
    imap_port INTEGER NOT NULL,
    imap_ssl INTEGER NOT NULL DEFAULT 1,
    smtp_host TEXT NOT NULL,
    smtp_port INTEGER NOT NULL,
    smtp_ssl INTEGER NOT NULL DEFAULT 1,
    auth_type TEXT NOT NULL DEFAULT 'app_password',
    username TEXT NOT NULL,
    encrypted_secret TEXT NOT NULL,
    sync_enabled INTEGER NOT NULL DEFAULT 1,
    signature_html TEXT NOT NULL DEFAULT '',
    signature_text TEXT NOT NULL DEFAULT '',
    auto_reply_enabled INTEGER NOT NULL DEFAULT 0,
    auto_reply_subject TEXT NOT NULL DEFAULT '',
    auto_reply_body TEXT NOT NULL DEFAULT '',
    auto_reply_start TEXT,
    auto_reply_end TEXT,
    auto_reply_days INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mailbox_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mailbox_id INTEGER NOT NULL REFERENCES mailbox_accounts(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'inbox',
    imap_name TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    attributes_json TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, mailbox_id, imap_name)
);

CREATE TABLE IF NOT EXISTS mailbox_folder_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mailbox_id INTEGER NOT NULL REFERENCES mailbox_accounts(id) ON DELETE CASCADE,
    folder_id INTEGER REFERENCES mailbox_folders(id) ON DELETE CASCADE,
    imap_name TEXT NOT NULL,
    uidvalidity INTEGER,
    last_uid INTEGER,
    last_sync_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, mailbox_id, imap_name)
);

CREATE TABLE IF NOT EXISTS sync_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mailbox_id INTEGER NOT NULL REFERENCES mailbox_accounts(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL DEFAULT 'mailbox_sync',
    trigger TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'queued',
    folder_role TEXT,
    imap_folder TEXT,
    folder_roles_json TEXT NOT NULL DEFAULT '[]',
    stats_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mailbox_id INTEGER REFERENCES mailbox_accounts(id) ON DELETE SET NULL,
    external_id TEXT NOT NULL,
    folder TEXT NOT NULL DEFAULT 'INBOX',
    folder_role TEXT NOT NULL DEFAULT 'inbox',
    imap_folder TEXT NOT NULL DEFAULT 'INBOX',
    subject TEXT NOT NULL,
    sender TEXT NOT NULL,
    recipients TEXT NOT NULL DEFAULT '[]',
    snippet TEXT NOT NULL DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    body_html TEXT NOT NULL DEFAULT '',
    raw_headers TEXT NOT NULL DEFAULT '{}',
    attachments_json TEXT NOT NULL DEFAULT '[]',
    unread INTEGER NOT NULL DEFAULT 1,
    starred INTEGER NOT NULL DEFAULT 0,
    has_attachments INTEGER NOT NULL DEFAULT 0,
    remote_content_allowed INTEGER NOT NULL DEFAULT 0,
    thread_id TEXT NOT NULL DEFAULT '',
    in_reply_to TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, mailbox_id, external_id)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    color TEXT NOT NULL DEFAULT '#2f7cf6',
    priority INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS message_tags (
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY(message_id, tag_id)
);

CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, key)
);

CREATE TABLE IF NOT EXISTS content_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mailbox_id INTEGER REFERENCES mailbox_accounts(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plugin_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'remote',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS installed_plugins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plugin_id TEXT NOT NULL,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    category TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    installed_at TEXT NOT NULL,
    UNIQUE(user_id, plugin_id)
);

CREATE TABLE IF NOT EXISTS shared_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    submitted_at TEXT NOT NULL,
    UNIQUE(user_id, type, item_id)
);

CREATE TABLE IF NOT EXISTS sync_peers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label TEXT NOT NULL DEFAULT '远程设备',
    url TEXT NOT NULL,
    remote_username TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_sync_at TEXT,
    last_status TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    snapshot_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    redirect_to TEXT NOT NULL DEFAULT '/',
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dav_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mailbox_account_id INTEGER REFERENCES mailbox_accounts(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    protocol TEXT NOT NULL,
    url TEXT NOT NULL,
    username TEXT NOT NULL,
    encrypted_password TEXT NOT NULL DEFAULT '',
    sync_enabled INTEGER NOT NULL DEFAULT 1,
    last_sync_at TEXT,
    last_sync_status TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pgp_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email_address TEXT NOT NULL,
    public_key_pem TEXT NOT NULL,
    private_key_pem TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    original_name TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    file_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS telemetry_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    properties_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    mailbox_id INTEGER NOT NULL,
    recipients_json TEXT NOT NULL DEFAULT '[]',
    cc_json TEXT NOT NULL DEFAULT '[]',
    bcc_json TEXT NOT NULL DEFAULT '[]',
    subject TEXT NOT NULL,
    body_text TEXT NOT NULL DEFAULT '',
    body_html TEXT NOT NULL DEFAULT '',
    format TEXT NOT NULL DEFAULT 'text',
    attachment_ids_json TEXT NOT NULL DEFAULT '[]',
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS auto_reply_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    mailbox_id INTEGER NOT NULL,
    reply_to TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_auto_reply_log_user_mailbox_reply
    ON auto_reply_log(user_id, mailbox_id, reply_to);

CREATE TABLE IF NOT EXISTS mail_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    condition_field TEXT NOT NULL,
    condition_op TEXT NOT NULL DEFAULT 'contains',
    condition_value TEXT NOT NULL,
    action_type TEXT NOT NULL,
    action_value TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT '',
    body_text TEXT NOT NULL DEFAULT '',
    body_html TEXT NOT NULL DEFAULT '',
    format TEXT NOT NULL DEFAULT 'text',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contact_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS contact_group_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES contact_groups(id) ON DELETE CASCADE,
    contact_id INTEGER NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    UNIQUE(group_id, contact_id)
);
"""


def _ensure_indexes(conn: sqlite3.Connection) -> None:
    """CREATE INDEX IF NOT EXISTS on the most frequent query columns.

    这些索引避免在 inbox 列表、sync_job 队列和 folder_state 查询时全表扫描。
    """
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_messages_user_folder_role
            ON messages(user_id, folder_role);

        CREATE INDEX IF NOT EXISTS idx_messages_user_unread
            ON messages(user_id, unread);

        CREATE INDEX IF NOT EXISTS idx_messages_user_received
            ON messages(user_id, received_at DESC);

        CREATE INDEX IF NOT EXISTS idx_messages_user_mailbox_external
            ON messages(user_id, mailbox_id, external_id);

        CREATE INDEX IF NOT EXISTS idx_messages_user_thread
            ON messages(user_id, thread_id);

        CREATE INDEX IF NOT EXISTS idx_mailbox_folders_user_mailbox
            ON mailbox_folders(user_id, mailbox_id);

        CREATE INDEX IF NOT EXISTS idx_sync_jobs_status
            ON sync_jobs(status);

        CREATE INDEX IF NOT EXISTS idx_mailbox_folder_state_lookup
            ON mailbox_folder_state(user_id, mailbox_id, imap_name);

        CREATE INDEX IF NOT EXISTS idx_content_items_user_kind
            ON content_items(user_id, kind);

        CREATE INDEX IF NOT EXISTS idx_dav_accounts_user
            ON dav_accounts(user_id);

        CREATE INDEX IF NOT EXISTS idx_pgp_keys_user_email
            ON pgp_keys(user_id, email_address);
    """)


db = Database(get_settings().database_path)
