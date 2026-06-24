# WuYou（一坞邮）

> 你的邮件，都在坞里
>
> WuYou. One emailbox. All yours.

WuYou 是一个开源的跨平台 Web 多邮箱管理工具，通过 Docker 一键部署，让你在一个界面里管理所有邮箱账户。

---

## 功能特性

- **多邮箱统一管理** —— 支持同时添加和管理多个邮箱账户，告别多标签页切换。
- **12 家服务商自动配置** —— Google、Microsoft、腾讯、阿里、Apple、网易、Yahoo、TOM、新浪、搜狐、Zoho、139/联通沃邮箱一键自动匹配 IMAP/SMTP。
- **聚合收件箱** —— 所有账户邮件汇集在同一视图，不再遗漏任何重要邮件。
- **未读汇总** —— 快速浏览所有账户的未读邮件数量与列表。
- **端到端 PGP 加密** —— 内置 PGP 密钥管理，发送和阅读加密邮件，保障通信隐私。
- **远程内容安全控制** —— 默认不加载远程图片和脚本，用户确认后才显示 HTML 邮件内容。
- **SMTP TLS/SSL 发信** —— 所有外发邮件默认通过加密通道传输。
- **日历** —— 内置日历视图，支持月/周/日切换，日程创建与管理。
- **通讯录** —— 联系人管理，支持分组、搜索和快速发信。
- **任务** —— 待办事项管理，标记完成、设置截止日期。
- **便签** —— 轻量笔记，随手记录灵感与备忘。
- **CalDAV / CardDAV 同步** —— 日历和通讯录可通过 CalDAV/CardDAV 与第三方客户端（如 Thunderbird）同步。
- **Google / Microsoft Graph 同步** —— 双向同步 Google 日历和 Outlook 日历、通讯录。
- **OAuth2 一键登录** —— 支持 Google、Microsoft、QQ、Yahoo、Zoho 等 OAuth2 授权，免去手动配置应用密码。
- **Thunderbird 全量数据迁移** —— 一键导入 Thunderbird 的 profile、prefs.js 和本地邮件目录。
- **多设备远程自动同步** —— 通过同步接口在多台设备间保持数据一致。
- **响应式布局** —— 适配 PC、平板和手机，随时随地查看邮件。
- **主题包** —— 内置日间/夜间主题，支持安装社区主题自定义外观。
- **语言包** —— 默认简体中文，内置繁体中文和英文，支持社区语言包扩展。
- **插件社区** —— 本地和在线插件生态，扩展功能，安装前自动校验清单。
- **翻译服务** —— 内置 MyMemory、LibreTranslate、Lingva、自定义 API 和 OpenAI 兼容大模型接口。
- **WebDAV 备份** —— 一键备份本地数据到 WebDAV 服务器。
- **热更新** —— 升级版本无需重建 Docker 镜像，数据与程序分离，安全无忧。
- **PBKDF2-SHA256 密码保护** —— 本地总账户密码使用强散列算法存储。
- **Fernet 加密存储邮箱密钥** —— 所有邮箱账户密码本地加密保存，杜绝明文泄露。

---

## 快速开始

详见 [部署指南](docs/deploy.md)，一条命令即可启动：

```bash
docker compose up -d
```

打开 `http://localhost:8000`。

---

## 技术栈

| 层级 | 技术 |
|---|---|
| 语言 | Python 3.12 |
| Web 框架 | FastAPI |
| 数据库 | SQLite（本地），PostgreSQL（可选） |
| 容器化 | Docker + Docker Compose |
| 前端 | 原生 JavaScript + CSS（零依赖 SPA） |
| 加密 | PBKDF2-SHA256 + Fernet + PGP |
| 同步协议 | IMAP / SMTP / CalDAV / CardDAV / MS Graph |

---

## 目录结构

```text
backend/app/
  api/          FastAPI 路由
  core/         配置、数据库、安全工具
  services/     邮件、翻译、插件、备份服务
  static/       Web 前端、语言包、主题包
plugin-community/local/  默认本地插件社区
language-packs/          语言包模板
theme-packs/             主题包模板
docs/                    架构和开发文档
```

---

## 截图

> 截图即将上线。

---

## 开源协议

本项目采用 **MIT License** 协议开源。提交语言包、主题包和插件时，请在清单里声明兼容的开源许可证。

---

## 贡献指南

欢迎参与 WuYou 的开发与生态建设！详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

贡献方向包括但不限于：

- 提交 Bug 报告和功能建议
- 开发新插件或主题包
- 翻译和完善语言包
- 改进文档和教程

---

## 更新日志

每个版本的详细变更见 [CHANGELOG.md](CHANGELOG.md)。
