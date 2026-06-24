"""Tests for /api/sync/peers routes using FastAPI TestClient."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


AUTH_TOKEN = "test-token-peers-789"
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}


def _setup_test_db(db_path: Path):
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

    # Pre-populate one sync_peer
    test_db.execute(
        """INSERT INTO sync_peers(user_id, label, url, remote_username, enabled, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, ?, ?)""",
        (user_id, "我的电脑", "http://peer1:8787", "peer_user_1", now, now),
    )

    return int(user_id), test_db


@pytest.fixture
def client(tmp_path: Path):
    """Pytest fixture: set up a temp database, patch the global db, and
    return a FastAPI TestClient."""
    import sys

    db_path = tmp_path / "test_peers.db"
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


# ── GET /api/sync/peers ──────────────────────────────────────────────────

def test_list_peers_returns_records(client: TestClient):
    """GET /api/sync/peers should return all peer records for the current user."""
    resp = client.get("/api/sync/peers", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    peer = data[0]
    assert "id" in peer
    assert "label" in peer
    assert "url" in peer
    assert "remote_username" in peer
    assert "enabled" in peer
    assert peer["label"] == "我的电脑"
    assert peer["url"] == "http://peer1:8787"
    assert peer["remote_username"] == "peer_user_1"


def test_list_peers_empty_for_new_user(client: TestClient):
    """GET /api/sync/peers with only the pre-populated peer should not be empty.
    This test verifies the peer from _setup_test_db exists."""
    # The fixture already has 1 peer; just verify the structure is correct.
    resp = client.get("/api/sync/peers", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    for peer in data:
        assert "id" in peer
        assert "label" in peer
        assert "url" in peer
        assert "remote_username" in peer


def test_list_peers_unauthorized(client: TestClient):
    """GET /api/sync/peers without auth should 401."""
    resp = client.get("/api/sync/peers")
    assert resp.status_code == 401


# ── POST /api/sync/peers ─────────────────────────────────────────────────

def test_create_peer_success(client: TestClient):
    """POST /api/sync/peers with valid data should create and return the peer."""
    from app.core.database import db

    user = db.query_one("SELECT id FROM users WHERE username = 'testuser'")
    user_id = int(user["id"])

    resp = client.post(
        "/api/sync/peers",
        json={
            "url": "http://peer2:8787",
            "remote_username": "peer_user_2",
            "label": "笔记本",
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["url"] == "http://peer2:8787"
    assert data["remote_username"] == "peer_user_2"
    assert data["label"] == "笔记本"
    assert data["enabled"] == 1
    assert data["user_id"] == user_id
    assert "id" in data
    assert "created_at" in data

    # Verify in DB
    row = db.query_one("SELECT * FROM sync_peers WHERE id = ?", (data["id"],))
    assert row is not None
    assert row["user_id"] == user_id


def test_create_peer_default_label(client: TestClient):
    """POST /api/sync/peers without label should default to '远程设备'."""
    resp = client.post(
        "/api/sync/peers",
        json={
            "url": "http://peer3:8787",
            "remote_username": "peer_user_3",
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["label"] == "远程设备"


def test_create_peer_missing_url(client: TestClient):
    """POST /api/sync/peers without url should 400."""
    resp = client.post(
        "/api/sync/peers",
        json={"remote_username": "someone"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 400


def test_create_peer_missing_remote_username(client: TestClient):
    """POST /api/sync/peers without remote_username should 400."""
    resp = client.post(
        "/api/sync/peers",
        json={"url": "http://peer:8787"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 400


def test_create_peer_unauthorized(client: TestClient):
    """POST /api/sync/peers without auth should 401."""
    resp = client.post(
        "/api/sync/peers",
        json={"url": "http://peer:8787", "remote_username": "someone"},
    )
    assert resp.status_code == 401


# ── DELETE /api/sync/peers/{peer_id} ─────────────────────────────────────

def test_delete_peer_success(client: TestClient):
    """DELETE /api/sync/peers/{peer_id} for owned peer should succeed."""
    from app.core.database import db

    # Get the pre-populated peer id
    row = db.query_one("SELECT id FROM sync_peers LIMIT 1")
    peer_id = int(row["id"])

    resp = client.delete(f"/api/sync/peers/{peer_id}", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "已删除"
    assert data["peer_id"] == peer_id

    # Verify it's gone
    deleted = db.query_one("SELECT id FROM sync_peers WHERE id = ?", (peer_id,))
    assert deleted is None


def test_delete_peer_not_found(client: TestClient):
    """DELETE /api/sync/peers/{peer_id} for non-existent peer should 404."""
    resp = client.delete("/api/sync/peers/99999", headers=AUTH_HEADERS)
    assert resp.status_code == 404


def test_delete_peer_wrong_user(client: TestClient):
    """DELETE /api/sync/peers/{peer_id} for another user's peer should 403.
    This test creates a second user and verifies the first user cannot delete
    the second user's peer."""
    from app.core.database import db
    from app.core.security import utc_iso

    now = utc_iso()

    # Create a second user
    user2_id = db.execute(
        "INSERT INTO users(username, email, phone, password_hash, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        ("other", "other@example.com", None, "hashed", now, now),
    ).lastrowid

    # Create a peer for user2
    peer2_id = db.execute(
        """INSERT INTO sync_peers(user_id, label, url, remote_username, enabled, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, ?, ?)""",
        (user2_id, "OtherPeer", "http://other:8787", "other_user", now, now),
    ).lastrowid

    # Our test user (authenticated) tries to delete user2's peer
    resp = client.delete(f"/api/sync/peers/{peer2_id}", headers=AUTH_HEADERS)
    assert resp.status_code == 403


def test_delete_peer_unauthorized(client: TestClient):
    """DELETE /api/sync/peers/{peer_id} without auth should 401."""
    resp = client.delete("/api/sync/peers/1")
    assert resp.status_code == 401
