# WuYou（一坞邮）项目记忆文档

> 生成时间：2026-06-23
> 目标：提供给下一个对话/助理，使其能无缝接续开发工作。

---

## 一、项目身份

| 项 | 值 |
|---|---|
| **项目名** | WuYou（一坞邮） |
| **Slogan 中文** | 你的邮件，都在坞里 |
| **Slogan 英文** | WuYou. One emailbox. All yours. |
| **版本** | v1.0.0 |
| **许可证** | MIT |
| **GitHub** | https://github.com/planover/WuYou |
| **Docker 镜像** | ghcr.io/planover/wuyou:latest |
| **本地工作目录** | `c:\Users\姓名\Documents\Codex\UniBox` |

---

## 二、技术架构

| 层级 | 技术 |
|---|---|
| 后端语言 | Python 3.12 |
| Web 框架 | FastAPI |
| 数据库 | SQLite（WAL 模式，本地单文件） |
| 前端 | 原生 JavaScript SPA + 原生 CSS（零 npm 依赖） |
| 加密 | PBKDF2-SHA256（密码哈希）+ Fernet（邮箱密钥加密）+ PGP |
| 容器化 | Docker + Docker Compose |
| CI/CD | GitHub Actions → 自动构建并推送到 GHCR |
| 环境变量前缀 | `WUYOU_` |
| 数据库文件名 | `wuyou.sqlite3` |
| 密钥文件 | `secret.key`（Fernet 密钥，与数据库一起备份） |

### 端口与端点

- FastAPI 主服务：`http://localhost:8000`
- 前端入口：`GET /` -> `static/index.html`
- API 前缀：`/api/`
- 健康检查：`GET /health`
- 远程同步端点默认：`http://localhost:8787/wuyou`

---

## 三、文件结构

```text
WuYou/
├── README.md / LICENSE (MIT) / CHANGELOG.md / CONTRIBUTING.md
├── Dockerfile / docker-compose.yml / .dockerignore / .env.example
├── .github/workflows/docker-build.yml   # CI: push main → 构建镜像到 GHCR
├── backend/
│   ├── requirements.txt                 # Python 依赖
│   └── app/
│       ├── main.py                      # FastAPI 入口，启动时 init db + sync + 热更新 + 遥测
│       ├── worker.py                    # 独立 sync worker（WUYOU_SYNC_MODE=worker 时用）
│       ├── models.py                    # Pydantic 请求/响应模型
│       ├── core/
│       │   ├── config.py                # Settings 类，WUYOU_ 环境变量，pydantic-settings
│       │   ├── database.py              # Database 类，threading.RLock + WAL + 自动迁移
│       │   └── security.py              # hash_password / verify_password / Fernet / token
│       ├── api/
│       │   ├── deps.py                  # 共享依赖（get_current_user, row_to_public_user）
│       │   ├── routes_auth.py           # 注册/登录/验证码/修改密码/修改联系方式/OAuth
│       │   ├── routes_accounts.py       # 邮箱账户 CRUD + Thunderbird 导入
│       │   ├── routes_mail.py           # 邮件列表/详情/切换已读/标签
│       │   ├── routes_items.py          # 统一条目 CRUD（日历/通讯录/任务/便签）
│       │   ├── routes_sync.py           # 同步 job 管理
│       │   ├── routes_sync_peers.py     # 远程同步设备管理 + 远程端点
│       │   ├── routes_sync_remotes.py   # 远程同步操作（push/pull）
│       │   ├── routes_dav.py            # CalDAV/CardDAV 账户管理
│       │   ├── routes_pgp.py            # PGP 密钥管理
│       │   ├── routes_plugins.py        # 插件社区
│       │   ├── routes_settings.py       # 用户设置
│       │   ├── routes_themes.py         # 主题管理
│       │   ├── routes_locales.py        # 语言包管理
│       │   ├── routes_translate.py      # 翻译服务
│       │   ├── routes_share.py          # 社区分享
│       │   ├── routes_system.py         # 系统信息
│       │   └── routes_telemetry.py      # 遥测
│       ├── services/
│       │   ├── mail_client.py           # IMAP/SMTP 客户端
│       │   ├── provider_catalog.py      # 12 家服务商自动配置
│       │   ├── pgp_crypto.py            # PGP 加解密
│       │   ├── thunderbird.py           # Thunderbird 数据迁移
│       │   ├── translation.py           # 翻译服务适配
│       │   ├── telemetry.py             # 遥测收集与上传
│       │   ├── backup.py                # WebDAV 备份
│       │   ├── plugins.py               # 插件引擎
│       │   ├── hot_reload.py            # 热更新文件监视
│       │   ├── locale_cache.py / theme_cache.py
│       │   ├── sms_adapter.py           # SMS 适配器
│       │   ├── dav/
│       │   │   ├── caldav.py / carddav.py / discovery.py
│       │   │   ├── google_tasks.py / ms_graph.py
│       │   └── sync/
│       │       ├── sync_engine.py       # IMAP 多文件夹增量同步核心
│       │       ├── executor_inprocess.py
│       │       ├── folder_discovery.py
│       │       ├── jobs.py
│       │       ├── remote_client.py
│       │       ├── snapshot.py
│       │       └── constants.py
│       ├── static/
│       │   ├── index.html               # SPA 入口（含 favicon）
│       │   ├── js/app.js                # 前端 SPA 全部逻辑
│       │   ├── css/app.css              # 全部样式（含响应式/暗色主题）
│       │   ├── locales/zh-CN.json en-US.json zh-TW.json
│       │   ├── themes/light.json dark.json
│       │   └── img/alipay-qr.svg
│       └── tests/                       # 21 个 pytest 测试文件，147 个用例全过
├── docs/
│   ├── api.md / architecture.md / deploy.md
│   └── superpowers/                     # 5 份设计文档 + 5 份实施计划
├── plugin-community/local/              # 默认本地插件社区
├── language-packs/template.json
├── theme-packs/template.json
├── fpn/                                 # 飞牛 FPK 包配置
├── scripts/                             # build_docker + start-dev
```

