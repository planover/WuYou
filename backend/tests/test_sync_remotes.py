"""Tests for /api/sync/remotes/* routes using FastAPI TestClient."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


AUTH_TOKEN = "test-token-remotes-456"
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}


def _setup_test_db(db_path: Path) -> tuple:
    """Create a temporary database, initialise schema, insert test fixtures.

    Returns (user_id, test_db).
    """
    from app.core.database import Database
    from app.core.security import utc_iso

    test_db = Database(db_path)
    test_db.init()

    now = utc_iso()
    token_hash = hashlib.sha256(AUTH_TOKEN.encode("utf-8")).hexdigest()

    # user
    user_id = test_db.execute(
        "INSERT INTO users(username, email, phone, password_hash, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        ("testuser", "test@example.com", None, "hashed", now, now),
    ).lastrowid

    # session
    test_db.execute(
        "INSERT INTO sessions(user_id, token_hash, expires_at, created_at) VALUES (?,?,?,?)",
        (user_id, token_hash, "2099-12-31T23:59:59+00:00", now),
    )

    # ── settings (2 items) ──
    test_db.execute(
        "INSERT INTO settings(user_id, key, value_json, updated_at) VALUES (?,?,?,?)",
        (user_id, "theme", '"dark"', now),
    )
    test_db.execute(
        "INSERT INTO settings(user_id, key, value_json, updated_at) VALUES (?,?,?,?)",
        (user_id, "lang", '"zh-CN"', now),
    )

    # ── tags (2 items) ──
    test_db.execute(
        "INSERT INTO tags(user_id, name, color, priority, created_at) VALUES (?,?,?,?,?)",
        (user_id, "重要", "#ff0000", 1, now),
    )
    test_db.execute(
        "INSERT INTO tags(user_id, name, color, priority, created_at) VALUES (?,?,?,?,?)",
        (user_id, "工作", "#0000ff", 2, now),
    )

    # ── mailbox_accounts (1 item) ──
    mailbox_id = test_db.execute(
        """INSERT INTO mailbox_accounts(
              user_id, display_name, email_address, provider,
              imap_host, imap_port, imap_ssl,
              smtp_host, smtp_port, smtp_ssl,
              auth_type, username, encrypted_secret, sync_enabled,
              created_at, updated_at
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            user_id, "TestMail", "test@example.com", "custom",
            "imap.example.com", 993, 1,
            "smtp.example.com", 465, 1,
            "app_password", "test@example.com", "encrypted", 1,
            now, now,
        ),
    ).lastrowid

    # ── mailbox_folders (2 items) ──
    test_db.execute(
        """INSERT INTO mailbox_folders(
              user_id, mailbox_id, role, imap_name, display_name,
              attributes_json, enabled, created_at, updated_at
           ) VALUES (?,?,?,?,?,?,?,?,?)""",
        (user_id, mailbox_id, "inbox", "INBOX", "INBOX", "[]", 1, now, now),
    )
    test_db.execute(
        """INSERT INTO mailbox_folders(
              user_id, mailbox_id, role, imap_name, display_name,
              attributes_json, enabled, created_at, updated_at
           ) VALUES (?,?,?,?,?,?,?,?,?)""",
        (user_id, mailbox_id, "sent", "Sent", "Sent", "[]", 1, now, now),
    )

    # ── installed_plugins (1 item) ──
    test_db.execute(
        """INSERT INTO installed_plugins(
              user_id, plugin_id, name, version, type, category,
              manifest_json, enabled, installed_at
           ) VALUES (?,?,?,?,?,?,?,?,?)""",
        (user_id, "plugin-1", "TestPlugin", "1.0", "tool", "misc", "{}", 1, now),
    )

    # ── content_items (1 item) ──
    test_db.execute(
        """INSERT INTO content_items(
              user_id, mailbox_id, kind, title, body, meta_json,
              created_at, updated_at
           ) VALUES (?,?,?,?,?,?,?,?)""",
        (user_id, mailbox_id, "note", "Test Note", "some body", "{}", now, now),
    )

    return int(user_id), test_db


@pytest.fixture
def client(tmp_path: Path):
    """Pytest fixture: set up a temp database, patch the global db, and
    return a FastAPI TestClient."""
    import sys

    db_path = tmp_path / "test_remotes.db"
    _user_id, test_db = _setup_test_db(db_path)

    # Monkey-patch the module-level db BEFORE importing the app.
    import app.core.database as _db_mod
    original_db = _db_mod.db
    _db_mod.db = test_db

    # Clear cached app modules so they reload with the patched db
    _cached = {}
    for _key in list(sys.modules.keys()):
        if _key.startswith("app.main") or _key.startswith("app.api"):
            _cached[_key] = sys.modules.pop(_key)

    # Import app AFTER the patch is in place
    from app.main import app as _fastapi_app
    tc = TestClient(_fastapi_app)

    yield tc

    # Restore cached modules and original db
    _db_mod.db = original_db
    sys.modules.update(_cached)


# ── POST /api/sync/remotes/pull ────────────────────────────────────────

