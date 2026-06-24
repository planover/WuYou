"""WuYou 统一内容条目 CRUD API。

管理 ``content_items`` 表中的四种条目类型：
- ``calendar_event`` — 日历事件
- ``contact`` — 联系人
- ``task`` — 任务
- ``note`` — 笔记

所有端点需要 Bearer token 认证。支持按类型、关键词、日期范围、状态、
分类进行过滤查询。

端点：
- ``GET /api/items`` — 列表查询（支持多条件过滤）
- ``GET /api/items/{id}`` — 单条详情
- ``POST /api/items`` — 创建条目
- ``PUT /api/items/{id}`` — 更新条目
- ``DELETE /api/items/{id}`` — 删除条目
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user, json_loads
from app.core.database import db
from app.core.security import utc_iso
from app.services.telemetry import track

router = APIRouter(prefix="/api/items", tags=["items"])

VALID_KINDS = {"calendar_event", "contact", "task", "note"}


# ── helpers ───────────────────────────────────────────────────────────────

def _item_to_dict(row) -> dict:
    """将 sqlite3.Row 转为前端友好的字典格式。

    ``meta_json`` 字段会被反序列化为 dict，方便前端直接使用。
    """
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "mailbox_id": row["mailbox_id"],
        "kind": row["kind"],
        "title": row["title"],
        "body": row["body"],
        "meta_json": json_loads(row["meta_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _check_ownership(item_id: int, user_id: int):
    """检查条目归属权。不存在返回 404，不属于当前用户返回 403。

    Args:
        item_id: 条目 ID。
        user_id: 当前登录用户 ID。

    Returns:
        条目的 ``(id, user_id)`` 行数据。

    Raises:
        HTTPException(404): 条目不存在。
        HTTPException(403): 条目不属于当前用户。
    """
    row = db.query_one(
        "SELECT id, user_id FROM content_items WHERE id = ?", (item_id,)
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="条目不存在。")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权操作该条目。")
    return row


# ── routes ────────────────────────────────────────────────────────────────

@router.get("")
def list_items(
    kind: str | None = Query(default=None, description="过滤类型: calendar_event | contact | task | note"),
    q: str | None = Query(default=None, description="在 title / body 中模糊搜索"),
    from_date: str | None = Query(default=None, description="日历事件范围起始 (ISO 日期/时间)"),
    to_date: str | None = Query(default=None, description="日历事件范围结束 (ISO 日期/时间)"),
    status: str | None = Query(default=None, description="任务状态过滤: todo | done"),
    category: str | None = Query(default=None, description="笔记分类过滤"),
    current_user: dict = Depends(get_current_user),
):
    """查询当前用户的条目列表，支持多维度组合过滤。

    过滤参数可任意组合。JSON 字段（如 ``meta_json`` 中的 ``start_at``、
    ``status``、``category``）通过 SQLite 的 ``json_extract`` 查询。
    """
    conditions = ["user_id = ?"]
    params: list = [current_user["user_id"]]

    if kind:
        if kind not in VALID_KINDS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"无效的类型: {kind}")
        conditions.append("kind = ?")
        params.append(kind)

    if q:
        conditions.append("(title LIKE ? OR body LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])

    if kind == "calendar_event":
        if from_date:
            conditions.append("json_extract(meta_json, '$.start_at') >= ?")
            params.append(from_date)
        if to_date:
            conditions.append("json_extract(meta_json, '$.start_at') <= ?")
            params.append(to_date)

    if kind == "task" and status:
        conditions.append("json_extract(meta_json, '$.status') = ?")
        params.append(status)

    if kind == "note" and category:
        conditions.append("json_extract(meta_json, '$.category') = ?")
        params.append(category)

    # 动态构建 WHERE — 所有条件字符串都是硬编码常量，用户输入仅通过 ? 参数化
    sql = f"SELECT * FROM content_items WHERE {' AND '.join(conditions)} ORDER BY id DESC"
    rows = db.query_all(sql, tuple(params))
    return {"items": [_item_to_dict(r) for r in rows]}


@router.get("/{item_id}")
def get_item(item_id: int, current_user: dict = Depends(get_current_user)):
    """获取单条条目详情（含完整的 meta_json）。"""
    row = _check_ownership(item_id, current_user["user_id"])
    # _check_ownership 已确认归属权，re-fetch 获取完整行
    full = db.query_one("SELECT * FROM content_items WHERE id = ?", (item_id,))
    return {"item": _item_to_dict(full)}


@router.post("", status_code=status.HTTP_200_OK)
def create_item(
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    """创建新条目。

    ``meta_json`` 可以是 dict 或已序列化的 JSON 字符串，内部统一转为字符串存储。

    Args:
        body: JSON body，需包含 ``kind``、``title``，可选 ``body``、
              ``meta_json``、``mailbox_id``。
    """
    kind = (body.get("kind") or "").strip()
    if kind not in VALID_KINDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"无效的类型: {kind}，仅支持 {', '.join(sorted(VALID_KINDS))}。",
        )

    title = (body.get("title") or "").strip()
    body_text = (body.get("body") or "")
    raw_meta = body.get("meta_json") or "{}"
    mailbox_id = body.get("mailbox_id") or None

    # meta_json 可能是 dict（前端直接传 JSON）或已序列化的字符串
    if isinstance(raw_meta, str):
        meta_json_str = raw_meta
    else:
        meta_json_str = json.dumps(raw_meta, ensure_ascii=False)

    if not title:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="标题不能为空。")

    now = utc_iso()
    db.execute(
        """INSERT INTO content_items(user_id, mailbox_id, kind, title, body, meta_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            current_user["user_id"],
            mailbox_id,
            kind,
            title,
            body_text,
            meta_json_str,
            now,
            now,
        ),
    )
    track("item_created", kind=kind)
    return {"message": "已创建。"}


@router.put("/{item_id}")
def update_item(
    item_id: int,
    body: dict,
    current_user: dict = Depends(get_current_user),
):
    """部分更新条目（仅更新传入的字段）。

    可更新字段：``title``、``body``、``meta_json``。未传入的字段保持不变。
    """
    _check_ownership(item_id, current_user["user_id"])

    updates: list[str] = []
    params: list = []

    if "title" in body:
        updates.append("title = ?")
        params.append(body["title"])
    if "body" in body:
        updates.append("body = ?")
        params.append(body["body"])
    if "meta_json" in body:
        updates.append("meta_json = ?")
        raw_meta = body["meta_json"]
        if isinstance(raw_meta, str):
            params.append(raw_meta)
        else:
            params.append(json.dumps(raw_meta, ensure_ascii=False))

    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="没有可更新的字段。")

    updates.append("updated_at = ?")
    params.append(utc_iso())
    params.append(item_id)

    db.execute(
        f"UPDATE content_items SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    return {"message": "已更新。"}


@router.delete("/{item_id}")
def delete_item(item_id: int, current_user: dict = Depends(get_current_user)):
    """删除指定条目。需确认归属权。"""
    _check_ownership(item_id, current_user["user_id"])
    db.execute("DELETE FROM content_items WHERE id = ?", (item_id,))
    return {"message": "已删除。"}
