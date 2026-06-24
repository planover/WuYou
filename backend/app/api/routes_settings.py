"""Settings, packs, backup, and product metadata routes."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user, json_loads
from app.core.config import REPO_ROOT, get_settings
from app.core.database import db
from app.core.security import utc_iso
from app.models import SettingsUpdate, WebDavBackupRequest
from app.services.backup import create_backup_archive, upload_webdav
from app.services.provider_catalog import list_providers
from app.services.translation import PROVIDER_OPTIONS

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def list_settings(current_user: dict = Depends(get_current_user)):
    rows = db.query_all("SELECT key, value_json FROM settings WHERE user_id = ?", (current_user["user_id"],))
    return {"settings": {row["key"]: json_loads(row["value_json"], None) for row in rows}}


@router.put("")
def update_setting(payload: SettingsUpdate, current_user: dict = Depends(get_current_user)):
    db.execute(
        """
        INSERT INTO settings(user_id, key, value_json, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
        """,
        (current_user["user_id"], payload.key, json.dumps(payload.value, ensure_ascii=False), utc_iso()),
    )
    return {"message": "设置已保存。"}


def _pack_files(path: Path) -> list[dict]:
    if not path.exists():
        return []
    packs = []
    for item in sorted(path.glob("*.json")):
        data = json.loads(item.read_text(encoding="utf-8"))
        packs.append({"id": item.stem, "file": item.name, "meta": data.get("meta", {})})
    return packs


@router.get("/packs")
def packs():
    static_root = Path(__file__).resolve().parents[1] / "static"
    return {
        "languages": _pack_files(static_root / "locales"),
        "themes": _pack_files(static_root / "themes"),
        "language_template": str(REPO_ROOT / "language-packs" / "template.json"),
        "theme_template": str(REPO_ROOT / "theme-packs" / "template.json"),
    }


@router.get("/capabilities")
def capabilities():
    return {
        "providers": list_providers(),
        "translation_providers": PROVIDER_OPTIONS,
        "features": {
            "remote_content_default": False,
            "attachment_auto_download": True,
            "end_to_end_encryption": "plugin-interface-reserved",
            "thunderbird_import": "profile-detection-ready-parser-next",
            "hot_update": "plugin-theme-language-packs-without-image-rebuild",
        },
    }


@router.post("/backup/webdav")
async def backup_webdav(payload: WebDavBackupRequest, current_user: dict = Depends(get_current_user)):
    archive = create_backup_archive(get_settings().data_dir)
    return await upload_webdav(
        archive,
        payload.url,
        payload.username,
        payload.password,
        get_settings().request_timeout_seconds,
    )


@router.get("/about")
def about():
    return {
        "name": "WuYou",
        "positioning": "跨平台 Docker 部署的多邮箱 Web 管理工具。",
        "license": "Apache-2.0",
        "core": "邮件管理、聚合收件箱、安全默认值、插件社区、语言包和主题包。",
    }

