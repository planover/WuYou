"""Tests for CalDAV/CardDAV URL discovery module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.dav.discovery import (
    discover_caldav_url,
    discover_carddav_url,
    _extract_domain,
    _extract_provider_key,
)


# ── Unit tests for helpers ──────────────────────────────────────────────

def test_extract_domain():
    assert _extract_domain("user@gmail.com") == "gmail.com"
    assert _extract_domain("test@icloud.com") == "icloud.com"
    assert _extract_domain("admin@sub.example.co.uk") == "sub.example.co.uk"


def test_extract_provider_key_gmail():
    assert _extract_provider_key("gmail.com") == "gmail"


def test_extract_provider_key_icloud():
    assert _extract_provider_key("icloud.com") == "icloud"


# ── Async tests: discover_caldav_url ────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_gmail_returns_google_url():
    """Gmail addresses should return the Google CalDAV URL."""
    email = "user@gmail.com"
    url = await discover_caldav_url(email)
    assert url is not None
    assert "apidata.googleusercontent.com/caldav/v2/" in url
    assert email in url


@pytest.mark.asyncio
async def test_discover_icloud_returns_icloud_url():
    """iCloud addresses should return the iCloud CalDAV URL."""
    email = "someone@icloud.com"
    url = await discover_caldav_url(email)
    assert url is not None
    assert "caldav.icloud.com" in url


@pytest.mark.asyncio
async def test_discover_unknown_returns_none():
    """An unknown/random domain with no .well-known should return None."""
    email = "user@totally-fake-domain-xyz123.example"
    # Mock httpx.AsyncClient so that .well-known requests fail
    with patch("app.services.dav.discovery.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        # Simulate 404 for .well-known/caldav
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_client.get = AsyncMock(return_value=mock_resp)

        mock_client_cls.return_value = mock_client

        url = await discover_caldav_url(email)
        assert url is None


@pytest.mark.asyncio
async def test_discover_outlook_returns_exchange_url():
    """Outlook addresses should return the Exchange/Office365 CalDAV URL."""
    email = "user@outlook.com"
    url = await discover_caldav_url(email)
    assert url is not None
    assert "outlook.office365.com" in url


@pytest.mark.asyncio
async def test_discover_yahoo_returns_yahoo_url():
    """Yahoo addresses should return the Yahoo CalDAV URL."""
    email = "user@yahoo.com"
    url = await discover_caldav_url(email)
    assert url is not None
    assert "yahoo.com" in url


@pytest.mark.asyncio
async def test_discover_me_returns_icloud_url():
    """me.com addresses should map to iCloud CalDAV."""
    email = "user@me.com"
    url = await discover_caldav_url(email)
    assert url is not None
    assert "caldav.icloud.com" in url


@pytest.mark.asyncio
async def test_discover_mac_returns_icloud_url():
    """mac.com addresses should map to iCloud CalDAV."""
    email = "user@mac.com"
    url = await discover_caldav_url(email)
    assert url is not None
    assert "caldav.icloud.com" in url


@pytest.mark.asyncio
async def test_discover_well_known_caldav_fallback():
    """When provider is unknown but .well-known works, return its URL."""
    email = "user@fastmail.com"
    # Mock httpx to return success for .well-known lookup
    with patch("app.services.dav.discovery.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"location": "https://caldav.fastmail.com/dav/calendars/"}
        mock_client.get = AsyncMock(return_value=mock_resp)

        mock_client_cls.return_value = mock_client

        url = await discover_caldav_url(email)
        assert url is not None
        assert "caldav.fastmail.com" in url


# ── Async tests: discover_carddav_url ───────────────────────────────────


@pytest.mark.asyncio
async def test_discover_carddav_gmail_returns_google_url():
    """Gmail CardDAV discovery should return Google Contacts URL."""
    email = "user@gmail.com"
    url = await discover_carddav_url(email)
    assert url is not None
    assert "carddav" in url.lower()
    assert email in url


@pytest.mark.asyncio
async def test_discover_carddav_icloud_returns_icloud_url():
    """iCloud CardDAV discovery should return iCloud Contacts URL."""
    email = "user@icloud.com"
    url = await discover_carddav_url(email)
    assert url is not None
    assert "icloud.com" in url


@pytest.mark.asyncio
async def test_discover_carddav_unknown_returns_none():
    """Unknown domain should return None for CardDAV as well."""
    email = "user@totally-fake-domain-abc456.example"
    with patch("app.services.dav.discovery.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_client.get = AsyncMock(return_value=mock_resp)

        mock_client_cls.return_value = mock_client

        url = await discover_carddav_url(email)
        assert url is None
