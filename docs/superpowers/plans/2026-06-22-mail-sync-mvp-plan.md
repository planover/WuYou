# WuYou 邮件同步 MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 WuYou（FastAPI + SQLite + 静态前端）基础上，实现“手动同步 + 定时后台同步（默认 30 分钟）”，并把同步范围扩展到 `INBOX + Sent + Trash + Archive + Junk/Spam`，支持增量拉取、任务队列、并发=2、可观测的同步任务状态；同时保留 `worker` 模式切换入口。

**Architecture:** 所有同步触发统一落到 `sync_jobs` 表；同步逻辑（IMAP folder 发现/映射、UID 增量拉取、附件下载、入库去重、游标更新）只写一份，由 `inprocess`（默认）执行器或 `worker` 进程消费队列执行。对无法映射的文件夹保留原始 IMAP 命名，作为 `custom` 文件夹记录并可选择开启同步。

**Tech Stack:** Python 3.12、FastAPI、SQLite(WAL)、imaplib/smtplib、pytest（新增用于测试）。

---

## 文件结构与改动点（先锁边界）

**新增/修改文件概览：**

- Modify: `backend/app/core/config.py`
- Modify: `backend/app/core/database.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/routes_accounts.py`
- Modify: `backend/app/api/routes_mail.py`
- Create: `backend/app/api/routes_sync.py`
- Modify: `backend/app/services/mail_client.py`
- Create: `backend/app/services/sync/constants.py`
- Create: `backend/app/services/sync/folder_discovery.py`
- Create: `backend/app/services/sync/sync_engine.py`
- Create: `backend/app/services/sync/jobs.py`
- Create: `backend/app/services/sync/executor_inprocess.py`
- Create: `backend/app/worker.py`（可选运行 worker 模式）
- Modify: `backend/requirements.txt`（增加 pytest 为开发测试依赖；如你倾向拆分 dev 依赖，后续再做）
- Create: `backend/tests/test_folder_discovery.py`
- Create: `backend/tests/test_sync_jobs.py`
- Create: `backend/tests/test_uid_incremental.py`
- Modify: `backend/app/static/js/app.js`
- (Optional) Modify: `backend/app/static/css/app.css`（增加 folder 切换 UI 样式）

---

## Task 1: 增加配置项（sync mode/interval/concurrency）

**Files:**
- Modify: `backend/app/core/config.py`

- [ ] **Step 1: 写一个最小单元测试，验证新配置默认值**

Create `backend/tests/test_settings_sync_defaults.py`：

```python
from app.core.config import get_settings


def test_sync_defaults():
    s = get_settings()
    assert s.sync_interval_minutes == 30
    assert s.sync_concurrency == 2
    assert s.sync_mode in {"inprocess", "worker"}
    assert s.sync_folders_default == ["inbox", "sent", "trash", "archive", "junk"]
```

- [ ] **Step 2: 运行测试（应失败，因为配置未实现）**

Run: `pytest -q`
Expected: FAIL（缺少配置字段）

- [ ] **Step 3: 实现配置字段**

在 `backend/app/core/config.py` 的 `Settings` 增加字段：

```python
    sync_mode: Literal["inprocess", "worker"] = "inprocess"
    sync_interval_minutes: int = 30
    sync_concurrency: int = 2
    sync_folders_default: list[str] = ["inbox", "sent", "trash", "archive", "junk"]
```

- [ ] **Step 4: 再跑测试（应通过）**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config.py backend/tests/test_settings_sync_defaults.py
git commit -m "feat(sync): add sync scheduler settings defaults"
```

---

## Task 2: 数据库 schema 与轻量迁移（SQLite 兼容老库）

**Files:**
- Modify: `backend/app/core/database.py`
- Test: `backend/tests/test_db_migrations.py`

> 说明：当前 `Database.init()` 只做 `executescript(SCHEMA)`；对已存在表不会新增列。必须实现“检测列存在 → ALTER TABLE ADD COLUMN”的轻量迁移。

- [ ] **Step 1: 写失败测试（创建旧库后 init 应自动补齐新列/新表）**

Create `backend/tests/test_db_migrations.py`：

```python
import sqlite3
from pathlib import Path