---

## 四、数据库表结构（核心）

| 表名 | 用途 |
|---|---|
| `users` | 总账户（username, email, phone, password_hash） |
| `sessions` | 登录会话（token_hash, expires_at） |
| `verification_codes` | 验证码（code_hash, expires_at, consumed_at） |
| `mailbox_accounts` | 邮箱账户（encrypted_secret 字段存加密密钥） |
| `mailbox_folders` | 邮箱文件夹（role, imap_name, enabled） |
| `mailbox_folder_state` | 文件夹同步状态（uidvalidity, last_uid） |
| `sync_jobs` | 同步任务队列（trigger, status, stats_json） |
| `messages` | 聚合邮件缓存（external_id, folder_role, imap_folder） |
| `tags` / `message_tags` | 标签系统 |
| `settings` | 用户设置（key-value JSON） |
| `content_items` | 统一条目（日历/通讯录/任务/便签） |
| `plugin_sources` / `installed_plugins` | 插件系统 |
| `sync_peers` / `sync_snapshots` | 远程设备同步 |
| `oauth_states` | OAuth 状态管理 |
| `dav_accounts` | CalDAV/CardDAV 账户 |
| `pgp_keys` | PGP 密钥 |
| `telemetry_events` | 遥测事件 |

---

## 五、已完成的审计与修复工作

本项目经历过**两轮全面代码审计**，以下是修复记录：

### 第一轮审计（63 个源码文件）

| 级别 | 问题数 | 关键修复 |
|---|---|---|
| **P0 致命** | 1 | `sync_engine.py` — `_iter_enabled_folders()` 空函数体，补全为按 role+enabled 过滤文件夹 |
| **P1 严重** | 6 | deps.py 移除 `users.password_hash` 泄露；CSS 12 个移动端断点类名不匹配修正；`--border` 变量补充；i18n key 补全至 132/130/130；JS 端 5 个不存在的 i18n key 修正 |
| **P2 中等** | 26 | TB 导入 POST 参数改用 Pydantic Body；IMAP UnboundLocalError 加守卫；SMTP STARTTLS 加 try/except；telemetry.py 新增 `telemetry_remote_url` + 函数改名 |
| **P3 轻微** | 14 | 文档补全、版本号 v1.0.0、占位符清理、LICENSE 从 Apache-2.0 改为 MIT、`.env.example` WUYOU_ 前缀统一；`app.js` API 端点 `/send-code` → `/verification-code` |

