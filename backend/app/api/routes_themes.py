"""Theme upload / list / detail / delete API."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import get_current_user
from app.core.config import Settings, get_settings
from app.services import theme_cache

router = APIRouter(prefix="/api/themes", tags=["themes"])

# ── static (built-in) themes directory ──────────────────────────────────
_HERE = Path(__file__).resolve().parent  # app/api/
_BUILTIN_DIR = _HERE.parent / "static" / "themes"  # app/static/themes/
_BUILTIN_IDS = {"light", "dark"}


# ── helpers ─────────────────────────────────────────────────────────────

def validate_theme_json(data: dict) -> str:
    """Validate theme JSON structure.

    Required fields:
      - ``meta.id`` (str, non-empty, only [a-zA-Z0-9_-])

    Returns the theme id on success.  Raises ValueError on failure.
    """
    import re

    if not isinstance(data, dict):
        raise ValueError("主题 JSON 必须是对象。")
    meta = data.get("meta")
    if not isinstance(meta, dict) or not meta.get("id"):
        raise ValueError("主题缺少 meta.id 字段。")
    theme_id = str(meta["id"]).strip()
    if not theme_id:
        raise ValueError("meta.id 不能为空。")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", theme_id):
        raise ValueError("meta.id 只能包含字母、数字、下划线和连字符。")
    return theme_id


def save_theme(settings: Settings, user_id: int, theme_id: str, data: dict) -> Path:
    """Persist a theme JSON to the user's theme directory.

    Returns the path where the file was written.
    """
    user_dir = settings.data_dir / "themes" / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / f"{theme_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _user_themes_dir(settings: Settings, user_id: int) -> Path:
    return settings.data_dir / "themes" / str(user_id)


def list_user_themes(settings: Settings, user_id: int) -> list[dict]:
    """Return all user-uploaded themes as a list of parsed dicts."""
    result: list[dict] = []
    user_dir = _user_themes_dir(settings, user_id)
    if not user_dir.is_dir():
        return result
    for f in sorted(user_dir.glob("*.json")):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                result.append(raw)
        except Exception:
            continue
    return result


def _load_builtin_themes() -> list[dict]:
    themes: list[dict] = []
    if not _BUILTIN_DIR.is_dir():
        return themes
    for f in sorted(_BUILTIN_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                themes.append(data)
        except Exception:
            continue
    return themes


def _find_builtin(theme_id: str) -> dict | None:
    cached = theme_cache.get(theme_id)
    if cached is not None:
        return cached
    path = _BUILTIN_DIR / f"{theme_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        theme_cache.set(theme_id, data)
        return data
    except Exception:
        return None


def _find_user_theme(settings: Settings, user_id: int, theme_id: str) -> dict | None:
    cache_key = f"{user_id}:{theme_id}"
    cached = theme_cache.get(cache_key)
    if cached is not None:
        return cached
    path = _user_themes_dir(settings, user_id) / f"{theme_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        theme_cache.set(cache_key, data)
        return data
    except Exception:
        return None


# ── routes ──────────────────────────────────────────────────────────────

@router.get("")
def list_themes(current_user: dict = Depends(get_current_user)):
    """Return available themes (built-in + user-uploaded)."""
    settings = get_settings()
    builtin = _load_builtin_themes()
    user_themes = list_user_themes(settings, current_user["user_id"])
    return {"themes": builtin + user_themes}


@router.post("")
async def upload_theme(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Upload a new theme package (.json).

    - ``meta.id`` is required.
    - Built-in ids (light / dark) cannot be overwritten.
    """
    settings = get_settings()

    # Read & parse
    raw = await file.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="主题文件不是有效的 JSON。") from exc

    # Validate structure
    try:
        theme_id = validate_theme_json(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Guard built-in ids
    if theme_id in _BUILTIN_IDS:
        raise HTTPException(
            status_code=409,
            detail=f"不能覆盖内置主题 '{theme_id}'。",
        )

    save_theme(settings, current_user["user_id"], theme_id, data)
    theme_cache.clear()  # invalidate after upload
    return {"message": "主题已上传。", "meta": data["meta"]}


@router.get("/{theme_id}")
def get_theme(
    theme_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return a single theme's JSON data.

    Looks up built-in themes first, then user-uploaded themes.
    """
    settings = get_settings()

    # 1. Built-in
    builtin = _find_builtin(theme_id)
    if builtin is not None:
        return builtin

    # 2. User
    user_theme = _find_user_theme(settings, current_user["user_id"], theme_id)
    if user_theme is not None:
        return user_theme

    raise HTTPException(status_code=404, detail=f"主题 '{theme_id}' 未找到。")


@router.delete("/{theme_id}")
def delete_theme(
    theme_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete a user-uploaded theme.

    Built-in themes cannot be deleted (returns 403).
    """
    settings = get_settings()

    if theme_id in _BUILTIN_IDS:
        raise HTTPException(
            status_code=403,
            detail=f"内置主题 '{theme_id}' 不能删除。",
        )

    path = _user_themes_dir(settings, current_user["user_id"]) / f"{theme_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"主题 '{theme_id}' 未找到。")

    path.unlink()
    theme_cache.clear()  # invalidate after delete
    return {"message": f"主题 '{theme_id}' 已删除。"}
