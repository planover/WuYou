# WuYou 插件/主题/语言包生态 MVP 设计稿

> 目标：把 WuYou 的插件社区、主题包、语言包从"骨架"做成真正可用、可上传、可分享、可一键安装的开源生态。

## 已确认决策

| 决策 | 选择 |
|------|------|
| 语言包/主题包深度 | **管理（上传+切换+即时生效）+ 社区分享（提交→待审核）** |
| QQ 邮箱主题 | **组件级**：CSS 变量 + 布局从"双栏"改为 QQ 的"左窄导航 + 中收件箱列表 + 右阅读区"三栏 |
| 在线插件校验 | **SHA256**：catalog `index.json` 每插件带 `sha256`，下载后 hash 匹配才安装 |
| 插件分类 | 仿 Chrome 扩展程序分类（已有 `PLUGIN_CATEGORIES`） |
| 本地/在线社区切换 | 默认本地，可切在线 URL；用户可添加多个社区源 |

---

## 1. 主题系统升级

### 1.1 QQ 邮箱风格默认主题

将现有"双栏"布局升级为 QQ 邮箱风格的三栏布局：

```
┌─────────────────────────────────────────────────────┐
│  顶部栏：品牌 U + WuYou  │  语言切换  日/夜  退出  │
├──────────┬──────────────────────┬───────────────────┤
│ 收件箱    │                      │                   │
│ 未读汇总  │   邮件列表            │   邮件阅读区       │
│ 写邮件    │   (发件人/主题/摘要)  │   (正文/附件)     │
│ 邮箱账户  │                      │                   │
│ 插件社区  │   文件夹切换 tab      │                   │
│ 设置      │   全部|收件箱|已发送..│                   │
│ 关于      │                      │                   │
├──────────┴──────────────────────┴───────────────────┤
│  状态栏 (可选)                                       │
└─────────────────────────────────────────────────────┘
```

### 1.2 CSS 变量体系增强

新增 QQ 邮箱风格变量（在现有 `:root` 基础上扩展）：

```css
:root {
  /* 现有变量保持 */
  --bg: #eef3f9;
  --surface: #ffffff;
  /* QQ 邮箱特有 */
  --sidebar-width: 180px;
  --list-width: 360px;
  --topbar-height: 52px;
  --qq-primary: #12b7f5;       /* QQ 邮箱品牌蓝 */
  --qq-hover: rgba(18, 183, 245, 0.08);
  --qq-active: rgba(18, 183, 245, 0.14);
}
```

### 1.3 主题包完整生命周期

| 操作 | 说明 |
|------|------|
| 上传 | POST multipart → 后端校验 JSON→存到 `{user_data}/themes/{id}.json` |
| 预览 | GET `/api/themes/{id}/preview` → 前端临时应用 |
| 激活 | PUT `/api/settings` `key=theme, value={id}` → 前端 `applyTheme()` 加载对应 CSS 变量 |
| 分享 | POST `/api/share/themes/{id}` → 写入社区的 `submissions/` 目录 |
| 删除 | DELETE `/api/themes/{id}`（仅限自上传，内置不可删） |

---

## 2. 语言包系统升级

### 2.1 语言包完整生命周期

与主题包完全对称：
- 上传 → 校验（必须含 `meta.id` / `meta.name` / `messages` 对象）→ 落盘到 `{user_data}/locales/{id}.json`
- 切换 → `PUT /api/settings key=locale value={id}` → 前端 `loadLocale()` 重新加载
- 分享 → 同主题
- 内置 `zh-CN / zh-TW / en-US` 不可删

### 2.2 语言包模板增强

`language-packs/template.json` 已有基础结构；增加 `minVersion` 字段以控制兼容性：
```json
{
  "meta": {
    "id": "my-pack",
    "name": "我的语言包",
    "author": "YourName",
    "version": "1.0",
    "minVersion": "0.1.0",
    "license": "Apache-2.0"
  },
  "messages": { ... }
}
```

---

## 3. 插件系统

### 3.1 当前 vs 将做

| 能力 | 当前 | 将做 |
|------|------|------|
| manifest 校验 | ✅ | 保持 + 增加 SHA256 |
| 本地 catalog | ✅ | 保持 |
| 远程 catalog 拉取 | ✅ | 保持 + 下载+校验+安装 |
| 安装（仅写 JSON） | ✅ | 改为下载 entry 资源 |
| 启用/停用 | ❌ | **新增** |
| 卸载 | ❌ | **新增**（从 records + files 中移出） |
| 上传 | ❌ | **新增** |
| 分享 | ❌ | **新增** |

### 3.2 Manifest 规范增强

```json
{
  "id": "mail-label-assistant",
  "name": "邮件标签助手",
  "version": "1.0.0",
  "type": "extension",
  "category": "效率工具",
  "description": "自动为邮件推荐标签",
  "entry": "main.js",
  "permissions": ["mail.read", "tags.write"],
  "license": "Apache-2.0",
  "minVersion": "0.1.0",
  "sha256": "abc123...",
  "author": "YourName",
  "homepage": "https://example.com"
}
```

### 3.3 在线安装流程

```
用户点击安装
  → GET {community}/plugins/{plugin-id}/{version}/{entry} 下载资源
  → 计算 SHA256 → 与 manifest.sha256 比较
  → 匹配：存到 installed_plugins/{plugin_id}/ 目录 + 写 installed_plugins 表
  → 不匹配：拒绝安装，提示"校验失败，文件可能被篡改"
```

