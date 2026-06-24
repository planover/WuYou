"""Tests for /api/items CRUD routes using FastAPI TestClient."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


AUTH_TOKEN = "test-token-123"
AUTH_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}


def _setup_test_db(db_path: Path):
    """Create a temporary database, initialise schema, insert a user + session."""
    from app.core.database import Database
    from app.core.security import utc_iso

    test_db = Database(db_path)
    test_db.init()

    now = utc_iso()
    token_hash = hashlib.sha256(AUTH_TOKEN.encode("utf-8")).hexdigest()

    user_id = test_db.execute(
        "INSERT INTO users(username, email, phone, password_hash, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)",
        ("testuser", "test@example.com", None, "hashed", now, now),
    ).lastrowid

    test_db.execute(
        "INSERT INTO sessions(user_id, token_hash, expires_at, created_at) VALUES (?,?,?,?)",
        (user_id, token_hash, "2099-12-31T23:59:59+00:00", now),
    )

    return int(user_id), test_db


@pytest.fixture
def client(tmp_path: Path):
    """Set up a temp database, patch the global db, return a FastAPI TestClient."""
    import sys

    db_path = tmp_path / "test_items.db"
    _setup_test_db(db_path)

    # Monkey-patch the module-level db BEFORE importing the app.
    import app.core.database as _db_mod

    original_db = _db_mod.db
    from app.core.database import Database

    test_db = Database(db_path)
    test_db.connect()
    _db_mod.db = test_db

    # Clear cached app modules so they reload with the patched db
    _cached = {}
    for _key in list(sys.modules.keys()):
        if _key.startswith("app.main") or _key.startswith("app.api"):
            _cached[_key] = sys.modules.pop(_key)

    from app.main import app as _fastapi_app

    tc = TestClient(_fastapi_app)

    yield tc

    # Restore cached modules and original db
    _db_mod.db = original_db
    sys.modules.update(_cached)


# ── test_create_calendar_event ────────────────────────────────────────────

def test_create_calendar_event(client: TestClient):
    """POST /api/items with kind=calendar_event should return 200 + data in db."""
    resp = client.post(
        "/api/items",
        json={
            "kind": "calendar_event",
            "title": "团队周会",
            "body": "每周一的例行会议",
            "meta_json": {"start_at": "2026-06-25T09:00:00+08:00", "end_at": "2026-06-25T10:00:00+08:00"},
            "mailbox_id": None,
        },
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "已创建。"

    # 验证数据入库
    import app.core.database as _db_mod

    row = _db_mod.db.query_one("SELECT * FROM content_items WHERE title = ?", ("团队周会",))
    assert row is not None
    assert row["kind"] == "calendar_event"
    assert row["body"] == "每周一的例行会议"


# ── test_list_by_kind ─────────────────────────────────────────────────────

def test_list_by_kind(client: TestClient):
    """GET /api/items?kind=contact 应返回空数组（未创建任何 contact）。"""
    resp = client.get("/api/items?kind=contact", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["items"] == []


# ── test_filter_by_date_range ─────────────────────────────────────────────

def test_filter_by_date_range(client: TestClient):
    """创建2个不同 start_at 的日历事件 → 按日期范围过滤应只返回匹配的。"""
    # 事件 A: 6月20日
    client.post(
        "/api/items",
        json={
            "kind": "calendar_event",
            "title": "事件A",
            "meta_json": {"start_at": "2026-06-20T08:00:00+08:00"},
        },
        headers=AUTH_HEADERS,
    )
    # 事件 B: 7月1日
    client.post(
        "/api/items",
        json={
            "kind": "calendar_event",
            "title": "事件B",
            "meta_json": {"start_at": "2026-07-01T14:00:00+08:00"},
        },
        headers=AUTH_HEADERS,
    )

    # 过滤 from_date=2026-06-25 → 只返回事件B
    resp = client.get(
        "/api/items?kind=calendar_event&from_date=2026-06-25",
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["title"] == "事件B"

    # 过滤 to_date=2026-06-21 → 只返回事件A
    resp2 = client.get(
        "/api/items?kind=calendar_event&to_date=2026-06-21",
        headers=AUTH_HEADERS,
    )
    assert resp2.status_code == 200
    items2 = resp2.json()["items"]
    assert len(items2) == 1
    assert items2[0]["title"] == "事件A"


# ── test_update_item ──────────────────────────────────────────────────────

def test_update_item(client: TestClient):
    """PUT /api/items/1 应返回 200 + 数据已变更。"""
    # 先创建一个条目
    client.post(
        "/api/items",
        json={
            "kind": "task",
            "title": "旧标题",
            "body": "旧内容",
            "meta_json": {"status": "todo"},
        },
        headers=AUTH_HEADERS,
    )

    # 更新
    resp = client.put(
        "/api/items/1",
        json={"title": "新标题", "body": "新内容", "meta_json": {"status": "done"}},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "已更新。"

    # 验证数据变更
    import app.core.database as _db_mod

    row = _db_mod.db.query_one("SELECT * FROM content_items WHERE id = ?", (1,))
    assert row is not None
    assert row["title"] == "新标题"
    assert row["body"] == "新内容"
    import json
    assert json.loads(row["meta_json"]) == {"status": "done"}


# ── test_delete_item ──────────────────────────────────────────────────────

def test_delete_item(client: TestClient):
    """DELETE /api/items/1 → 200 + GET 再查 → 404。"""
    # 先创建一个条目
    client.post(
        "/api/items",
        json={
            "kind": "note",
            "title": "待删除笔记",
            "meta_json": {"category": "ideas"},
        },
        headers=AUTH_HEADERS,
    )

    # 删除
    resp = client.delete("/api/items/1", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["message"] == "已删除。"

    # 再次查询应返回 404
    resp_get = client.get("/api/items/1", headers=AUTH_HEADERS)
    assert resp_get.status_code == 404


# ── test_404_not_found ────────────────────────────────────────────────────

def test_404_not_found(client: TestClient):
    """GET /api/items/999 应返回 404（不存在的条目）。"""
    resp = client.get("/api/items/999", headers=AUTH_HEADERS)
    assert resp.status_code == 404
