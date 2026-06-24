# WuYou 插件/主题/语言包生态 MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 WuYou 基础上，实现：1) 主题包/语言包完整生命周期（上传/启用/分享/删除）；2) QQ 邮箱风格三栏布局；3) 插件在线下载 + SHA256 校验安装 + 启用/停用/卸载；4) 社区分享系统（提审→待审核）。

**Architecture:** 独立路由层处理主题/语言包 CURD + 上传；插件安装改为下载资源 + SHA256 校验 + 落盘 files 目录；前端的主题切换改为加载对应 CSS 变量 JSON 实时注入；前端语言切换改为从任意 JSON 路径加载；QQ 邮箱三栏为纯 CSS/JS 布局改造。

**Tech Stack:** Python 3.12、FastAPI、HTML/CSS/JS（静态前端）、SQLite(WAL)、hashlib（SHA256）、httpx（下载插件资源）、pytest。

---

## 文件结构与改动点

- Modify: `backend/app/core/database.py`（新增 `shared_items` 表，扩展 `installed_plugins.enabled`）
- Create: `backend/app/api/routes_themes.py`
- Create: `backend/app/api/routes_locales.py`
- Modify: `backend/app/api/routes_plugins.py`
- Create: `backend/app/api/routes_share.py`
- Modify: `backend/app/api/routes_settings.py`（未改动，仅作为参考）
- Modify: `backend/app/services/plugins.py`（新增下载资源 + SHA256 校验 + 文件目录管理）
- Modify: `backend/app/static/css/app.css`（QQ 邮箱三栏布局改造）
- Modify: `backend/app/static/js/app.js`（主题动态注入 + 语言动态替换 + 插件启用/停用/卸载 UI + 主题/语言管理面板）
- Modify: `backend/app/static/locales/zh-CN.json`（新增 UI 文案键）
- Modify: `backend/app/main.py`（注册新路由）
- Create: `backend/tests/test_theme_upload.py`
- Create: `backend/tests/test_locale_upload.py`
- Create: `backend/tests/test_plugin_install_url.py`
- Create: `backend/tests/test_share.py`

---

### Task 1：数据库迁移（shared_items + installed_plugins.enabled）

**Files:**
- Modify: `backend/app/core/database.py`

- [ ] **Step 1：在 SCHEMA 中新增 `shared_items` 表**

在 `SCHEMA` 字符串末尾（`installed_plugins` 之后）添加：

```sql
CREATE TABLE IF NOT EXISTS shared_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    submitted_at TEXT NOT NULL,
    UNIQUE(user_id, type, item_id)
);
```

- [ ] **Step 2：在 `Database.init()` 增加 enabled 列迁移**

在 `init()` 末尾、`_ensure_indexes` 调用之前，添加：

```python
plugin_cols = {
    row["name"]
    for row in connection.execute("PRAGMA table_info(installed_plugins)").fetchall()
}
if "enabled" not in plugin_cols:
    connection.execute(
        "ALTER TABLE installed_plugins ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
    )
```

- [ ] **Step 3：运行 pytest 验证无回归**

Run: `cd backend; python -m pytest -q`
Expected: 41 passed

---

### Task 2：主题包上传/列表/删除 API

**Files:**
- Create: `backend/app/api/routes_themes.py`
- Create: `backend/tests/test_theme_upload.py`

- [ ] **Step 1：写测试**

Create `backend/tests/test_theme_upload.py`：

```python
import io
import json
from app.core.config import get_settings

def test_theme_validate_rejects_missing_meta():
    from app.api.routes_themes import validate_theme_json
    try:
        validate_theme_json({"no_meta": True})
        assert False, "should have raised"
    except ValueError:
        pass

def test_theme_save_and_list(tmp_path):
    from app.api.routes_themes import save_theme, list_user_themes
    settings = get_settings()
    theme_id = "test-theme"
    save_theme(settings, 1, theme_id, {"meta": {"id": theme_id, "name": "测试主题"}, "variables": {"--bg": "#fff"}})
    themes = list_user_themes(settings, 1)
    assert any(t["id"] == theme_id for t in themes)
```

- [ ] **Step 2：实现 routes_themes.py**