from app.core.database import Database


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def test_db_init_adds_new_tables_and_columns(tmp_path: Path):
    db_path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT UNIQUE,
          email TEXT UNIQUE,
          phone TEXT UNIQUE,
          password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          mailbox_id INTEGER,
          external_id TEXT NOT NULL,
          folder TEXT NOT NULL DEFAULT 'INBOX',
          subject TEXT NOT NULL,
          sender TEXT NOT NULL,
          recipients TEXT NOT NULL DEFAULT '[]',
          snippet TEXT NOT NULL DEFAULT '',
          body_text TEXT NOT NULL DEFAULT '',
          body_html TEXT NOT NULL DEFAULT '',
          raw_headers TEXT NOT NULL DEFAULT '{}',
          attachments_json TEXT NOT NULL DEFAULT '[]',
          unread INTEGER NOT NULL DEFAULT 1,
          starred INTEGER NOT NULL DEFAULT 0,
          has_attachments INTEGER NOT NULL DEFAULT 0,
          remote_content_allowed INTEGER NOT NULL DEFAULT 0,
          received_at TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(user_id, mailbox_id, external_id)
        );
        """
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    db.init()

    conn2 = sqlite3.connect(db_path)
    assert "sync_jobs" in {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "mailbox_folders" in {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "mailbox_folder_state" in {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    cols = _cols(conn2, "messages")
    assert "folder_role" in cols
    assert "imap_folder" in cols
```

- [ ] **Step 2: 跑测试（应失败）**

Run: `pytest -q`
Expected: FAIL（表/列不存在）

- [ ] **Step 3: 在 `Database.init()` 中加入轻量迁移函数**

实现思路（写入 `backend/app/core/database.py`）：

1. `executescript(SCHEMA)` 确保新表能创建（对不存在的表生效）。
2. 对“需要新增列的老表”执行：
   - `PRAGMA table_info(messages)` 判断列是否存在
   - 不存在则 `ALTER TABLE messages ADD COLUMN folder_role TEXT NOT NULL DEFAULT 'inbox'`
   - 不存在则 `ALTER TABLE messages ADD COLUMN imap_folder TEXT NOT NULL DEFAULT 'INBOX'`

示例代码片段（需写到 `Database` 类里）：

```python
    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        cols = {row["name"] for row in self.connect().execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            self.connect().execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    def init(self) -> None:
        with self._lock:
            connection = self.connect()
            connection.executescript(SCHEMA)
            # migrations
            self._ensure_column("messages", "folder_role", "folder_role TEXT NOT NULL DEFAULT 'inbox'")
            self._ensure_column("messages", "imap_folder", "imap_folder TEXT NOT NULL DEFAULT 'INBOX'")
            connection.commit()
```

- [ ] **Step 4: 扩展 SCHEMA 新增三张表与 messages 新列（用于新库直接创建）**

在 `SCHEMA` 中：
- 为 `messages` CREATE TABLE 语句追加 `folder_role`、`imap_folder`
- 追加 `mailbox_folders`、`mailbox_folder_state`、`sync_jobs` 三张表

- [ ] **Step 5: 再跑测试（应通过）**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/database.py backend/tests/test_db_migrations.py
git commit -m "feat(sync): add sync tables and sqlite migrations"
```

---

## Task 3: 文件夹发现与映射（special-use 优先，猜测兜底，保留 custom）

**Files:**
- Create: `backend/app/services/sync/constants.py`
- Create: `backend/app/services/sync/folder_discovery.py`
- Test: `backend/tests/test_folder_discovery.py`

- [ ] **Step 1: 写测试（给定 folder 列表/flags，能映射到 role；无法映射则 custom）**

Create `backend/tests/test_folder_discovery.py`：

```python
from app.services.sync.folder_discovery import classify_folder


def test_classify_by_special_use():
    assert classify_folder(imap_name="[Gmail]/Sent Mail", flags=["\\\\Sent"]) == "sent"
    assert classify_folder(imap_name="[Gmail]/Trash", flags=["\\\\Trash"]) == "trash"
    assert classify_folder(imap_name="[Gmail]/Spam", flags=["\\\\Junk"]) == "junk"


def test_classify_by_guess():
    assert classify_folder(imap_name="已发送", flags=[]) == "sent"
    assert classify_folder(imap_name="垃圾邮件", flags=[]) == "junk"
    assert classify_folder(imap_name="Archive", flags=[]) == "archive"


def test_classify_fallback_custom():
    assert classify_folder(imap_name="项目组-通知", flags=[]) == "custom"
```

- [ ] **Step 2: 跑测试（应失败）**

Run: `pytest -q`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `classify_folder()` 与关键词表**

Create `backend/app/services/sync/constants.py`：

```python
ROLE_INBOX = "inbox"
ROLE_SENT = "sent"
ROLE_TRASH = "trash"
ROLE_ARCHIVE = "archive"
ROLE_JUNK = "junk"
ROLE_CUSTOM = "custom"

DEFAULT_ROLES = [ROLE_INBOX, ROLE_SENT, ROLE_TRASH, ROLE_ARCHIVE, ROLE_JUNK]

SPECIAL_USE_TO_ROLE = {
    r"\Sent": ROLE_SENT,
    r"\Trash": ROLE_TRASH,
    r"\Archive": ROLE_ARCHIVE,
    r"\Junk": ROLE_JUNK,
    r"\Spam": ROLE_JUNK,
}

GUESS_PATTERNS = {
    ROLE_SENT: ["sent", "已发送", "发件箱", "outbox"],
    ROLE_TRASH: ["trash", "deleted", "已删除", "垃圾箱"],
    ROLE_ARCHIVE: ["archive", "归档"],
    ROLE_JUNK: ["junk", "spam", "垃圾邮件"],
}
```

Create `backend/app/services/sync/folder_discovery.py`：

```python
from __future__ import annotations

from .constants import GUESS_PATTERNS, ROLE_CUSTOM, SPECIAL_USE_TO_ROLE


def classify_folder(imap_name: str, flags: list[str]) -> str:
    for flag in flags:
        role = SPECIAL_USE_TO_ROLE.get(flag)
        if role:
            return role
    lower = imap_name.lower()
    for role, keywords in GUESS_PATTERNS.items():
        if any(key.lower() in lower for key in keywords):
            return role
    return ROLE_CUSTOM
```

- [ ] **Step 4: 再跑测试（应通过）**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/sync backend/tests/test_folder_discovery.py
git commit -m "feat(sync): add imap folder role mapping with custom fallback"
```

---

## Task 4: sync_jobs 队列与并发控制（DB 驱动队列）

**Files:**
- Create: `backend/app/services/sync/jobs.py`
- Test: `backend/tests/test_sync_jobs.py`

- [ ] **Step 1: 写测试（queued → running → success/failed 的状态流）**

Create `backend/tests/test_sync_jobs.py`：

```python
import json
from pathlib import Path

from app.core.database import Database
from app.services.sync.jobs import create_job, claim_next_job, finish_job


def test_job_lifecycle(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    db.init()

    user_id = db.execute(
        "INSERT INTO users(username, email, phone, password_hash, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        ("u", None, None, "x", "t", "t"),
    ).lastrowid
    mailbox_id = db.execute(
        """
        INSERT INTO mailbox_accounts(
          user_id, display_name, email_address, provider, imap_host, imap_port, imap_ssl,
          smtp_host, smtp_port, smtp_ssl, auth_type, username, encrypted_secret, sync_enabled,
          created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (user_id, "m", "m@example.com", "custom", "imap", 993, 1, "smtp", 465, 1, "app_password", "m", "enc", 1, "t", "t"),
    ).lastrowid

    job_id = create_job(db, int(user_id), int(mailbox_id), trigger="manual", folder_roles=["inbox"])
    job = claim_next_job(db, concurrency=1)
    assert job["id"] == job_id
    finish_job(db, job_id=job_id, ok=True, stats={"inserted": 1})

    row = db.query_one("SELECT status, stats_json FROM sync_jobs WHERE id = ?", (job_id,))
    assert row["status"] == "success"
    assert json.loads(row["stats_json"])["inserted"] == 1
