"""Tests for snapshot engine -- build_full_snapshot and merge_snapshot."""

from pathlib import Path

from app.core.database import Database
from app.core.security import utc_iso
from app.services.sync.snapshot import build_full_snapshot, merge_snapshot


# ── helpers ────────────────────────────────────

def _insert_user(db: Database) -> int:
    return db.execute(
        "INSERT INTO users(username, email, phone, password_hash, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("test", None, None, "hash", utc_iso(), utc_iso()),
    ).lastrowid


# ── build_full_snapshot ────────────────────────

def test_build_full_snapshot_returns_six_categories(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.init()
    user_id = _insert_user(db)

    snapshot = build_full_snapshot(db, user_id)

    data_categories = {
        "settings", "tags", "mailbox_accounts",
        "folder_mappings", "installed_plugins", "content_items",
    }
    assert data_categories.issubset(snapshot.keys())
    assert isinstance(snapshot["snapshot_id"], str)
    assert len(snapshot["snapshot_id"]) > 0


def test_snapshot_excludes_encrypted_secret(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.init()
    user_id = _insert_user(db)

    db.execute(
        """INSERT INTO mailbox_accounts(user_id, display_name, email_address, provider,
           imap_host, imap_port, imap_ssl, smtp_host, smtp_port, smtp_ssl,
           auth_type, username, encrypted_secret, sync_enabled, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id, "test", "t@example.com", "custom",
            "imap.example.com", 993, 1, "smtp.example.com", 465, 1,
            "app_password", "t@example.com", "secret123", 1,
            utc_iso(), utc_iso(),
        ),
    )

    snapshot = build_full_snapshot(db, user_id)
    accounts = snapshot["mailbox_accounts"]
    assert len(accounts) == 1
    assert "encrypted_secret" not in accounts[0]
    # 确保常用字段仍然存在
    assert accounts[0]["email_address"] == "t@example.com"


# ── merge_snapshot: tags ───────────────────────

def test_merge_tags_inserts_new(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.init()
    user_id = _insert_user(db)

    now = utc_iso()
    incoming = {
        "snapshot_id": "s1",
        "tags": [
            {
                "name": "urgent",
                "color": "#ff0000",
                "priority": 9,
                "created_at": now,
            },
        ],
    }

    summary, conflicts = merge_snapshot(db, user_id, incoming)

    assert conflicts == []
    assert summary["tags"]["inserted"] == 1

    row = db.query_one("SELECT * FROM tags WHERE user_id = ? AND name = ?", (user_id, "urgent"))
    assert row is not None
    assert row["color"] == "#ff0000"


def test_merge_tags_remote_newer_overwrites(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.init()
    user_id = _insert_user(db)

    older = "2025-01-01T00:00:00+00:00"
    newer = "2025-06-01T00:00:00+00:00"

    # 本地已有一个 old tag
    db.execute(
        "INSERT INTO tags(user_id, name, color, priority, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, "urgent", "#000000", 0, older),
    )

    incoming = {
        "snapshot_id": "s1",
        "tags": [
            {
                "name": "urgent",
                "color": "#ff0000",
                "priority": 9,
                "created_at": newer,
            },
        ],
    }

    summary, conflicts = merge_snapshot(db, user_id, incoming)

    assert conflicts == []
    assert summary["tags"]["updated"] == 1

    row = db.query_one("SELECT * FROM tags WHERE user_id = ? AND name = ?", (user_id, "urgent"))
    assert row["color"] == "#ff0000"
    assert row["priority"] == 9


# ── merge_snapshot: settings ───────────────────

def test_merge_settings_overwrites(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.init()
    user_id = _insert_user(db)

    older = "2025-01-01T00:00:00+00:00"
    newer = "2025-06-01T00:00:00+00:00"

    # 本地已有一个旧 setting
    db.execute(
        "INSERT INTO settings(user_id, key, value_json, updated_at) VALUES (?, ?, ?, ?)",
        (user_id, "theme", '"dark"', older),
    )

    incoming = {
        "snapshot_id": "s1",
        "settings": [
            {
                "key": "theme",
                "value_json": '"light"',
                "updated_at": newer,
            },
        ],
    }

    summary, conflicts = merge_snapshot(db, user_id, incoming)

    assert conflicts == []
    assert summary["settings"]["updated"] == 1

    row = db.query_one("SELECT * FROM settings WHERE user_id = ? AND key = ?", (user_id, "theme"))
    assert row["value_json"] == '"light"'
    assert row["updated_at"] == newer
