"""Backup helpers for local and WebDAV storage."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import httpx


def create_backup_archive(data_dir: Path) -> Path:
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=backup_dir)
    temp.close()
    archive_path = Path(temp.name)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in data_dir.rglob("*"):
            if path.is_file() and path != archive_path:
                archive.write(path, path.relative_to(data_dir))
    return archive_path


async def upload_webdav(archive_path: Path, url: str, username: str | None, password: str | None, timeout: int) -> dict:
    auth = (username, password) if username and password else None
    target_url = url.rstrip("/") + "/" + archive_path.name
    async with httpx.AsyncClient(timeout=timeout, auth=auth) as client:
        response = await client.put(target_url, content=archive_path.read_bytes())
        response.raise_for_status()
    return {"message": "备份已上传至 WebDAV。", "target": target_url}