```python
"""Theme pack management routes."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.database import db
from app.core.security import utc_iso

router = APIRouter(prefix="/api/themes", tags=["themes"])

BUILTIN_THEMES = {"light", "dark"}
THEMES_DIR = get_settings().data_dir / "themes"


def validate_theme_json(data: dict) -> dict:
    if "meta" not in data or "id" not in data.get("meta", {}):
        raise ValueError("主题包缺少 meta.id")
    return data


def save_theme(settings, user_id: int, theme_id: str, data: dict):
    user_dir = settings.data_dir / "themes" / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / f"{theme_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_user_themes(settings, user_id: int) -> list[dict]:
    user_dir = settings.data_dir / "themes" / str(user_id)
    if not user_dir.exists():
        return []
    themes = []
    for path in sorted(user_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        themes.append({"id": data["meta"]["id"], "name": data["meta"].get("name", path.stem), "file": path.name, "meta": data["meta"]})
    return themes


@router.get("")
def list_themes(current_user: dict = Depends(get_current_user)):
    settings = get_settings()
    static_root = Path(__file__).resolve().parents[1] / "static"
    builtin = []
    for path in sorted(static_root.glob("themes/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        builtin.append({"id": path.stem, "name": data.get("meta", {}).get("name", path.stem), "builtin": True, "meta": data.get("meta", {})})
    user_themes = [{"builtin": False, **t} for t in list_user_themes(settings, current_user["user_id"])]
    return {"themes": builtin + user_themes}


@router.post("")
def upload_theme(file: UploadFile, current_user: dict = Depends(get_current_user)):
    if not file.filename or not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="请上传 JSON 格式的主题包。")
    content = file.file.read()
    data = json.loads(content)
    try:
        validate_theme_json(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    theme_id = data["meta"]["id"]
    if theme_id in BUILTIN_THEMES:
        raise HTTPException(status_code=400, detail="内置主题不可覆盖。")
    save_theme(get_settings(), current_user["user_id"], theme_id, data)
    return {"message": "主题包已上传。", "id": theme_id}


@router.delete("/{theme_id}")
def delete_theme(theme_id: str, current_user: dict = Depends(get_current_user)):
    if theme_id in BUILTIN_THEMES:
        raise HTTPException(status_code=403, detail="内置主题不可删除。")
    settings = get_settings()
    user_dir = settings.data_dir / "themes" / str(current_user["user_id"])
    path = user_dir / f"{theme_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="主题不存在。")
    path.unlink()
    return {"message": "主题已删除。"}


@router.get("/{theme_id}")
def get_theme(theme_id: str):
    settings = get_settings()
    if theme_id in BUILTIN_THEMES:
        static_root = Path(__file__).resolve().parents[1] / "static"
        path = static_root / "themes" / f"{theme_id}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        raise HTTPException(status_code=404, detail="主题不存在。")
    for uid_dir in settings.data_dir.glob("themes/*"):
        path = uid_dir / f"{theme_id}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="主题不存在。")
```

- [ ] **Step 3：在 main.py 注册路由**

在 `backend/app/main.py` 中添加 `from app.api import routes_themes` 和 `app.include_router(routes_themes.router)`。

- [ ] **Step 4：运行 pytest**

Run: `cd backend; python -m pytest -q`
Expected: 43+ passed

---

### Task 3：语言包上传/列表/删除 API

**Files:**
- Create: `backend/app/api/routes_locales.py`
- Create: `backend/tests/test_locale_upload.py`

- [ ] **Step 1：创建 routes_locales.py**

