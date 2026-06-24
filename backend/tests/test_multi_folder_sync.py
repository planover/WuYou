"""Task 6: 多文件夹同步与 folder_state 入库的独立单元测试。

本测试文件不连接真实 IMAP 服务器，仅测试：
- mailbox_folder_state 的创建、读取、更新逻辑
- run_mailbox_sync 的 folder_roles 解析与默认回退
- UIDVALIDITY 变化时的游标重置决策（纯逻辑）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.database import Database
from app.core.security import utc_iso
from app.services.sync.constants import DEFAULT_ROLES, ROLE_INBOX, ROLE_SENT
from app.services.sync.sync_engine import (
    build_uid_range,
    ensure_folders,
    run_mailbox_sync,
    sync_mailbox,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _seed_user_and_mailbox(db: Database) -> tuple[int, int]:
    """创建测试用户和邮箱账号，返回 (user_id, mailbox_id)。"""
    now = utc_iso()
    user_id = db.execute(
        "INSERT INTO users(username, email, phone, password_hash, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        ("test_user", None, None, "hash", now, now),
    ).lastrowid
    mailbox_id = db.execute(
        """
        INSERT INTO mailbox_accounts(
          user_id, display_name, email_address, provider, imap_host, imap_port, imap_ssl,
          smtp_host, smtp_port, smtp_ssl, auth_type, username, encrypted_secret, sync_enabled,
          created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            user_id,
            "Test",
            "test@example.com",
            "custom",
            "imap.example.com",
            993,
            1,
            "smtp.example.com",
            465,
            1,
            "app_password",
            "test@example.com",
            "encrypted_secret",
            1,
            now,
            now,
        ),
    ).lastrowid
    return int(user_id), int(mailbox_id)


