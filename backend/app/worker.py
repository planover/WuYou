"""WuYou standalone sync worker.

Runs independently of the FastAPI server.  Controlled by the environment
variable ``WUYOU_SYNC_MODE=worker``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# ── ensure ``app`` is importable regardless of cwd ─────────────────────
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parent.parent  # backend/
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import logging  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import Database  # noqa: E402
from app.core.security import utc_iso  # noqa: E402
from app.services.sync.executor_inprocess import SyncExecutorInprocess  # noqa: E402

logger = logging.getLogger("wuyou.worker")

# ── rainbow startup banner (ANSI escape codes, zero dependencies) ──────

_COLORS = [31, 33, 32, 36, 34, 35]  # red, yellow, green, cyan, blue, magenta

_BANNER_RAW = r"""
__  __      _  ____
\ \/ /_ __ (_)/ _  ) ___  __  __
 \  /|  _ \| |/ _  |/ _ \/ / / /
 /  \| |_) | | (_) | (_) ) /_/ /
/_/\_\_.__/|_|\___/ \___/ \__, /
                          /___/
     Standalone Sync Worker
"""


def _colour(c: int, text: str) -> str:
    return f"\033[{c}m{text}\033[0m"


def _print_banner() -> None:
    """Print a rainbow-coloured startup banner to stdout."""
    lines = _BANNER_RAW.strip("\n").split("\n")
    for i, line in enumerate(lines):
        color = _COLORS[i % len(_COLORS)]
        print(_colour(color, line))
    print()


# ── job cleanup ────────────────────────────────────────────────────────


def _cleanup_running_jobs(db: Database) -> None:
    """Mark all ``running`` jobs as ``canceled`` (reason = worker restart)."""
    now = utc_iso()
    cur = db.execute(
        "UPDATE sync_jobs "
        "SET status = 'canceled', error = 'worker restart', updated_at = ? "
        "WHERE status = 'running'",
        (now,),
    )
    if cur.rowcount:
        logger.info("Cleaned up %s running job(s) after worker restart", cur.rowcount)


# ── main ───────────────────────────────────────────────────────────────


def main() -> None:
    """Bootstrap the worker and enter the main loop."""
    settings = get_settings()
    db = Database(settings.database_path)
    db.init()

    _cleanup_running_jobs(db)

    executor = SyncExecutorInprocess(db, settings)
    logger.info(
        "Worker started (concurrency=%s, interval=%s min)",
        settings.sync_concurrency,
        settings.sync_interval_minutes,
    )

    try:
        while True:
            try:
                had_work = executor.step()
            except Exception:
                logger.exception("executor step error")
                had_work = False

            if not had_work:
                time.sleep(5)
    except KeyboardInterrupt:
        print()
        logger.info("Worker 已停止")


if __name__ == "__main__":
    _print_banner()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()