```

- [ ] **Step 2: 跑测试（应失败）**

Run: `pytest -q`
Expected: FAIL（jobs 模块不存在）

- [ ] **Step 3: 实现 `jobs.py`（create/claim/finish）**

Create `backend/app/services/sync/jobs.py`（核心函数必须纯 DB，可同时被 inprocess/worker 调用）：

```python
from __future__ import annotations

import json
from typing import Any, Iterable

from app.core.database import Database
from app.core.security import utc_iso


def create_job(db: Database, user_id: int, mailbox_id: int, trigger: str, folder_roles: list[str]) -> int:
    now = utc_iso()
    cur = db.execute(
        """
        INSERT INTO sync_jobs(user_id, mailbox_id, trigger, status, folder_roles_json, stats_json, error, created_at)
        VALUES (?, ?, ?, 'queued', ?, '{}', NULL, ?)
        """,
        (user_id, mailbox_id, trigger, json.dumps(folder_roles, ensure_ascii=False), now),
    )
    return int(cur.lastrowid)


def claim_next_job(db: Database, concurrency: int) -> dict | None:
    # 简化实现：由执行器控制并发；这里仅挑一个 queued 的任务并标记 running
    row = db.query_one(
        "SELECT * FROM sync_jobs WHERE status = 'queued' ORDER BY id ASC LIMIT 1"
    )
    if not row:
        return None
    db.execute(
        "UPDATE sync_jobs SET status = 'running', started_at = ? WHERE id = ? AND status = 'queued'",
        (utc_iso(), row["id"]),
    )
    # 重新读一次，确保拿到最新 started_at
    return dict(db.query_one("SELECT * FROM sync_jobs WHERE id = ?", (row["id"],)))


