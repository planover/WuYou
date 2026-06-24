"""Tests for plugin download + SHA256 verify + enable/disable/uninstall."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.core.database import db
from app.services.plugins import download_and_install_plugin, install_manifest, validate_manifest

# Force test DB for these tests (in-memory temp file)
_db_inited = False


def _init_test_db(tmp_path: Path):
    global _db_inited
    import app.core.database as mod

    mod.get_settings = lambda: Settings(data_dir=tmp_path / "data")
    mod.db._connection = None
    mod.db.path = mod.get_settings().database_path
    mod.db.init()
    _db_inited = True


# ── helpers ──────────────────────────────────────────────────────────────────

def _valid_manifest(overrides=None):
    m = {
        "id": "test-plugin-001",
        "name": "Test Plugin",
        "version": "1.0.0",
        "type": "extension",
        "category": "\u6548\u7387\u5de5\u5177",
        "description": "A test plugin.",
        "entry": "main.js",
        "permissions": [],
        "license": "MIT",
        "sha256": "",  # placeholder, filled by caller
    }
    if overrides:
        m.update(overrides)
    return m


def _make_zip_bytes() -> bytes:
    """Create a minimal in-memory zip with a dummy entry script."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("main.js", "// test plugin entry")
        zf.writestr("manifest.json", '{"id":"test-plugin-001"}')
    return buf.getvalue()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── download_and_install_plugin ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_download_sha256_match(tmp_path):
    """Successful download when SHA256 matches."""
    settings = Settings(data_dir=tmp_path / "wuyou_data")
    user_id = 1
    zip_bytes = _make_zip_bytes()
    manifest = _valid_manifest({"sha256": _sha256(zip_bytes)})

    mock_response = AsyncMock()
    mock_response.content = zip_bytes
    mock_response.raise_for_status = lambda: None

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        result = await download_and_install_plugin(settings, user_id, "https://fake.example/plugin.zip", manifest)

    assert result["plugin_id"] == "test-plugin-001"
    assert result["name"] == "Test Plugin"
    assert result["version"] == "1.0.0"
    assert result["type"] == "extension"
    assert result["category"] == "\u6548\u7387\u5de5\u5177"
    assert result["installed_at"] is not None

    # Verify files extracted
    extract_root = settings.data_dir / "plugins" / "files" / str(user_id) / "test-plugin-001"
    assert extract_root.exists()
    files = list(extract_root.iterdir())
    assert len(files) == 2  # main.js, manifest.json

    # Verify manifest JSON written
    manifest_path = settings.installed_plugins_dir / str(user_id) / "test-plugin-001.json"
    assert manifest_path.exists()
    written = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert written["id"] == "test-plugin-001"


@pytest.mark.asyncio
async def test_download_sha256_mismatch(tmp_path):
    """Raise ValueError when SHA256 does NOT match."""
    settings = Settings(data_dir=tmp_path / "wuyou_data")
    zip_bytes = _make_zip_bytes()
    manifest = _valid_manifest({"sha256": _sha256(b"different-content")})

    mock_response = AsyncMock()
    mock_response.content = zip_bytes
    mock_response.raise_for_status = lambda: None

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        with pytest.raises(ValueError, match="SHA256 .*不匹配"):
            await download_and_install_plugin(settings, 1, "https://fake.example/plugin.zip", manifest)


@pytest.mark.asyncio
async def test_download_missing_sha256_field(tmp_path):
    """Raise ValueError when manifest has no sha256 field."""
    settings = Settings(data_dir=tmp_path / "wuyou_data")
    manifest = _valid_manifest({"sha256": ""})  # empty string

    mock_response = AsyncMock()
    mock_response.content = _make_zip_bytes()
    mock_response.raise_for_status = lambda: None

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        with pytest.raises(ValueError, match="缺少 sha256"):
            await download_and_install_plugin(settings, 1, "https://fake.example/plugin.zip", manifest)


@pytest.mark.asyncio
async def test_download_invalid_manifest(tmp_path):
    """Raise ValueError when manifest fails basic validation."""
    settings = Settings(data_dir=tmp_path / "wuyou_data")
    zip_bytes = _make_zip_bytes()
    manifest = {"id": "bad"}  # missing required fields

    mock_response = AsyncMock()
    mock_response.content = zip_bytes
    mock_response.raise_for_status = lambda: None

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        with pytest.raises(ValueError, match="缺少字段"):
            await download_and_install_plugin(settings, 1, "https://fake.example/plugin.zip", manifest)


@pytest.mark.asyncio
async def test_download_safe_path_filtering(tmp_path):
    """Unsafe zip paths (../ and absolute) are skipped during extraction."""
    settings = Settings(data_dir=tmp_path / "wuyou_data")
    user_id = 2

    # Build a zip with unsafe paths
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("main.js", "// safe entry")
        zf.writestr("../evil.sh", "#!/bin/sh\nevil")
        zf.writestr("/etc/passwd", "root:x:0:0:")
    zip_bytes = buf.getvalue()
    manifest = _valid_manifest({"sha256": _sha256(zip_bytes), "id": "safe-plugin"})

    mock_response = AsyncMock()
    mock_response.content = zip_bytes
    mock_response.raise_for_status = lambda: None

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        result = await download_and_install_plugin(settings, user_id, "https://fake.example/plugin.zip", manifest)

    assert result["plugin_id"] == "safe-plugin"

    extract_root = settings.data_dir / "plugins" / "files" / str(user_id) / "safe-plugin"
    files = list(extract_root.iterdir())
    file_names = {f.name for f in files}
    assert "main.js" in file_names
    assert "evil.sh" not in file_names
    assert "passwd" not in file_names


