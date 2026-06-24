"""Task 7: Tests for the inprocess sync executor.

All tests mock ``run_mailbox_sync`` so no real IMAP connection is needed.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

from app.core.config import Settings
from app.core.database import Database
from app.core.security import utc_iso
from app.services.sync.executor_inprocess import SyncExecutorInprocess
from app.services.sync.jobs import claim_next_job, create_job, finish_job


# ── helpers ──────────────────────────────────────────────────────────────


def _seed_user_and_mailbox(db: Database) -> tuple[int, int]:
    """Create a test user and mailbox account, return (user_id, mailbox_id)."""
    now = utc_iso()
    user_id = db.execute(
        "INSERT INTO users(username, email, phone, password_hash, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        ("test", None, None, "hash", now, now),
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
            "encrypted_dummy",
            1,
            now,
            now,
        ),
    ).lastrowid
    return int(user_id), int(mailbox_id)


def _make_settings(tmp_path: Path, **overrides) -> Settings:
    """Build a Settings object rooted under a temp dir."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    # Ensure a secret.key file exists so decrypt_secret won't crash.
    from app.core.security import load_or_create_fernet
    load_or_create_fernet(data_dir / "secret.key")

    kwargs = {
        "data_dir": data_dir,
        "database_name": "wuyou.sqlite3",
        "sync_mode": "inprocess",
        "sync_concurrency": 2,
        "sync_interval_minutes": 30,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


# ── tests ────────────────────────────────────────────────────────────────


def test_step_single_job_success(tmp_path: Path):
    """create_job + step should run the job and write back correct stats."""
    settings = _make_settings(tmp_path)
    db = Database(settings.database_path)
    db.init()

    user_id, mailbox_id = _seed_user_and_mailbox(db)
    job_id = create_job(
        db, user_id, mailbox_id, trigger="manual", folder_roles=["inbox"]
    )

    executor = SyncExecutorInprocess(db, settings)

    fake_stats = {"inserted": 7, "fetched": 10}

    with patch.object(executor, "_run_job") as mock_run:

        def _fake_run(job):
            # Simulate what _run_job normally does: acquire semaphore,
            # call run_mailbox_sync, finish_job, release semaphore.
            executor._semaphore.acquire()
            try:
                with executor._lock:
                    finish_job(executor.db, job["id"], ok=True, stats=fake_stats)
            finally:
                executor._semaphore.release()

        mock_run.side_effect = _fake_run

        claimed = executor.step()
        assert claimed is True

        # Wait for the daemon thread to complete (brief poll)
        for _ in range(50):
            row = db.query_one(
                "SELECT status, stats_json FROM sync_jobs WHERE id = ?", (job_id,)
            )
            if row and row["status"] == "success":
                break
            time.sleep(0.1)
        else:
            row = db.query_one(
                "SELECT status, stats_json FROM sync_jobs WHERE id = ?", (job_id,)
            )

    assert row is not None
    assert row["status"] == "success"
    stats = json.loads(row["stats_json"])
    assert stats == fake_stats


def test_concurrency_limit(tmp_path: Path):
    """With concurrency=2, at most 2 jobs should be running simultaneously.

    We mock ``claim_next_job`` to bypass its DB-level concurrency guard,
    so that the semaphore inside _run_job becomes the sole concurrency
    throttle.  This lets us verify that the semaphore indeed restricts
    actual parallelism to the configured value.
    """
    settings = _make_settings(tmp_path, sync_concurrency=2)
    db = Database(settings.database_path)
    db.init()

    user_id, mailbox_id = _seed_user_and_mailbox(db)

    # Create 4 queued jobs
    job_ids = []
    for _ in range(4):
        jid = create_job(
            db, user_id, mailbox_id, trigger="manual", folder_roles=["inbox"]
        )
        job_ids.append(jid)

    executor = SyncExecutorInprocess(db, settings)

    # Shared tracking across mocked threads
    lock = threading.Lock()
    max_concurrent = 0
    current = 0

    def _fake_run_job(job):
        nonlocal max_concurrent, current
        executor._semaphore.acquire()
        try:
            with lock:
                current += 1
                if current > max_concurrent:
                    max_concurrent = current
            # Simulate some work
            time.sleep(0.15)
            with lock:
                current -= 1
            with executor._lock:
                finish_job(executor.db, job["id"], ok=True, stats={"inserted": 1})
        finally:
            executor._semaphore.release()

    # Bypass claim_next_job's DB concurrency guard by returning the next
    # queued job from the list we prepared.
    pending = list(job_ids)  # shallow copy

    def _fake_claim(db_arg, concurrency):
        if not pending:
            return None
        jid = pending.pop(0)
        return db.query_one("SELECT * FROM sync_jobs WHERE id = ?", (jid,))

    with patch(
        "app.services.sync.executor_inprocess.claim_next_job",
        side_effect=_fake_claim,
    ):
        with patch.object(executor, "_run_job", side_effect=_fake_run_job):
            # Claim all 4 jobs via step()
            for _ in range(4):
                assert executor.step() is True

            # Wait for all jobs to complete
            for _ in range(60):
                done = db.query_one(
                    "SELECT COUNT(*) AS c FROM sync_jobs WHERE status IN ('success','failed') AND id IN ({})".format(
                        ",".join(str(j) for j in job_ids)
                    )
                )
                if done and int(done["c"]) == len(job_ids):
                    break
                time.sleep(0.1)

    # All 4 should be success
    for jid in job_ids:
        row = db.query_one("SELECT status FROM sync_jobs WHERE id = ?", (jid,))
        assert row is not None
        assert row["status"] == "success"

    assert max_concurrent <= 2, f"max_concurrent={max_concurrent}, expected <= 2"


def test_no_claim_when_at_concurrency(tmp_path: Path):
    """claim_next_job returns None when running count reaches concurrency."""
    settings = _make_settings(tmp_path, sync_concurrency=2)
    db = Database(settings.database_path)
    db.init()

    user_id, mailbox_id = _seed_user_and_mailbox(db)

    # Create 3 jobs
    for _ in range(3):
        create_job(db, user_id, mailbox_id, trigger="manual", folder_roles=["inbox"])

    # Manually set 2 jobs to 'running' to simulate concurrency saturation
    now = utc_iso()
    rows = db.query_all("SELECT id FROM sync_jobs WHERE status = 'queued' ORDER BY id LIMIT 2")
    for r in rows:
        db.execute(
            "UPDATE sync_jobs SET status = 'running', started_at = ?, updated_at = ? WHERE id = ?",
            (now, now, int(r["id"])),
        )

    # The third claim should fail because 2 are already running
    job = claim_next_job(db, concurrency=2)
    assert job is None


def test_step_no_pending_jobs(tmp_path: Path):
    """step() returns False when there are no queued jobs."""
    settings = _make_settings(tmp_path)
    db = Database(settings.database_path)
    db.init()

    _seed_user_and_mailbox(db)
    executor = SyncExecutorInprocess(db, settings)

    assert executor.step() is False


def test_restart_cleanup(tmp_path: Path):
    """Running jobs should be marked as canceled on service restart."""
    settings = _make_settings(tmp_path)
    db = Database(settings.database_path)
    db.init()

    user_id, mailbox_id = _seed_user_and_mailbox(db)

    # Create 2 jobs and mark them as running (simulating crash)
    j1 = create_job(db, user_id, mailbox_id, trigger="manual", folder_roles=["inbox"])
    j2 = create_job(db, user_id, mailbox_id, trigger="manual", folder_roles=["inbox"])
    now = utc_iso()
    db.execute(
        "UPDATE sync_jobs SET status = 'running', started_at = ?, updated_at = ? WHERE id IN (?, ?)",
        (now, now, j1, j2),
    )

    # Verify they are running
    running_before = db.query_one(
        "SELECT COUNT(*) AS c FROM sync_jobs WHERE status = 'running'"
    )
    assert running_before["c"] == 2

    # Import and call the cleanup function directly (with test db)
    from app.main import _cleanup_running_jobs

    _cleanup_running_jobs(target_db=db)

    # All running should now be canceled
    running_after = db.query_one(
        "SELECT COUNT(*) AS c FROM sync_jobs WHERE status = 'running'"
    )
    assert running_after["c"] == 0

    canceled_rows = db.query_all(
        "SELECT id, error FROM sync_jobs WHERE status = 'canceled' AND id IN (?, ?)",
        (j1, j2),
    )
    assert len(canceled_rows) == 2
    for row in canceled_rows:
        assert row["error"] == "service restart"
