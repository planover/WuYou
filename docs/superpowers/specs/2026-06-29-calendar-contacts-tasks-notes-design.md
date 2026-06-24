# WuYou 日历/通讯录/任务/便签 + CalDAV/CardDAV/EWS 同步 MVP 设计稿

> 目标：在 WuYou 中内建"日历（含 CalDAV 双向同步）""通讯录（含 CardDAV 双向同步）""任务（含 Google Tasks/EWS 同步）""便签（纯本地富文本）"四大模块，使用统一的 `content_items` 表作为数据存储，与现有邮件核心深度整合。

## 已确认决策

| 决策 | 选择 |
|------|------|
| 模块范围 | **全量 4 块**：日历 + 通讯录 + 任务 + 便签 |
| 外部同步协议 | **CalDAV + CardDAV + Google Tasks + Microsoft EWS/Graph** |
| 端点发现 | **自动发现**（.well-known / SRV / 邮箱域名推导） + **手动填入** |
| 任务系统 | **富任务系统**：截止日期/优先级/状态/提醒 + 外部同步 |
| 便签 | **纯本地**：类别/置顶/颜色/标签 + Markdown 编辑（不同步） |
| 外部 Provider | **Google API（日历+联系人+任务） + Microsoft Graph（日历+联系人+任务） + 通用 CalDAV/CardDAV** |

---

## 1. 数据模型

### 1.1 复用 `content_items` 表（已存在）

