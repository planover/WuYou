# WuYou 验证码发送 + OAuth2 邮箱登录 + Thunderbird 全量迁移 MVP 设计稿

> 目标：1）验证码从"开发模式回显"升级为"真实邮件+短信发送"；2）OAuth2 接入 Google/Microsoft/QQ/Yahoo/Zoho + 通用自定义 OAuth2；3）Thunderbird 全量导入（prefs.js 账户提取 + ImapMail 邮件 + 标签/文件夹）

## 已确认决策

| 决策 | 选择 |
|------|------|
| 验证码发送 | **邮件 + 短信都做**：邮件用现有 SMTP → 内置系统发件地址；短信留接口（可后续对接云短信） |
| OAuth2 providers | **全量**：Google / Microsoft / QQ(腾讯) / Yahoo / Zoho + 通用自定义 OAuth2（用户填 endpoint） |
| Thunderbird | **全量迁移**：账户配置提取 + 本地邮件导入 + 标签/文件夹结构迁移 |

---

## 第一部分：验证码真实发送

### 1.1 架构

```
用户请求验证码
  → 生成 6 位数字码（现有 make_verification_code）
  → 哈希存库（现有 hash_token → verification_codes 表）
  → 根据 target_type 分发：
      email → send_verification_email(smtp, to=target, code)
      phone → send_verification_sms(provider, to=target, code)
  → 返回 { message: "验证码已发送至 xxx" }
  → dev_code 仍保留（environment=development 时额外返回）
```

### 1.2 邮件发送

内置 SMTP 发信：用 WuYou 提供的"系统发件地址"。需要一个固定的系统邮箱来发送验证码。

配置项新增：
```python
system_smtp_enabled: bool = False        # 是否需要配置系统发件邮箱后验证码邮件才可用
system_smtp_host: str = ""
system_smtp_port: int = 465
system_smtp_ssl: bool = True
system_smtp_username: str = ""
system_smtp_password: str = ""           # 或授权码
system_from_address: str = "noreply@wuyou.local"
```

发送逻辑：
```python
def send_verification_email(to: str, code: str):
    msg = EmailMessage()
    msg["From"] = settings.system_from_address
    msg["To"] = to
    msg["Subject"] = "WuYou 验证码"
    msg.set_content(f"您的验证码是：{code}\n\n有效期 10 分钟。")
    # 复用现有 SMTP 发信逻辑
```

### 1.3 短信发送（接口预留）

短信通过适配器模式实现，当前只做接口定义 + 一个 console 打印适配器（开发环境），生产环境需用户自行填入 API key。

配置项：
```python
sms_provider: str = ""           # ""(禁用) / "aliyun" / "tencent" / "custom"
sms_api_key: str = ""
sms_api_secret: str = ""
sms_sign_name: str = ""          # 短信签名
sms_template_id: str = ""        # 短信模板 ID
```

适配器接口：
```python
class SmsAdapter(ABC):
    @abstractmethod
    async def send(self, phone: str, code: str) -> bool: ...

class ConsoleSmsAdapter(SmsAdapter):   # 开发模式：打印到 stdout
class AliyunSmsAdapter(SmsAdapter):    # 后续实现
class TencentSmsAdapter(SmsAdapter):   # 后续实现
class CustomSmsAdapter(SmsAdapter):    # 用户自定义 HTTP 回调
```

### 1.4 体验优化

- 同 target+purpose 60 秒内禁止重复发送（防刷）
- 邮件模板包含 WuYou 品牌 Logo / 链接

---

## 第二部分：OAuth2 邮箱登录

### 2.1 流程

```
用户在添加邮箱页面
  → 选择"OAuth2 登录"方式
  → 选择服务商（Google/Microsoft/QQ/Yahoo/Zoho）
  → 前端 window.location 重定向到服务商授权页面
  → 用户同意授权
  → 重定向回 WuYou 回调地址 /api/auth/oauth/callback
  → 后端用授权码换取 access_token + refresh_token
  → 调服务商 API 获取用户 email 地址
  → 自动创建或更新 mailbox_accounts（auth_type=oauth2, encrypted_secret 存 refresh_token）
  → 返回会话 token + 前端自动跳转到账户页
```

### 2.2 Provider 配置

每个 OAuth2 provider 需要 4 个端点：

