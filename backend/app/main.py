"""WuYou（一坞邮） FastAPI 应用入口。

启动时执行：
1. 数据库初始化（建表 + 迁移 + 索引）
2. 内置邮件同步调度器启动（inprocess 模式下）
3. 远程设备间同步后台线程启动
4. 热更新文件监视器启动
5. 遥测后台刷新线程启动

关闭时执行：最后一次遥测 flush，确保不丢事件。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import routes_accounts, routes_auth, routes_dav, routes_items, routes_locales, routes_mail, routes_pgp, routes_plugins, routes_settings, routes_share, routes_sync, routes_sync_peers, routes_sync_remotes, routes_system, routes_telemetry, routes_themes, routes_translate
from app.core.config import get_settings
from app.core.database import db
from app.core.security import utc_iso
from app.services.sync.executor_inprocess import SyncExecutorInprocess
from app.services.sync.jobs import create_job
from app.services.sync.remote_client import run_remote_sync_cycle

logger = logging.getLogger(__name__)

settings = get_settings()
app = FastAPI(title=settings.app_name, version="1.0.1")

origins = ["*"] if settings.allow_origins == "*" else [item.strip() for item in settings.allow_origins.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_auth.router)
app.include_router(routes_dav.router)
app.include_router(routes_items.router)
app.include_router(routes_accounts.router)
app.include_router(routes_mail.router)
app.include_router(routes_pgp.router)
app.include_router(routes_settings.router)
app.include_router(routes_plugins.router)
app.include_router(routes_sync.router)
app.include_router(routes_sync_peers.router)
app.include_router(routes_sync_peers.remote_router)
app.include_router(routes_sync_remotes.router)
app.include_router(routes_themes.router)
app.include_router(routes_locales.router)
app.include_router(routes_translate.router)
app.include_router(routes_share.router)
app.include_router(routes_system.router)
app.include_router(routes_telemetry.router)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── in-process sync helpers ──────────────────────────────────────────────

def _start_inprocess_sync() -> None:
    """启动内置邮件同步调度器和执行器（sync_mode == inprocess 时调用）。

    1. 清理上次异常退出遗留的 running 状态 job
    2. 启动执行器后台线程（轮询 job 队列并执行同步）
    3. 启动调度器定时器（周期性扫描 enabled 邮箱并创建 scheduled job）
    """

    # 1. Clean up orphaned running jobs from a previous crash / restart
    _cleanup_running_jobs()

    # 2. Start the executor background loop
    executor = SyncExecutorInprocess(db, settings)

    def _executor_loop() -> None:
        while True:
            try:
                executor.step()
            except Exception:
                logger.exception("executor step error")
            time.sleep(5)

    _exec_thread = threading.Thread(target=_executor_loop, daemon=True)
    _exec_thread.start()
    logger.info(
        "Inprocess executor started (concurrency=%s)", settings.sync_concurrency
    )

    # 3. Start the scheduler (recursive threading.Timer, first scan fires shortly)
    interval = settings.sync_interval_minutes * 60

    def _scheduler_scan() -> None:
        try:
            _do_schedule_scan()
        except Exception:
            logger.exception("scheduler scan error")
        finally:
            t = threading.Timer(interval, _scheduler_scan)
            t.daemon = True
            t.start()

    # Fire the first scan after a short delay so the app is fully ready
    _first_timer = threading.Timer(0.5, _scheduler_scan)
    _first_timer.daemon = True
    _first_timer.start()
    logger.info(
        "Inprocess scheduler started (interval=%s minutes)", settings.sync_interval_minutes
    )


def _cleanup_running_jobs(target_db=None) -> None:
    """将上次异常退出时处于 running 状态的 job 全部标记为 canceled。

    Args:
        target_db: 目标数据库实例（默认使用模块级 db）。
    """
    d = target_db if target_db is not None else db
    now = utc_iso()
    cur = d.execute(
        "UPDATE sync_jobs SET status = 'canceled', error = 'service restart', updated_at = ? WHERE status = 'running'",
        (now,),
    )
    if cur.rowcount:
        logger.info("Cleaned up %s running job(s) after restart", cur.rowcount)


def _do_schedule_scan() -> None:
    """单次扫描：为每个 sync_enabled=1 且尚无排队/运行中 job 的邮箱创建 scheduled job。"""
    accounts = db.query_all(
        "SELECT * FROM mailbox_accounts WHERE sync_enabled = 1"
    )
    created = 0
    for row in accounts:
        account = dict(row)
        existing = db.query_one(
            "SELECT id FROM sync_jobs WHERE mailbox_id = ? AND status IN ('queued', 'running')",
            (account["id"],),
        )
        if existing is None:
            create_job(
                db,
                account["user_id"],
                account["id"],
                trigger="scheduled",
                folder_roles=settings.sync_folders_default,
            )
            created += 1
    if created:
        logger.info("Scheduler created %s scheduled job(s)", created)


# ── remote sync background helpers ────────────────────────────────────────

def _start_remote_sync() -> None:
    """启动设备间远程同步后台线程。

    独立于邮箱同步调度器运行，每隔 ``sync_remote_interval_minutes`` 分钟
    对所有 enabled sync_peer 执行 push/pull 操作。
    """

    def _remote_sync_loop() -> None:
        interval = settings.sync_remote_interval_minutes * 60
        # brief initial wait so the app is fully ready
        time.sleep(2)
        while True:
            try:
                asyncio.run(run_remote_sync_cycle(db, settings))
            except Exception:
                logger.exception("remote sync cycle error")
            time.sleep(interval)

    _remote_thread = threading.Thread(target=_remote_sync_loop, daemon=True)
    _remote_thread.start()
    logger.info(
        "Remote sync background thread started (interval=%s minutes)",
        settings.sync_remote_interval_minutes,
    )


# ── FastAPI events ───────────────────────────────────────────────────────

@app.on_event("startup")
def startup() -> None:
    """FastAPI startup 事件：数据库初始化、同步调度器、远程同步、热更新、遥测。"""
    db.init()

    if settings.sync_mode == "inprocess":
        _start_inprocess_sync()

    _start_remote_sync()

    # ── Hot-reload watcher ────────────────────────────────────────────
    if settings.hot_reload_enabled:
        from app.services.hot_reload import start_watcher, watch_directory, reload_static_assets
        static_dir = Path(__file__).resolve().parent / "static"
        data_dir = Path(__file__).resolve().parent / "data"
        watch_directory(static_dir, reload_static_assets)
        watch_directory(data_dir, reload_static_assets)
        start_watcher(settings.hot_reload_interval_seconds)

    # ── 遥测后台刷新线程（每 30 分钟将内存队列中的事件写入数据库）
    _start_telemetry_flush()


@app.on_event("shutdown")
def shutdown() -> None:
    """FastAPI shutdown 事件：最后一次遥测 flush，避免事件丢失。"""
    from app.services.telemetry import flush
    try:
        flush(db, settings)
    except Exception:
        logger.exception("telemetry: final flush failed")


# ── telemetry background thread ───────────────────────────────────────────

_TELEMETRY_FLUSH_INTERVAL = 30 * 60  # every 30 minutes


def _start_telemetry_flush() -> None:
    """Launch a daemon thread that periodically flushes the telemetry queue."""

    def _telemetry_loop() -> None:
        from app.services.telemetry import flush
        while True:
            time.sleep(_TELEMETRY_FLUSH_INTERVAL)
            try:
                flush(db, settings)
            except Exception:
                logger.exception("telemetry: periodic flush error")

    _tele_thread = threading.Thread(target=_telemetry_loop, daemon=True)
    _tele_thread.start()
    logger.info("Telemetry background flush started (interval=%s minutes)", _TELEMETRY_FLUSH_INTERVAL // 60)


@app.get("/")
def index() -> FileResponse:
    """前端入口：返回 static/index.html。"""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    """健康检查端点，返回应用状态和名称。"""
    return {"status": "ok", "app": settings.app_name}
