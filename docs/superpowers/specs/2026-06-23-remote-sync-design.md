# WuYou 总账户远程同步 MVP 设计稿

> 目标：让多台设备的 WuYou 通过"总账户密码"自动双向同步用户资料（设置/标签/邮箱账户信息/文件夹映射/插件清单/日历通讯录等），同步服务内置于 WuYou 自身，无需额外部署。

## 已确认决策

| 决策 | 选择 |
|------|------|
| 同步内容 | **全量**：设置项 + 标签 + 邮箱账户基本信息（不含密钥）+ 文件夹映射 + 已安装插件清单 + content_items |
| 服务端 | **内置于 WuYou**：自身即同步服务器，另一台填对方地址即可 |
| 同步机制 | **自动双向合并**：定时 + 手动触发，"最后修改时间"做冲突调解 |
| 认证方式 | **总账户密码**：用用户名+密码登录远程 WuYou API |

---

## 1. 架构

### 1.1 总体流程

```
┌──────────────┐                    ┌──────────────┐
│  WuYou A    │ ←── 每 N 分钟 ──→  │  WuYou B    │
│  (本地)      │   HTTP API 双向    │  (远程)      │
│              │   推拉合并          │              │
│  user: alice │                    │  user: alice │
└──────────────┘                    └──────────────┘
       │                                    │
       └──── 同一个总账户(用户名+密码) ──────┘
             鉴权：POST /api/auth/login
             同步：POST /api/sync/remotes/*
```

### 1.2 内嵌服务端

WuYou 不加新端口、不加新容器。在现有 FastAPI 中增加 `/api/sync/remotes` 路由组：

- `POST /api/sync/remotes/pull` — 远程端（服务端）返回当前用户的全量数据快照
- `POST /api/sync/remotes/push` — 远程端接受并合并本地推送的数据
- `POST /api/sync/remotes/status` — 远程端返回版本摘要（供本地判断是否需要同步）

认证方式：客户端每次请求先调 `/api/auth/login` 获取临时 Bearer token，然后带 token 调同步接口。

---

## 2. 同步数据模型

### 2.1 同步快照（Snapshot）

一次 `pull` 返回的 JSON 结构：

```json
{
  "snapshot_id": "2026-06-23T12:00:00Z",
  "user_public": {
    "username": "alice",
    "email": "alice@example.com"
  },
  "settings": {
    "locale": "zh-CN",
    "theme": "light",
    "remote_content_default": false,
    "attachment_auto_download": true,
    "telemetry_enabled": false,
    "translation_provider": "mymemory",
    "remote_sync_endpoint": "http://192.168.1.100:8000"
  },
  "tags": [
    {"name": "重要", "color": "#d93025", "priority": 9, "updated_at": "..."},
    ...
  ],
  "mailbox_accounts": [
    {
      "display_name": "工作邮箱",
      "email_address": "work@gmail.com",
      "provider": "gmail",
      "imap_host": "imap.gmail.com",
      "imap_port": 993,
      "imap_ssl": true,
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 465,
      "smtp_ssl": true,
      "auth_type": "app_password",
      "username": "work@gmail.com",
      "sync_enabled": true,
      "updated_at": "..."
    }
  ],
  "folder_mappings": [
    {"mailbox_email": "work@gmail.com", "role": "inbox", "imap_name": "INBOX", "enabled": true, "updated_at": "..."},
    ...
  ],
  "installed_plugins": [
    {"plugin_id": "mail-label", "name": "邮件标签助手", "version": "1.0", "type": "extension", "category": "效率工具", "installed_at": "..."},
    ...
  ],
  "content_items": [
    {"kind": "contact", "title": "张三", "body": "...", "meta_json": "...", "updated_at": "..."},
    ...
  ]
}
```

### 2.2 冲突调解策略

每条数据带 `updated_at`（ISO 8601 UTC）。合并时：

1. 本地无、远程有 → 本地写入远程值
2. 本地有、远程无 → 本地不删（保留），并标记为"推送中"
3. 本地有、远程有且 `remote.updated_at > local.updated_at` → 覆盖本地
4. 本地有、远程有且 `remote.updated_at < local.updated_at` → 保留本地（待推送）
5. `updated_at` 相同 → 保留本地（不冲突覆盖）

