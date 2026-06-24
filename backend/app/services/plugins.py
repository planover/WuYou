"""Plugin community catalog and installation helpers."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx

from app.core.config import Settings
from app.core.security import utc_iso


PLUGIN_CATEGORIES = [
    "效率工具",
    "安全加密",
    "翻译与AI",
    "邮件规则",
    "导入导出",
    "主题外观",
    "开发者工具",
]

PLUGIN_TYPES = ["extension", "theme", "language-pack", "connector"]
REQUIRED_FIELDS = {"id", "name", "version", "type", "category", "description", "entry", "permissions", "license"}


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    missing = REQUIRED_FIELDS - set(manifest)
    if missing:
        raise ValueError("插件清单缺少字段：" + ", ".join(sorted(missing)))
    if manifest["type"] not in PLUGIN_TYPES:
        raise ValueError("插件类型不受支持。")
    if not isinstance(manifest["permissions"], list):
        raise ValueError("permissions 必须是数组。")
    if ".." in manifest["entry"] or manifest["entry"].startswith(("/", "\\")):
        raise ValueError("entry 不能指向插件目录外部。")
    return manifest


def load_local_catalog(settings: Settings) -> dict[str, Any]:
    index_path = settings.local_plugin_community_dir / "index.json"
    if not index_path.exists():
        return {"name": "本地插件开发社区", "plugins": []}
    with index_path.open("r", encoding="utf-8") as handle:
        catalog = json.load(handle)
    for plugin in catalog.get("plugins", []):
        validate_manifest(plugin)
        plugin["source"] = "local"
    return catalog


async def load_remote_catalog(url: str, timeout: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        catalog = response.json()
    for plugin in catalog.get("plugins", []):
        validate_manifest(plugin)
        plugin["source"] = url
    return catalog


def install_manifest(settings: Settings, user_id: int, manifest: dict[str, Any]) -> dict[str, Any]:
    validated = validate_manifest(manifest)
    user_dir = settings.installed_plugins_dir / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    target = user_dir / f"{validated['id']}.json"
    target.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "plugin_id": validated["id"],
        "name": validated["name"],
        "version": validated["version"],
        "type": validated["type"],
        "category": validated["category"],
        "manifest_json": json.dumps(validated, ensure_ascii=False),
        "installed_at": utc_iso(),
    }


def list_installed_files(settings: Settings, user_id: int) -> list[dict[str, Any]]:
    user_dir = settings.installed_plugins_dir / str(user_id)
    if not user_dir.exists():
        return []
    installed = []
    for path in sorted(Path(user_dir).glob("*.json")):
        installed.append(json.loads(path.read_text(encoding="utf-8")))
    return installed


def _is_safe_zip_path(member: zipfile.ZipInfo) -> bool:
    """Return True if the archive member path does not escape the target dir."""
    name = member.filename.replace("\\", "/")
    if name.startswith("/") or ".." in name:
        return False
    return True


async def download_and_install_plugin(
    settings: Settings, user_id: int, url: str, manifest: dict[str, Any]
) -> dict[str, Any]:
    """Download a plugin zip from *url*, verify SHA256, extract and persist."""
    validated = validate_manifest(manifest)

    # 1) Download
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        response = await client.get(url)
        response.raise_for_status()
        raw_bytes = response.content

    # 2) SHA256 check
    expected_sha = manifest.get("sha256", "")
    if not expected_sha:
        raise ValueError("manifest 缺少 sha256 字段")
    actual_sha = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha != expected_sha:
        raise ValueError(
            f"SHA256 不匹配：期望 {expected_sha[:16]}... 实际 {actual_sha[:16]}..."
        )

    # 3) Extract to files dir, filtering unsafe paths
    plugin_id = validated["id"]
    extract_root = settings.data_dir / "plugins" / "files" / str(user_id) / plugin_id
    extract_root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            if not _is_safe_zip_path(member):
                continue
            # Flatten: extract file name only, discard internal paths
            target_path = extract_root / Path(member.filename).name
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(zf.read(member))

    # 4) Write manifest JSON to installed plugins dir
    user_dir = settings.installed_plugins_dir / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = user_dir / f"{plugin_id}.json"
    manifest_path.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "plugin_id": validated["id"],
        "name": validated["name"],
        "version": validated["version"],
        "type": validated["type"],
        "category": validated["category"],
        "manifest_json": json.dumps(validated, ensure_ascii=False),
        "installed_at": utc_iso(),
    }