```python
"""Language pack management routes."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.api.deps import get_current_user
from app.core.config import get_settings

router = APIRouter(prefix="/api/locales", tags=["locales"])

BUILTIN_LOCALES = {"zh-CN", "zh-TW", "en-US"}


def validate_locale_json(data: dict) -> dict:
    if "meta" not in data or "id" not in data.get("meta", {}):
        raise ValueError("语言包缺少 meta.id")
    if "messages" not in data or not isinstance(data["messages"], dict):
        raise ValueError("语言包缺少 messages 对象")
    return data


def save_locale(settings, user_id: int, locale_id: str, data: dict):
    user_dir = settings.data_dir / "locales" / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    path = user_dir / f"{locale_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_user_locales(settings, user_id: int) -> list[dict]:
    user_dir = settings.data_dir / "locales" / str(user_id)
    if not user_dir.exists():
        return []
    locales = []
    for path in sorted(user_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        locales.append({"id": data["meta"]["id"], "name": data["meta"].get("name", path.stem), "file": path.name, "meta": data["meta"]})
    return locales


@router.get("")
def list_locales(current_user: dict = Depends(get_current_user)):
    settings = get_settings()
    static_root = Path(__file__).resolve().parents[1] / "static"
    builtin = []
    for path in sorted(static_root.glob("locales/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        builtin.append({"id": path.stem, "name": data.get("meta", {}).get("name", path.stem), "builtin": True, "meta": data.get("meta", {})})
    user_locales = [{"builtin": False, **t} for t in list_user_locales(settings, current_user["user_id"])]
    return {"locales": builtin + user_locales}


@router.post("")
def upload_locale(file: UploadFile, current_user: dict = Depends(get_current_user)):
    if not file.filename or not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="请上传 JSON 格式的语言包。")
    content = file.file.read()
    data = json.loads(content)
    try:
        validate_locale_json(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    locale_id = data["meta"]["id"]
    if locale_id in BUILTIN_LOCALES:
        raise HTTPException(status_code=400, detail="内置语言包不可覆盖。")
    save_locale(get_settings(), current_user["user_id"], locale_id, data)
    return {"message": "语言包已上传。", "id": locale_id}


@router.delete("/{locale_id}")
def delete_locale(locale_id: str, current_user: dict = Depends(get_current_user)):
    if locale_id in BUILTIN_LOCALES:
        raise HTTPException(status_code=403, detail="内置语言包不可删除。")
    settings = get_settings()
    user_dir = settings.data_dir / "locales" / str(current_user["user_id"])
    path = user_dir / f"{locale_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="语言包不存在。")
    path.unlink()
    return {"message": "语言包已删除。"}


@router.get("/{locale_id}")
def get_locale(locale_id: str):
    settings = get_settings()
    if locale_id in BUILTIN_LOCALES:
        static_root = Path(__file__).resolve().parents[1] / "static"
        path = static_root / "locales" / f"{locale_id}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        raise HTTPException(status_code=404, detail="语言包不存在。")
    for uid_dir in settings.data_dir.glob("locales/*"):
        path = uid_dir / f"{locale_id}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="语言包不存在。")
```

- [ ] **Step 2：在 main.py 注册 `routes_locales`**

- [ ] **Step 3：写测试 `test_locale_upload.py`**

与主题包测试结构对称。

- [ ] **Step 4：运行 pytest**

Run: `cd backend; python -m pytest -q`
Expected: 45+ passed

---

### Task 4：插件在线下载 + SHA256 校验 + 文件安装

**Files:**
- Modify: `backend/app/services/plugins.py`
- Modify: `backend/app/api/routes_plugins.py`
- Create: `backend/tests/test_plugin_install_url.py`

- [ ] **Step 1：在 plugins.py 新增 `download_and_install_plugin` 函数**

```python
import hashlib
import tempfile
import zipfile

async def download_and_install_plugin(settings: Settings, user_id: int, url: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """下载插件 zip → SHA256 校验 → 解压到 files 目录 → 写 installed_plugins 表。"""
    validated = validate_manifest(manifest)
    expected_sha256 = validated.get("sha256", "")

    # 下载
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        raw = resp.content

    # SHA256 校验
    if expected_sha256:
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected_sha256:
            raise ValueError(f"SHA256 校验失败：期望 {expected_sha256[:12]}... 实际 {actual[:12]}...")

    # 解压到 files 目录
    user_dir = settings.data_dir / "plugins" / "files" / str(user_id)
    plugin_dir = user_dir / validated["id"]
    if plugin_dir.exists():
        import shutil
        shutil.rmtree(plugin_dir)
    plugin_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)

    try:
        with zipfile.ZipFile(tmp_path, "r") as zf:
            for member in zf.namelist():
                if member.startswith("/") or ".." in member:
                    raise ValueError(f"插件包包含非法路径：{member}")
                zf.extract(member, plugin_dir)
    finally:
        tmp_path.unlink(missing_ok=True)

    # 写 manifest 到 installed 目录
    manifest_dir = settings.installed_plugins_dir / str(user_id)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{validated['id']}.json"
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
```

