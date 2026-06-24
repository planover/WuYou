"""Tests for locale upload, validation, and file management."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.routes_locales import (
    BUILTIN_LOCALES,
    _user_locales_dir,
    validate_locale_json,
)

# ── helpers ─────────────────────────────────────────────────────────────────


def _locale_dict(meta_id: str | None = None, messages: dict | None = None) -> dict:
    """Build a minimal locale dict for testing."""
    data: dict = {}
    if meta_id is not None:
        data["meta"] = {"id": meta_id, "name": f"Locale {meta_id}"}
    if messages is not None:
        data["messages"] = messages
    return data


# ── validate_locale_json tests ──────────────────────────────────────────────


def test_locale_validate_rejects_missing_meta():
    """validate_locale_json() raises ValueError when meta is missing."""
    data = _locale_dict(messages={"key": "value"})
    # data has no "meta" key
    with pytest.raises(ValueError, match="缺少 meta"):
        validate_locale_json(data)


def test_locale_validate_rejects_missing_messages():
    """validate_locale_json() raises ValueError when messages is missing."""
    data = _locale_dict(meta_id="fr-FR")
    # data has no "messages" key
    with pytest.raises(ValueError, match="缺少有效的 messages"):
        validate_locale_json(data)


def test_locale_validate_rejects_empty_messages():
    """validate_locale_json() raises ValueError when messages is an empty dict."""
    data = _locale_dict(meta_id="fr-FR", messages={})
    with pytest.raises(ValueError, match="缺少有效的 messages"):
        validate_locale_json(data)


def test_locale_validate_rejects_missing_meta_id():
    """validate_locale_json() raises ValueError when meta.id is missing or empty."""
    data = {"meta": {}, "messages": {"k": "v"}}
    with pytest.raises(ValueError, match="缺少有效的 meta"):
        validate_locale_json(data)

    data = {"meta": {"id": ""}, "messages": {"k": "v"}}
    with pytest.raises(ValueError, match="缺少有效的 meta"):
        validate_locale_json(data)

    data = {"meta": {"id": "   "}, "messages": {"k": "v"}}
    with pytest.raises(ValueError, match="缺少有效的 meta"):
        validate_locale_json(data)


def test_locale_validate_passes_for_valid_locale():
    """validate_locale_json() returns the locale id for a valid locale."""
    locale_id = validate_locale_json(_locale_dict(meta_id="fr-FR", messages={"greeting": "Bonjour"}))
    assert locale_id == "fr-FR"


# ── save & list integration test ────────────────────────────────────────────


def test_locale_save_and_list(tmp_path, monkeypatch):
    """Save a user locale file, then verify it appears in the listing."""
    # Use a temp data_dir so we don't touch the real data directory
    from app.core import config as cfg

    monkeypatch.setattr(cfg, "BACKEND_ROOT", tmp_path)
    data_dir = tmp_path / "app" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Build a settings object that uses our temp data_dir
    class _FakeSettings:
        pass

    _FakeSettings.data_dir = data_dir

    monkeypatch.setattr(cfg, "get_settings", lambda: _FakeSettings())

    user_id = 42
    locale_data = _locale_dict(meta_id="ja-JP", messages={"hello": "こんにちは"})

    # Save the locale file in the user directory
    user_dir = _user_locales_dir(user_id)
    dest = user_dir / "ja-JP.json"
    dest.write_text(json.dumps(locale_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Verify the file exists and content is valid
    assert dest.exists()
    saved = json.loads(dest.read_text(encoding="utf-8"))
    assert saved["meta"]["id"] == "ja-JP"
    assert saved["messages"]["hello"] == "こんにちは"

    # Verify validate_locale_json accepts the saved file
    locale_id = validate_locale_json(saved)
    assert locale_id == "ja-JP"

    # Verify the user-uploaded file is listable by looking at the directory
    files = list(user_dir.glob("*.json"))
    assert len(files) >= 1
    filenames = [f.name for f in files]
    assert "ja-JP.json" in filenames


def test_builtin_locales_set():
    """Ensure the built-in locale set contains the expected three values."""
    assert BUILTIN_LOCALES == {"zh-CN", "zh-TW", "en-US"}