### 第二轮审计（用户反馈驱动）

| 类别 | 修复内容 |
|---|---|
| 日历/通讯录/便签 | API 响应提取从直接赋值改为 `data.items`；meta_json 字段路径修正；保存后自动刷新列表 |
| 任务页 | 完整 CRUD 实现（看板三列 + 状态切换按钮 + 删除按钮 + 回车快速添加） |
| 同步任务查看 | inbox toolbar 新增「同步任务」按钮 + modal 展示最近 5 条 job |
| 设置页 | 从静态展示改为可交互表单（主题/语言/遥测/远程同步地址/修改密码/修改邮箱） |
| 关于页 | 删除 donateHint 文案；更新日志改为 modal 弹窗内联展示 |
| 写邮件 | 新增格式工具栏（加粗/斜体/列表/链接→Markdown 插入）；发送/保存草稿/取消按钮 |
| 登录注册 | 改为卡片式居中布局 + slogan；分注册/登录两个 tab 切换 |
| Favicon | SVG inline，蓝色圆角方块 + 邮箱 emoji |
| 标题栏 | 点击品牌名跳回收件箱；用户头像下拉菜单（设置/关于/退出） |
| 侧栏 | 可折叠（48px 最小）+ 可拖拽调整宽度（160-400px）|
| CSS 滚动 | `.workspace` / `.page-pane` 设置 `overflow: auto` |
| i18n | 新增 settings.* / auth.logout / nav.syncJobs 等 key |
| 邮件账户页 | 每个账户卡片展示连接状态（在线/离线/错误/同步中）+ 最后同步时间 + 同步按钮 |
| routes_auth.py | 新增 PUT /api/auth/change-password 和 PUT /api/auth/change-contact 端点 |

---

## 六、部署方式

### 方法一：直接拉预构建镜像（推荐）

```bash
# 1. 下载 docker-compose.yml
curl -O https://raw.githubusercontent.com/planover/WuYou/main/docker-compose.yml

# 2. 启动
docker compose up -d
```

镜像自动从 `ghcr.io/planover/wuyou:latest` 拉取。

**注意**：GHCR 包默认私有的，需要先在 https://github.com/planover/WuYou/pkgs/container/wuyou/settings 中将 **Change visibility** 设为 **Public**。

### 方法二：本地构建

```bash
git clone https://github.com/planover/WuYou.git
cd WuYou
docker compose up -d
```

### CI/CD

- `.github/workflows/docker-build.yml`：push main 自动构建镜像并推到 GHCR
- 需要 `permissions.packages: write`
- 镜像 tag：`ghcr.io/planover/wuyou:latest`（全小写）

---

## 七、待完成工作

以下是用户在第一版使用后提交的反馈，**尚未全部完成**。部分已修复，以下是当前状态：

### 核心功能与交互

| 问题 | 状态 | 说明 |
|---|---|---|
| 同步任务进度无查看入口 | **已修复** | inbox toolbar 已加按钮 + modal |
| 关于页无效文案与错误链接 | **已修复** | 删除 donateHint，更新日志改为 modal |
| 设置页完全不可用 | **已修复** | 改为完整交互表单 |
| 日历/通讯录/便签保存后不显示 | **已修复** | API 响应提取 + meta_json 路径修正 |
| 任务页为静态摆件 | **已修复** | 看板三列 CRUD 完整实现 |
| 写邮件页功能过于简陋 | **已修复** | 格式工具栏 + 发送/草稿/取消按钮 |

### 用户体验与界面

| 问题 | 状态 | 说明 |
|---|---|---|
| 界面语言混杂 | **已修复** | zh-CN.json 补全至 136 key |
| 页面内容溢出无法滚动 | **已修复** | CSS overflow 修正 |
| 三栏布局宽度固定 | **已修复** | 侧栏可拖拽 + 折叠 |
| 标题栏无交互 | **已修复** | 品牌点击 + 用户下拉菜单 |
| 缺少 Favicon | **已修复** | SVG inline favicon |

### 账户与安全

