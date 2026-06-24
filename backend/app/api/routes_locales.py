"""Locale / language pack routes."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.services import locale_cache

BUILTIN_LOCALES: set[str] = {"zh-CN", "zh-TW", "en-US"}

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
BUILTIN_LOCALES_DIR = STATIC_DIR / "locales"

router = APIRouter(prefix="/api/locales", tags=["locales"])


# ── helpers ────────────────────────────────────────────────────────────────

def _user_locales_dir(user_id: int) -> Path:
    settings = get_settings()
    path = settings.data_dir / "locales" / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json_strict(path: Path) -> dict:
    """Read a JSON file and return the parsed dict.  Raises HTTPException on
    any read / parse error."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="语言包文件不存在。")
    except Exception:
        raise HTTPException(status_code=500, detail="读取语言包文件失败。")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="语言包 JSON 格式无效。")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="语言包 JSON 必须是对象。")
    return data


def validate_locale_json(data: dict) -> str:
    """Validate locale JSON structure.

    Required fields:
      - ``meta.id`` (str, non-empty, only [a-zA-Z0-9_-])
      - ``messages`` (dict, non-empty)

    Returns the locale id on success.  Raises ValueError on failure.
    """
    import re

    if not isinstance(data, dict):
        raise ValueError("语言包必须是 JSON 对象。")
    meta = data.get("meta")
    if not isinstance(meta, dict):
        raise ValueError("语言包缺少 meta 对象。")
    locale_id = meta.get("id")
    if not isinstance(locale_id, str) or not locale_id.strip():
        raise ValueError("语言包缺少有效的 meta.id。")
    locale_id = locale_id.strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", locale_id):
        raise ValueError("meta.id 只能包含字母、数字、下划线和连字符。")
    messages = data.get("messages")
    if not isinstance(messages, dict) or len(messages) == 0:
        raise ValueError("语言包缺少有效的 messages 对象。")
    return locale_id


# ── routes ─────────────────────────────────────────────────────────────────


@router.get("")
def list_locales(current_user: dict = Depends(get_current_user)):
    """List available locale packs (built-in static/locales/*.json + user-uploaded)."""
    result: list[dict] = []

    # 1. Built-in locales
    if BUILTIN_LOCALES_DIR.exists():
        for f in sorted(BUILTIN_LOCALES_DIR.glob("*.json")):
            try:
                data = _read_json_strict(f)
                meta = data.get("meta", {}) if isinstance(data, dict) else {}
                result.append({
                    "id": meta.get("id", f.stem),
                    "name": meta.get("name", f.stem),
                    "author": meta.get("author", ""),
                    "source": "builtin",
                    "filename": f.name,
                })
            except HTTPException:
                continue  # skip unreadable / invalid files

    # 2. User-uploaded locales
    user_dir = _user_locales_dir(current_user["user_id"])
    if user_dir.exists():
        for f in sorted(user_dir.glob("*.json")):
            try:
                data = _read_json_strict(f)
                meta = data.get("meta", {}) if isinstance(data, dict) else {}
                result.append({
                    "id": meta.get("id", f.stem),
                    "name": meta.get("name", f.stem),
                    "author": meta.get("author", ""),
                    "source": "user",
                    "filename": f.name,
                })
            except HTTPException:
                continue

    return {"locales": result}


@router.post("")
async def upload_locale(file: UploadFile, current_user: dict = Depends(get_current_user)):
    """Upload a custom locale pack (.json).

    - Validates ``meta.id`` and ``messages`` object.
    - Built-in locale ids (zh-CN / zh-TW / en-US) cannot be overwritten.
    """
    if not file.filename or not file.filename.lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="仅支持上传 .json 文件。")

    try:
        raw = await file.read()
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="上传的文件不是有效的 JSON。")
    except Exception:
        raise HTTPException(status_code=400, detail="读取上传文件失败。")

    # Validate structure
    try:
        locale_id = validate_locale_json(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Built-in locale protection
    if locale_id in BUILTIN_LOCALES:
        raise HTTPException(status_code=403, detail=f"不允许覆盖内置语言包 {locale_id}。")

    # Save to user directory
    user_dir = _user_locales_dir(current_user["user_id"])
    safe_filename = locale_id + ".json"
    dest = user_dir / safe_filename
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    locale_cache.clear()  # invalidate after upload

    return {
        "message": "语言包已上传。",
        "locale": {
            "id": locale_id,
            "name": data.get("meta", {}).get("name", locale_id),
            "source": "user",
            "filename": safe_filename,
        },
    }


@router.get("/{locale_id}")
def get_locale(locale_id: str, current_user: dict = Depends(get_current_user)):
    """Fetch a single locale pack JSON (built-in first, then user directory)."""
    # 0. Check cache
    cached = locale_cache.get(locale_id)
    if cached is not None:
        return cached

    # 1. Try built-in
    builtin_path = BUILTIN_LOCALES_DIR / f"{locale_id}.json"
    if builtin_path.exists():
        data = _read_json_strict(builtin_path)
        locale_cache.set(locale_id, data)
        return data

    # 2. Try user directory
    user_path = _user_locales_dir(current_user["user_id"]) / f"{locale_id}.json"
    if user_path.exists():
        data = _read_json_strict(user_path)
        locale_cache.set(locale_id, data)
        return data

    raise HTTPException(status_code=404, detail=f"语言包 {locale_id} 不存在。")


@router.delete("/{locale_id}")
def delete_locale(locale_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a user-uploaded locale pack.

    Built-in locales cannot be deleted (returns 403).
    """
    if locale_id in BUILTIN_LOCALES:
        raise HTTPException(status_code=403, detail=f"不允许删除内置语言包 {locale_id}。")

    user_path = _user_locales_dir(current_user["user_id"]) / f"{locale_id}.json"
    if not user_path.exists():
        raise HTTPException(status_code=404, detail=f"语言包 {locale_id} 不存在。")

    user_path.unlink()
    locale_cache.clear()  # invalidate after delete
    return {"message": f"语言包 {locale_id} 已删除。"}