def test_pull_returns_six_categories(client: TestClient):
    """POST /api/sync/remotes/pull should return all 6 data categories."""
    resp = client.post("/api/sync/remotes/pull", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "snapshot_id" in data
    assert isinstance(data["snapshot_id"], str)
    assert len(data["snapshot_id"]) > 0
    assert "created_at" in data
    assert "data" in data

    categories = data["data"]
    # Exactly the 6 expected keys
    expected_keys = {
        "settings",
        "tags",
        "mailbox_accounts",
        "mailbox_folders",
        "installed_plugins",
        "content_items",
    }
    assert set(categories.keys()) == expected_keys

    # Verify correct counts
    assert len(categories["settings"]) == 2
    assert len(categories["tags"]) == 2
    assert len(categories["mailbox_accounts"]) == 1
    assert len(categories["mailbox_folders"]) == 2
    assert len(categories["installed_plugins"]) == 1
    assert len(categories["content_items"]) == 1

    # Spot-check tag structure
    tag_names = {t["name"] for t in categories["tags"]}
    assert tag_names == {"重要", "工作"}


def test_pull_with_last_known_snapshot_id(client: TestClient):
    """POST /api/sync/remotes/pull with last_known_snapshot_id should still
    return a full snapshot."""
    resp = client.post(
        "/api/sync/remotes/pull",
        json={"last_known_snapshot_id": "some-old-snap"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "snapshot_id" in data
    assert "data" in data


def test_pull_unauthorized(client: TestClient):
    """POST /api/sync/remotes/pull without auth should 401."""
    resp = client.post("/api/sync/remotes/pull")
    assert resp.status_code == 401


# ── POST /api/sync/remotes/push ────────────────────────────────────────

def test_push_merges_tags(client: TestClient):
    """POST /api/sync/remotes/push should merge incoming tags into the
    local database."""
    from app.core.database import db

    # Get the test user_id from the patched db
    user = db.query_one("SELECT id FROM users WHERE username = 'testuser'")
    user_id = int(user["id"])

    payload = {
        "snapshot_id": "remote-snap-001",
        "data": {
            "settings": [],
            "tags": [
                {
                    "id": 99,
                    "name": "新标签",
                    "color": "#00ff00",
                    "priority": 3,
                    "created_at": "2025-01-01T00:00:00+00:00",
                }
            ],
            "mailbox_accounts": [],
            "mailbox_folders": [],
            "installed_plugins": [],
            "content_items": [],
        },
    }

    resp = client.post("/api/sync/remotes/push", json=payload, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["remote_snapshot_id"] == "remote-snap-001"
    assert "merged" in data
    assert "conflicts" in data
    assert data["merged"]["tags"] >= 1
    assert data["conflicts"] == []

    # Verify the tag was actually inserted
    tag = db.query_one(
        "SELECT * FROM tags WHERE user_id = ? AND name = ?",
        (user_id, "新标签"),
    )
    assert tag is not None
    assert tag["color"] == "#00ff00"
    assert tag["priority"] == 3


def test_push_merges_settings(client: TestClient):
    """POST /api/sync/remotes/push should merge incoming settings."""
    from app.core.database import db

    user = db.query_one("SELECT id FROM users WHERE username = 'testuser'")
    user_id = int(user["id"])

    payload = {
        "snapshot_id": "remote-snap-002",
        "data": {
            "settings": [
                {"key": "new_setting", "value_json": '"hello"', "updated_at": "2025-01-01T00:00:00+00:00"},
            ],
            "tags": [],
            "mailbox_accounts": [],
            "mailbox_folders": [],
            "installed_plugins": [],
            "content_items": [],
        },
    }

    resp = client.post("/api/sync/remotes/push", json=payload, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["merged"]["settings"] >= 1

    # Verify setting was inserted
    row = db.query_one(
        "SELECT key, value_json FROM settings WHERE user_id = ? AND key = ?",
        (user_id, "new_setting"),
    )
    assert row is not None
    assert row["value_json"] == '"hello"'


def test_push_missing_data_field(client: TestClient):
    """POST /api/sync/remotes/push without data field should return 400."""
    resp = client.post(
        "/api/sync/remotes/push",
        json={"snapshot_id": "x"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 400


def test_push_unauthorized(client: TestClient):
    """POST /api/sync/remotes/push without auth should 401."""
    resp = client.post("/api/sync/remotes/push", json={"data": {}})
    assert resp.status_code == 401


# ── POST /api/sync/remotes/status ──────────────────────────────────────

def test_status_returns_correct_structure(client: TestClient):
    """POST /api/sync/remotes/status should return snapshot_id and summary
    with correct counts for all 6 categories."""
    resp = client.post("/api/sync/remotes/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "snapshot_id" in data
    assert "summary" in data

    summary = data["summary"]
    expected_counts = {
        "settings_count": 2,
        "tags_count": 2,
        "mailbox_count": 1,
        "folder_mapping_count": 2,
        "plugins_count": 1,
        "content_items_count": 1,
    }
    for key, expected in expected_counts.items():
        assert summary[key] == expected, f"{key}: expected {expected}, got {summary[key]}"


def test_status_snapshot_id_after_pull(client: TestClient):
    """After a pull, the status endpoint should return the new snapshot_id."""
    # Do a pull first to create a snapshot
    pull_resp = client.post("/api/sync/remotes/pull", headers=AUTH_HEADERS)
    pull_data = pull_resp.json()
    pulled_snap_id = pull_data["snapshot_id"]

    # Now check status
    status_resp = client.post("/api/sync/remotes/status", headers=AUTH_HEADERS)
    status_data = status_resp.json()
    assert status_data["snapshot_id"] == pulled_snap_id


def test_status_unauthorized(client: TestClient):
    """POST /api/sync/remotes/status without auth should 401."""
    resp = client.post("/api/sync/remotes/status")
    assert resp.status_code == 401