### 3.4 插件生命周期状态

```
uninstalled → installed(disabled) → enabled → uninstalled
                ↓
            installed(enabled)  ←→  disabled
```

状态存在 `installed_plugins.enabled`（INTEGER 1/0）。

---

## 4. 社区分享系统

### 4.1 流程图

```
用户本地包 → 点击"分享到社区"
  → POST /api/share/{type}/{id}
  → 后端校验包完整性 → 写入 {community}/submissions/{type}/{id}.json
  → 返回 "已提交到社区等待审核"
```

### 4.2 分享数据模型

```json
// {community}/submissions/{type}/{id}.json
{
  "type": "theme",           // theme | language-pack | extension
  "submitted_by": "username",
  "submitted_at": "2026-...",
  "status": "pending",       // pending | approved | rejected
  "manifest": { ... },
  "reason": "rejected: ...", // 审核拒绝原因
}
```

### 4.3 本地/在线社区切换

用户设置中已有 `plugin_sources` 表记录社区地址。
前端在插件页面提供下拉切换当前社区源。
默认：本地社区（`plugin-community/local/`）。

---

## 5. 数据模型变更

### 5.1 `installed_plugins` 表扩展

新增字段：`enabled INTEGER NOT NULL DEFAULT 1`

### 5.2 新表：`shared_items`

```sql
CREATE TABLE IF NOT EXISTS shared_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    type TEXT NOT NULL,           -- theme | language-pack | extension
    item_id TEXT NOT NULL,        -- 对应包的 id
    manifest_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    submitted_at TEXT NOT NULL,
    UNIQUE(user_id, type, item_id)
);
```

### 5.3 目录结构

```
data/
  themes/              ← 用户上传的主题包
    {user_id}/
      {theme_id}.json
  locales/             ← 用户上传的语言包
    {user_id}/
      {locale_id}.json
  plugins/
    installed/         ← 已安装插件的 manifest
      {user_id}/
        {plugin_id}.json
    files/             ← 已安装插件的资源文件
      {user_id}/
        {plugin_id}/
          main.js
          style.css
          ...
```

---

## 6. API 设计

### 6.1 主题包 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/themes` | 列出可用主题（内置 + 用户上传） |
| POST | `/api/themes` | 上传主题包（multipart JSON） |
| GET | `/api/themes/{id}` | 获取单主题详情 |
| DELETE | `/api/themes/{id}` | 删除用户上传的主题 |

### 6.2 语言包 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/locales` | 列出可用语言包 |
| POST | `/api/locales` | 上传语言包 |
| GET | `/api/locales/{id}` | 获取单语言包详情 |
| DELETE | `/api/locales/{id}` | 删除用户上传的语言包 |

### 6.3 插件管理 API（新增）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/plugins/{plugin_id}/enable` | 启用插件 |
| POST | `/api/plugins/{plugin_id}/disable` | 停用插件 |
| DELETE | `/api/plugins/{plugin_id}` | 卸载插件 |
| POST | `/api/plugins/install/url` | 从 URL 下载并安装（替代现在的仅 JSON 模式） |

### 6.4 分享 API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/share` | 分享包到社区（body: `{type, item_id}`） |
| GET | `/api/share/submissions` | 查看自己的分享记录 |

---

## 7. 前端改造要点

### 7.1 QQ 邮箱三栏布局

CSS 改动：
- `.app-shell` 改为三列：`1fr 360px minmax(0, 1fr)`
- 左侧导航保持 `.sidebar`
- 中间邮件列表 `.list-pane`
- 右侧阅读区 `.reader-pane`
- 邮件列表页不再和阅读区挤两个 pane，改为始终三栏

### 7.2 插件社区页面增强

- 每个插件卡片增加"启用/停用"开关、"卸载"按钮
- "安装"按钮区分：已安装→灰色已装；未安装→蓝色可装
- 在线社区面板增加"输入 URL + 加载"与"已连接社区源下拉选择"
- 增加"上传插件"按钮（仅扩展类，主题/语言在各自页面管）

### 7.3 设置页增加"外观"和"语言"管理

- 主题管理：列出所有主题卡片 + 缩略色块预览 + "启用"按钮 + "上传" + "分享" + "删除"
- 语言管理：列出所有语言包 + "切换"按钮 + "上传" + "分享" + "删除"

### 7.4 全站头部增加 QQ 品牌色

顶部栏背景色从白色改为蓝色渐变（`#12b7f5`→`#0d9bd4`），提升辨识度。

---

## 8. 测试计划

| 测试项 | 验证点 |
|--------|--------|
| 主题上传 | 上传合法 JSON → 列表出现 → 启用 → 全站 CSS 变化 |
| 语言包上传 | 上传合法 JSON → 列表出现 → 切换 → 全站文字变 |
| 插件在线安装 | 输入 URL → 下载 + SHA256 校验 → 写入 installed → 可启用/停用 |
| 分享 | 点击分享 → submissions/ 出现文件 → 提交记录可见 |
| 内置包保护 | 删除 zh-CN → 403（不可删） |
| 三栏布局 | 768px+ 宽度下始终三栏；窄屏自适应为单栏 |
| 日夜切换在 QQ 主题下 | 夜间模式三栏正常显示 |

---

## 9. 开源与合规

- 所有包必须声明 `license` 字段
- 内置 QQ 风格主题和语言包采用 `Apache-2.0`
- 第三方插件由上传者声明许可证并自行负责