**关键约束**：`updated_at` 以**本地时钟**为准。两台设备应在部署时设置正确的时区（Docker 中通过 `TZ=Asia/Shanghai` 等）。后续可升级为 Lamport 时间戳。

---

## 3. API 设计

### 3.1 推送（本地 → 远程）

```
POST /api/sync/remotes/push
Authorization: Bearer <token>

Body:
{
  "client_snapshot_id": "2026-06-23T11:55:00Z",
  "settings": { "locale": "zh-CN", ... },
  "tags": [...],
  "mailbox_accounts": [...],
  "folder_mappings": [...],
  "installed_plugins": [...],
  "content_items": [...]
}

Response 200:
{
  "remote_snapshot_id": "2026-06-23T12:00:00Z",
  "merged": { "settings": 1, "tags": 2, "mailbox_accounts": 0, ... },
  "conflicts": []
}
```

服务端逻辑：
1. 接收客户端数据
2. 逐表按 updated_at 合并
3. 返回合并统计与冲突列表

### 3.2 拉取（远程 → 本地）

```
POST /api/sync/remotes/pull
Authorization: Bearer <token>

Body:
{ "last_known_snapshot_id": "2026-06-23T11:50:00Z" }

Response 200:
{
  "snapshot_id": "2026-06-23T12:00:00Z",
  "changed_since": "2026-06-23T11:50:00Z",
  "data": { <全量快照> }
}
```

`last_known_snapshot_id` 为可选：传入则只返回此时间之后有变更的数据（增量），不传则返回全量。

### 3.3 状态询问（轻量，用于判断是否需要同步）

```
POST /api/sync/remotes/status
Authorization: Bearer <token>

Response 200:
{
  "snapshot_id": "2026-06-23T12:00:00Z",
  "summary": {
    "settings_count": 7,
    "tags_count": 5,
    "mailbox_count": 2,
    "folder_mapping_count": 10,
    "plugins_count": 1,
    "content_items_count": 0
  }
}
```

本地客户端定期调此接口，对比自己的状态，有差异时才触发 pull/push。

### 3.4 本地触发端点（供前端手动触发）

```
POST /api/sync/remote/now
Authorization: Bearer <token>

Body:
{ "action": "pull" | "push" | "full" }

Response 200:
{ "message": "同步已启动。", "job_id": "sync-xxx" }
```

---

## 4. 数据模型（新增）

### 4.1 新表：`sync_peers`

记录已配对的远程 WuYou 实例。

```sql
CREATE TABLE IF NOT EXISTS sync_peers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label TEXT NOT NULL DEFAULT '远程设备',
    url TEXT NOT NULL,
    remote_username TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_sync_at TEXT,
    last_status TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 4.2 新表：`sync_snapshots`

记录每次同步的快照版本用于增量判断。

```sql
CREATE TABLE IF NOT EXISTS sync_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    snapshot_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
```

---

## 5. 服务端同步逻辑（核心引擎）

### 5.1 全量快照生成

```python
def build_full_snapshot(db, user_id):
    settings = db.query_all("SELECT key, value_json, updated_at FROM settings WHERE user_id=?", (user_id,))
    tags = db.query_all("SELECT name, color, priority, updated_at FROM tags WHERE user_id=?", (user_id,))
    # 邮箱账户（不含 encrypted_secret）
    accounts = db.query_all(
        "SELECT display_name, email_address, provider, imap_host, imap_port, "
        "imap_ssl, smtp_host, smtp_port, smtp_ssl, auth_type, username, "
        "sync_enabled, updated_at FROM mailbox_accounts WHERE user_id=?",
        (user_id,))
    # 文件夹映射
    folders = db.query_all(
        "SELECT mf.role, mf.imap_name, mf.enabled, ma.email_address AS mailbox_email, mf.updated_at "
        "FROM mailbox_folders mf JOIN mailbox_accounts ma ON mf.mailbox_id=ma.id "
        "WHERE mf.user_id=?", (user_id,))
    # 插件清单
    plugins = db.query_all(
        "SELECT plugin_id, name, version, type, category, enabled, installed_at, updated_at "
        "FROM installed_plugins WHERE user_id=? AND enabled=1", (user_id,))
    # content_items
    content = db.query_all(
        "SELECT kind, title, body, meta_json, updated_at FROM content_items WHERE user_id=?", (user_id,))
    return { ... }
