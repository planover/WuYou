# 贡献指南

欢迎提交代码、语言包、主题包和插件。为了让 WuYou 适合开源协作，请遵守以下规则。

## 代码

- 默认使用 MIT 兼容代码。
- 新功能优先服务普通用户的邮件管理体验。
- 安全相关改动必须说明威胁模型和默认行为。
- UI 文案默认简体中文，并补充英文和繁体中文键值。

## 语言包

复制 `language-packs/template.json`，填写 `meta` 和 `messages`。语言包建议使用 CC0-1.0、MIT 或 Apache-2.0。

## 主题包

复制 `theme-packs/template.json`，填写主题色 token。主题应保证日间和夜间模式下文字可读、按钮状态清晰。

## 插件

插件必须提供 `plugin.json`，至少包含：

- `id`
- `name`
- `version`
- `type`
- `category`
- `description`
- `entry`
- `permissions`
- `license`

插件不得默认读取邮箱密钥，不得绕过用户确认加载远程内容。