```sql
-- 现有表，无需修改
CREATE TABLE IF NOT EXISTS content_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mailbox_id INTEGER REFERENCES mailbox_accounts(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,         -- calendar_event / contact / task / note
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',        -- 描述/正文/备注 Markdown
    meta_json TEXT NOT NULL DEFAULT '{}', -- 结构化字段
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 1.2 `meta_json` 按 kind 定义

#### calendar_event

```json
{
  "start_at": "2026-07-01T09:00:00+08:00",
  "end_at": "2026-07-01T10:00:00+08:00",
  "all_day": false,
  "location": "会议室 A",
  "recurrence_rule": "FREQ=WEEKLY;BYDAY=MO,WE",
  "recurrence_end": "2026-12-31",
  "reminders": [{"minutes": 30}, {"minutes": 1440}],
  "attendees": [{"name": "张三", "email": "zhangsan@example.com"}],
  "caldav_uid": "abc123@google.com",
  "color": "#1d73e8",
  "sync_source": "google_calendar",
  "sync_etag": "abc123",
  "sync_updated": "2026-07-01T09:00:00Z"
}
```

#### contact

```json
{
  "full_name": "张三",
  "first_name": "三",
  "last_name": "张",
  "emails": [{"type": "work", "value": "zhangsan@example.com"}],
  "phones": [{"type": "mobile", "value": "+86-138-xxxx-xxxx"}],
  "addresses": [{"type": "home", "street": "...", "city": "...", "postal": "..."}],
  "organization": "某公司",
  "job_title": "工程师",
  "birthday": "1990-01-15",
  "photo_url": "",
  "groups": ["工作", "亲友"],
  "carddav_uid": "xyz789@icloud.com",
  "carddav_etag": "xyz789",
  "sync_source": "icloud",
  "sync_updated": "2026-07-01T09:00:00Z"
}
```

#### task

```json
{
  "due_date": "2026-07-10",
  "priority": 7,                // 1-10，10 最高
  "status": "todo",             // todo / in_progress / done
  "reminder_at": "2026-07-09T08:00:00+08:00",
  "tags": ["工作", "紧急"],
  "sync_source": "google_tasks",
  "sync_tasklist_id": "MT...",
  "sync_task_id": "abc123",
  "sync_etag": "abc123",
  "sync_updated": "2026-07-01T09:00:00Z"
}
```

#### note

```json
{
  "pinned": false,
  "color": "#f4b400",
  "category": "工作笔记",
  "tags": ["会议纪要", "2026Q3"],
  "format": "markdown"          // markdown / html / plain
}
```

---

## 2. 前端导航与视图

### 2.1 新增 4 个 Tab

现有 7 个 tab 基础上新增：

```javascript
// app.js views 数组
["calendar", "nav.calendar", "日历", "Calendar"],
["contacts", "nav.contacts", "通讯录", "Contacts"],
["tasks", "nav.tasks", "任务", "Tasks"],
["notes", "nav.notes", "便签", "Notes"],
```

### 2.2 日历视图（calendar）

- **月份视图（默认）**：标准 7 列 × 5~6 行月历，每天最多显示 3 个事件 + "+N 更多"
- **周视图**：7 列时间网格（00:00–23:59 每小时一行）
- **日视图**：单列时间线 + 左侧事件卡片
- 点击日期/时间槽 → 弹出新建事件表单
- 点击事件 → 弹出详情/编辑
- 顶部工具栏：`← 2026年6月 → | 今天 | 月 周 日`

### 2.3 通讯录视图（contacts）

- **列表视图**：右侧姓名列表 + 左侧详情卡片
- **网格视图**：头像 + 姓名 + 邮箱网格
- 搜索框：支持姓名/邮箱/电话模糊搜索
- 点击联系人 → 详情卡片（编辑/删除/发邮件快捷按钮）

### 2.4 任务视图（tasks）

- **看板视图**：三列 `todo / in_progress / done`
- **列表视图**：按优先级/截止日期排序
- 每张卡片：标题 + 截止日期 + 优先级色标 + 标签
- 拖拽切换状态
- 快速新建：顶部输入框回车添加

### 2.5 便签视图（notes）

- **网格视图**：彩色便签卡片，2~3 列自适应
- **列表视图**：标题 + 摘要
- 分类侧栏：左侧 category 列表 + 置顶筛选
- 点击便签 → 全屏 Markdown 编辑器
- 新建：右上角 `+` 按钮弹出编辑器

---

## 3. 后端 API 设计

### 3.1 统一 content_items CRUD（复用现有思路）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/items?kind=calendar_event&from=2026-07-01&to=2026-07-31` | 日历事件（时间范围过滤） |
| GET | `/api/items?kind=contact&q=张三` | 联系人（搜索） |
| GET | `/api/items?kind=task&status=todo` | 任务（按状态过滤） |
| GET | `/api/items?kind=note&category=工作笔记` | 便签（按分类过滤） |
| POST | `/api/items` | 新建任意类型 item |
| PUT | `/api/items/{id}` | 更新 item |
| DELETE | `/api/items/{id}` | 删除 item |
| GET | `/api/items/{id}` | 详情 |

请求体示例（新建日历事件）：

```json
{
  "kind": "calendar_event",
  "title": "团队周会",
  "body": "讨论 Q3 目标和分工",
  "meta_json": "{\"start_at\":\"2026-07-01T09:00:00+08:00\",\"end_at\":\"2026-07-01T10:00:00+08:00\",\"location\":\"会议室A\",\"color\":\"#1d73e8\"}"
}
```

### 3.2 CalDAV/CardDAV 账户管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/dav/accounts` | 添加 DAV 账户（calendar/contacts/tasks discovery） |
| GET | `/api/dav/accounts` | 列表（关联某个 mailbox_account） |
| DELETE | `/api/dav/accounts/{id}` | 移除 |
| POST | `/api/dav/accounts/{id}/sync` | 手动触发同步 |
| POST | `/api/dav/discover` | 自动发现端点（输入邮箱地址） |

添加账户请求体：

```json
{
  "mailbox_account_id": 1,
  "kind": "calendar",           // calendar / contacts / tasks
  "protocol": "caldav",         // caldav / carddav / google_tasks / ms_graph
  "url": "https://...",         // 可选（自动发现时留空）
  "username": "alice@gmail.com",
  "password": "xxxx"            // 或用 OAuth token
}
```