- [ ] **Step 2：在 routes_plugins.py 新增 `POST /api/plugins/install/url` 端点**

```python
@router.post("/install/url")
async def install_from_url(payload: dict, current_user: dict = Depends(get_current_user)):
    url = payload.get("url")
    manifest = payload.get("manifest")
    if not url or not manifest:
        raise HTTPException(status_code=400, detail="url 和 manifest 不能为空。")
    try:
        installed_data = await download_and_install_plugin(
            get_settings(), current_user["user_id"], url, manifest
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # 写 installed_plugins 表 ...
    return {"message": "插件已安装。", "plugin": installed_data}
```

- [ ] **Step 3：新增启用/停用/卸载端点**

```python
@router.post("/{plugin_id}/enable")
def enable_plugin(plugin_id: str, current_user: dict = Depends(get_current_user)):
    db.execute(
        "UPDATE installed_plugins SET enabled = 1 WHERE user_id = ? AND plugin_id = ?",
        (current_user["user_id"], plugin_id),
    )
    return {"message": "插件已启用。"}

@router.post("/{plugin_id}/disable")
def disable_plugin(plugin_id: str, current_user: dict = Depends(get_current_user)):
    db.execute(
        "UPDATE installed_plugins SET enabled = 0 WHERE user_id = ? AND plugin_id = ?",
        (current_user["user_id"], plugin_id),
    )
    return {"message": "插件已停用。"}

@router.delete("/{plugin_id}")
def uninstall_plugin(plugin_id: str, current_user: dict = Depends(get_current_user)):
    db.execute(
        "DELETE FROM installed_plugins WHERE user_id = ? AND plugin_id = ?",
        (current_user["user_id"], plugin_id),
    )
    settings = get_settings()
    plugin_dir = settings.data_dir / "plugins" / "files" / str(current_user["user_id"]) / plugin_id
    if plugin_dir.exists():
        import shutil
        shutil.rmtree(plugin_dir)
    return {"message": "插件已卸载。"}
```

- [ ] **Step 4：运行 pytest**

Run: `cd backend; python -m pytest -q`
Expected: 47+ passed

---

### Task 5：社区分享 API 与 shared_items 表

**Files:**
- Create: `backend/app/api/routes_share.py`
- Create: `backend/tests/test_share.py`

- [ ] **Step 1：创建 routes_share.py**

```python
"""Share items to community submissions."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.core.database import db
from app.core.security import utc_iso

router = APIRouter(prefix="/api/share", tags=["share"])


@router.post("")
def share_item(payload: dict, current_user: dict = Depends(get_current_user)):
    item_type = payload.get("type")
    item_id = payload.get("item_id")
    if not item_type or item_id is None:
        raise HTTPException(status_code=400, detail="type 和 item_id 不能为空。")
    if item_type not in {"theme", "language-pack", "extension"}:
        raise HTTPException(status_code=400, detail="type 必须是 theme/language-pack/extension。")

    existing = db.query_one(
        "SELECT id FROM shared_items WHERE user_id = ? AND type = ? AND item_id = ?",
        (current_user["user_id"], item_type, item_id),
    )
    if existing:
        raise HTTPException(status_code=409, detail="该包已经提交过分享审核。")

    db.execute(
        """
        INSERT INTO shared_items(user_id, type, item_id, manifest_json, status, submitted_at)
        VALUES (?, ?, ?, ?, 'pending', ?)
        """,
        (
            current_user["user_id"],
            item_type,
            str(item_id),
            json.dumps(payload.get("manifest", {}), ensure_ascii=False),
            utc_iso(),
        ),
    )
    return {"message": "已提交到社区等待审核。"}


@router.get("/submissions")
def list_submissions(current_user: dict = Depends(get_current_user)):
    rows = db.query_all(
        "SELECT * FROM shared_items WHERE user_id = ? ORDER BY id DESC",
        (current_user["user_id"],),
    )
    return {"submissions": [dict(row) for row in rows]}
```

- [ ] **Step 2：在 main.py 注册 routes_share**

- [ ] **Step 3：创建 test_share.py**