| Provider | auth_url | token_url | userinfo_url | scope |
|----------|----------|-----------|--------------|-------|
| Google | https://accounts.google.com/o/oauth2/v2/auth | https://oauth2.googleapis.com/token | https://www.googleapis.com/oauth2/v3/userinfo | openid email https://mail.google.com/ |
| Microsoft | https://login.microsoftonline.com/common/oauth2/v2.0/authorize | https://login.microsoftonline.com/common/oauth2/v2.0/token | https://graph.microsoft.com/v1.0/me | openid email offline_access https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send |
| QQ | https://graph.qq.com/oauth2.0/authorize | https://graph.qq.com/oauth2.0/token | https://graph.qq.com/oauth2.0/me | get_user_info |
| Yahoo | https://api.login.yahoo.com/oauth2/request_auth | https://api.login.yahoo.com/oauth2/get_token | https://api.login.yahoo.com/openid/v1/userinfo | openid email mail-r |
| Zoho | https://accounts.zoho.com/oauth/v2/auth | https://accounts.zoho.com/oauth/v2/token | https://accounts.zoho.com/oauth/user/info | ZohoMail.accounts.READ |

### 2.3 通用自定义 OAuth2

用户手工填入：
- `oauth_auth_url`
- `oauth_token_url`
- `oauth_userinfo_url`
- `oauth_scope`
- `oauth_client_id` / `oauth_client_secret`

后端用同一条 `/api/auth/oauth/callback` 处理所有回调，按 `state` 参数中的 `provider` 字段区分。

### 2.4 配置项新增

```python
oauth_client_id: str = ""         # 全局（可被单个 provider 覆盖）
oauth_client_secret: str = ""
oauth_redirect_uri: str = ""      # http(s)://your-domain/api/auth/oauth/callback

# 按 provider 各自的 client_id/secret（优先级高于全局）
oauth_google_client_id: str = ""
oauth_google_client_secret: str = ""
oauth_ms_client_id: str = ""
oauth_ms_client_secret: str = ""
oauth_qq_client_id: str = ""
oauth_qq_client_secret: str = ""
oauth_yahoo_client_id: str = ""
oauth_yahoo_client_secret: str = ""
oauth_zoho_client_id: str = ""
oauth_zoho_client_secret: str = ""
```

### 2.5 安全

- state 参数含随机 nonce（存 sessions 表或内存 10 分钟 TTL），回调时校验防 CSRF
- OAuth refresh_token 加密存储（复用 `encrypted_secret` 字段 + Fernet 加密）
- 不要求用户填 OAuth 密钥才能使用 WuYou——auth_type 默认仍是 `app_password`

---

## 第三部分：Thunderbird 全量迁移

### 3.1 总体流程

```
用户选择 Thunderbird profile 目录
  → POST /api/accounts/thunderbird/import { profile_path }
  → 1. 解析 prefs.js → 提取邮件服务器配置 → 创建 mailbox_accounts
  → 2. 扫描 ImapMail/{server}/ 下文件夹目录 → 读取 .msf 索引 + 对应无扩展名 mbox 文件
  → 3. 解析 mbox 内每封邮件（RFC822）→ 提取 headers/正文/附件
  → 4. 导入到 messages 表 + 创建对应 folder_role 映射
  → 5. 迁移标签（如果 Thunderbird 有自定义标签存储在 prefs.js 中）
  → 返回报告：{ accounts_imported: 3, folders_parsed: 12, messages_imported: 4521 }
```

### 3.2 prefs.js 解析

`prefs.js` 格式（Mozilla 配置 JS）：
```
user_pref("mail.server.server1.hostname", "imap.gmail.com");
user_pref("mail.server.server1.port", 993);
user_pref("mail.server.server1.userName", "alice@gmail.com");
user_pref("mail.server.server1.name", "alice@gmail.com");
user_pref("mail.server.server1.socketType", 3);      // 3=SSL
user_pref("mail.server.server1.type", "imap");
user_pref("mail.server.server2.hostname", "smtp.gmail.com");
```

解析器用正则提取 `user_pref("KEY", VALUE)` 对，按 `serverN` 分组聚合。

### 3.3 ImapMail 目录结构

```
ImapMail/
  imap.gmail.com/
    INBOX.msf          ← 索引文件（可选，用于获取 meta）
    INBOX              ← 无扩展名 mbox 文件（每封邮件 RFC822 连接）
    [Gmail].msf/
    [Gmail].sbd/
      Sent Mail.msf
      Sent Mail
      Trash.msf
      Trash
```

### 3.4 mbox 解析

mbox 格式：每封邮件以 `From ` 开头的行分隔（注意后面有空格）。

```python
def parse_mbox(file_path: Path) -> list[bytes]:
    """解析 mbox 文件，返回每封邮件的原始 RFC822 bytes。"""
    raw = file_path.read_bytes()
    messages = []
    for chunk in re.split(rb'\nFrom .*\d{4}\n', raw):
        if chunk.strip():
            messages.append(chunk)
    return messages
```