### 3.3 新表：`dav_accounts`

```sql
CREATE TABLE IF NOT EXISTS dav_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mailbox_account_id INTEGER REFERENCES mailbox_accounts(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,              -- calendar / contacts / tasks
    protocol TEXT NOT NULL,          -- caldav / carddav / google_api / ms_graph
    url TEXT NOT NULL,
    username TEXT NOT NULL,
    encrypted_password TEXT NOT NULL,
    sync_enabled INTEGER NOT NULL DEFAULT 1,
    last_sync_at TEXT,
    last_sync_status TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

---

## 4. CalDAV / CardDAV 同步引擎

### 4.1 端点自动发现

参照 RFC 6764，按优先级尝试：

1. `.well-known`：`GET https://{domain}/.well-known/caldav` → 返回 calendar 根 URL
2. DNS SRV：`_caldavs._tcp.{domain}` → 返回 host:port
3. 邮箱域名推导（fallback）：
   - Google → `https://apidata.googleusercontent.com/caldav/v2/{email}/events/`
   - iCloud → `https://caldav.icloud.com/`
   - QQ邮箱 → `https://caldav.mail.qq.com/`
   - 其他 → `https://{domain}/.well-known/caldav` 再试

```python
def discover_caldav_url(email: str) -> str | None:
    domain = email.split("@")[-1]
    for pattern in DISCOVERY_PATTERNS:
        url = pattern.format(domain=domain, email=email)
        try:
            resp = httpx.options(url, timeout=10)
            if resp.status_code < 400:
                return url
        except Exception:
            continue
    return None
```

### 4.2 CalDAV 同步流程

```
本地 last_sync_token
  → PROPFIND /calendar/{token} → 获取变更列表（新增/修改/删除）
  → 对每个变更的 .ics 文件 GET + 解析
  → 对比本地 content_items（按 caldav_uid + sync_etag）
  → 远程新 → 本地 INSERT
  → 远程改 → 本地 UPDATE
  → 远程删 → 本地 DELETE
  → 本地改 → 远程 PUT 新 .ics
  → 本地新 → 远程 PUT 新 .ics
  → 保存新 sync_token
```

### 4.3 CardDAV 同步流程

同 CalDAV，协议为 `PROPFIND /addressbooks/{user}/{book}/`，数据格式 `.vcf`。

### 4.4 Google Tasks API 同步

用 `tasks.googleapis.com/tasks/v1`：
- `GET /users/@me/lists` → tasklist 列表
- `GET /tasks/v1/lists/{tasklist}/tasks?showCompleted=true&updatedMin={last_sync}` → 变更任务
- 本地 ↔ Google 双向合并（按 `sync_etag`）

### 4.5 Microsoft Graph 同步

用 `graph.microsoft.com/v1.0`：
- **日历**：`GET /me/calendar/events?$filter=lastModifiedDateTime gt {last_sync}`
- **联系人**：`GET /me/contacts?$filter=lastModifiedDateTime gt {last_sync}`
- **任务**：`GET /me/planner/tasks` 或 `GET /me/outlook/tasks`

---

## 5. 与现有系统的整合

### 5.1 远程同步（已实现）

`content_items` 已在远程同步快照中包含（上一个 MVP），无需额外改动。

### 5.2 OAuth2 复用（已实现）

CalDAV/CardDAV/GoogleAPI/MicrosoftGraph 复用现有 OAuth2 token：
- `mailbox_accounts.encrypted_secret` 存 OAuth refresh_token
- `dav_accounts` 可关联 `mailbox_account_id`，密码留空时可回退到对应 OAuth token

### 5.3 邮件联动

- 日历事件中点击 attendee email → 打开写邮件界面并预填收件人
- 通讯录联系人 → "发邮件"按钮直接跳转 compose
- 任务提醒 → 到时生成 in-app 通知

---

## 6. 前端路由与状态

### 6.1 日历状态

