# WuYou API 摘要

## 认证

- `POST /api/auth/register`：注册总账户。
- `POST /api/auth/login`：使用密码或验证码登录。
- `POST /api/auth/verification-code`：生成验证码。开发环境会返回 `dev_code`，生产环境需要接入短信或邮件发送服务。
- `POST /api/auth/change-password`：使用原密码或验证码修改密码。
- `POST /api/auth/change-contact`：使用验证码修改手机号或邮箱。

## 邮箱账户

- `GET /api/accounts`：列出邮箱账户。
- `POST /api/accounts`：新增邮箱账户，支持自动匹配 IMAP/SMTP。
- `POST /api/accounts/{id}/sync`：同步收件箱。
- `POST /api/accounts/thunderbird/import`：Thunderbird 导入预留接口。

## 邮件

- `GET /api/mail/inbox`：聚合收件箱，可筛选 `all/read/unread`。
- `GET /api/mail/messages/{id}`：读取邮件详情。
- `POST /api/mail/messages/{id}/read`：修改已读/未读。
- `POST /api/mail/messages/{id}/remote-content`：允许或禁止加载远程内容。
- `GET /api/mail/tags` / `POST /api/mail/tags`：标签管理。
- `POST /api/mail/send`：发信。
- `GET /api/mail/search`：搜索邮件和预留内容项。

## 扩展

- `GET /api/plugins/catalog`：读取本地或在线插件社区。
- `POST /api/plugins/install`：安装插件清单。
- `GET /api/settings/packs`：列出语言包和主题包。
- `POST /api/translate`：翻译文本。
- `POST /api/settings/backup/webdav`：上传本地备份到 WebDAV。
