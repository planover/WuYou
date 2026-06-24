"""CardDAV sync engine.

Fetches contacts via CardDAV PROPFIND queries, parses vCard (.vcf) data,
and upserts them into the content_items table.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from xml.etree import ElementTree

import httpx

from app.core.database import Database
from app.core.security import utc_iso

logger = logging.getLogger(__name__)

# CardDAV XML namespaces
CARDDAV_NS = "urn:ietf:params:xml:ns:carddav"
D_NS = "DAV:"

PROPFIND_BODY = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:carddav">
  <D:prop>
    <D:getetag/>
    <C:address-data/>
  </D:prop>
</D:propfind>"""


def _parse_contact_from_vcard(
    vcard_text: str, href: str, etag: str
) -> dict[str, Any] | None:
    """Parse a single VCARD (vCard 3.0/4.0) and return a contact dict.

    Returns None on parse failure.  Uses simple line-by-line parsing
    for MVP to avoid external vcard-parsing dependency.
    """
    try:
        lines = vcard_text.strip().splitlines()
    except Exception:
        return None

    if not lines or not lines[0].strip().upper().startswith("BEGIN:VCARD"):
        return None

    uid = ""
    fn = ""
    email_val = ""
    tel = ""
    org = ""
    note = ""

    for raw_line in lines:
        line = raw_line.strip()
        if ":" not in line:
            continue
        # Handle folded lines (not folded in this simple parser)
        prop_name, _, prop_value = line.partition(":")
        prop_name = prop_name.upper().split(";")[0]  # strip parameters
        prop_value = prop_value.strip()

        if prop_name == "UID":
            uid = prop_value
        elif prop_name == "FN":
            fn = prop_value
        elif prop_name == "EMAIL" and not email_val:
            email_val = prop_value
        elif prop_name == "TEL" and not tel:
            tel = prop_value
        elif prop_name == "ORG":
            org = prop_value
        elif prop_name == "NOTE" and not note:
            note = prop_value

    if not uid:
        uid = fn or href

    if not fn:
        fn = uid

    meta = {
        "carddav_uid": uid,
        "sync_etag": etag,
        "href": href,
        "email": email_val,
        "tel": tel,
        "org": org,
    }

    return {
        "uid": uid,
        "etag": etag,
        "title": fn,
        "body": note,
        "meta_json": json.dumps(meta, ensure_ascii=False),
    }


async def _fetch_carddav_contacts(
    url: str, username: str, password: str, timeout: float = 30.0
) -> list[dict[str, Any]]:
    """Send a CardDAV PROPFIND and parse the multistatus XML response.

    Returns a list of parsed contact dicts.
    """
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.request(
            method="PROPFIND",
            url=url,
            content=PROPFIND_BODY,
            auth=(username, password),
            headers={
                "Content-Type": "application/xml; charset=utf-8",
                "Depth": "1",
            },
        )

    if resp.status_code >= 400:
        logger.error(
            "CardDAV PROPFIND failed: %s %s", resp.status_code, resp.text[:500]
        )
        return []

    # Parse XML multistatus response
    try:
        root = ElementTree.fromstring(resp.text)
    except ElementTree.ParseError as exc:
        logger.error("CardDAV XML parse error: %s", exc)
        return []

    contacts: list[dict[str, Any]] = []
    for response_el in root.iter(f"{{{D_NS}}}response"):
        href_el = response_el.find(f"{{{D_NS}}}href")
        if href_el is None:
            continue
        href = (href_el.text or "").strip()

        etag = ""
        vcard_text = ""

        for propstat in response_el.iter(f"{{{D_NS}}}propstat"):
            prop = propstat.find(f"{{{D_NS}}}prop")
            if prop is None:
                continue
            etag_el = prop.find(f"{{{D_NS}}}getetag")
            if etag_el is not None:
                etag = (etag_el.text or "").strip().strip('"')
            addrdata_el = prop.find(f"{{{CARDDAV_NS}}}address-data")
            if addrdata_el is not None and addrdata_el.text:
                vcard_text = addrdata_el.text

        if not vcard_text:
            continue

        parsed = _parse_contact_from_vcard(vcard_text, href, etag)
        if parsed:
            contacts.append(parsed)

    return contacts


def _upsert_contacts(
    db: Database,
    user_id: int,
    account_id: int,
    contacts: list[dict[str, Any]],
) -> int:
    """Insert or update contacts in content_items table.

    Deduplication is based on carddav_uid stored in meta_json.
    """
    now = utc_iso()
    synced = 0

    for contact in contacts:
        uid = contact["uid"]
        etag = contact["etag"]
        meta = json.loads(contact["meta_json"])

        existing = db.query_one(
            """
            SELECT id, meta_json FROM content_items
            WHERE user_id = ? AND kind = 'contact'
              AND json_extract(meta_json, '$.carddav_uid') = ?
            """,
            (user_id, uid),
        )

        if existing:
            try:
                old_meta = json.loads(existing["meta_json"] or "{}")
                old_etag = old_meta.get("sync_etag", "")
            except (json.JSONDecodeError, TypeError):
                old_etag = ""

            if old_etag == etag:
                continue

            db.execute(
                """
                UPDATE content_items
                SET title = ?, body = ?, meta_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (contact["title"], contact["body"], contact["meta_json"], now, existing["id"]),
            )
        else:
            db.execute(
                """
                INSERT INTO content_items(
                  user_id, mailbox_id, kind, title, body, meta_json, created_at, updated_at
                ) VALUES (?, ?, 'contact', ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    account_id,
                    contact["title"],
                    contact["body"],
                    contact["meta_json"],
                    now,
                    now,
                ),
            )
        synced += 1

    return synced


async def sync_carddav(
    db: Database,
    user_id: int,
    account_dict: dict[str, Any],
    password: str,
) -> dict[str, Any]:
    """Synchronize CardDAV contacts for a dav_account.

    Workflow:
    1. Send CardDAV PROPFIND query
    2. Parse returned XML for .vcf data
    3. Parse each .vcf to extract contact details
    4. INSERT OR UPDATE content_items (dedup by carddav_uid + sync_etag)
    5. Update dav_accounts.last_sync_at

    Args:
        db: Database instance.
        user_id: The user's ID.
        account_dict: A dav_accounts row as a dict.
        password: The decrypted password for CardDAV auth.

    Returns:
        {"synced": N}
    """
    url = account_dict.get("url", "")
    username = account_dict.get("username", "")

    if not url:
        logger.warning("No CardDAV URL configured for account %s", account_dict.get("id"))
        return {"synced": 0}

    logger.info("Starting CardDAV sync for user %s account %s", user_id, account_dict.get("id"))

    # 1-2. Fetch and parse contacts
    try:
        contacts = await _fetch_carddav_contacts(url, username, password)
    except Exception as exc:
        logger.exception("CardDAV fetch failed for account %s", account_dict.get("id"))
        db.execute(
            "UPDATE dav_accounts SET last_sync_status = ?, updated_at = ? WHERE id = ?",
            (f"fetch_error: {exc}", utc_iso(), int(account_dict["id"])),
        )
        return {"synced": 0}

    # 3-4. Upsert contacts
    account_id = int(account_dict.get("id", 0))
    synced = _upsert_contacts(db, user_id, account_id, contacts)

    # 5. Update last_sync_at
    now = utc_iso()
    db.execute(
        "UPDATE dav_accounts SET last_sync_at = ?, last_sync_status = 'ok', updated_at = ? WHERE id = ?",
        (now, now, int(account_dict["id"])),
    )

    logger.info("CardDAV sync complete: synced=%d", synced)
    return {"synced": synced}