def finish_job(db: Database, job_id: int, ok: bool, stats: dict[str, Any] | None = None, error: str | None = None) -> None:
    status = "success" if ok else "failed"
    db.execute(
        """
        UPDATE sync_jobs
        SET status = ?, finished_at = ?, stats_json = ?, error = ?
        WHERE id = ?
        """,
        (status, utc_iso(), json.dumps(stats or {}, ensure_ascii=False), error, job_id),
    )
```

> 注意：真正并发限制会在 `executor_inprocess.py` / `worker.py` 外层做“最多同时跑 N 个”，`claim_next_job()` 先做最小能力。后续如果需要严格并发锁，再把“running 数量统计 + claim”放进同一事务。

- [ ] **Step 4: 再跑测试（应通过）**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/sync/jobs.py backend/tests/test_sync_jobs.py
git commit -m "feat(sync): add db-backed sync_jobs queue primitives"
```

---

## Task 5: UID 增量拉取与入库（改造 mail_client，同步多 folder）

**Files:**
- Modify: `backend/app/services/mail_client.py`
- Create: `backend/app/services/sync/sync_engine.py`
- Test: `backend/tests/test_uid_incremental.py`

目标：把现有 `sync_inbox()` 从“INBOX + SEARCH ALL”改为：
- 支持指定 folder（真实 IMAP name）
- 用 UID 增量拉取
- 返回统一结构：`fetched/inserted/last_uid` 等统计

- [ ] **Step 1: 写测试（验证 UID 范围构造与 last_uid 更新）**

Create `backend/tests/test_uid_incremental.py`：

```python
from app.services.sync.sync_engine import build_uid_range


def test_uid_range():
    assert build_uid_range(0) == "1:*"
    assert build_uid_range(9) == "10:*"
```

- [ ] **Step 2: 跑测试（应失败）**

Run: `pytest -q`
Expected: FAIL（sync_engine 不存在）

- [ ] **Step 3: 实现 sync_engine 的纯函数与“单 folder 拉取接口（先不连真实 IMAP）”**

Create `backend/app/services/sync/sync_engine.py`：

```python
from __future__ import annotations

import json
from typing import Any


def build_uid_range(last_uid: int) -> str:
    return f"{max(last_uid + 1, 1)}:*"
```

- [ ] **Step 4: 再跑测试（应通过）**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 5: 扩展实现：把 `mail_client.py` 拆出“按 folder 增量拉取”函数**

在 `backend/app/services/mail_client.py` 中：
- 新增 `sync_folder_incremental(account, secret, imap_folder, last_uid, attachment_root)` 返回：
  - `messages: list[dict]`
  - `new_last_uid: int`
  - `uidvalidity: int|None`

