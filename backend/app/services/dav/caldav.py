"""CalDAV sync engine.

Fetches calendar events via CalDAV REPORT queries, parses iCalendar (.ics)
data, and upserts them into the content_items table.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree

import httpx
from icalendar import Calendar

from app.core.database import Database
from app.core.security import utc_iso

logger = logging.getLogger(__name__)

# CalDAV XML namespaces used in REPORT requests/responses
CALDAV_NS = "urn:ietf:params:xml:ns:caldav"
D_NS = "DAV:"

CALDAV_REPORT_BODY = """<?xml version="1.0" encoding="utf-8"?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <D:getetag/>
    <C:calendar-data/>
  </D:prop>
  <C:filter>
    <C:comp-filter name="VCALENDAR">
      <C:comp-filter name="VEVENT">
        <C:time-range start="{start}" end="{end}"/>
      </C:comp-filter>
    </C:comp-filter>
  </C:filter>
</C:calendar-query>"""


def _build_timerange() -> tuple[str, str]:
    """Build ISO-8601 UTC time range: past 30 days to future 90 days."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=30)).strftime("%Y%m%dT000000Z")
    end = (now + timedelta(days=90)).strftime("%Y%m%dT235959Z")
    return start, end


def _parse_event_from_ics(
    ics_data: str, href: str, etag: str
) -> dict[str, Any] | None:
    """Parse a single VEVENT from iCalendar data.

    Returns a dict with keys: uid, etag, href, title, body, dtstart,
    dtend, location, meta_json.  Returns None on parse failure.
    """
    try:
        cal = Calendar.from_ical(ics_data)
    except Exception:
        logger.warning("Failed to parse iCalendar data for %s", href)
        return None

    for component in cal.walk("VEVENT"):
        uid = str(component.get("uid", ""))
        if not uid:
            uid = href  # fallback

        summary = str(component.get("summary", ""))
        description = str(component.get("description", ""))
        location = str(component.get("location", ""))

        # Normalize datetime fields to ISO-8601 strings
        dtstart = _dt_to_iso(component.get("dtstart"))
        dtend = _dt_to_iso(component.get("dtend"))

        meta = {
            "caldav_uid": uid,
            "sync_etag": etag,
            "href": href,
            "dtstart": dtstart,
            "dtend": dtend,
            "location": location,
            "status": str(component.get("status", "")),
            "categories": str(component.get("categories", "")),
        }

        return {
            "uid": uid,
            "etag": etag,
            "href": href,
            "title": summary,
            "body": description,
            "meta_json": json.dumps(meta, ensure_ascii=False),
            "dtstart": dtstart,
            "dtend": dtend,
        }
    return None


def _dt_to_iso(dt) -> str | None:
    """Convert an icalendar datetime/date to ISO-8601 string."""
    if dt is None:
        return None
    try:
        dt_val = dt.dt
        if isinstance(dt_val, datetime):
            if dt_val.tzinfo is None:
                dt_val = dt_val.replace(tzinfo=timezone.utc)
            return dt_val.isoformat()
        # It's a date (no time component)
        return dt_val.isoformat()
    except Exception:
        return str(dt)


async def _fetch_caldav_events(
    url: str, username: str, password: str, timeout: float = 30.0
) -> list[dict[str, Any]]:
    """Send a CalDAV REPORT and parse the multistatus XML response.

    Returns a list of parsed event dicts.
    """
    start, end = _build_timerange()
    body = CALDAV_REPORT_BODY.format(start=start, end=end)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.request(
            method="REPORT",
            url=url,
            content=body,
            auth=(username, password),
            headers={
                "Content-Type": "application/xml; charset=utf-8",
                "Depth": "1",
            },
        )

    if resp.status_code >= 400:
        logger.error("CalDAV REPORT failed: %s %s", resp.status_code, resp.text[:500])
        return []

    # Parse XML multistatus response
    try:
        root = ElementTree.fromstring(resp.text)
    except ElementTree.ParseError as exc:
        logger.error("CalDAV XML parse error: %s", exc)
        return []

    events: list[dict[str, Any]] = []
    for response_el in root.iter(f"{{{D_NS}}}response"):
        href_el = response_el.find(f"{{{D_NS}}}href")
        if href_el is None:
            continue
        href = (href_el.text or "").strip()

        etag = ""
        ics_text = ""

        # MVP 简化：跳过 <D:status> 检查。生产环境应验证 propstat status 为 200 OK
        for propstat in response_el.iter(f"{{{D_NS}}}propstat"):
            prop = propstat.find(f"{{{D_NS}}}prop")
            if prop is None:
                continue
            etag_el = prop.find(f"{{{D_NS}}}getetag")
            if etag_el is not None:
                etag = (etag_el.text or "").strip().strip('"')
            caldata_el = prop.find(f"{{{CALDAV_NS}}}calendar-data")
            if caldata_el is not None and caldata_el.text:
                ics_text = caldata_el.text

        if not ics_text:
            continue

        parsed = _parse_event_from_ics(ics_text, href, etag)
        if parsed:
            events.append(parsed)

    return events