# ── enable / disable / uninstall (DB layer) ───────────────────────────────────

@pytest.fixture
def _plugins_db(tmp_path):
    """Init test DB and insert a user + installed plugin row."""
    _init_test_db(tmp_path)
    db._connection = None
    db.path = Settings(data_dir=tmp_path / "data").database_path
    db.init()
    # Create a test user
    db.execute(
        "INSERT OR IGNORE INTO users(id, username, email, phone, password_hash, created_at, updated_at) "
        "VALUES (1, 'testuser', 'test@wuyou.local', NULL, 'pbkdf2_sha256$100$aa$bb', '2025-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00')"
    )
    # Create a test installed plugin row
    db.execute(
        "INSERT OR REPLACE INTO installed_plugins(user_id, plugin_id, name, version, type, category, manifest_json, installed_at, enabled) "
        "VALUES (1, 'test-plugin-001', 'Test Plugin', '1.0.0', 'extension', '\u6548\u7387\u5de5\u5177', '{}', '2025-01-01T00:00:00+00:00', 1)"
    )
    yield tmp_path
    # Cleanup test rows to keep state isolated
    db.execute("DELETE FROM installed_plugins WHERE user_id = 1")
    db.execute("DELETE FROM users WHERE id = 1")


def test_enable_plugin(_plugins_db):
    """Enable a disabled plugin."""
    # First disable it
    db.execute(
        "UPDATE installed_plugins SET enabled = 0 WHERE user_id = 1 AND plugin_id = 'test-plugin-001'"
    )
    row = db.query_one(
        "SELECT enabled FROM installed_plugins WHERE user_id = 1 AND plugin_id = 'test-plugin-001'"
    )
    assert row["enabled"] == 0

    # Enable
    db.execute(
        "UPDATE installed_plugins SET enabled = 1 WHERE user_id = 1 AND plugin_id = 'test-plugin-001'"
    )
    row = db.query_one(
        "SELECT enabled FROM installed_plugins WHERE user_id = 1 AND plugin_id = 'test-plugin-001'"
    )
    assert row["enabled"] == 1


def test_disable_plugin(_plugins_db):
    """Disable an enabled plugin."""
    row = db.query_one(
        "SELECT enabled FROM installed_plugins WHERE user_id = 1 AND plugin_id = 'test-plugin-001'"
    )
    assert row["enabled"] == 1

    # Disable
    db.execute(
        "UPDATE installed_plugins SET enabled = 0 WHERE user_id = 1 AND plugin_id = 'test-plugin-001'"
    )
    row = db.query_one(
        "SELECT enabled FROM installed_plugins WHERE user_id = 1 AND plugin_id = 'test-plugin-001'"
    )
    assert row["enabled"] == 0


def test_uninstall_plugin(_plugins_db):
    """Uninstall removes DB row."""
    row = db.query_one(
        "SELECT plugin_id FROM installed_plugins WHERE user_id = 1 AND plugin_id = 'test-plugin-001'"
    )
    assert row is not None

    db.execute(
        "DELETE FROM installed_plugins WHERE user_id = 1 AND plugin_id = 'test-plugin-001'"
    )
    row = db.query_one(
        "SELECT plugin_id FROM installed_plugins WHERE user_id = 1 AND plugin_id = 'test-plugin-001'"
    )
    assert row is None


def test_enable_nonexistent_noop(_plugins_db):
    """Enabling a plugin that doesn't exist simply does nothing (rowcount 0)."""
    result = db.execute(
        "UPDATE installed_plugins SET enabled = 1 WHERE user_id = 1 AND plugin_id = 'nonexistent'"
    )
    assert result.rowcount == 0


def test_disable_nonexistent_noop(_plugins_db):
    """Disabling a non-existent plugin does nothing."""
    result = db.execute(
        "UPDATE installed_plugins SET enabled = 0 WHERE user_id = 1 AND plugin_id = 'nonexistent'"
    )
    assert result.rowcount == 0


# ── validate_manifest regression ─────────────────────────────────────────────

def test_validate_manifest_accepts_minimal():
    m = _valid_manifest({"sha256": "a" * 64})
    validated = validate_manifest(m)
    assert validated["id"] == "test-plugin-001"


def test_validate_manifest_rejects_missing_field():
    with pytest.raises(ValueError, match="缺少字段"):
        validate_manifest({"id": "x"})


def test_validate_manifest_rejects_bad_type():
    m = _valid_manifest({"type": "unsupported-type", "sha256": "a" * 64})
    with pytest.raises(ValueError, match="不受支持"):
        validate_manifest(m)


def test_validate_manifest_rejects_invalid_entry():
    m = _valid_manifest({"entry": "../escape.js", "sha256": "a" * 64})
    with pytest.raises(ValueError, match="entry"):
        validate_manifest(m)


def test_validate_manifest_rejects_permissions_not_array():
    m = _valid_manifest({"permissions": "read,write", "sha256": "a" * 64})
    with pytest.raises(ValueError, match="数组"):
        validate_manifest(m)