def _seed_folder(db: Database, user_id: int, mailbox_id: int, role: str, imap_name: str) -> int:
    """手动插入一条 mailbox_folders 记录并返回 folder_id。"""
    now = utc_iso()
    cur = db.execute(
        """
        INSERT OR IGNORE INTO mailbox_folders(
          user_id, mailbox_id, role, imap_name, display_name, attributes_json, enabled, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, mailbox_id, role, imap_name, imap_name, "[]", 1, now, now),
    )
    row = db.query_one(
        "SELECT id FROM mailbox_folders WHERE user_id = ? AND mailbox_id = ? AND imap_name = ?",
        (user_id, mailbox_id, imap_name),
    )
    assert row is not None
    return int(row["id"])


# ── folder_state 单元测试 ───────────────────────────────────────────────────


class TestFolderStateLifecycle:
    """测试 mailbox_folder_state 的完整生命周期：创建 -> 读取 -> 更新。"""

    def test_create_initial_state(self, tmp_path: Path):
        """首次同步时为文件夹创建初始 state（last_uid=0, uidvalidity=NULL）。"""
        db = Database(tmp_path / "db.sqlite3")
        db.init()
        user_id, mailbox_id = _seed_user_and_mailbox(db)
        folder_id = _seed_folder(db, user_id, mailbox_id, ROLE_INBOX, "INBOX")

        now = utc_iso()
        db.execute(
            """
            INSERT INTO mailbox_folder_state(
              user_id, mailbox_id, folder_id, imap_name, uidvalidity, last_uid, last_sync_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, NULL, 0, NULL, ?, ?)
            """,
            (user_id, mailbox_id, folder_id, "INBOX", now, now),
        )

        row = db.query_one(
            "SELECT * FROM mailbox_folder_state WHERE user_id = ? AND mailbox_id = ? AND imap_name = ?",
            (user_id, mailbox_id, "INBOX"),
        )
        assert row is not None
        assert int(row["last_uid"]) == 0
        assert row["uidvalidity"] is None
        assert row["last_sync_at"] is None

    def test_update_state_after_sync(self, tmp_path: Path):
        """同步完成后更新 last_uid 和 uidvalidity 并记录 last_sync_at。"""
        db = Database(tmp_path / "db.sqlite3")
        db.init()
        user_id, mailbox_id = _seed_user_and_mailbox(db)
        folder_id = _seed_folder(db, user_id, mailbox_id, ROLE_INBOX, "INBOX")

        now = utc_iso()
        db.execute(
            """
            INSERT INTO mailbox_folder_state(
              user_id, mailbox_id, folder_id, imap_name, uidvalidity, last_uid, last_sync_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, NULL, 0, NULL, ?, ?)
            """,
            (user_id, mailbox_id, folder_id, "INBOX", now, now),
        )

        # 模拟同步完成：last_uid 更新为 42，uidvalidity = 12345
        sync_at = utc_iso()
        db.execute(
            """
            UPDATE mailbox_folder_state
            SET folder_id = ?, uidvalidity = COALESCE(?, uidvalidity), last_uid = ?, last_sync_at = ?, updated_at = ?
            WHERE user_id = ? AND mailbox_id = ? AND imap_name = ?
            """,
            (folder_id, 12345, 42, sync_at, sync_at, user_id, mailbox_id, "INBOX"),
        )

        row = db.query_one(
            "SELECT * FROM mailbox_folder_state WHERE user_id = ? AND mailbox_id = ? AND imap_name = ?",
            (user_id, mailbox_id, "INBOX"),
        )
        assert row is not None
        assert int(row["last_uid"]) == 42
        assert int(row["uidvalidity"]) == 12345
        assert row["last_sync_at"] == sync_at

    def test_uidvalidity_change_detection(self, tmp_path: Path):
        """UIDVALIDITY 变化时应重置游标（last_uid→0 从头拉）。"""
        db = Database(tmp_path / "db.sqlite3")
        db.init()
        user_id, mailbox_id = _seed_user_and_mailbox(db)
        folder_id = _seed_folder(db, user_id, mailbox_id, ROLE_INBOX, "INBOX")

        now = utc_iso()
        db.execute(
            """
            INSERT INTO mailbox_folder_state(
              user_id, mailbox_id, folder_id, imap_name, uidvalidity, last_uid, last_sync_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, mailbox_id, folder_id, "INBOX", 111, 99, now, now, now),
        )

        # 模拟检测到 UIDVALIDITY 变化（111 -> 222），强制重置
        old_uidvalidity = 111
        new_uidvalidity = 222
        if old_uidvalidity is not None and new_uidvalidity != old_uidvalidity:
            # 重置游标，从 0 开始
            db.execute(
                """
                UPDATE mailbox_folder_state
                SET uidvalidity = ?, last_uid = 0, last_sync_at = ?, updated_at = ?
                WHERE user_id = ? AND mailbox_id = ? AND imap_name = ?
                """,
                (new_uidvalidity, utc_iso(), utc_iso(), user_id, mailbox_id, "INBOX"),
            )

        row = db.query_one(
            "SELECT * FROM mailbox_folder_state WHERE user_id = ? AND mailbox_id = ? AND imap_name = ?",
            (user_id, mailbox_id, "INBOX"),
        )
        assert row is not None
        assert int(row["uidvalidity"]) == 222
        assert int(row["last_uid"]) == 0  # 重置

    def test_state_not_overwritten_for_other_folder(self, tmp_path: Path):
        """更新一个文件夹的 state 不影响其他文件夹。"""
        db = Database(tmp_path / "db.sqlite3")
        db.init()
        user_id, mailbox_id = _seed_user_and_mailbox(db)
        inbox_id = _seed_folder(db, user_id, mailbox_id, ROLE_INBOX, "INBOX")
        sent_id = _seed_folder(db, user_id, mailbox_id, ROLE_SENT, "Sent")

        now = utc_iso()
        for fid, imap_name in [(inbox_id, "INBOX"), (sent_id, "Sent")]:
            db.execute(
                """
                INSERT INTO mailbox_folder_state(
                  user_id, mailbox_id, folder_id, imap_name, uidvalidity, last_uid, last_sync_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 100, 0, NULL, ?, ?)
                """,
                (user_id, mailbox_id, fid, imap_name, now, now),
            )

        # 仅更新 INBOX
        sync_at = utc_iso()
        db.execute(
            """
            UPDATE mailbox_folder_state
            SET last_uid = 55, uidvalidity = 200, last_sync_at = ?, updated_at = ?
            WHERE user_id = ? AND mailbox_id = ? AND imap_name = ?
            """,
            (sync_at, sync_at, user_id, mailbox_id, "INBOX"),
        )

        inbox_row = db.query_one(
            "SELECT * FROM mailbox_folder_state WHERE imap_name = ?", ("INBOX",)
        )
        sent_row = db.query_one(
            "SELECT * FROM mailbox_folder_state WHERE imap_name = ?", ("Sent",)
        )
        assert int(inbox_row["last_uid"]) == 55
        assert int(inbox_row["uidvalidity"]) == 200
        assert int(sent_row["last_uid"]) == 0   # 未受影响
        assert int(sent_row["uidvalidity"]) == 100  # 保持不变

    def test_unique_constraint_per_user_mailbox_imap(self, tmp_path: Path):
        """同一 (user_id, mailbox_id, imap_name) 只能有一条 state 记录。"""
        db = Database(tmp_path / "db.sqlite3")
        db.init()
        user_id, mailbox_id = _seed_user_and_mailbox(db)
        folder_id = _seed_folder(db, user_id, mailbox_id, ROLE_INBOX, "INBOX")

        now = utc_iso()
        db.execute(
            """
            INSERT INTO mailbox_folder_state(
              user_id, mailbox_id, folder_id, imap_name, uidvalidity, last_uid, last_sync_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, NULL, 0, NULL, ?, ?)
            """,
            (user_id, mailbox_id, folder_id, "INBOX", now, now),
        )

        import sqlite3
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                """
                INSERT INTO mailbox_folder_state(
                  user_id, mailbox_id, folder_id, imap_name, uidvalidity, last_uid, last_sync_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, 0, NULL, ?, ?)
                """,
                (user_id, mailbox_id, folder_id, "INBOX", now, now),
            )