实现要点（在计划执行时写完整代码）：
- `SELECT imap_folder`
- 获取 UIDVALIDITY：优先 `client.response("UIDVALIDITY")`
- `UID SEARCH UID <range>` 取 UID 列表
- 分批 `UID FETCH <uids> (RFC822 FLAGS)` 解析邮件并复用现有 `_extract_body()`
- message dict 追加：
  - `folder_role`（由外层传入）
  - `imap_folder`（真实 folder name）
- `new_last_uid = max(uids)`（无增量则保持 old）

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/mail_client.py backend/app/services/sync/sync_engine.py backend/tests/test_uid_incremental.py
git commit -m "feat(sync): add uid incremental sync building blocks"
```

---

## Task 6: 多文件夹同步（folder mapping + folder_state + messages 入库）

**Files:**
- Create: `backend/app/services/sync/sync_engine.py`（继续补齐）
- Modify: `backend/app/services/mail_client.py`
- Modify: `backend/app/core/database.py`（如需要补充索引）

- [ ] **Step 1: 实现“确保 folder 映射存在”的服务函数**

在 `sync_engine.py` 增加：
- `ensure_folders(db, mailbox_account, secret) -> list[folder_rows]`
  - 如果 `mailbox_folders` 没数据：连接 IMAP 做 LIST→分类→写入表
  - 必须写入：`inbox`（imap_name=INBOX）至少一条
  - 未映射 folder 写 `custom`，默认 enabled=0

- [ ] **Step 2: 实现“同步一个 mailbox_id”**

在 `sync_engine.py` 增加：
- `sync_mailbox(db, mailbox_row, secret, folder_roles, attachment_root) -> stats`
流程：
1. 读取/建立 folder 映射
2. 对每个 role 找到 enabled 的 imap_name（`custom` 除非用户开启）
3. 读取 `mailbox_folder_state`（没有就初始化 last_uid=0）
4. 调用 `sync_folder_incremental(...)`
5. 对每封邮件执行 INSERT OR IGNORE（复用现有插入 SQL，但需补 `folder_role/imap_folder`）
6. 更新 `mailbox_folder_state`（uidvalidity/last_uid/last_sync_at/last_error）
7. 汇总 stats

- [ ] **Step 3: 本地跑一次手动同步冒烟（用你自己的邮箱测试）**

Run: `docker compose up --build`
操作：登录 → 添加邮箱 → 点击同步
Expected：`sync_jobs` 记录 + 邮件列表能看到多个 folder 的邮件（至少 INBOX）

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/sync/sync_engine.py backend/app/services/mail_client.py backend/app/core/database.py
git commit -m "feat(sync): sync multiple folders with folder_state cursor"
```

---

## Task 7: inprocess 执行器与 scheduler（默认 30 分钟 + 并发=2）

**Files:**
- Create: `backend/app/services/sync/executor_inprocess.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: 实现执行器（线程池 + DB 轮询队列）**

Create `backend/app/services/sync/executor_inprocess.py`：
- `class InProcessSyncExecutor:`
  - `start()`：启动 N 个 worker 线程（N=sync_concurrency）
  - `stop()`：停止线程
  - `run_loop()`：循环：
    - `claim_next_job()`
    - 读取 mailbox、解密 secret
    - 调用 `sync_mailbox()`
    - `finish_job()`

并实现 `run_scheduler_loop()`：
- 每 `sync_interval_minutes` 创建 scheduled job（跳过已有 queued/running 的 mailbox）

- [ ] **Step 2: 在 FastAPI startup 启动执行器（仅 inprocess 模式）**

修改 `backend/app/main.py`：
- `startup()` 中：
  - `db.init()`
  - 若 `settings.sync_mode == "inprocess"`：启动 executor + scheduler 线程

- [ ] **Step 3: 手动触发 + 等待定时任务冒烟**

Expected：
- 手动同步可以立即排队执行
- 30 分钟后（可临时把 interval 改 1 分钟测试）会自动创建 scheduled job

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/sync/executor_inprocess.py backend/app/main.py
git commit -m "feat(sync): add inprocess executor and scheduler"
```

---

## Task 8: worker 模式入口（wuyou-worker）

