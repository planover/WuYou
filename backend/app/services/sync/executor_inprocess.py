"""Task 7: In-process sync executor with semaphore-based concurrency control."""

from __future__ import annotations

import logging
import threading

from app.core.config import Settings
from app.core.database import Database
from app.core.security import decrypt_secret
from app.services.sync.jobs import claim_next_job, finish_job
from app.services.sync.sync_engine import run_mailbox_sync

logger = logging.getLogger(__name__)


class SyncExecutorInprocess:
    """In-process executor that claims queued jobs and runs them in background threads.

    Concurrency is controlled by a Semaphore whose value is taken from
    ``settings.sync_concurrency``.  A ``threading.Lock`` serialises
    write-back calls to ``finish_job`` so that statistics are never
    interleaved.
    """

    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self._lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(settings.sync_concurrency)

    # ── public API ──────────────────────────────────────────────────────

    def step(self) -> bool:
        """Claim one queued job (if any) and hand it to a worker thread.

        Returns ``True`` when a job was claimed, ``False`` otherwise.
        """
        job = claim_next_job(self.db, self.settings.sync_concurrency)
        if job is None:
            return False

        t = threading.Thread(target=self._run_job, args=(job,), daemon=True)
        t.start()
        return True

    # ── internals ───────────────────────────────────────────────────────

    def _run_job(self, job: dict) -> None:
        """Execute a single sync job in a thread.

        The semaphore guarantees at most *concurrency* threads are
        running at the same time.  The lock serialises writes to the
        database so that ``finish_job`` calls never race.
        """
        self._semaphore.acquire()
        try:
            account = self.db.query_one(
                "SELECT * FROM mailbox_accounts WHERE id = ?",
                (job["mailbox_id"],),
            )
            if not account:
                with self._lock:
                    finish_job(self.db, job["id"], ok=False, error="mailbox not found")
                return

            secret = decrypt_secret(
                account["encrypted_secret"], self.settings.secret_key_path
            )
            stats = run_mailbox_sync(
                self.db, self.settings, job, dict(account), secret
            )

            with self._lock:
                finish_job(self.db, job["id"], ok=True, stats=stats)
        except Exception as exc:
            logger.exception("Sync job %s failed", job.get("id"))
            with self._lock:
                finish_job(
                    self.db,
                    job["id"],
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
        finally:
            self._semaphore.release()
