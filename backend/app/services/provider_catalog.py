"""Known email provider settings used for automatic account configuration."""

from __future__ import annotations

from copy import deepcopy


PROVIDERS = [
    {
        "id": "gmail",
        "name": "Google Gmail",
        "domains": ["gmail.com", "googlemail.com"],
        "imap": {"host": "imap.gmail.com", "port": 993, "ssl": True},
        "smtp": {"host": "smtp.gmail.com", "port": 465, "ssl": True},
        "auth": ["oauth2", "app_password"],
        "hint": "建议使用 OAuth2 或 Google 应用专用密码。",
    },
    {
        "id": "microsoft",
        "name": "Microsoft Outlook / Hotmail",
        "domains": ["outlook.com", "hotmail.com", "live.com", "msn.com"],
        "imap": {"host": "outlook.office365.com", "port": 993, "ssl": True},
        "smtp": {"host": "smtp-mail.outlook.com", "port": 587, "ssl": False},
        "auth": ["oauth2", "app_password"],
        "hint": "SMTP 端口 587 会使用 STARTTLS 加密。",
    },
    {
        "id": "tencent",
        "name": "腾讯邮箱 / QQ邮箱",
        "domains": ["qq.com", "foxmail.com", "vip.qq.com"],
        "imap": {"host": "imap.qq.com", "port": 993, "ssl": True},
        "smtp": {"host": "smtp.qq.com", "port": 465, "ssl": True},
        "auth": ["app_password"],
        "hint": "请在 QQ 邮箱设置中开启 IMAP/SMTP，并使用授权码。",
    },
    {
        "id": "aliyun",
        "name": "阿里邮箱",
        "domains": ["aliyun.com", "aliyun-inc.com"],
        "imap": {"host": "imap.mxhichina.com", "port": 993, "ssl": True},
        "smtp": {"host": "smtp.mxhichina.com", "port": 465, "ssl": True},
        "auth": ["password", "app_password"],
        "hint": "自建域名邮箱可手动覆盖 IMAP/SMTP。",
    },
    {
        "id": "icloud",
        "name": "Apple iCloud Mail",
        "domains": ["icloud.com", "me.com", "mac.com"],
        "imap": {"host": "imap.mail.me.com", "port": 993, "ssl": True},
        "smtp": {"host": "smtp.mail.me.com", "port": 587, "ssl": False},
        "auth": ["app_password"],
        "hint": "通常需要 Apple 应用专用密码。",
    },
    {
        "id": "netease",
        "name": "网易邮箱",
        "domains": ["163.com", "126.com", "yeah.net"],
        "imap": {"host": "imap.163.com", "port": 993, "ssl": True},
        "smtp": {"host": "smtp.163.com", "port": 465, "ssl": True},
        "auth": ["app_password"],
        "hint": "126/yeah 邮箱可手动改为对应服务器域名。",
    },
    {
        "id": "yahoo",
        "name": "Yahoo Mail",
        "domains": ["yahoo.com", "ymail.com"],
        "imap": {"host": "imap.mail.yahoo.com", "port": 993, "ssl": True},
        "smtp": {"host": "smtp.mail.yahoo.com", "port": 465, "ssl": True},
        "auth": ["app_password", "oauth2"],
        "hint": "建议使用应用专用密码。",
    },
    {
        "id": "tom",
        "name": "TOM邮箱",
        "domains": ["tom.com", "vip.tom.com"],
        "imap": {"host": "imap.tom.com", "port": 993, "ssl": True},
        "smtp": {"host": "smtp.tom.com", "port": 465, "ssl": True},
        "auth": ["password", "app_password"],
        "hint": "企业或 VIP 邮箱可手动调整服务器。",
    },
    {
        "id": "sina",
        "name": "新浪邮箱",
        "domains": ["sina.com", "sina.cn", "vip.sina.com"],
        "imap": {"host": "imap.sina.com", "port": 993, "ssl": True},
        "smtp": {"host": "smtp.sina.com", "port": 465, "ssl": True},
        "auth": ["password", "app_password"],
        "hint": "需在邮箱设置里开启客户端服务。",
    },
    {
        "id": "sohu",
        "name": "搜狐邮箱",
        "domains": ["sohu.com"],
        "imap": {"host": "imap.sohu.com", "port": 993, "ssl": True},
        "smtp": {"host": "smtp.sohu.com", "port": 465, "ssl": True},
        "auth": ["password", "app_password"],
        "hint": "需在邮箱设置里开启 IMAP/SMTP。",
    },
    {
        "id": "zoho",
        "name": "Zoho Mail",
        "domains": ["zoho.com", "zohomail.com"],
        "imap": {"host": "imap.zoho.com", "port": 993, "ssl": True},
        "smtp": {"host": "smtp.zoho.com", "port": 465, "ssl": True},
        "auth": ["app_password", "oauth2"],
        "hint": "企业域名邮箱建议手动确认区域服务器。",
    },
    {
        "id": "139",
        "name": "中国移动 139 邮箱",
        "domains": ["139.com"],
        "imap": {"host": "imap.139.com", "port": 993, "ssl": True},
        "smtp": {"host": "smtp.139.com", "port": 465, "ssl": True},
        "auth": ["password", "sms_code"],
        "hint": "手机验证码登录需要服务商开放客户端协议能力。",
    },
    {
        "id": "wo",
        "name": "联通沃邮箱",
        "domains": ["wo.cn"],
        "imap": {"host": "imap.wo.cn", "port": 993, "ssl": True},
        "smtp": {"host": "smtp.wo.cn", "port": 465, "ssl": True},
        "auth": ["password", "sms_code"],
        "hint": "手机验证码登录需要服务商开放客户端协议能力。",
    },
]


def list_providers() -> list[dict]:
    return deepcopy(PROVIDERS)


def discover_provider(email_address: str) -> dict | None:
    domain = email_address.split("@")[-1].lower().strip()
    for provider in PROVIDERS:
        if domain in provider["domains"]:
            return deepcopy(provider)
    return None


def custom_provider(email_address: str, imap_host: str, smtp_host: str) -> dict:
    domain = email_address.split("@")[-1].lower().strip()
    return {
        "id": f"custom:{domain}",
        "name": "自建域名邮箱",
        "domains": [domain],
        "imap": {"host": imap_host, "port": 993, "ssl": True},
        "smtp": {"host": smtp_host, "port": 465, "ssl": True},
        "auth": ["password", "app_password", "key"],
        "hint": "自建域名邮箱由用户手动提供服务器配置。",
    }