```javascript
const calendarState = {
  currentDate: new Date(),          // 当前查看的日期
  viewMode: "month",                // month / week / day
  events: [],                       // 当前时间段内的事件
  selectedEventId: null,            // 当前选中的事件
  showCreateDialog: false,          // 新建事件弹窗
  createDialogDate: null,           // 预填日期
};
```

### 6.2 任务状态

```javascript
const tasksState = {
  viewMode: "kanban",               // kanban / list
  filterStatus: null,               // null / todo / in_progress / done
  filterTag: null,
  tasks: [],
};
```

### 6.3 便签状态

```javascript
const notesState = {
  viewMode: "grid",                 // grid / list
  filterCategory: null,
  filterPinned: false,
  notes: [],
};
```

---

## 7. 国际化文案（zh-CN.json 新增）

```json
"nav.calendar": "日历",
"nav.contacts": "通讯录",
"nav.tasks": "任务",
"nav.notes": "便签",

"calendar.today": "今天",
"calendar.newEvent": "新建事件",
"calendar.eventTitle": "标题",
"calendar.eventStart": "开始时间",
"calendar.eventEnd": "结束时间",
"calendar.eventAllDay": "全天",
"calendar.eventLocation": "地点",
"calendar.eventRecurrence": "重复",
"calendar.eventReminder": "提醒",
"calendar.eventAttendees": "参与者",
"calendar.eventDelete": "删除事件",
"calendar.month": "月",
"calendar.week": "周",
"calendar.day": "日",

"contacts.search": "搜索联系人",
"contacts.new": "新建联系人",
"contacts.name": "姓名",
"contacts.email": "邮箱",
"contacts.phone": "电话",
"contacts.organization": "公司",
"contacts.sendEmail": "发邮件",
"contacts.empty": "暂无联系人",

"tasks.kanban": "看板",
"tasks.list": "列表",
"tasks.new": "新建任务",
"tasks.priority": "优先级",
"tasks.dueDate": "截止日期",
"tasks.status.todo": "待办",
"tasks.status.inProgress": "进行中",
"tasks.status.done": "已完成",
"tasks.reminder": "提醒时间",

"notes.new": "新建便签",
"notes.title": "标题",
"notes.category": "分类",
"notes.pin": "置顶",
"notes.color": "颜色",
"notes.tags": "标签",
"notes.empty": "暂无便签",

"dav.title": "日历/联系人/任务同步",
"dav.add": "添加同步账户",
"dav.kind": "类型",
"dav.kindCalendar": "日历",
"dav.kindContacts": "通讯录",
"dav.kindTasks": "任务",
"dav.url": "服务器地址",
"dav.discover": "自动发现",
"dav.manual": "手动填写",
"dav.sync": "立即同步",
"dav.lastSync": "上次同步",
```

---

## 8. 测试计划

| 测试项 | 验证点 |
|--------|--------|
| content_items CRUD | POST/GET/PUT/DELETE 按 kind 过滤正确 |
| CalDAV 端点发现 | Gmail → 返回正确 CalDAV URL |
| CalDAV 同步 | mock PROPFIND → 事件增量合并正确 |
| CardDAV 同步 | mock PROPFIND → 联系人增量合并正确 |
| 日历前端 | 月/周/日视图渲染 + 创建事件弹窗 |
| 通讯录前端 | 列表/网格切换 + 搜索 + 发邮件联动 |
| 任务前端 | 看板/列表切换 + 拖拽 + 状态变更 |
| 便签前端 | Markdown 编辑器 + 分类/置顶/颜色 |
| 集成冒烟 | 全量 pytest + 端到端 API 调用 |

---

## 9. 开源与合规

- CalDAV/CardDAV 客户端纯 Python 实现（httpx + icalendar/vobject 解析）
- 不使用闭源日历库
- 同步数据仅存储本地 content_items 表
- Google API / Microsoft Graph 调用需用户自行申请 OAuth 应用
- 使用 `httpx`（MIT）和 `icalendar`（BSD）库
