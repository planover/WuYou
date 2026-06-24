"""Microsoft Graph API sync engine.

Pulls events, contacts, and tasks from Microsoft Graph and upserts them
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

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# ── Event helpers ───────────────────────────────────────────────────────


async def _fetch_graph_events(access_token: str, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Fetch calendar events from MS Graph /me/events."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{GRAPH_API_BASE}/me/events"
    # Consider top-level properties only
    params = {
        "$top": 200,
        "$orderby": "lastModifiedDateTime desc",
        "$select": "id,subject,bodyPreview,body,start,end,location,lastModifiedDateTime",
    }

    all_events: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=timeout) as client:
        while url:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code >= 400:
                logger.error("MS Graph events error: %s %s", resp.status_code, resp.text[:500])
                break
            data = resp.json()
            items = data.get("value", [])
            all_events.extend(items)
            url = data.get("@odata.nextLink", "")
            params = {}  # nextLink includes params

    return all_events


def _graph_event_to_content(event: dict[str, Any]) -> dict[str, Any]:
    """Convert an MS Graph event item to a content_items row dict."""
    event_id = event.get("id", "")
    subject = event.get("subject", "")
    body_content = ""
    body_obj = event.get("body", {})
    if isinstance(body_obj, dict):
        body_content = body_obj.get("content", "")
    body_preview = event.get("bodyPreview", "")

    start_dt = ""
    start_obj = event.get("start", {})
    if isinstance(start_obj, dict):
        start_dt = start_obj.get("dateTime", "")

    end_dt = ""
    end_obj = event.get("end", {})
    if isinstance(end_obj, dict):
        end_dt = end_obj.get("dateTime", "")

    location_name = ""
    loc_obj = event.get("location", {})
    if isinstance(loc_obj, dict):
        location_name = loc_obj.get("displayName", "")

    meta = {
        "graph_id": event_id,
        "type": "event",
        "start": start_dt,
        "end": end_dt,
        "location": location_name,
        "lastModifiedDateTime": event.get("lastModifiedDateTime", ""),
    }

    return {
        "kind": "event",
        "uid": f"graph_event:{event_id}",
        "title": subject,
        "body": body_content or body_preview,
        "meta_json": json.dumps(meta, ensure_ascii=False),
        "updated": event.get("lastModifiedDateTime", ""),
    }


# ── Contact helpers ─────────────────────────────────────────────────────


async def _fetch_graph_contacts(access_token: str, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Fetch contacts from MS Graph /me/contacts."""
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"{GRAPH_API_BASE}/me/contacts"
    params = {
        "$top": 500,
        "$select": "id,displayName,givenName,surname,emailAddresses,businessPhones,"
                   "mobilePhone,companyName,jobTitle,lastModifiedDateTime",
    }

    all_contacts: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=timeout) as client:
        while url:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code >= 400:
                logger.error("MS Graph contacts error: %s %s", resp.status_code, resp.text[:500])
                break
            data = resp.json()
            items = data.get("value", [])
            all_contacts.extend(items)
            url = data.get("@odata.nextLink", "")
            params = {}

    return all_contacts


def _graph_contact_to_content(contact: dict[str, Any]) -> dict[str, Any]:
    """Convert an MS Graph contact item to a content_items row dict."""
    contact_id = contact.get("id", "")
    display_name = contact.get("displayName", "")
    given_name = contact.get("givenName", "")
    surname = contact.get("surname", "")

    emails = []
    for email_obj in contact.get("emailAddresses", []) or []:
        if isinstance(email_obj, dict):
            emails.append(email_obj.get("address", ""))

    phones = (contact.get("businessPhones") or []) + [contact.get("mobilePhone", "") or ""]
    phones = [p for p in phones if p]

    meta = {
        "graph_id": contact_id,
        "type": "contact",
        "givenName": given_name,
        "surname": surname,
        "emails": emails,
        "phones": phones,
        "companyName": contact.get("companyName", ""),
        "jobTitle": contact.get("jobTitle", ""),
        "lastModifiedDateTime": contact.get("lastModifiedDateTime", ""),
    }

    return {
        "kind": "contact",
        "uid": f"graph_contact:{contact_id}",
        "title": display_name or f"{given_name} {surname}".strip(),
        "body": "",
        "meta_json": json.dumps(meta, ensure_ascii=False),
        "updated": contact.get("lastModifiedDateTime", ""),
    }


