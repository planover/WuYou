"""Tests for theme upload / validate / save / list."""

from __future__ import annotations

import json

import pytest

from app.core.config import Settings
from app.api.routes_themes import (
    list_user_themes,
    save_theme,
    validate_theme_json,
)


# ── validate_theme_json ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "data,expected_msg",
    [
        ({}, "meta.id"),
        ({"meta": {}}, "meta.id"),
        ({"meta": {"name": "test"}}, "meta.id"),
        ({"meta": {"id": ""}}, "meta.id"),          # empty string
        ({"meta": None}, "meta.id"),
    ],
)
def test_theme_validate_rejects_missing_meta(data, expected_msg):
    with pytest.raises(ValueError, match=expected_msg):
        validate_theme_json(data)


def test_theme_validate_accepts_valid():
    validate_theme_json({"meta": {"id": "my-custom-theme"}})
    validate_theme_json({
        "meta": {"id": "ocean", "name": "Ocean Blue"},
        "tokens": {"bg": "#eef"},
    })


# ── save + list ──────────────────────────────────────────────────────────

def test_theme_save_and_list(tmp_path):
    """After save_theme, list_user_themes should include the saved data."""
    settings = Settings(data_dir=tmp_path)
    user_id = 1

    theme_data = {
        "meta": {"id": "custom-sunset", "name": "Sunset"},
        "tokens": {"bg": "#ffaa00", "text": "#222"},
    }

    # Initially empty
    assert list_user_themes(settings, user_id) == []

    # Save
    saved_path = save_theme(settings, user_id, "custom-sunset", theme_data)
    assert saved_path.exists()
    assert saved_path.suffix == ".json"

    # List
    themes = list_user_themes(settings, user_id)
    assert len(themes) == 1
    assert themes[0]["meta"]["id"] == "custom-sunset"
    assert themes[0]["tokens"]["bg"] == "#ffaa00"

    # Verify raw file content
    raw = json.loads(saved_path.read_text(encoding="utf-8"))
    assert raw["meta"]["id"] == "custom-sunset"


def test_theme_save_multiple(tmp_path):
    """Saving multiple themes for the same user should list all of them."""
    settings = Settings(data_dir=tmp_path)
    user_id = 42

    for i in range(3):
        save_theme(settings, user_id, f"theme-{i}", {
            "meta": {"id": f"theme-{i}", "name": f"Theme {i}"},
        })

    themes = list_user_themes(settings, user_id)
    assert len(themes) == 3
    ids = {t["meta"]["id"] for t in themes}
    assert ids == {"theme-0", "theme-1", "theme-2"}
