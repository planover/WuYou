"""Tests for /api/sync/jobs routes using FastAPI TestClient."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


AUTH_TOKEN = "test-token-123"
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}
FOLDER_ROLES_DEFAULT = ["inbox", "sent", "trash", "archive", "junk"]


def _setup_test_db(db_path: Path) -> tuple:
    """Create a temporary database, initialise schema, insert test fixtures.

    Returns (user_id, mailbox_id, job_ids_by_status) where job_ids_by_status
    maps status strings to lists of job ids.
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

    # mailbox
    mailbox_id = test_db.execute(
        """
        INSERT INTO mailbox_accounts(
          user_id, display_name, email_address, provider,
          imap_host, imap_port, imap_ssl,
          smtp_host, smtp_port, smtp_ssl,
          auth_type, username, encrypted_secret, sync_enabled,
          created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            user_id,
            "TestMail",
            "test@example.com",
            "custom",
            "imap.example.com", 993, 1,
            "smtp.example.com", 465, 1,
            "app_password",
            "test@example.com",
            "encrypted",
            1,
            now,
            now,
        ),
    ).lastrowid

    # second mailbox (for isolation tests)
    mailbox2_id = test_db.execute(
        """
        INSERT INTO mailbox_accounts(
          user_id, display_name, email_address, provider,
          imap_host, imap_port, imap_ssl,
          smtp_host, smtp_port, smtp_ssl,
          auth_type, username, encrypted_secret, sync_enabled,
          created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            user_id,
            "Mail2",
            "mail2@example.com",
            "custom",
            "imap2.example.com", 993, 1,
            "smtp2.example.com", 465, 1,
            "app_password",
            "mail2@example.com",
            "encrypted",
            1,
            now,
            now,
        ),
    ).lastrowid

    def _insert_job(status, mb_id, trigger="manual"):
        return test_db.execute(
            """
            INSERT INTO sync_jobs(
              user_id, mailbox_id, trigger, status, folder_roles_json,
              stats_json, error, started_at, finished_at, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                user_id,
                mb_id,
                trigger,
                status,
                json.dumps(FOLDER_ROLES_DEFAULT),
                json.dumps({"inserted": 5} if status == "success" else {"inserted": 0}),
                "test error" if status == "failed" else None,
                now if status in ("running", "success", "failed") else None,
                now if status in ("success", "failed") else None,
                now,
                now,
            ),
        ).lastrowid

    # Insert jobs with various statuses
    job_ids = {
        "queued":  [_insert_job("queued",  mailbox_id),  _insert_job("queued",  mailbox2_id)],
        "running": [_insert_job("running", mailbox_id)],
        "success": [_insert_job("success", mailbox_id)],
        "failed":  [_insert_job("failed",  mailbox_id)],
    }

    return int(user_id), int(mailbox_id), int(mailbox2_id), job_ids, test_db


@pytest.fixture
def client(tmp_path: Path):
    """Pytest fixture: set up a temp database, patch the global db, and
    return a FastAPI TestClient."""
    import sys

    db_path = tmp_path / "test.db"
    user_id, mailbox_id, mailbox2_id, job_ids, test_db = _setup_test_db(db_path)

    # Monkey-patch the module-level db BEFORE importing the app.
    # Use a distinct name to avoid shadowing the `app` package later.
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


# ── POST /api/sync/jobs ────────────────────────────────────────────────

def test_create_sync_job_success(client: TestClient):
    """POST /api/sync/jobs with a valid mailbox_id should return job_id."""
    # mailbox_id is the first mailbox (belongs to our test user)
    from app.core.database import db

    # Get an existing mailbox_id from the db directly
    row = db.query_one(
        "SELECT id FROM mailbox_accounts WHERE user_id = (SELECT id FROM users WHERE username = 'testuser') LIMIT 1"
    )
    mailbox_id = int(row["id"])

    resp = client.post(
        "/api/sync/jobs",
        json={"mailbox_id": mailbox_id},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert data["message"] == "\u5df2\u52a0\u5165\u540c\u6b65\u961f\u5217"
    assert isinstance(data["job_id"], int)

    # Verify the job was actually created
    job = db.query_one("SELECT * FROM sync_jobs WHERE id = ?", (data["job_id"],))
    assert job is not None
    assert job["status"] == "queued"
    assert job["trigger"] == "manual"


def test_create_sync_job_with_folder_roles(client: TestClient):
    """POST /api/sync/jobs with explicit folder_roles should store them."""
    from app.core.database import db

    row = db.query_one(
        "SELECT id FROM mailbox_accounts WHERE user_id = (SELECT id FROM users WHERE username = 'testuser') LIMIT 1"
    )
    mailbox_id = int(row["id"])

    custom_roles = ["inbox", "sent"]
    resp = client.post(
        "/api/sync/jobs",
        json={"mailbox_id": mailbox_id, "folder_roles": custom_roles},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "\u5df2\u52a0\u5165\u540c\u6b65\u961f\u5217"

    job = db.query_one("SELECT * FROM sync_jobs WHERE id = ?", (data["job_id"],))
    assert json.loads(job["folder_roles_json"]) == custom_roles


def test_create_sync_job_not_owned_mailbox(client: TestClient):
    """POST /api/sync/jobs for a mailbox not belonging to the user should 404."""
    resp = client.post(
        "/api/sync/jobs",
        json={"mailbox_id": 99999},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 404


def test_create_sync_job_unauthorized(client: TestClient):
    """POST /api/sync/jobs without auth should 401."""
    resp = client.post("/api/sync/jobs", json={"mailbox_id": 1})
    assert resp.status_code == 401


def test_create_sync_job_missing_mailbox_id(client: TestClient):
    """POST /api/sync/jobs without mailbox_id should 400."""
    resp = client.post("/api/sync/jobs", json={}, headers=AUTH_HEADERS)
    assert resp.status_code == 400


# ── GET /api/sync/jobs ──────────────────────────────────────────────────

def test_list_jobs_all(client: TestClient):
    """GET /api/sync/jobs without filters should return all user's jobs."""
    resp = client.get("/api/sync/jobs", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 5  # queued*2 + running + success + failed
    for job in data:
        assert "id" in job
        assert "status" in job
        assert "mailbox_id" in job


def test_list_jobs_filter_mailbox_id(client: TestClient):
    """Filtering by mailbox_id should only return jobs for that mailbox."""
    from app.core.database import db

    row = db.query_one(
        "SELECT id FROM mailbox_accounts WHERE email_address = 'mail2@example.com'"
    )
    mailbox2_id = int(row["id"])

    resp = client.get(
        f"/api/sync/jobs?mailbox_id={mailbox2_id}",
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    for job in data:
        assert job["mailbox_id"] == mailbox2_id


def test_list_jobs_filter_status(client: TestClient):
    """Filtering by status should only return matching jobs."""
    resp = client.get("/api/sync/jobs?status=failed", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    for job in data:
        assert job["status"] == "failed"


def test_list_jobs_filter_combined(client: TestClient):
    """Combined mailbox_id + status filter."""
    from app.core.database import db

    row = db.query_one(
        "SELECT id FROM mailbox_accounts WHERE email_address = 'test@example.com'"
    )
    mb_id = int(row["id"])

    resp = client.get(
        f"/api/sync/jobs?mailbox_id={mb_id}&status=success",
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    for job in data:
        assert job["mailbox_id"] == mb_id
        assert job["status"] == "success"


def test_list_jobs_unauthorized(client: TestClient):
    """GET /api/sync/jobs without auth should 401."""
    resp = client.get("/api/sync/jobs")
    assert resp.status_code == 401


# ── GET /api/sync/jobs/{job_id} ────────────────────────────────────────

def test_get_job_detail(client: TestClient):
    """GET /api/sync/jobs/{job_id} should return full job details."""
    from app.core.database import db

    row = db.query_one(
        "SELECT id FROM sync_jobs WHERE status = 'success' LIMIT 1"
    )
    job_id = int(row["id"])

    resp = client.get(f"/api/sync/jobs/{job_id}", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == job_id
    assert data["status"] == "success"
    assert data["error"] is None
    assert data["started_at"] is not None
    assert data["finished_at"] is not None
    assert "stats_json" in data


def test_get_job_detail_failed(client: TestClient):
    """Detail for a failed job should include error field."""
    from app.core.database import db

    row = db.query_one(
        "SELECT id FROM sync_jobs WHERE status = 'failed' LIMIT 1"
    )
    job_id = int(row["id"])

    resp = client.get(f"/api/sync/jobs/{job_id}", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"
    assert data["error"] == "test error"


def test_get_job_not_found(client: TestClient):
    """Non-existent job should 404."""
    resp = client.get("/api/sync/jobs/99999", headers=AUTH_HEADERS)
    assert resp.status_code == 404


def test_get_job_unauthorized(client: TestClient):
    """Unauthenticated request should 401."""
    resp = client.get("/api/sync/jobs/1")
    assert resp.status_code == 401