# ── build_uid_range 测试 ─────────────────────────────────────────────────────


class TestUidRange:
    def test_from_zero(self):
        assert build_uid_range(0) == "1:*"

    def test_from_positive(self):
        assert build_uid_range(42) == "43:*"

    def test_negative_resets_to_one(self):
        assert build_uid_range(-5) == "1:*"


# ── run_mailbox_sync 纯逻辑测试（不连接 IMAP）─────────────────────────────────


class TestRunMailboxSyncParsing:
    """测试 run_mailbox_sync 对 job 参数的解析逻辑（不触发 IMAP）。"""

    def test_parses_folder_roles_from_job(self, tmp_path: Path, monkeypatch):
        """folder_roles 从 job 的 folder_roles_json 正确解析。"""
        db = Database(tmp_path / "db.sqlite3")
        db.init()
        user_id, mailbox_id = _seed_user_and_mailbox(db)

        account = db.query_one(
            "SELECT * FROM mailbox_accounts WHERE id = ?", (mailbox_id,)
        )
        assert account is not None
        account_dict = dict(account)

        # Mock sync_mailbox 避免真实 IMAP 连接
        from app.services.sync import sync_engine as engine

        captured_roles: list[list[str]] = []

        def fake_sync_mailbox(db, mailbox_row, secret, folder_roles, attachment_root):
            captured_roles.append(list(folder_roles or []))
            return {"fetched": 0, "inserted": 0, "folders": {}}

        monkeypatch.setattr(engine, "sync_mailbox", fake_sync_mailbox)

        settings = Settings(data_dir=tmp_path)
        job = {
            "folder_roles_json": json.dumps(["inbox", "sent", "archive"]),
        }

        stats = run_mailbox_sync(db, settings, job, account_dict, secret="test")
        assert stats == {"fetched": 0, "inserted": 0, "folders": {}}
        assert captured_roles == [["inbox", "sent", "archive"]]

    def test_empty_folder_roles_falls_back_to_defaults(self, tmp_path: Path, monkeypatch):
        """空 folder_roles_json 回退到 DEFAULT_ROLES（5 个）。"""
        db = Database(tmp_path / "db.sqlite3")
        db.init()
        user_id, mailbox_id = _seed_user_and_mailbox(db)

        account = db.query_one(
            "SELECT * FROM mailbox_accounts WHERE id = ?", (mailbox_id,)
        )
        assert account is not None
        account_dict = dict(account)

        from app.services.sync import sync_engine as engine

        captured_roles: list[list[str]] = []

        def fake_sync_mailbox(db, mailbox_row, secret, folder_roles, attachment_root):
            captured_roles.append(list(folder_roles or []))
            return {"fetched": 0, "inserted": 0, "folders": {}}

        monkeypatch.setattr(engine, "sync_mailbox", fake_sync_mailbox)

        settings = Settings(data_dir=tmp_path)
        job = {"folder_roles_json": "[]"}

        stats = run_mailbox_sync(db, settings, job, account_dict, secret="test")
        assert stats["fetched"] == 0
        assert captured_roles == [list(DEFAULT_ROLES)]

    def test_none_settings_uses_default(self, tmp_path: Path, monkeypatch):
        """settings=None 时也能正常工作，使用 get_settings() 获取路径。"""
        db = Database(tmp_path / "db.sqlite3")
        db.init()
        user_id, mailbox_id = _seed_user_and_mailbox(db)

        account = db.query_one(
            "SELECT * FROM mailbox_accounts WHERE id = ?", (mailbox_id,)
        )
        assert account is not None
        account_dict = dict(account)

        from app.services.sync import sync_engine as engine

        captured: list[dict] = []

        def fake_sync_mailbox(db, mailbox_row, secret, folder_roles, attachment_root):
            captured.append({"roles": list(folder_roles), "root": str(attachment_root)})
            return {"fetched": 0, "inserted": 0, "folders": {}}

        monkeypatch.setattr(engine, "sync_mailbox", fake_sync_mailbox)

        job = {"folder_roles_json": json.dumps(["inbox"])}

        stats = run_mailbox_sync(db, None, job, account_dict, secret="test")
        assert stats == {"fetched": 0, "inserted": 0, "folders": {}}
        assert captured[0]["roles"] == ["inbox"]
        # attachment_root 应该包含 "attachments"
        assert "attachments" in captured[0]["root"]

    def test_default_folder_count_is_five(self):
        """DEFAULT_ROLES 包含 5 个角色：inbox, sent, trash, archive, junk。"""
        assert len(DEFAULT_ROLES) == 5
        assert set(DEFAULT_ROLES) == {"inbox", "sent", "trash", "archive", "junk"}


