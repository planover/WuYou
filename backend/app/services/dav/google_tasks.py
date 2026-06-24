"""Google Tasks API v1 sync engine.

Pulls tasklists and tasks from the Google Tasks API and upserts them
into the content_items table.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.core.database import Database
from app.core.security import utc_iso

logger = logging.getLogger(__name__)

TASKS_API_BASE = "https://tasks.googleapis.com/tasks/v1"


def _make_task_id(tasklist_id: str, task_id: str) -> str:
    """Build a unique sync_task_id from tasklist + task IDs."""
    return f"{tasklist_id}/{task_id}"


async def _fetch_tasklists(access_token: str, timeout: float = 15.0) -> list[dict[str, Any]]:
    """Fetch all tasklists for the authenticated user."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{TASKS_API_BASE}/users/@me/lists"

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url, headers=headers)

    if resp.status_code >= 400:
        logger.error("Google Tasks API tasklists error: %s %s", resp.status_code, resp.text[:500])
        return []

    data = resp.json()
    return data.get("items", [])


async def _fetch_tasks(
    tasklist_id: str, access_token: str, timeout: float = 15.0
) -> list[dict[str, Any]]:
    """Fetch all tasks (non-deleted) for a given tasklist."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{TASKS_API_BASE}/lists/{tasklist_id}/tasks"

    params = {
        "showCompleted": "true",
        "showHidden": "true",
    }

    all_items: list[dict[str, Any]] = []
    page_token: str | None = None

    async with httpx.AsyncClient(timeout=timeout) as client:
        while True:
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code >= 400:
                logger.error(
                    "Google Tasks API tasks error for %s: %s %s",
                    tasklist_id,
                    resp.status_code,
                    resp.text[:500],
                )
                break
            data = resp.json()
            items = data.get("items", [])
            all_items.extend(items)

            page_token = data.get("nextPageToken")
            if not page_token:
                break

    return all_items


def _task_to_content(
    task: dict[str, Any], tasklist_id: str
) -> dict[str, Any]:
    """Convert a Google Task API item dict to a content_items row fields."""
    task_id = task.get("id", "")
    sync_task_id = _make_task_id(tasklist_id, task_id)
    title = task.get("title", "")
    notes = task.get("notes", "")
    status = task.get("status", "needsAction")
    due = task.get("due", "")  # RFC 3339

    meta = {
        "sync_task_id": sync_task_id,
        "tasklist_id": tasklist_id,
        "task_id": task_id,
        "status": status,
        "due": due,
        "updated": task.get("updated", ""),
        "parent": task.get("parent", ""),
        "position": task.get("position", ""),
        "links": task.get("links", []),
    }

    return {
        "sync_task_id": sync_task_id,
        "title": title,
        "body": notes or "",
        "meta_json": json.dumps(meta, ensure_ascii=False),
    }


def _upsert_tasks(
    db: Database,
    user_id: int,
    account_id: int,
    tasks: list[dict[str, Any]],
) -> int:
    """Upsert tasks into content_items, dedup by sync_task_id."""
    now = utc_iso()
    synced = 0

    for task in tasks:
        sid = task["sync_task_id"]

        existing = db.query_one(
            """
            SELECT id, meta_json FROM content_items
            WHERE user_id = ? AND kind = 'task'
              AND json_extract(meta_json, '$.sync_task_id') = ?
            """,
            (user_id, sid),
        )

        if existing:
            # Check if the task was updated (compare meta_json.updated field)
            try:
                new_meta = json.loads(task["meta_json"])
                old_meta = json.loads(existing["meta_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                new_meta = {}
                old_meta = {}

            new_updated = new_meta.get("updated", "")
            old_updated = old_meta.get("updated", "")

            if new_updated and old_updated and new_updated <= old_updated:
                continue

            db.execute(
                """
                UPDATE content_items
                SET title = ?, body = ?, meta_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (task["title"], task["body"], task["meta_json"], now, existing["id"]),
            )
        else:
            db.execute(
                """
                INSERT INTO content_items(
                  user_id, mailbox_id, kind, title, body, meta_json, created_at, updated_at
                ) VALUES (?, ?, 'task', ?, ?, ?, ?, ?)
                """,
                (user_id, account_id, task["title"], task["body"], task["meta_json"], now, now),
            )
        synced += 1

    return synced


async def sync_google_tasks(
    db: Database,
    user_id: int,
    account_dict: dict[str, Any],
    access_token: str,
) -> dict[str, Any]:
    """Synchronize Google Tasks for a dav_account.

    Workflow:
    1. Fetch all tasklists via Google Tasks API v1
    2. For each tasklist, fetch all tasks (with pagination)
    3. Convert tasks to content_items format
    4. Upsert to content_items (dedup by sync_task_id)
    5. Update dav_accounts.last_sync_at

    Args:
        db: Database instance.
        user_id: The user's ID.
        account_dict: A dav_accounts row as a dict.
        access_token: OAuth2 access token for Google API.

    Returns:
        {"synced": N}
    """
    logger.info("Starting Google Tasks sync for user %s account %s", user_id, account_dict.get("id"))

    # 1. Fetch tasklists
    try:
        tasklists = await _fetch_tasklists(access_token)
    except Exception as exc:
        logger.exception("Google Tasks list fetch failed")
        db.execute(
            "UPDATE dav_accounts SET last_sync_status = ?, updated_at = ? WHERE id = ?",
            (f"fetch_error: {exc}", utc_iso(), int(account_dict["id"])),
        )
        return {"synced": 0}

    # 2. Fetch tasks for each tasklist
    all_task_dicts: list[dict[str, Any]] = []
    for tl in tasklists:
        tl_id = tl.get("id", "")
        if not tl_id:
            continue
        try:
            tasks = await _fetch_tasks(tl_id, access_token)
        except Exception as exc:
            logger.warning("Failed to fetch tasks for list %s: %s", tl_id, exc)
            continue

        for t in tasks:
            converted = _task_to_content(t, tl_id)
            all_task_dicts.append(converted)

    # 3-4. Upsert to content_items
    account_id = int(account_dict.get("id", 0))
    synced = _upsert_tasks(db, user_id, account_id, all_task_dicts)

    # 5. Update last_sync_at
    now = utc_iso()
    db.execute(
        "UPDATE dav_accounts SET last_sync_at = ?, last_sync_status = 'ok', updated_at = ? WHERE id = ?",
        (now, now, int(account_dict["id"])),
    )

    logger.info("Google Tasks sync complete: synced=%d", synced)
    return {"synced": synced}
