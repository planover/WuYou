"""Tests for /api/share routes using FastAPI TestClient."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


AUTH_TOKEN = "test-token-123"
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}


def _setup_test_db(db_path: Path):
    """Create a temporary database, initialise schema, insert a user + session."""
    from app.core.database import Database
    from app.core.security import utc_iso

    test_db = Database(db_path)
    test_db.init()

    now = utc_iso()
    token_hash = hashlib.sha256(AUTH_TOKEN.encode("utf-8")).hexdigest()

    user_id = test_db.execute(
        "INSERT INTO users(username, email, phone, password_hash, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        ("testuser", "test@example.com", None, "hashed", now, now),
    ).lastrowid

    test_db.execute(
        "INSERT INTO sessions(user_id, token_hash, expires_at, created_at) VALUES (?,?,?,?)",
        (user_id, token_hash, "2099-12-31T23:59:59+00:00", now),
    )

    return int(user_id), test_db


@pytest.fixture
def client(tmp_path: Path):
    """Set up a temp database, patch the global db, return a FastAPI TestClient."""
    import sys

    db_path = tmp_path / "test_share.db"
    _setup_test_db(db_path)

    # Monkey-patch the module-level db BEFORE importing the app.
    import app.core.database as _db_mod

    original_db = _db_mod.db
    from app.core.database import Database

    test_db = Database(db_path)
    test_db.connect()
    _db_mod.db = test_db

    # Clear cached app modules so they reload with the patched db
    _cached = {}
    for _key in list(sys.modules.keys()):
        if _key.startswith("app.main") or _key.startswith("app.api"):
            _cached[_key] = sys.modules.pop(_key)

    from app.main import app as _fastapi_app

    tc = TestClient(_fastapi_app)

    yield tc

    # Restore cached modules and original db
    _db_mod.db = original_db
    sys.modules.update(_cached)


# ── test_share_theme ─────────────────────────────────────────────────────

def test_share_theme(client: TestClient):
    """POST /api/share with a valid theme submission should return 200 + message."""
    resp = client.post(
        "/api/share",
        json={
            "type": "theme",
            "item_id": "ocean-blue",
            "manifest": {"meta": {"id": "ocean-blue", "name": "Ocean Blue"}},
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "message" in data
    assert data["message"] == "分享已提交，等待审核。"


# ── test_share_duplicate ─────────────────────────────────────────────────

def test_share_duplicate(client: TestClient):
    """Submitting the same type+item_id twice should return 409 on the second attempt."""
    payload = {
        "type": "theme",
        "item_id": "dark-mode",
        "manifest": {"meta": {"id": "dark-mode"}},
    }

    # First submission
    resp1 = client.post("/api/share", json=payload, headers=AUTH_HEADERS)
    assert resp1.status_code == 200

    # Second submission (duplicate)
    resp2 = client.post("/api/share", json=payload, headers=AUTH_HEADERS)
    assert resp2.status_code == 409
    assert "重复" in resp2.json()["detail"]


# ── test_share_invalid_type ──────────────────────────────────────────────

def test_share_invalid_type(client: TestClient):
    """Submitting with type='invalid' should return 400."""
    resp = client.post(
        "/api/share",
        json={
            "type": "invalid",
            "item_id": "some-item",
            "manifest": {},
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 400
    assert "type" in resp.json()["detail"]


# ── test_list_submissions ────────────────────────────────────────────────

def test_list_submissions(client: TestClient):
    """After submitting, GET /api/share/submissions should include the record."""
    # Submit first
    resp = client.post(
        "/api/share",
        json={
            "type": "language-pack",
            "item_id": "ja-JP",
            "manifest": {"locale": "ja-JP"},
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200

    # Fetch submissions
    resp_get = client.get("/api/share/submissions", headers=AUTH_HEADERS)
    assert resp_get.status_code == 200
    data = resp_get.json()
    assert "submissions" in data
    assert len(data["submissions"]) == 1
    sub = data["submissions"][0]
    assert sub["type"] == "language-pack"
    assert sub["item_id"] == "ja-JP"
    assert sub["status"] == "pending"
    assert sub["manifest"] == {"locale": "ja-JP"}
