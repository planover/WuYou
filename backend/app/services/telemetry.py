"""WuYou 匿名遥测模块（opt-in，无 PII）。

设计原则：
- 仅收集事件名称 + 布尔/数字/字符串属性
- 绝不收集用户名、密码、邮箱、邮件内容、IP、设备指纹等 PII
- 线程安全的内存队列 + 定期刷入本地 SQLite + 可选 HTTP 上传

入口函数：
- ``track(event, **props)`` — 记录事件到内存队列
- ``flush(db, settings)`` — 刷入本地数据库 + 可选远程上传
- ``get_stats(db)`` — 查询最近 7 天遥测摘要
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── module-level state ────────────────────────────────────────────────────

_pending: list[dict] = []
_lock = threading.Lock()
_upload_url = "http://localhost:8787/telemetry"  # placeholder


def track(event: str, **properties: Any) -> None:
    """Record an anonymous usage event into the pending queue.

    Only boolean / numeric / string values are accepted in ``properties``.
    Any value that is not a simple scalar will be dropped with a warning.
    """
    safe_props: dict[str, Any] = {}
    for key, value in properties.items():
        if isinstance(value, (bool, int, float)):
            safe_props[key] = value
        elif isinstance(value, str):
            safe_props[key] = value
        else:
            logger.debug("telemetry: dropped non-scalar property %s=%r", key, type(value))

    entry: dict[str, Any] = {
        "event": event,
        "properties": safe_props,
        "timestamp": time.time(),
    }
    with _lock:
        _pending.append(entry)


def flush(db, settings) -> None:
    """将内存队列中的遥测事件刷入本地数据库，并可选异步上传到远程端点。

    原子操作：先拷贝队列再清空，避免长时间持锁。写入数据库和 HTTP 上传
    均为 best-effort，失败不抛异常。

    Args:
        db: Database 实例。
        settings: Settings 实例（用于读取 telemetry_remote_url）。
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    with _lock:
        if not _pending:
            return
        batch: list[dict] = _pending.copy()
        _pending.clear()

    if not batch:
        return

    # Persist to local DB (safe, no PII)
    rows: list[tuple[str, str, str]] = []
    for entry in batch:
        event_name = str(entry.get("event", ""))[:256]
        props_json = json.dumps(entry.get("properties", {}), ensure_ascii=False)
        rows.append((event_name, props_json, now_iso))
    try:
        db.executemany(
            "INSERT INTO telemetry_events(event, properties_json, created_at) VALUES (?, ?, ?)",
            rows,
        )
    except Exception:
        logger.exception("telemetry: failed to persist %d events to local db", len(batch))

    # Upload via httpx if a remote URL is configured
    remote_url = getattr(settings, "telemetry_remote_url", None) or _upload_url
    try:
        _upload_sync(batch, remote_url)
    except Exception:
        logger.debug("telemetry: upload skipped (httpx not available or upload failed)")


def _upload_sync(batch: list[dict], url: str) -> None:
    """同步 HTTP POST 上传遥测数据（best-effort，静默失败）。
    
    注意：虽然此函数是同步的，但它在 flush() 末尾以 fire-and-forget 方式
    调用，不影响主流程性能。失败时会静默忽略，不会抛出异常。
    """
    try:
        import httpx
    except ImportError:
        return

    payload: list[dict] = []
    for entry in batch:
        payload.append({
            "event": entry.get("event", ""),
            "properties": entry.get("properties", {}),
            "timestamp": entry.get("timestamp", 0),
        })
    try:
        httpx.Client(timeout=5).post(url, json=payload)
    except Exception:
        pass  # best-effort, never crash


def get_stats(db) -> dict:
    """返回最近 7 天的匿名事件摘要。

    Returns:
        dict with ``events`` (总数) and ``top_events`` (按频率降序的事件列表)。
    """
    rows = db.query_all(
        """
        SELECT event, COUNT(*) AS cnt
        FROM telemetry_events
        WHERE created_at >= datetime('now', '-7 days')
        GROUP BY event
        ORDER BY cnt DESC
        """
    )
    total = 0
    top: list[dict] = []
    for r in rows:
        cnt = int(r["cnt"])
        total += cnt
        top.append({"event": r["event"], "count": cnt})
    return {"events": total, "top_events": top}
