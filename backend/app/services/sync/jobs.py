from __future__ import annotations

import json
from typing import Any

from app.core.database import Database
from app.core.security import utc_iso


def create_job(
    db: Database,
    user_id: int,
    mailbox_id: int,
    trigger: str,
    folder_roles: list[str],
) -> int:
    """Create a queued sync job and return its id."""

    now = utc_iso()
    cur = db.execute(
        """
        INSERT INTO sync_jobs(
          user_id,
          mailbox_id,
          trigger,
          status,
          folder_roles_json,
          stats_json,
          error,
          created_at,
          updated_at
        ) VALUES (?, ?, ?, 'queued', ?, '{}', NULL, ?, ?)
        """,
        (
            int(user_id),
            int(mailbox_id),
            str(trigger),
            json.dumps(folder_roles, ensure_ascii=False),
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def claim_next_job(db: Database, concurrency: int) -> dict | None:
    """Claim the next queued job (if available) and mark it running.

    Notes:
    - Real concurrency enforcement is handled by the executor/worker.
    - Here we provide a minimal guard: if running jobs >= concurrency, don't claim.
    """

    if concurrency <= 0:
        return None

    running = db.query_one(
        "SELECT COUNT(*) AS c FROM sync_jobs WHERE status = 'running'"
    )
    if running and int(running["c"]) >= int(concurrency):
        return None

    row = db.query_one(
        "SELECT * FROM sync_jobs WHERE status = 'queued' ORDER BY id ASC LIMIT 1"
    )
    if not row:
        return None

    now = utc_iso()
    db.execute(
        """
        UPDATE sync_jobs
        SET status = 'running', started_at = ?, updated_at = ?
        WHERE id = ? AND status = 'queued'
        """,
        (now, now, int(row["id"])),
    )

    claimed = db.query_one("SELECT * FROM sync_jobs WHERE id = ?", (int(row["id"]),))
    if not claimed:
        return None
    return dict(claimed)


def finish_job(
    db: Database,
    job_id: int,
    ok: bool,
    stats: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Mark job as success/failed and persist stats/error."""

    status = "success" if ok else "failed"
    now = utc_iso()
    db.execute(
        """
        UPDATE sync_jobs
        SET status = ?, finished_at = ?, stats_json = ?, error = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            now,
            json.dumps(stats or {}, ensure_ascii=False),
            error,
            now,
            int(job_id),
        ),
    )