def _upsert_events(
    db: Database,
    user_id: int,
    account_id: int,
    events: list[dict[str, Any]],
) -> int:
    """Insert or update events in content_items table.

    Deduplication is based on caldav_uid + sync_etag stored in meta_json.
    A simple approach: for each event, check if a row with matching
    caldav_uid exists; if yes and etag changed, UPDATE; else INSERT.

    Returns the number of synced (inserted/updated) items.
    """
    now = utc_iso()
    synced = 0

    for event in events:
        uid = event["uid"]
        etag = event["etag"]
        meta = json.loads(event["meta_json"])

        # Look up existing item by caldav_uid in meta_json
        existing = db.query_one(
            """
            SELECT id, meta_json FROM content_items
            WHERE user_id = ? AND kind = 'event'
              AND json_extract(meta_json, '$.caldav_uid') = ?
            """,
            (user_id, uid),
        )

        if existing:
            # Check if etag changed
            try:
                old_meta = json.loads(existing["meta_json"] or "{}")
                old_etag = old_meta.get("sync_etag", "")
            except (json.JSONDecodeError, TypeError):
                old_etag = ""

            if old_etag == etag:
                continue  # unchanged, skip

            # Update
            db.execute(
                """
                UPDATE content_items
                SET title = ?, body = ?, meta_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (event["title"], event["body"], event["meta_json"], now, existing["id"]),
            )
        else:
            # Insert
            db.execute(
                """
                INSERT INTO content_items(
                  user_id, mailbox_id, kind, title, body, meta_json, created_at, updated_at
                ) VALUES (?, ?, 'event', ?, ?, ?, ?, ?)
                """,
                (user_id, account_id, event["title"], event["body"], event["meta_json"], now, now),
            )
        synced += 1

    return synced


async def sync_caldav(
    db: Database,
    user_id: int,
    account_dict: dict[str, Any],
    password: str,
) -> dict[str, Any]:
    """Synchronize CalDAV events for a dav_account.

    Workflow:
    1. Send CalDAV REPORT query (past 30 days to future 90 days)
    2. Parse returned XML for .ics data
    3. Parse each .ics to extract VEVENT details
    4. INSERT OR UPDATE content_items (dedup by caldav_uid + sync_etag)
    5. Update dav_accounts.last_sync_at

    Args:
        db: Database instance.
        user_id: The user's ID.
        account_dict: A dav_accounts row as a dict.
        password: The decrypted password for CalDAV auth.

    Returns:
        {"synced": N}
    """
    url = account_dict.get("url", "")
    username = account_dict.get("username", "")

    if not url:
        logger.warning("No CalDAV URL configured for account %s", account_dict.get("id"))
        return {"synced": 0}

    logger.info("Starting CalDAV sync for user %s account %s", user_id, account_dict.get("id"))

    # 1-2. Fetch and parse events
    try:
        events = await _fetch_caldav_events(url, username, password)
    except Exception as exc:
        logger.exception("CalDAV fetch failed for account %s", account_dict.get("id"))
        db.execute(
            "UPDATE dav_accounts SET last_sync_status = ?, updated_at = ? WHERE id = ?",
            (f"fetch_error: {exc}", utc_iso(), int(account_dict["id"])),
        )
        return {"synced": 0}

    # 3-4. Upsert events
    account_id = int(account_dict.get("id", 0))
    synced = _upsert_events(db, user_id, account_id, events)

    # 5. Update last_sync_at
    now = utc_iso()
    db.execute(
        "UPDATE dav_accounts SET last_sync_at = ?, last_sync_status = 'ok', updated_at = ? WHERE id = ?",
        (now, now, int(account_dict["id"])),
    )

    logger.info("CalDAV sync complete: synced=%d", synced)
    return {"synced": synced}