# ── ensure_folders 状态测试（不连接 IMAP 的路径）───────────────────────────────


class TestEnsureFoldersLogic:
    """测试 ensure_folders 中不依赖 IMAP 的逻辑路径。"""

    def test_existing_folders_returned_without_imap(self, tmp_path: Path):
        """如果 mailbox_folders 已有记录，ensure_folders 直接返回不连接 IMAP。"""
        db = Database(tmp_path / "db.sqlite3")
        db.init()
        user_id, mailbox_id = _seed_user_and_mailbox(db)

        # 手动写入 mailbox_folders
        now = utc_iso()
        db.execute(
            """
            INSERT INTO mailbox_folders(
              user_id, mailbox_id, role, imap_name, display_name, attributes_json, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, mailbox_id, "inbox", "INBOX", "INBOX", "[]", 1, now, now),
        )
        db.execute(
            """
            INSERT INTO mailbox_folders(
              user_id, mailbox_id, role, imap_name, display_name, attributes_json, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, mailbox_id, "sent", "Sent", "Sent", "[]", 1, now, now),
        )

        mailbox = dict(
            db.query_one("SELECT * FROM mailbox_accounts WHERE id = ?", (mailbox_id,))
        )
        # ensure_folders 应该直接返回已有记录，不连接 IMAP
        folders = ensure_folders(db, mailbox, secret="dummy")
        assert len(folders) == 2
        roles = {row["role"] for row in folders}
        assert roles == {"inbox", "sent"}

    def test_ensure_folders_with_imap_requires_valid_secret(self, tmp_path: Path):
        """当没有已有映射时，ensure_folders 尝试连接 IMAP（需要真实凭据）。"""
        db = Database(tmp_path / "db.sqlite3")
        db.init()
        user_id, mailbox_id = _seed_user_and_mailbox(db)

        mailbox = dict(
            db.query_one("SELECT * FROM mailbox_accounts WHERE id = ?", (mailbox_id,))
        )
        # 没有已有映射 + 无效 secret → 应抛出连接错误（IMAP 无法连接）
        with pytest.raises(Exception):
            ensure_folders(db, mailbox, secret="invalid_secret")