每封 RFC822 邮件用 `email.message_from_bytes()` 解析，完全复用现有 `_extract_body()` / `_safe_filename()` 等函数。

### 3.5 文件夹映射

ImapMail 目录名直接对应 IMAP folder name，用现有 `classify_folder()` 映射到 role。

`.sbd/` 子目录表示嵌套文件夹，展平为 `父/子` 格式。

### 3.6 导入策略

- 全量写入 `messages` 表（`INSERT OR IGNORE`，依赖 `UNIQUE(user_id, mailbox_id, external_id)` 去重）
- 大批量分页写入（每 100 封一批 executemany）
- 导入过程中显示进度（可选：在 sync_jobs 里记录一个 thunderbird_import 任务）
- 导入后自动 `ensure_folders` + `last_uid=0`（下次邮箱同步从当前增量开始）

---

## 4. 数据模型变更

### 4.1 `mailbox_accounts` 扩展

无新增列——`auth_type="oauth2"` 时 `encrypted_secret` 存储加密后的 refresh_token。

### 4.2 `verification_codes` 扩展

新增列：`rate_limit_at TEXT`（同 target+purpose 60 秒内不可重复请求）。

### 4.3 新表：`oauth_states`

```sql
CREATE TABLE IF NOT EXISTS oauth_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    redirect_to TEXT NOT NULL DEFAULT '/',
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

---

## 5. API 设计

### 5.1 验证码 API（改造现有）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/verification-code` | 改造：根据 target_type 真实发送邮件或短信，增加 rate_limit 检查 |

### 5.2 OAuth2 API（新增）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/auth/oauth/authorize` | 生成 state + 重定向到服务商授权页面（返回 `{auth_url}` 或 302） |
| GET | `/api/auth/oauth/callback` | OAuth 回调端点（code + state→换 token→存 refresh_token→创建 account） |
| GET | `/api/auth/oauth/providers` | 列出已配置的 OAuth2 服务商列表 |

### 5.3 Thunderbird 导入 API（改造现有）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/accounts/thunderbird/import` | 改造：接收 profile_path → 解析 → 导入 → 返回报告 |

---

## 6. 前端改造

### 6.1 验证码发送

- 请求验证码按钮增加 60 秒倒计时防重复点击
- 发送成功 toast "验证码已发送至 xxx@xxx.com" 或 "验证码已发送至 138****1234"
- 发送失败 toast "系统发件邮箱未配置，请联系管理员" 或 "短信服务未配置"

### 6.2 OAuth2 登录

- 添加邮箱页面 → auth_type 下拉新增 "OAuth2 一键登录"选项
- 选 OAuth2 → 显示服务商选择下拉（Google / Microsoft / QQ邮箱 / Yahoo / Zoho / 自定义）
- 点"连接"→ 重定向到 OAuth 授权页
- 回调后自动跳回 WuYou 并显示"已成功连接 xxx@gmail.com"

### 6.3 Thunderbird 导入

- 邮箱账户页增加"导入 Thunderbird 数据"按钮
- 输入 profile 路径（可帮助提示默认路径：`%APPDATA%/Thunderbird/Profiles/`）
- 点"导入"→ 进度展示 → 完成报告弹窗

---

## 7. 测试计划

| 测试项 | 验证点 |
|--------|--------|
| 验证码邮件发送 | SMTP 配置正确时可真实发送邮件 |
| 验证码短信适配器 | console 适配器打印验证码，接口对后续扩展开放 |
| 验证码防刷 | 同 target+purpose 60 秒内第二次请求返回 429 |
| OAuth2 state 生成 | state 唯一存库，10 分钟过期 |
| OAuth2 callback | code→token→userinfo→创建 account（mock HTTP 响应） |
| Thunderbird prefs 解析 | 示例 prefs.js → 正确提取 host/port/username/ssl |
| Thunderbird mbox 解析 | 示例 mbox → 正确拆分为 N 封 RFC822 |
| Thunderbird 全量导入 | profile 目录 → accounts+folders+messages 全部入库 |
| 集成测试 | POST /api/auth/verification-code → 返回 dev_code + 真实发送不报错 |
| 集成测试 | OAuth2 Google 完整流程（若配置了真实 client_id） |

---

## 8. 开源与合规

- OAuth2 接入不引入闭源 SDK——纯 HTTP 调用（httpx）
- 短信适配器接口抽象，各实现需用户自行获取 API key
- Thunderbird prefs.js / mbox 解析纯 Python 实现，无第三方格式库依赖
- 邮件/短信内容不含追踪像素或第三方监控
