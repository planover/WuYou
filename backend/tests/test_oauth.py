"""Tests for OAuth2 provider routes using FastAPI TestClient."""

from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

AUTH_TOKEN = "test-token-oauth"
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}


def _setup_oauth_test_db(db_path: Path):
    """Create a temporary database, initialise schema, insert test fixtures.

    Returns (db_instance, user_id).
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

    return test_db, int(user_id)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Pytest fixture: set up a temp database, patch the global db,
    set OAuth env vars, and return a FastAPI TestClient."""
    import sys

    # Set OAuth client ID / secret env vars so authorize + callback can resolve them
    monkeypatch.setenv("WUYOU_OAUTH_GOOGLE_CLIENT_ID", "google-test-client-id")
    monkeypatch.setenv("WUYOU_OAUTH_GOOGLE_CLIENT_SECRET", "google-test-client-secret")
    monkeypatch.setenv("WUYOU_OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/oauth/callback")

    # Also clear the lru_cache on get_settings so it picks up the env vars
    from app.core.config import get_settings
    get_settings.cache_clear()

    db_path = tmp_path / "test.db"
    test_db, user_id = _setup_oauth_test_db(db_path)

    # Monkey-patch the module-level db BEFORE importing the app
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

    # Restore lru_cache
    get_settings.cache_clear()


# ── GET /api/auth/oauth/providers ──────────────────────────────────────

def test_list_providers(client: TestClient):
    """GET /api/auth/oauth/providers returns list of {id, name}."""
    resp = client.get("/api/auth/oauth/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 5
    provider_ids = {p["id"] for p in data}
    assert provider_ids == {"google", "microsoft", "qq", "yahoo", "zoho"}
    for p in data:
        assert "id" in p
        assert "name" in p
        assert isinstance(p["name"], str)


# ── GET /api/auth/oauth/authorize ──────────────────────────────────────

def test_authorize_returns_auth_url_with_client_id_and_scope(client: TestClient):
    """authorize should return an auth_url containing the correct client_id and scope."""
    resp = client.get("/api/auth/oauth/authorize?provider=google")
    assert resp.status_code == 200
    data = resp.json()
    assert "auth_url" in data
    auth_url = data["auth_url"]
    # Check that the URL starts with the Google auth endpoint
    assert auth_url.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    # Check it contains the expected query parameters
    assert "client_id=google-test-client-id" in auth_url
    assert "scope=openid+email+profile" in auth_url
    assert "response_type=code" in auth_url
    assert "state=" in auth_url


def test_authorize_persists_state_in_db(client: TestClient):
    """authorize should store a state record in oauth_states table."""
    resp = client.get("/api/auth/oauth/authorize?provider=google&redirect_to=/inbox")
    assert resp.status_code == 200

    from app.core.database import db
    from app.core.security import parse_utc, now_utc

    # Query all oauth_states to verify one was created
    rows = db.query_all("SELECT * FROM oauth_states")
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "google"
    assert row["redirect_to"] == "/inbox"
    # TTL should be ~10 minutes in the future
    expires = parse_utc(row["expires_at"])
    assert expires > now_utc()
    assert (expires - now_utc()).total_seconds() < 601  # within 10 min + epsilon


def test_authorize_rejects_unknown_provider(client: TestClient):
    """authorize with an unknown provider should return 400."""
    resp = client.get("/api/auth/oauth/authorize?provider=unknown")
    assert resp.status_code == 400


def test_authorize_qq_returns_501(client: TestClient):
    """authorize with QQ should return 501 (not implemented yet)."""
    resp = client.get("/api/auth/oauth/authorize?provider=qq")
    assert resp.status_code == 501


# ── GET /api/auth/oauth/callback ───────────────────────────────────────

def test_callback_invalid_state_returns_400(client: TestClient):
    """callback with a state that does not exist should return 400."""
    resp = client.get("/api/auth/oauth/callback?code=abc&state=nonexistent_state")
    assert resp.status_code == 400


def test_callback_expired_state_returns_400(client: TestClient):
    """callback with an expired state should return 400."""
    from app.core.database import db

    # Insert an already-expired state
    db.execute(
        "INSERT INTO oauth_states(state, provider, redirect_to, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("expired_state", "google", "/", "2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
    )

    resp = client.get("/api/auth/oauth/callback?code=abc&state=expired_state")
    assert resp.status_code == 400


def test_callback_success_returns_token(client: TestClient):
    """callback with valid state and mocked httpx should return token and email."""
    from app.core.database import db
    from app.core.security import utc_iso, now_utc

    # Insert a valid state
    state_val = "valid_state_for_callback_test"
    expires = utc_iso(now_utc() + timedelta(minutes=10))
    db.execute(
        "INSERT INTO oauth_states(state, provider, redirect_to, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (state_val, "google", "/", expires, utc_iso()),
    )

    # Build mock responses for httpx
    mock_token_resp = MagicMock()
    mock_token_resp.raise_for_status = MagicMock()
    mock_token_resp.json.return_value = {
        "access_token": "mock_access_token_xyz",
        "refresh_token": "mock_refresh_token_xyz",
    }

    mock_userinfo_resp = MagicMock()
    mock_userinfo_resp.raise_for_status = MagicMock()
    mock_userinfo_resp.json.return_value = {
        "email": "newuser@gmail.com",
        "name": "Test User",
    }

    # Mock httpx.AsyncClient in the routes_auth module
    import app.api.routes_auth as auth_mod

    mock_instance = MagicMock()
    mock_instance.post = AsyncMock(return_value=mock_token_resp)
    mock_instance.get = AsyncMock(return_value=mock_userinfo_resp)

    with patch.object(auth_mod.httpx, "AsyncClient") as mock_async_client_cls:
        mock_async_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_async_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        resp = client.get(
            f"/api/auth/oauth/callback?code=test_auth_code&state={state_val}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert isinstance(data["token"], str)
        assert len(data["token"]) > 0
        assert data["email"] == "newuser@gmail.com"

    # Verify the state was cleaned up
    row = db.query_one("SELECT * FROM oauth_states WHERE state = ?", (state_val,))
    assert row is None

    # Verify the user was created
    user = db.query_one("SELECT * FROM users WHERE email = ?", ("newuser@gmail.com",))
    assert user is not None

    # Verify the mailbox_accounts was created with oauth2 auth_type
    mb = db.query_one(
        "SELECT * FROM mailbox_accounts WHERE user_id = ? AND email_address = ?",
        (user["id"], "newuser@gmail.com"),
    )
    assert mb is not None
    assert mb["auth_type"] == "oauth2"
    assert mb["provider"] == "gmail"
