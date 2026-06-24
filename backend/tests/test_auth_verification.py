"""Tests for POST /api/auth/verification-code rate-limit and SMTP guard."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


AUTH_TOKEN = "test-token-123"
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}


def _setup_test_db(db_path: Path):
    """Create a temp database, initialise schema, insert a user + session."""
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

    db_path = tmp_path / "test_auth_verification.db"
    _setup_test_db(db_path)

    import app.core.database as _db_mod

    original_db = _db_mod.db
    from app.core.database import Database

    test_db = Database(db_path)
    test_db.connect()
    _db_mod.db = test_db

    # Clear cached app modules so they reload with the patched db
    for key in list(sys.modules.keys()):
        if key.startswith("app.main") or key.startswith("app.api"):
            sys.modules.pop(key, None)

    from app.main import app as _fastapi_app

    tc = TestClient(_fastapi_app)

    yield tc

    # Restore original db
    _db_mod.db = original_db
    for key in list(sys.modules.keys()):
        if key.startswith("app.main") or key.startswith("app.api"):
            sys.modules.pop(key, None)


class TestRateLimit60s:
    """Verify that two requests for the same (target, purpose) within 60 s are rate-limited."""

    def test_rate_limit_60s(self, client: TestClient):
        """First request succeeds, second within 60 s returns 429."""
        payload = {"target_type": "phone", "target": "+8613800138000", "purpose": "login"}

        # First request — should succeed (console adapter always returns True)
        resp1 = client.post("/api/auth/verification-code", json=payload)
        assert resp1.status_code == 200, resp1.text
        data1 = resp1.json()
        assert data1["message"] == "验证码已发送。"
        # dev_code should be present since default environment != "production"
        assert data1["dev_code"] is not None

        # Second request with same target + purpose — rate limited
        resp2 = client.post("/api/auth/verification-code", json=payload)
        assert resp2.status_code == 429, resp2.text
        detail = resp2.json()["detail"]
        assert "秒后重试" in detail

    def test_different_targets_are_not_rate_limited(self, client: TestClient):
        """Different target values should bypass the rate limit."""
        payload_a = {"target_type": "phone", "target": "+8611111111111", "purpose": "login"}
        payload_b = {"target_type": "phone", "target": "+8622222222222", "purpose": "login"}

        resp_a = client.post("/api/auth/verification-code", json=payload_a)
        assert resp_a.status_code == 200

        resp_b = client.post("/api/auth/verification-code", json=payload_b)
        assert resp_b.status_code == 200


class TestSmtpUnconfigured:
    """Verify email verification returns 503 when SMTP is not configured."""

    def test_smtp_unconfigured(self, client: TestClient):
        """system_smtp_host="" by default — email verification must return 503."""
        payload = {"target_type": "email", "target": "test@example.com", "purpose": "login"}

        resp = client.post("/api/auth/verification-code", json=payload)
        assert resp.status_code == 503, resp.text
        detail = resp.json()["detail"]
        assert "邮件服务未配置" in detail
