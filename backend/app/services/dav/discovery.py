"""CalDAV / CardDAV URL discovery via known providers + .well-known.

Uses httpx.AsyncClient for async HTTP requests.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# ── Known provider CalDAV URLs ──────────────────────────────────────────
KNOWN_CALDAV: dict[str, str] = {
    "gmail": "https://apidata.googleusercontent.com/caldav/v2/{email}/events/",
    "googlemail": "https://apidata.googleusercontent.com/caldav/v2/{email}/events/",
    "icloud": "https://caldav.icloud.com/",
    "me": "https://caldav.icloud.com/",
    "mac": "https://caldav.icloud.com/",
    "outlook": "https://outlook.office365.com/caldav/",
    "hotmail": "https://outlook.office365.com/caldav/",
    "live": "https://outlook.office365.com/caldav/",
    "qq": "https://caldav.qq.com/",
    "foxmail": "https://caldav.qq.com/",
    "yahoo": "https://caldav.calendar.yahoo.com/",
    "zoho": "https://calendar.zoho.com/caldav/",
}

# ── Known provider CardDAV URLs ─────────────────────────────────────────
KNOWN_CARDDAV: dict[str, str] = {
    "gmail": "https://www.googleapis.com/carddav/v1/principals/{email}/lists/default/",
    "googlemail": "https://www.googleapis.com/carddav/v1/principals/{email}/lists/default/",
    "icloud": "https://contacts.icloud.com/",
    "me": "https://contacts.icloud.com/",
    "mac": "https://contacts.icloud.com/",
    "yahoo": "https://carddav.address.yahoo.com/",
    "zoho": "https://contacts.zoho.com/carddav/",
}


def _extract_domain(email: str) -> str:
    """Extract the domain part from an email address, lowercased."""
    return email.rsplit("@", 1)[-1].lower().strip()


def _extract_provider_key(domain: str) -> str | None:
    """Map a domain to a known provider key.

    Returns the first part of the domain (e.g. "gmail" from "gmail.com")
    or an alias mapping.
    """
    # Direct subdomain matches
    for known in KNOWN_CALDAV:
        if domain == f"{known}.com" or domain.endswith(f".{known}.com"):
            return known
    # Try prefix of the domain (e.g. "gmail.com" -> "gmail")
    prefix = domain.split(".")[0]
    if prefix in KNOWN_CALDAV:
        return prefix
    return None


def _resolve_caldav_url_from_provider(email: str) -> str | None:
    """Resolve a CalDAV URL from a known provider dict for the given email."""
    domain = _extract_domain(email)
    key = _extract_provider_key(domain)
    if key and key in KNOWN_CALDAV:
        return KNOWN_CALDAV[key].format(email=email)
    return None


def _resolve_carddav_url_from_provider(email: str) -> str | None:
    """Resolve a CardDAV URL from a known provider dict for the given email."""
    domain = _extract_domain(email)
    key = _extract_provider_key(domain)
    if key and key in KNOWN_CARDDAV:
        return KNOWN_CARDDAV[key].format(email=email)
    return None


async def _try_well_known(domain: str, path: str, timeout: float = 5.0) -> str | None:
    """Try .well-known/{path} discovery for both https and http.

    Returns the first non-4xx/5xx URL, or None.
    """
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for scheme in ("https", "http"):
            url = f"{scheme}://{domain}/.well-known/{path}"
            try:
                resp = await client.get(url)
                if resp.status_code < 400:
                    # The response might redirect or return a URL in body
                    location = resp.headers.get("location")
                    if location:
                        return location
                    # Some servers return the principal URL in the body
                    # For MVP, just return the URL itself as a success signal
                    return url
            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
                continue
    return None


async def discover_caldav_url(email: str) -> str | None:
    """Discover CalDAV URL for the given email address.

    Priority:
    1. Known provider dict (gmail→Google, icloud/me/mac→iCloud,
       outlook/hotmail/live→Exchange, qq/foxmail→QQ, yahoo→Yahoo, zoho→Zoho)
    2. .well-known/caldav (https then http)
    3. Return None

    Args:
        email: The user's email address.

    Returns:
        The discovered CalDAV URL, or None.
    """
    # 1. Try known provider
    url = _resolve_caldav_url_from_provider(email)
    if url:
        logger.debug("CalDAV URL resolved from known provider for %s: %s", email, url)
        return url

    # 2. Try .well-known/caldav
    domain = _extract_domain(email)
    url = await _try_well_known(domain, "caldav")
    if url:
        logger.debug("CalDAV URL discovered via .well-known for %s: %s", email, url)
        return url

    logger.debug("No CalDAV URL discovered for %s", email)
    return None


async def discover_carddav_url(email: str) -> str | None:
    """Discover CardDAV URL for the given email address.

    Priority:
    1. Known provider dict (gmail→Google CardDAV, icloud→iCloud CardDAV)
    2. .well-known/carddav (https then http)
    3. Return None

    Args:
        email: The user's email address.

    Returns:
        The discovered CardDAV URL, or None.
    """
    # 1. Try known provider
    url = _resolve_carddav_url_from_provider(email)
    if url:
        logger.debug("CardDAV URL resolved from known provider for %s: %s", email, url)
        return url

    # 2. Try .well-known/carddav
    domain = _extract_domain(email)
    url = await _try_well_known(domain, "carddav")
    if url:
        logger.debug("CardDAV URL discovered via .well-known for %s: %s", email, url)
        return url

    logger.debug("No CardDAV URL discovered for %s", email)
    return None