# ── Task helpers ────────────────────────────────────────────────────────


async def _fetch_graph_tasks(access_token: str, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Fetch tasks from MS Graph /me/planner/tasks or /me/todo/lists/tasks.

    Uses Microsoft To Do (todo) API as the modern task endpoint.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    # First get task lists
    lists_url = f"{GRAPH_API_BASE}/me/todo/lists"

    all_tasks: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=timeout) as client:
        # Fetch lists
        resp = await client.get(lists_url, headers=headers)
        if resp.status_code >= 400:
            logger.error("MS Graph todo lists error: %s %s", resp.status_code, resp.text[:500])
            return []

        lists_data = resp.json()
        task_lists = lists_data.get("value", [])

        for tl in task_lists:
            list_id = tl.get("id", "")
            list_name = tl.get("displayName", "")
            tasks_url = f"{GRAPH_API_BASE}/me/todo/lists/{list_id}/tasks"
            params = {"$top": 200}

            while tasks_url:
                tasks_resp = await client.get(tasks_url, headers=headers, params=params)
                if tasks_resp.status_code >= 400:
                    logger.error(
                        "MS Graph tasks error for list %s: %s %s",
                        list_id,
                        tasks_resp.status_code,
                        tasks_resp.text[:500],
                    )
                    break
                tasks_data = tasks_resp.json()
                items = tasks_data.get("value", [])

                for item in items:
                    item["_list_id"] = list_id
                    item["_list_name"] = list_name
                    all_tasks.append(item)

                tasks_url = tasks_data.get("@odata.nextLink", "")
                params = {}

    return all_tasks


def _graph_task_to_content(task: dict[str, Any]) -> dict[str, Any]:
    """Convert an MS Graph todo task item to a content_items row dict."""
    task_id = task.get("id", "")
    title = task.get("title", "")
    body_content = ""
    body_obj = task.get("body", {})
    if isinstance(body_obj, dict):
        body_content = body_obj.get("content", "")

    status = task.get("status", "notStarted")
    importance = task.get("importance", "normal")
    due_dt = ""
    due_obj = task.get("dueDateTime", {})
    if isinstance(due_obj, dict):
        due_dt = f"{due_obj.get('dateTime','')} {due_obj.get('timeZone','')}".strip()

    meta = {
        "sync_task_id": f"ms_graph:{task.get('_list_id','')}/{task_id}",
        "graph_id": task_id,
        "list_id": task.get("_list_id", ""),
        "list_name": task.get("_list_name", ""),
        "type": "task",
        "status": status,
        "importance": importance,
        "due": due_dt,
        "lastModifiedDateTime": task.get("lastModifiedDateTime", ""),
    }

    return {
        "kind": "task",
        "uid": f"graph_task:{task_id}",
        "title": title,
        "body": body_content,
        "meta_json": json.dumps(meta, ensure_ascii=False),
        "updated": task.get("lastModifiedDateTime", ""),
    }


# ── Upsert helpers ──────────────────────────────────────────────────────