```python
def test_share_and_list_submission(tmp_path):
    from app.core.database import Database
    from app.api.routes_share import share_item
    # 建临时库 + user → mock current_user → 调share_item → 验证shared_items行存在
```

- [ ] **Step 4：运行 pytest**

Run: `cd backend; python -m pytest -q`
Expected: 49+ passed

---

### Task 6：QQ 邮箱三栏布局（纯 CSS + 轻 JS）

**Files:**
- Modify: `backend/app/static/css/app.css`
- Modify: `backend/app/static/js/app.js`

- [ ] **Step 1：CSS 三栏改造**

将 `.app-shell` 从 `grid-template-columns: 208px 1fr` 改为 `180px 360px 1fr`：

```css
.app-shell {
    height: 100vh;
    display: grid;
    grid-template-columns: 180px 360px 1fr;
    grid-template-rows: 52px 1fr;
}

.topbar {
    grid-column: 1 / 4;
    background: linear-gradient(135deg, #12b7f5, #0d9bd4);
    color: #fff;
}

.topbar .brand { color: #fff; }
.topbar .brand .brand-mark {
    background: linear-gradient(135deg, #fff, rgba(255,255,255,0.7));
    color: #12b7f5;
}

.top-actions select, .top-actions button {
    color: #fff;
    background: rgba(255,255,255,0.15);
    border-color: rgba(255,255,255,0.25);
}

.mail-layout {
    height: 100%;
    display: grid;
    grid-template-columns: 1fr;
    /* 三栏已在 shell 层拆分，这里去掉 inner split */
}

.list-pane {
    border-right: 1px solid var(--line);
}

.reader-pane {
    /* 已有独立列 */
}

/* 窄屏自适应 */
@media (max-width: 860px) {
    .app-shell {
        grid-template-columns: 1fr;
        grid-template-rows: 52px auto 1fr;
    }
    .mail-layout {
        grid-template-columns: 1fr;
        grid-template-rows: auto 1fr;
    }
}
```

- [ ] **Step 2：JS 端文件夹切换移到中间栏 toolbar 下方**

现有 `renderInbox` 的 `.folder-tabs` 已在 mailbox 列表上方，保持不动。但确保当用户点击邮件时，阅读区在右侧栏显示，而非替换中间栏。

修改 `openMessage` → 将 reader 渲染到 `.reader-pane`（右侧栏），邮件列表保留在 `.list-pane`（中间栏）。

- [ ] **Step 3：运行冒烟测试**

Run: `python smoke_test.py`（如果 uvicorn 还在跑）
Expected: 前端三栏交互正常

---

### Task 7：前端主题动态注入 + 语言动态切换 + 设置管理面板

**Files:**
- Modify: `backend/app/static/js/app.js`
- Modify: `backend/app/static/locales/zh-CN.json`

- [ ] **Step 1：主题动态注入函数 `applyTheme()`**

改 `applyTheme()` 从"仅切换 `data-theme`" 变为：

```javascript
async function applyTheme(themeId) {
  state.theme = themeId;
  localStorage.setItem("wuyou.theme", themeId);

  if (themeId === "light" || themeId === "dark") {
    document.documentElement.dataset.theme = themeId;
    return;
  }

  // 用户自定义主题：拉取 JSON → 注入 CSS 变量
  try {
    const theme = await fetch(`/api/themes/${themeId}`).then(r => r.json());
    const vars = theme.variables || {};
    const root = document.documentElement;
    for (const [key, value] of Object.entries(vars)) {
      root.style.setProperty(key, value);
    }
  } catch {
    toast("主题加载失败", "error");
  }
}
```

- [ ] **Step 2：语言包动态切换**

修改 `loadLocale()` 支持从 API 加载用户上传的语言包：

```javascript
async function loadLocale(localeId) {
  let url = `/static/locales/${localeId}.json`;
  try {
    const resp = await fetch(url);
    if (!resp.ok) {
      // 尝试从用户 API 加载
      const apiResp = await fetch(`/api/locales/${localeId}`);
      if (!apiResp.ok) throw new Error("未找到语言包");
      state.dict = (await apiResp.json()).messages || {};
    } else {
      state.dict = (await resp.json()).messages || {};
    }
  } catch {
    toast("语言包加载失败，回退到简体中文", "error");
    state.locale = "zh-CN";
    loadLocale("zh-CN");
    return;
  }
  document.documentElement.lang = localeId;
  state.locale = localeId;
  localStorage.setItem("wuyou.locale", localeId);
}
```