**Files:**
- Create: `backend/app/worker.py`
- (Optional) Modify: `Dockerfile` / `docker-compose.yml`

- [ ] **Step 1: 实现 `python -m app.worker` 的主循环**

`worker.py` 做：
- `db.init()`
- while True：
  - claim job
  - sync_mailbox
  - finish job
  - sleep 1~2 秒

- [ ] **Step 2: 文档补充运行方式**

在 `README.md` 增加：
- `UNIBOX_SYNC_MODE=worker` 时如何启动 worker
  - 方案：同镜像不同 command（compose 增加一个 `wuyou-worker` service）

- [ ] **Step 3: Commit**

```bash
git add backend/app/worker.py README.md
git commit -m "feat(sync): add optional worker process to consume sync_jobs"
```

---

## Task 9: API 路由（/api/sync/jobs + accounts sync 改为入队）

**Files:**
- Create: `backend/app/api/routes_sync.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/routes_accounts.py`

- [ ] **Step 1: 新增 routes_sync**

实现：
- `POST /api/sync/jobs`：创建任务返回 job_id
- `GET /api/sync/jobs`：列出任务（分页可后续做，先 limit 50）
- `GET /api/sync/jobs/{id}`：任务详情

- [ ] **Step 2: 改造 `POST /api/accounts/{id}/sync`**

从“直接同步并写入 messages”改为：
- `create_job(...)`（trigger=manual）
- 返回 `{message:"已加入队列", job_id: ...}`

- [ ] **Step 3: main.py include_router**

将 `routes_sync.router` 加入 app。

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/routes_sync.py backend/app/api/routes_accounts.py backend/app/main.py
git commit -m "feat(sync): add sync jobs api and enqueue account sync"
```

---

## Task 10: 邮件列表按 folder_role 筛选 + 前端 UI 切换

**Files:**
- Modify: `backend/app/api/routes_mail.py`
- Modify: `backend/app/static/js/app.js`
- (Optional) Modify: `backend/app/static/css/app.css`

- [ ] **Step 1: 后端 inbox API 增加 folder_role 参数**

`GET /api/mail/inbox`：
- 新增 `folder_role`，允许 `all|inbox|sent|trash|archive|junk|custom`
- SQL where 增加 `messages.folder_role = ?`（当不是 all）

- [ ] **Step 2: 前端增加 folder 切换控件**

在 `renderInbox()` 的 toolbar 增加下拉：
- `全部/收件箱/已发送/垃圾箱/归档/垃圾邮件`
- 切换后重新请求 `/api/mail/inbox?folder_role=...`

- [ ] **Step 3: 同步按钮改为“入队 + 轮询任务状态（可选）”**

前端 `syncAll()`：
- 调用 `/api/accounts/{id}/sync` 得到 `job_id`
- toast：`已加入队列`
- 可选：每 2 秒 poll `/api/sync/jobs/{job_id}`，直到 success/failed（最多 60 秒）

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/routes_mail.py backend/app/static/js/app.js backend/app/static/css/app.css
git commit -m "feat(ui): add folder role filter and sync job status polling"
```

---

## Task 11: 测试与验收清单（必须跑通）

**Commands:**
- [ ] `pytest -q`（单元测试全绿）
- [ ] `docker compose up --build`（可启动）
- [ ] 手动同步：添加 1 个邮箱 → 同步 → 列表出现邮件
- [ ] 多邮箱并发：添加 3 个邮箱 → 点“同步全部” → 同时只跑 2 个，1 个排队
- [ ] folder_role 切换：至少 INBOX 与 Sent 能切换看到不同邮件（若服务商支持或存在邮件）
- [ ] 远程内容默认阻止与附件自动下载：保持现有行为不回归

---

## 计划自检（由执行者在实现前再做一遍）

- 覆盖检查：设计稿中 `sync_jobs / mailbox_folders / folder_state / folder_role` 均在任务中有实现对应。
- 占位扫描：计划中未出现 `TODO/TBD`，每个任务都有明确文件路径、代码骨架与运行命令。
- 命名一致：role 枚举统一为 `inbox/sent/trash/archive/junk/custom`；映射失败保留原 IMAP 名到 `imap_name`。

