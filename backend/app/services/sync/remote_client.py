"""Remote sync client -- device-to-device sync engine (Task 5).

Provides:
- Low-level HTTP helpers (_login_remote, _pull_snapshot, _push_snapshot, _get_status)
- Full sync-cycle orchestrator (sync_with_peer)
- Background dispatcher (run_remote_sync_cycle)
- In-memory password store (store_sync_password / _get_stored_password)
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import Settings
from app.core.database import Database
from app.core.security import utc_iso
from app.services.sync.snapshot import build_full_snapshot, merge_snapshot

logger = logging.getLogger(__name__)

# ── In-memory password store ────────────────────────────────────────────

_sync_passwords: dict[int, str] = {}


def store_sync_password(user_id: int, password: str) -> None:
    """Store the remote sync password in memory for the given user."""
    _sync_passwords[user_id] = password


def _get_stored_password(user_id: int) -> str | None:
    """Retrieve the stored password for the given user."""
    return _sync_passwords.get(user_id)


# ── Low-level remote HTTP helpers ──────────────────────────────────────


async def _login_remote(url: str, username: str, password: str, timeout: int) -> str | None:
    """POST {url}/api/auth/login -- obtain a Bearer token.

    Returns the token string on success, or ``None`` on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.post(
                f"{url.rstrip('/')}/api/auth/login",
                json={"identifier": username, "password": password},
            )
            if resp.status_code == 200:
                data: dict = resp.json()
                return data.get("token")
            logger.warning(
                "Remote login failed (%s): HTTP %s %s",
                url,
                resp.status_code,
                resp.text[:300],
            )
            return None
    except Exception:
        logger.exception("Remote login error for %s", url)
        return None


