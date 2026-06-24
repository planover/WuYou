"""Plugin community routes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.database import db
from app.core.security import utc_iso
from app.models import PluginInstallRequest, PluginInstallUrlRequest, PluginSourceCreate
from app.services.plugins import (
    PLUGIN_CATEGORIES,
    download_and_install_plugin,
    install_manifest,
    list_installed_files,
    load_local_catalog,
    load_remote_catalog,
)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


@router.get("/catalog")
async def catalog(source_url: str | None = None):
    settings = get_settings()
    if source_url:
        catalog_data = await load_remote_catalog(source_url, settings.request_timeout_seconds)
    else:
        catalog_data = load_local_catalog(settings)
    return {"categories": PLUGIN_CATEGORIES, "catalog": catalog_data}


@router.get("/installed")
def installed(current_user: dict = Depends(get_current_user)):
    db_rows = db.query_all(
        "SELECT plugin_id, name, version, type, category, installed_at FROM installed_plugins WHERE user_id = ?",
        (current_user["user_id"],),
    )
    return {"installed": [dict(row) for row in db_rows], "files": list_installed_files(get_settings(), current_user["user_id"])}


@router.post("/install")
def install(payload: PluginInstallRequest, current_user: dict = Depends(get_current_user)):
    try:
        installed_data = install_manifest(get_settings(), current_user["user_id"], payload.manifest)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.execute(
        """
        INSERT INTO installed_plugins(user_id, plugin_id, name, version, type, category, manifest_json, installed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, plugin_id) DO UPDATE SET
            name = excluded.name,
            version = excluded.version,
            type = excluded.type,
            category = excluded.category,
            manifest_json = excluded.manifest_json,
            installed_at = excluded.installed_at
        """,
        (
            current_user["user_id"],
            installed_data["plugin_id"],
            installed_data["name"],
            installed_data["version"],
            installed_data["type"],
            installed_data["category"],
            installed_data["manifest_json"],
            installed_data["installed_at"],
        ),
    )
    return {"message": "插件已安装或更新。", "plugin": installed_data}


@router.post("/install/url")
async def install_url(payload: PluginInstallUrlRequest, current_user: dict = Depends(get_current_user)):
    try:
        installed_data = await download_and_install_plugin(
            get_settings(), current_user["user_id"], payload.url, payload.manifest
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.execute(
        """
        INSERT INTO installed_plugins(user_id, plugin_id, name, version, type, category, manifest_json, installed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, plugin_id) DO UPDATE SET
            name = excluded.name,
            version = excluded.version,
            type = excluded.type,
            category = excluded.category,
            manifest_json = excluded.manifest_json,
            installed_at = excluded.installed_at
        """,
        (
            current_user["user_id"],
            installed_data["plugin_id"],
            installed_data["name"],
            installed_data["version"],
            installed_data["type"],
            installed_data["category"],
            installed_data["manifest_json"],
            installed_data["installed_at"],
        ),
    )
    return {"message": "插件已安装或更新。", "plugin": installed_data}


@router.post("/{plugin_id}/enable")
def enable_plugin(plugin_id: str, current_user: dict = Depends(get_current_user)):
    result = db.execute(
        "UPDATE installed_plugins SET enabled = 1 WHERE user_id = ? AND plugin_id = ?",
        (current_user["user_id"], plugin_id),
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="未找到已安装的插件。")
    return {"message": "插件已启用。", "plugin_id": plugin_id}


@router.post("/{plugin_id}/disable")
def disable_plugin(plugin_id: str, current_user: dict = Depends(get_current_user)):
    result = db.execute(
        "UPDATE installed_plugins SET enabled = 0 WHERE user_id = ? AND plugin_id = ?",
        (current_user["user_id"], plugin_id),
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="未找到已安装的插件。")
    return {"message": "插件已停用。", "plugin_id": plugin_id}


@router.delete("/{plugin_id}")
def uninstall_plugin(plugin_id: str, current_user: dict = Depends(get_current_user)):
    result = db.execute(
        "DELETE FROM installed_plugins WHERE user_id = ? AND plugin_id = ?",
        (current_user["user_id"], plugin_id),
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="未找到已安装的插件。")
    # Remove files directory
    files_dir = get_settings().data_dir / "plugins" / "files" / str(current_user["user_id"]) / plugin_id
    if files_dir.exists():
        shutil.rmtree(files_dir)
    # Remove manifest JSON
    manifest_path = get_settings().installed_plugins_dir / str(current_user["user_id"]) / f"{plugin_id}.json"
    if manifest_path.exists():
        manifest_path.unlink()
    return {"message": "插件已卸载。", "plugin_id": plugin_id}


@router.get("/sources")
def list_sources(current_user: dict = Depends(get_current_user)):
    rows = db.query_all("SELECT * FROM plugin_sources WHERE user_id = ? ORDER BY id DESC", (current_user["user_id"],))
    return {"sources": [dict(row) for row in rows]}


@router.post("/sources")
def add_source(payload: PluginSourceCreate, current_user: dict = Depends(get_current_user)):
    db.execute(
        "INSERT INTO plugin_sources(user_id, name, url, kind, enabled, created_at) VALUES (?, ?, ?, ?, 1, ?)",
        (current_user["user_id"], payload.name, payload.url, payload.kind, utc_iso()),
    )
    return {"message": "插件社区地址已保存。"}