| 问题 | 状态 | 说明 |
|---|---|---|
| 登录注册流程过于简陋 | **已修复** | 卡片式居中 + 注册/登录 tab 切换 |
| 缺少密码与账户安全管理功能 | **已修复** | 新增 change-password + change-contact 端点 + 设置页表单 |

### 邮箱账户管理

| 问题 | 状态 | 说明 |
|---|---|---|
| 邮箱账户管理页面信息不完整 | **已修复** | 状态指示 + 最后同步时间 + 同步按钮 |

### 可能存在的残留问题

1. **富文本编辑器**：当前格式工具栏只做 Markdown 语法插入，不是所见即所得编辑器。如需要可引入轻量编辑器（如 Quill/TinyMCE）。
2. **邮件发送功能**：写邮件表单已完善，但需要实际 SMTP 配置才能发送。
3. **CalDAV/CardDAV 真实服务器测试**：代码已实现，架构文档注明需要真实服务器环境测试。
4. **i18n 完整性**：en-US.json 和 zh-TW.json 在本次修复中未同步更新所有新增 key，需要补全。
5. **CSS 文件**：上一轮推送时 app.css 曾被截断为 67 字节（已修复），但版本可能不是最新的完整版。下一轮需要确认 GitHub 上的 app.css 与本地文件一致。

---

## 八、关键代码模式

### 前端 SPA 核心（app.js）

- `state` 对象承载全局状态（token、locale、theme、dict、view、user、messages、accounts、tags）
- `calendarState`、`contactsState`、`tasksState`、`notesState` 分别管理各模块状态
- `t(key, fallback)` 函数做 i18n 翻译，从 `state.dict` 取值
- `esc(value)` 做 HTML 转义
- `api(path, method, body)` 是统一请求函数，自动带 Authorization header
- `route(view)` 切换视图，调用对应的 `renderXxx()` 函数
- `renderShell()` 渲染主布局（topbar + sidebar + workspace）
- `renderAuth()` 渲染登录/注册页
- 所有渲染函数命名：`renderInbox()`、`renderCalendar()`、`renderSettings()` 等

### 后端 API 模式

- 所有路由在 `backend/app/api/routes_*.py` 中
- `deps.py` 提供 `get_current_user` 依赖注入
- `db` 是模块级 Database 单例（`from app.core.database import db`）
- `settings` 通过 `get_settings()` 获取
- 密码哈希用 `hash_password()` / `verify_password()`
- 邮箱密钥加解密用 `encrypt_secret()` / `decrypt_secret()` + Fernet
- 时间用 `utc_iso()` 格式化

---

## 九、给下一个对话的工作建议

### 优先级排序

1. **验证 CSS 完整性**：检查 GitHub 上 `backend/app/static/css/app.css` 是否等于本地完整版本（~1124 行），如果不完整需重新推送。
2. **补全 en-US.json 和 zh-TW.json**：参照 zh-CN.json 的新增 key，同步补全英文和繁体中文。
3. **端到端功能测试**：确保日历/通讯录/任务/便签的创建→保存→刷新→编辑→删除流程完全正常。
4. **邮件发送端到端测试**：配置一个真实 SMTP，验证写邮件→发送流程。
5. **性能优化**：检查 app.js 文件大小（106KB），考虑按模块拆分或延迟加载。
6. **安全性加固**：检查所有路由是否正确鉴权（get_current_user 依赖）。
7. **GitHub Actions 镜像构建**：确认 push 后自动构建成功，且 GHCR 包已设为 Public。

### 快速开发命令

```bash
# 在本地启动开发服务器
cd c:\Users\姓名\Documents\Codex\UniBox\backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 运行全部测试
cd c:\Users\姓名\Documents\Codex\UniBox\backend
python -m pytest -q

# 推送到 GitHub（MCP 方式）
# 使用 run_mcp: server_name="mcp_GitHub", tool_name="create_or_update_file"
# args: {"owner":"planover","repo":"WuYou","branch":"main","path":"...","content":"...","message":"..."}
```

### 本地文件路径

- 项目根：`c:\Users\姓名\Documents\Codex\UniBox`
- 临时工作：`c:\Users\姓名\.trae\work\6a38a9ac7041fec6b9bf1247`

---

*此文档将项目的完整上下文、技术细节、修复记录和待办事项打包，供新对话无缝接续。*