async def _pull_snapshot(url: str, token: str, timeout: int) -> dict | None:
    """POST {url}/api/sync/remotes/pull -- fetch remote snapshot.

    Returns the full response dict on success, or ``None`` on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.post(
                f"{url.rstrip('/')}/api/sync/remotes/pull",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(
                "Remote pull failed (%s): HTTP %s %s",
                url,
                resp.status_code,
                resp.text[:300],
            )
            return None
    except Exception:
        logger.exception("Remote pull error for %s", url)
        return None


async def _push_snapshot(url: str, token: str, snapshot: dict, timeout: int) -> dict | None:
    """POST {url}/api/sync/remotes/push -- send local snapshot.

    Returns the response dict on success, or ``None`` on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.post(
                f"{url.rstrip('/')}/api/sync/remotes/push",
                json=snapshot,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(
                "Remote push failed (%s): HTTP %s %s",
                url,
                resp.status_code,
                resp.text[:300],
            )
            return None
    except Exception:
        logger.exception("Remote push error for %s", url)
        return None


async def _get_status(url: str, token: str, timeout: int) -> dict | None:
    """POST {url}/api/sync/remotes/status -- query remote-side summary.

    Returns the status dict on success, or ``None`` on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.post(
                f"{url.rstrip('/')}/api/sync/remotes/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(
                "Remote status failed (%s): HTTP %s %s",
                url,
                resp.status_code,
                resp.text[:300],
            )
            return None
    except Exception:
        logger.exception("Remote status error for %s", url)
        return None


# ── Sync cycle orchestrator ────────────────────────────────────────────


async def sync_with_peer(
    db: Database,
    settings: Settings,
    peer: dict,
    password: str,
) -> dict[str, Any]:
    """Run a complete sync cycle against a single remote peer.

    Steps
    -----
    1. Login to the remote peer
    2. Build a local full-snapshot and push it to the peer
    3. Pull the remote peer's snapshot
    4. Merge the remote snapshot into the local database
    5. Update ``last_sync_at`` / ``last_status`` on the sync_peers row

    Returns a result dict with keys ``peer_id``, ``label``, ``url``,
    ``status`` (``"ok"`` / ``"partial"`` / ``"error"``), ``push_result``,
    ``pull_result``, ``merge_summary``, ``merge_conflicts``, and ``error``.
    """
    url = peer["url"]
    username = peer["remote_username"]
    user_id = int(peer["user_id"])
    timeout = settings.request_timeout_seconds
    now = utc_iso()

    result: dict[str, Any] = {
        "peer_id": peer["id"],
        "label": peer.get("label", ""),
        "url": url,
        "status": "ok",
        "push_result": None,
        "pull_result": None,
        "merge_summary": None,
        "merge_conflicts": [],
        "error": None,
    }

    # ── 1. Login ──
    token = await _login_remote(url, username, password, timeout)
    if token is None:
        result["status"] = "error"
        result["error"] = "remote login failed"
        db.execute(
            "UPDATE sync_peers SET last_status = ?, last_sync_at = ?, updated_at = ? WHERE id = ?",
            ("login_failed", now, now, peer["id"]),
        )
        return result

    # ── 2. Build local snapshot and push ──
    try:
        local_snapshot = build_full_snapshot(db, user_id)
        # Wrap into the shape expected by the remote /api/sync/remotes/push endpoint
        push_payload: dict[str, Any] = {
            "snapshot_id": local_snapshot["snapshot_id"],
            "data": {
                "settings": local_snapshot.get("settings", []),
                "tags": local_snapshot.get("tags", []),
                "mailbox_accounts": local_snapshot.get("mailbox_accounts", []),
                "mailbox_folders": local_snapshot.get("folder_mappings", []),
                "installed_plugins": local_snapshot.get("installed_plugins", []),
                "content_items": local_snapshot.get("content_items", []),
            },
        }
        result["push_result"] = await _push_snapshot(url, token, push_payload, timeout)
    except Exception:
        logger.exception("Push to peer %s failed", peer["id"])

    # ── 3. Pull remote snapshot ──
    pull_resp = await _pull_snapshot(url, token, timeout)
    if pull_resp is None:
        result["status"] = "partial"
        result["error"] = (result["error"] or "") + "; pull failed"
        db.execute(
            "UPDATE sync_peers SET last_status = ?, last_sync_at = ?, updated_at = ? WHERE id = ?",
            ("pull_failed", now, now, peer["id"]),
        )
        return result

    result["pull_result"] = pull_resp

    # ── 4. Merge remote snapshot locally ──
    try:
        remote_data = pull_resp.get("data", {}) or {}
        incoming: dict[str, Any] = {
            "snapshot_id": pull_resp.get("snapshot_id", ""),
            "settings": remote_data.get("settings", []),
            "tags": remote_data.get("tags", []),
            "mailbox_accounts": remote_data.get("mailbox_accounts", []),
            "folder_mappings": remote_data.get("mailbox_folders", []),
            "installed_plugins": remote_data.get("installed_plugins", []),
            "content_items": remote_data.get("content_items", []),
        }
        summary, conflicts = merge_snapshot(db, user_id, incoming)
        result["merge_summary"] = summary
        result["merge_conflicts"] = conflicts
    except Exception as exc:
        logger.exception("Merge failed for peer %s", peer["id"])
        result["status"] = "partial"
        result["error"] = (result["error"] or "") + f"; merge failed: {exc}"
        db.execute(
            "UPDATE sync_peers SET last_status = ?, last_sync_at = ?, updated_at = ? WHERE id = ?",
            ("merge_failed", now, now, peer["id"]),
        )
        return result

    # ── 5. Mark sync successful ──
    db.execute(
        "UPDATE sync_peers SET last_status = ?, last_sync_at = ?, updated_at = ? WHERE id = ?",
        ("synced", now, now, peer["id"]),
    )

    return result


# ── Background dispatcher ──────────────────────────────────────────────


async def run_remote_sync_cycle(db: Database, settings: Settings, user_id: int | None = None) -> None:
    """Iterate over every enabled ``sync_peers`` row and run
    :func:`sync_with_peer` for each.

    Passwords are resolved from the in-memory ``_sync_passwords`` dict.
    Peers without a stored password are silently skipped.

    If ``user_id`` is provided, only peers belonging to that user are synced.
    """
    if user_id is not None:
        peers = db.query_all("SELECT * FROM sync_peers WHERE enabled = 1 AND user_id = ?", (user_id,))
    else:
        peers = db.query_all("SELECT * FROM sync_peers WHERE enabled = 1")
    if not peers:
        return

    for peer_row in peers:
        peer = dict(peer_row)
        user_id = int(peer["user_id"])
        password = _get_stored_password(user_id)
        if not password:
            logger.warning(
                "No sync password stored for user_id=%s, skipping peer %s (%s)",
                user_id,
                peer["id"],
                peer.get("label", ""),
            )
            continue

        try:
            result = await sync_with_peer(db, settings, peer, password)
            if result["status"] != "ok":
                logger.warning(
                    "Remote sync with peer %s (%s) completed with status=%s: %s",
                    peer["id"],
                    peer.get("label", ""),
                    result["status"],
                    result.get("error", ""),
                )
        except Exception:
            logger.exception("sync_with_peer crashed for peer %s", peer["id"])