```

### 5.2 合并算法（push 端接收时执行）

```python
def merge_snapshot(db, user_id, client_data):
    merged = {}
    conflicts = []

    # 对每类数据：
    # 1. 取本地记录集
    # 2. 取远程记录集
    # 3. 按主键（name/mailbox_email/plugin_id 等）对齐
    # 4. 对每条：比较 updated_at，取新的
    # 5. 写回 DB

    # 示例：tags 合并
    local_tags = {t["name"]: t for t in db.query_all(...) }
    remote_tags = {t["name"]: t for t in client_data.get("tags", [])}
    for name, remote in remote_tags.items():
        local = local_tags.get(name)
        if local is None:
            # 远程有、本地无 → 写入
            db.execute("INSERT INTO tags(...) VALUES(...)")
            merged["tags"] += 1
        elif parse_utc(remote["updated_at"]) > parse_utc(local["updated_at"]):
            # 远程更新 → 覆盖
            db.execute("UPDATE tags SET ... WHERE user_id=? AND name=?")
            merged["tags"] += 1
        elif parse_utc(remote["updated_at"]) == parse_utc(local["updated_at"]):
            # 时间相同 → 不动
            pass
        else:
            # 远程旧 → 记录冲突（本地在下次 push 时覆盖）
            conflicts.append({"item": "tags." + name, "reason": "local_newer"})

    return merged, conflicts
```

---

## 6. 客户端同步调度器

### 6.1 定时自动同步

配置项：
- `sync_remote_interval_minutes: int = 15`（默认 15 分钟）

调度器流程（与现有的 inprocess scheduler 解耦，独立线程）：
1. 每 N 分钟：
   - 从 `sync_peers` 表取所有 enabled peer
   - 对每个 peer：先用 `POST /sync/remotes/status` 判断是否有变更
   - 有变更：`POST /sync/remotes/push` 推送本地变更 → `POST /sync/remotes/pull` 拉取远程变更
   - 将本地合并结果写库

### 6.2 手动触发

前端设置页"同步管理"面板中提供：
- "立即同步（推拉全量）"按钮
- "仅推送"按钮
- "仅拉取"按钮
- 上次同步时间与状态显示

---

## 7. 前端

### 7.1 设置页新增"远程同步"面板

- 配对管理：添加/编辑/删除 sync_peers（URL + 用户名）
- 状态显示：上次同步时间、最近状态（成功/失败/原因）
- 手动操作按钮

### 7.2 API 端点前端对照

| 前端 UI | 后端端点 |
|---------|---------|
| 远程设备列表 | GET `/api/sync/peers` |
| 添加设备 | POST `/api/sync/peers` |
| 删除设备 | DELETE `/api/sync/peers/{id}` |
| 立即全量同步 | POST `/api/sync/remote/now` `{action:"full"}` |
| 仅推送 | POST `/api/sync/remote/now` `{action:"push"}` |
| 仅拉取 | POST `/api/sync/remote/now` `{action:"pull"}` |
| 同步历史 | GET `/api/sync/remote/history` |

---

## 8. 安全考虑

- 邮箱密钥/密码（`encrypted_secret`）**永不在同步中传输**。快照中 `mailbox_accounts` 不含此字段。
- 同步认证复用现有 `hash_password` + `verify_password` 的 login 端点。
- 远程 WuYou 应部署在可信网络（局域网/VPN），不在公网明文传输总账户密码。
- 后续可扩展 HTTPS + mTLS 加密传输通道。

---

## 9. 测试计划

| 测试项 | 验证点 |
|--------|--------|
| 快照生成 | build_full_snapshot 返回完整 6 类数据 |
| 合并-tags | 远程新 tag → 本地写入；远程旧 → 冲突记录 |
| 合并-settings | remote_updated > local → 覆盖 |
| 合并-accounts | 不含 encrypted_secret |
| 双端推拉 | 启动两台 WuYou → 定时触发 → 两端数据一致 |
| 手动触发 | POST /api/sync/remote/now → 返回 job_id → 状态可查 |

---

## 10. 开源与合规

- 同步引擎纯 Python 实现，无第三方同步库依赖
- HTTP 通信使用 `httpx`（MIT 许可证）
- 数据内容不包含邮件正文或附件（仅用户资料级别的元数据）