- [ ] **Step 3：设置页增加主题管理和语言管理**

在 `renderSettings()` 增加两个面板：

**主题管理卡片**：调用 `/api/themes` → 列出所有主题卡片 → 每张含色块 + "启用"按钮 + "分享"按钮 + "删除"按钮（内置不可删）

**语言管理卡片**：调用 `/api/locales` → 列出所有语言包 → "切换"按钮 + "上传"按钮 + "分享"按钮 + "删除"按钮

- [ ] **Step 4：增加上传按钮（文件选择器 + POST）**

在设置页面增加上传表单：
```html
<input type="file" id="theme-upload" accept=".json" />
<button id="upload-theme">上传主题</button>
```

JS 处理用 `FormData` + `fetch POST /api/themes`。

- [ ] **Step 5：补充 zh-CN.json 语言包新增键**

添加新键：
```json
{
  "settings.themesManage": "主题管理",
  "settings.localesManage": "语言管理",
  "settings.upload": "上传",
  "settings.share": "分享到社区",
  "settings.delete": "删除",
  "settings.builtinProtected": "内置不可删除",
  "plugins.enable": "启用",
  "plugins.disable": "停用",
  "plugins.uninstall": "卸载",
  "plugins.uploadPlugin": "上传插件",
  "share.submit": "已提交到社区等待审核。",
  "share.alreadySubmitted": "该包已经提交过分享审核。",
}
```

---

### Task 8：插件社区前端增强 + 启用/停用/卸载

**Files:**
- Modify: `backend/app/static/js/app.js`

- [ ] **Step 1：插件卡片增加操作按钮**

在 `renderPlugins()` 中，已安装插件卡片增加：
- "启用"/"停用" toggle 按钮
- "卸载" 按钮
- "分享" 按钮

```javascript
<div class="item-card">
  <h3>${esc(plugin.name)}</h3>
  <p>${esc(plugin.description)}</p>
  <p class="muted">状态：${enabled ? "已启用" : "已停用"}</p>
  <button class="btn" data-toggle-plugin="${plugin.id}" data-enabled="${enabled}">
    ${enabled ? t("plugins.disable", "停用") : t("plugins.enable", "启用")}
  </button>
  <button class="btn danger" data-uninstall="${plugin.id}">卸载</button>
</div>
```

- [ ] **Step 2：事件绑定**

```javascript
document.querySelectorAll("[data-toggle-plugin]").forEach(b => {
  b.addEventListener("click", async () => {
    const action = b.dataset.enabled === "true" ? "disable" : "enable";
    await api(`/api/plugins/${b.dataset.togglePlugin}/${action}`, { method: "POST" });
    toast("插件状态已更新。");
    renderPlugins();
  });
});

document.querySelectorAll("[data-uninstall]").forEach(b => {
  b.addEventListener("click", async () => {
    if (!confirm("确认卸载该插件？")) return;
    await api(`/api/plugins/${b.dataset.uninstall}`, { method: "DELETE" });
    toast("插件已卸载。");
    renderPlugins();
  });
});
```

- [ ] **Step 3：在线安装改为走 install/url 端点**

"安装"按钮对在线插件改为 POST `/api/plugins/install/url`，传 `{url, manifest}`。

---

### Task 9：冒烟测试与最终验收

**Files:**
- None（仅运行测试 + 手动验证）

- [ ] **Step 1：运行全量 pytest**

Run: `cd backend; python -m pytest -q`
Expected: 49+ passed, 0 failed

- [ ] **Step 2：启动服务器端到端验证**

```bash
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

验证清单：
- 注册 → 登录 → 看到 QQ 邮箱蓝色三栏布局
- 切换主题为 dark → 夜间模式正常
- 设置页上传主题 JSON → 列表出现 → 启用 → 变量生效
- 上传语言包 JSON → 列表中 → 切换到新语言 → 文案变化
- 插件社区 → 安装 → 启用/停用/卸载正常
- 分享主题/语言包 → 提交记录可见
- 内置包删除被拦截（403）
