import json
from pathlib import Path

from app.core.database import Database
from app.services.sync.jobs import claim_next_job, create_job, finish_job


def test_job_lifecycle(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    db.init()

    user_id = db.execute(
        "INSERT INTO users(username, email, phone, password_hash, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        ("u", None, None, "x", "t", "t"),
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
            "m",
            "m@example.com",
            "custom",
            "imap",
            993,
            1,
            "smtp",
            465,
            1,
            "app_password",
            "m",
            "enc",
            1,
            "t",
            "t",
        ),
    ).lastrowid

    job_id = create_job(db, int(user_id), int(mailbox_id), trigger="manual", folder_roles=["inbox"])
    job = claim_next_job(db, concurrency=1)
    assert job is not None
    assert job["id"] == job_id
    finish_job(db, job_id=job_id, ok=True, stats={"inserted": 1})

    row = db.query_one("SELECT status, stats_json FROM sync_jobs WHERE id = ?", (job_id,))
    assert row is not None
    assert row["status"] == "success"
    assert json.loads(row["stats_json"])["inserted"] == 1