def _upsert_graph_items(
    db: Database,
    user_id: int,
    account_id: int,
    items: list[dict[str, Any]],
) -> dict[str, int]:
    """Upsert items into content_items, one item at a time.

    Each item dict must have 'kind', 'title', 'body', 'meta_json', 'uid'.

    Returns dict of {event_count, contact_count, task_count}.
    """
    now = utc_iso()
    counts = {"event": 0, "contact": 0, "task": 0}

    for item in items:
        kind = item.get("kind", "event")
        uid = item.get("uid", "")
        updated = item.get("updated", "")

        existing = db.query_one(
            """
            SELECT id, meta_json FROM content_items
            WHERE user_id = ? AND kind = ? AND json_extract(meta_json, '$.graph_id') = ?
            """,
            (user_id, kind, uid.split(":", 1)[-1] if uid.startswith("graph_") else uid),
        )

        if existing:
            try:
                old_meta = json.loads(existing["meta_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                old_meta = {}

            old_updated = old_meta.get("lastModifiedDateTime", "")
            if updated and old_updated and updated <= old_updated:
                continue

            db.execute(
                """
                UPDATE content_items
                SET title = ?, body = ?, meta_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (item["title"], item["body"], item["meta_json"], now, existing["id"]),
            )
        else:
            db.execute(
                """
                INSERT INTO content_items(
                  user_id, mailbox_id, kind, title, body, meta_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, account_id, kind, item["title"], item["body"], item["meta_json"], now, now),
            )
        counts[kind] = counts.get(kind, 0) + 1

    return counts


# ── Main sync entry ─────────────────────────────────────────────────────


async def sync_ms_graph(
    db: Database,
    user_id: int,
    account_dict: dict[str, Any],
    access_token: str,
) -> dict[str, Any]:
    """Synchronize Microsoft Graph events, contacts, and tasks.

    Workflow:
    1. Fetch /me/events
    2. Fetch /me/contacts
    3. Fetch /me/todo/lists/{id}/tasks
    4. Convert to content_items format
    5. Upsert to content_items table
    6. Update dav_accounts.last_sync_at

    Args:
        db: Database instance.
        user_id: The user's ID.
        account_dict: A dav_accounts row as a dict.
        access_token: OAuth2 access token for MS Graph.

    Returns:
        {"events": N, "contacts": M, "tasks": K}
    """
    logger.info("Starting MS Graph sync for user %s account %s", user_id, account_dict.get("id"))
    account_id = int(account_dict.get("id", 0))

    all_items: list[dict[str, Any]] = []
    fetch_errors: list[str] = []

    # 1. Fetch events
    try:
        events = await _fetch_graph_events(access_token)
        for ev in events:
            item = _graph_event_to_content(ev)
            all_items.append(item)
    except Exception as exc:
        msg = f"events: {exc}"
        fetch_errors.append(msg)
        logger.exception("MS Graph event fetch failed")

    # 2. Fetch contacts
    try:
        contacts = await _fetch_graph_contacts(access_token)
        for ct in contacts:
            item = _graph_contact_to_content(ct)
            all_items.append(item)
    except Exception as exc:
        msg = f"contacts: {exc}"
        fetch_errors.append(msg)
        logger.exception("MS Graph contact fetch failed")

    # 3. Fetch tasks
    try:
        tasks = await _fetch_graph_tasks(access_token)
        for tk in tasks:
            item = _graph_task_to_content(tk)
            all_items.append(item)
    except Exception as exc:
        msg = f"tasks: {exc}"
        fetch_errors.append(msg)
        logger.exception("MS Graph task fetch failed")

    # 4-5. Upsert
    counts = _upsert_graph_items(db, user_id, account_id, all_items)

    # 6. Update last_sync_at
    status = "ok"
    if fetch_errors:
        status = f"partial: {'; '.join(fetch_errors)}"

    now = utc_iso()
    db.execute(
        "UPDATE dav_accounts SET last_sync_at = ?, last_sync_status = ?, updated_at = ? WHERE id = ?",
        (now, status, now, int(account_dict["id"])),
    )

    logger.info(
        "MS Graph sync complete: events=%d contacts=%d tasks=%d",
        counts.get("event", 0),
        counts.get("contact", 0),
        counts.get("task", 0),
    )
    return {
        "events": counts.get("event", 0),
        "contacts": counts.get("contact", 0),
        "tasks": counts.get("task", 0),
    }
