"""WuYou 认证与账户管理路由。

提供的端点：
- ``POST /api/auth/register`` — 注册新用户（用户名/邮箱/手机号 + 密码）
- ``POST /api/auth/login`` — 登录（密码或验证码）
- ``POST /api/auth/verification-code`` — 发送验证码（邮件或短信）
- ``GET /api/auth/me`` — 获取当前用户信息
- ``POST /api/auth/logout`` — 退出登录
- ``POST /api/auth/change-password`` — 修改密码
- ``POST /api/auth/change-contact`` — 修改联系方式
- ``GET /api/auth/oauth/providers`` — 列出可用 OAuth 服务商
- ``GET /api/auth/oauth/authorize`` — 生成 OAuth 授权 URL
- ``GET /api/auth/oauth/callback`` — OAuth 回调处理

内部辅助函数处理验证码发送（邮件/SMS）、种子数据创建等。
"""

from __future__ import annotations

import json
import secrets as _secrets
import smtplib
import ssl
from datetime import timedelta
from email.mime.text import MIMEText
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status

from app.api.deps import get_current_user, row_to_public_user
from app.core.config import get_settings
from app.core.database import db
from app.core.security import (
    create_session_token,
    encrypt_secret,
    hash_password,
    hash_token,
    make_verification_code,
    now_utc,
    parse_utc,
    session_expiry,
    utc_iso,
    verify_password,
)
from app.models import (
    ContactChangeRequest,
    LoginRequest,
    PasswordChangeRequest,
    PasswordResetRequest,
    PublicUser,
    RegisterRequest,
    SessionResponse,
    VerificationCodeRequest,
    VerificationCodeResponse,
)
from app.services.provider_catalog import discover_provider
from app.services.sms_adapter import get_sms_adapter
from app.services.telemetry import track


def _send_verification_email(settings, to: str, code: str) -> bool:
    """通过系统 SMTP 发送验证码邮件。

    Args:
        settings: Settings 实例。
        to: 收件人邮箱。
        code: 6 位验证码。

    Returns:
        True 表示发送成功，False 表示失败（调用方负责返回 HTTP 503）。
    """
    host = settings.system_smtp_host
    port = int(settings.system_smtp_port)
    use_ssl_flag = bool(settings.system_smtp_ssl)
    username = settings.system_smtp_username
    password = settings.system_smtp_password
    from_addr = settings.system_from_address or "noreply@wuyou.local"

    msg = MIMEText(
        f"您的 WuYou 验证码是：{code}（10 分钟内有效）。\n\n"
        f"如非本人操作，请忽略此邮件。",
        _charset="utf-8",
    )
    msg["Subject"] = "WuYou 验证码"
    msg["From"] = from_addr
    msg["To"] = to

    if use_ssl_flag:
        context = ssl.create_default_context()
        try:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as server:
                if username and password:
                    server.login(username, password)
                server.send_message(msg)
            return True
        except Exception:
            return False
    else:
        try:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.ehlo()
                if server.has_extn("STARTTLS"):
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                if username and password:
                    server.login(username, password)
                server.send_message(msg)
            return True
        except Exception:
            return False


router = APIRouter(prefix="/api/auth", tags=["auth"])


DEFAULT_TAGS = [
    ("重要", "#d93025", 9),
    ("待处理", "#f29900", 7),
    ("工作", "#2f7cf6", 5),
    ("账单", "#0b8043", 4),
    ("稍后阅读", "#8e44ad", 3),
]

OAUTH_PROVIDERS = {
    "google": {
        "name": "Google",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scope": "openid email profile",
        "client_id_key": "oauth_google_client_id",
        "client_secret_key": "oauth_google_client_secret",
    },
    "microsoft": {
        "name": "Microsoft",
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/oidc/userinfo",
        "scope": "openid email profile offline_access",
        "client_id_key": "oauth_ms_client_id",
        "client_secret_key": "oauth_ms_client_secret",
    },
    "qq": {
        "name": "QQ",
        "auth_url": "https://graph.qq.com/oauth2.0/authorize",
        "token_url": "https://graph.qq.com/oauth2.0/token",
        "userinfo_url": "https://graph.qq.com/oauth2.0/me",
        "scope": "get_user_info",
        "client_id_key": "oauth_qq_client_id",
        "client_secret_key": "oauth_qq_client_secret",
    },
    "yahoo": {
        "name": "Yahoo",
        "auth_url": "https://api.login.yahoo.com/oauth2/request_auth",
        "token_url": "https://api.login.yahoo.com/oauth2/get_token",
        "userinfo_url": "https://api.login.yahoo.com/openid/v1/userinfo",
        "scope": "openid email profile",
        "client_id_key": "oauth_yahoo_client_id",
        "client_secret_key": "oauth_yahoo_client_secret",
    },
    "zoho": {
        "name": "Zoho",
        "auth_url": "https://accounts.zoho.com/oauth/v2/auth",
        "token_url": "https://accounts.zoho.com/oauth/v2/token",
        "userinfo_url": "https://accounts.zoho.com/oauth/user/info",
        "scope": "email profile",
        "client_id_key": "oauth_zoho_client_id",
        "client_secret_key": "oauth_zoho_client_secret",
    },
}


def _create_session(user_id: int) -> str:
    """为用户创建新会话并返回令牌。

    Args:
        user_id: 用户 ID。

    Returns:
        明文会话令牌（客户端应存储此值，后续请求用 Bearer 头携带）。
    """
    settings = get_settings()
    token = create_session_token()
    db.execute(
        "INSERT INTO sessions(user_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (user_id, hash_token(token), session_expiry(settings.session_days), utc_iso()),
    )
    return token


def _seed_user_defaults(user_id: int) -> None:
    """为新注册用户创建默认标签、设置项和欢迎邮件。

    Args:
        user_id: 新用户的 ID。
    """
    now = utc_iso()
    db.executemany(
        "INSERT OR IGNORE INTO tags(user_id, name, color, priority, created_at) VALUES (?, ?, ?, ?, ?)",
        [(user_id, name, color, priority, now) for name, color, priority in DEFAULT_TAGS],
    )
    defaults = {
        "locale": "zh-CN",
        "theme": "light",
        "remote_content_default": False,
        "attachment_auto_download": True,
        "telemetry_enabled": get_settings().telemetry_enabled_default,
        "remote_sync_endpoint": get_settings().default_remote_sync_endpoint,
        "translation_provider": get_settings().default_translation_provider,
    }
    for key, value in defaults.items():
        db.execute(
            """
            INSERT OR IGNORE INTO settings(user_id, key, value_json, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, key, json.dumps(value, ensure_ascii=False), now),
        )
    demo_message = db.query_one("SELECT id FROM messages WHERE user_id = ? LIMIT 1", (user_id,))
    if not demo_message:
        db.execute(
            """
            INSERT INTO messages(
                user_id, mailbox_id, external_id, folder, subject, sender, recipients, snippet,
                body_text, body_html, raw_headers, attachments_json, unread, starred, has_attachments,
                remote_content_allowed, received_at, created_at, updated_at
            ) VALUES (?, NULL, ?, 'INBOX', ?, ?, '[]', ?, ?, '', '{}', '[]', 1, 0, 0, 0, ?, ?, ?)
            """,
            (
                user_id,
                "welcome-local-message",
                "欢迎使用 WuYou",
                "WuYou <welcome@local>",
                "这里会汇总所有邮箱的未读邮件。默认不会加载邮件里的远程图片或追踪内容。",
                '欢迎使用 WuYou。\n\n请先在左侧进入\u201c邮箱账户\u201d添加 IMAP/SMTP 账户，然后点击同步收件箱。',
                now,
                now,
                now,
            ),
        )


def _find_user(identifier: str):
    """通过用户名、邮箱或手机号查找用户。

    Args:
        identifier: 用户名 / 邮箱 / 手机号（三种字段均匹配）。

    Returns:
        匹配的 users 行 dict，未找到返回 None。
    """
    return db.query_one(
        "SELECT * FROM users WHERE username = ? OR email = ? OR phone = ?",
        (identifier, identifier, identifier),
    )


def _verify_code(target: str, code: str, purpose: str) -> bool:
    """验证一次性验证码。

    从 ``verification_codes`` 表取出最新未消费记录，校验：
    1. 目标匹配（邮箱/手机号）
    2. 用途匹配（login / change_password / change_contact）
    3. 未过期
    4. 哈希匹配

    验证成功后自动标记为已消费（consumed_at），防重放。

    Args:
        target: 验证码接收目标（邮箱或手机号）。
        code: 用户输入的验证码明文。
        purpose: 验证码用途标识。

    Returns:
        True 表示验证通过。
    """
    row = db.query_one(
        """
        SELECT * FROM verification_codes
        WHERE target = ? AND purpose = ? AND consumed_at IS NULL
        ORDER BY id DESC LIMIT 1
        """,
        (target, purpose),
    )
    if not row:
        return False
    if now_utc() > parse_utc(row["expires_at"]):
        return False
    if hash_token(code) != row["code_hash"]:
        return False
    db.execute("UPDATE verification_codes SET consumed_at = ? WHERE id = ?", (utc_iso(), row["id"]))
    return True


@router.post("/register", response_model=SessionResponse)
def register(payload: RegisterRequest) -> SessionResponse:
    """注册新用户。

    成功后自动创建 session 并返回令牌。同时为新用户 seed 默认标签、设置和欢迎邮件。
    用户名/邮箱/手机号任一冲突均返回 400。
    """
    now = utc_iso()
    try:
        cursor = db.execute(
            """
            INSERT INTO users(username, email, phone, password_hash, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload.username,
                str(payload.email) if payload.email else None,
                payload.phone,
                hash_password(payload.password),
                now,
                now,
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="用户名、邮箱或手机号已被占用。") from exc
    user_id = int(cursor.lastrowid)
    _seed_user_defaults(user_id)
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    track("user_registered")
    return SessionResponse(token=_create_session(user_id), user=PublicUser(**row_to_public_user(user)))


@router.post("/login", response_model=SessionResponse)
def login(payload: LoginRequest) -> SessionResponse:
    """用户登录。支持密码或验证码两种方式。

    先通过 identifier（用户名/邮箱/手机号）查找用户，再用密码或验证码校验。
    """
    user = _find_user(payload.identifier)
    if not user:
        raise HTTPException(status_code=401, detail="账户不存在。")
    ok = False
    if payload.password:
        ok = verify_password(payload.password, user["password_hash"])
    elif payload.code:
        ok = _verify_code(payload.identifier, payload.code, "login")
    if not ok:
        raise HTTPException(status_code=401, detail="登录凭证不正确。")
    track("user_logged_in")
    return SessionResponse(token=_create_session(user["id"]), user=PublicUser(**row_to_public_user(user)))


@router.post("/verification-code", response_model=VerificationCodeResponse)
async def create_code(payload: VerificationCodeRequest) -> VerificationCodeResponse:
    """发送验证码（邮件或短信）。

    含 60 秒冷却限制（同 target + purpose），10 分钟有效期。
    开发环境下额外返回 dev_code 字段方便调试。
    """
    settings = get_settings()

    # ── Rate limit: 60 s cooldown per (target, purpose) ──
    recent = db.query_one(
        """
        SELECT created_at FROM verification_codes
        WHERE target = ? AND purpose = ?
        ORDER BY id DESC LIMIT 1
        """,
        (payload.target, payload.purpose),
    )
    if recent is not None:
        elapsed = (now_utc() - parse_utc(recent["created_at"])).total_seconds()
        if elapsed < 60:
            wait = int(60 - elapsed) + 1
            raise HTTPException(
                status_code=http_status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"请 {wait} 秒后重试",
            )

    code = make_verification_code()
    db.execute(
        """
        INSERT INTO verification_codes(target_type, target, purpose, code_hash, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            payload.target_type,
            payload.target,
            payload.purpose,
            hash_token(code),
            utc_iso(now_utc() + timedelta(minutes=10)),
            utc_iso(),
        ),
    )

    # ── Deliver the code ──
    sent = False
    if payload.target_type == "email":
        host = settings.system_smtp_host
        if not host:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="邮件服务未配置。",
            )
        sent = _send_verification_email(settings, payload.target, code)
        if not sent:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="验证码邮件发送失败，请稍后重试。",
            )
    elif payload.target_type == "phone":
        adapter = get_sms_adapter(settings)
        sent = await adapter.send(payload.target, code)
        if not sent:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="短信发送失败，请稍后重试。",
            )

    dev_code = code if settings.environment != "production" else None
    return VerificationCodeResponse(message="验证码已发送。", dev_code=dev_code)


@router.get("/me", response_model=PublicUser)
def me(current_user: dict = Depends(get_current_user)) -> PublicUser:
    """返回当前登录用户的基本信息。"""
    return PublicUser(**row_to_public_user(current_user))


@router.post("/logout")
def logout(current_user: dict = Depends(get_current_user)):
    """退出登录——删除当前 session 记录。

    仅删除当前使用的会话（按 sessions.id 精确匹配），不影响该用户的其他设备。
    """
    db.execute("DELETE FROM sessions WHERE id = ?", (current_user["id"],))
    return {"message": "已退出登录。"}


@router.post("/change-password")
def change_password(payload: PasswordChangeRequest, current_user: dict = Depends(get_current_user)):
    """修改密码。需提供旧密码或验证码验证身份。"""
    if payload.old_password:
        row = db.query_one("SELECT password_hash FROM users WHERE id = ?", (current_user["user_id"],))
        if not row:
            raise HTTPException(status_code=404, detail="用户不存在。")
        ok = verify_password(payload.old_password, row["password_hash"])
    elif payload.code and payload.target:
        ok = _verify_code(payload.target, payload.code, "change_password")
    else:
        ok = False
    if not ok:
        raise HTTPException(status_code=400, detail="请使用原密码或验证码验证身份。")
    db.execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
        (hash_password(payload.new_password), utc_iso(), current_user["user_id"]),
    )
    return {"message": "密码已更新。"}


@router.put("/change-password")
def change_password_put(payload: PasswordChangeRequest, current_user: dict = Depends(get_current_user)):
    """修改密码 (PUT)。需提供旧密码验证身份。"""
    if not payload.old_password or not payload.new_password:
        raise HTTPException(status_code=400, detail="请提供原密码和新密码。")
    row = db.query_one("SELECT password_hash FROM users WHERE id = ?", (current_user["user_id"],))
    if not row:
        raise HTTPException(status_code=404, detail="用户不存在。")
    if not verify_password(payload.old_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码不正确。")
    db.execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
        (hash_password(payload.new_password), utc_iso(), current_user["user_id"]),
    )
    return {"message": "密码已更新。"}


@router.post("/reset-password")
def reset_password(payload: PasswordResetRequest):
    """忘记密码 - 通过验证码重置密码。无需登录。"""
    user = _find_user(payload.identifier)
    if not user:
        raise HTTPException(status_code=404, detail="账户不存在。")
    if not _verify_code(payload.identifier, payload.code, "reset_password"):
        raise HTTPException(status_code=400, detail="验证码不正确或已过期。")
    db.execute(
        "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
        (hash_password(payload.new_password), utc_iso(), user["id"]),
    )
    return {"message": "密码已重置，请重新登录。"}


@router.post("/change-contact")
def change_contact(payload: ContactChangeRequest, current_user: dict = Depends(get_current_user)):
    """修改绑定邮箱或手机号。需验证码确认。"""
    if not _verify_code(payload.target, payload.code, "change_contact"):
        raise HTTPException(status_code=400, detail="验证码不正确或已过期。")
    if payload.target_type == "email":
        db.execute(
            "UPDATE users SET email = ?, updated_at = ? WHERE id = ?",
            (payload.target, utc_iso(), current_user["user_id"]),
        )
    else:
        db.execute(
            "UPDATE users SET phone = ?, updated_at = ? WHERE id = ?",
            (payload.target, utc_iso(), current_user["user_id"]),
        )
    return {"message": "联系方式已更新。"}


@router.put("/change-contact")
def change_contact_put(payload: ContactChangeRequest, current_user: dict = Depends(get_current_user)):
    """修改绑定邮箱或手机号 (PUT)。需验证码确认。"""
    if not _verify_code(payload.target, payload.code, "change_contact"):
        raise HTTPException(status_code=400, detail="验证码不正确或已过期。")
    if payload.target_type == "email":
        db.execute(
            "UPDATE users SET email = ?, updated_at = ? WHERE id = ?",
            (payload.target, utc_iso(), current_user["user_id"]),
        )
    else:
        db.execute(
            "UPDATE users SET phone = ?, updated_at = ? WHERE id = ?",
            (payload.target, utc_iso(), current_user["user_id"]),
        )
    return {"message": "联系方式已更新。"}


# ── OAuth2 provider routes ─────────────────────────────────────────────


@router.get("/oauth/providers")
def list_oauth_providers():
    """Return a summary list (id + name) of available OAuth2 providers."""
    return [
        {"id": pid, "name": cfg["name"]}
        for pid, cfg in OAUTH_PROVIDERS.items()
    ]


@router.get("/oauth/authorize")
def oauth_authorize(
    provider: str = Query(..., description="Provider id: google / microsoft / yahoo / zoho / qq"),
    redirect_to: str = Query("/", description="Front-end path to redirect after login"),
):
    """Generate the OAuth2 authorization URL and persist a state nonce."""
    cfg = OAUTH_PROVIDERS.get(provider)
    if not cfg:
        raise HTTPException(status_code=400, detail=f"不支持的 OAuth provider: {provider}")

    # QQ is reserved for a future iteration
    if provider == "qq":
        raise HTTPException(
            status_code=501,
            detail="QQ 邮箱 OAuth 流程特殊（需先获取 openid 再拉取用户信息），将在下个版本补充。",
        )

    settings = get_settings()
    client_id = getattr(settings, cfg["client_id_key"], "")
    if not client_id:
        raise HTTPException(
            status_code=500,
            detail=f"OAuth provider '{provider}' 的 client_id 未配置。",
        )

    state = _secrets.token_urlsafe(32)
    expires_at = utc_iso(now_utc() + timedelta(minutes=10))

    db.execute(
        "INSERT INTO oauth_states(state, provider, redirect_to, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (state, provider, redirect_to, expires_at, utc_iso()),
    )

    params = {
        "client_id": client_id,
        "redirect_uri": settings.oauth_redirect_uri,
        "response_type": "code",
        "scope": cfg["scope"],
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"{cfg['auth_url']}?{urlencode(params)}"
    return {"auth_url": auth_url}


@router.get("/oauth/callback")
async def oauth_callback(
    code: str = Query(..., description="Authorization code from the provider"),
    state: str = Query(..., description="State nonce echoed back by the provider"),
):
    """Handle the OAuth2 redirect: exchange code, fetch userinfo, create session."""
    settings = get_settings()

    # ── 1. Validate state ──
    row = db.query_one(
        "SELECT * FROM oauth_states WHERE state = ? AND expires_at > ?",
        (state, utc_iso()),
    )
    if not row:
        raise HTTPException(status_code=400, detail="OAuth state 无效或已过期，请重新授权。")

    provider_id = row["provider"]
    cfg = OAUTH_PROVIDERS.get(provider_id)
    if not cfg:
        raise HTTPException(status_code=400, detail=f"未知的 OAuth provider: {provider_id}")

    # QQ is reserved
    if provider_id == "qq":
        raise HTTPException(
            status_code=501,
            detail="QQ 邮箱 OAuth 流程特殊（需先获取 openid 再拉取用户信息），将在下个版本补充。",
        )

    client_id = getattr(settings, cfg["client_id_key"], "")
    client_secret = getattr(settings, cfg["client_secret_key"], "")

    # ── 2. Exchange code for token ──
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            cfg["token_url"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oauth_redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        try:
            token_resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"OAuth token 交换失败: {exc.response.status_code}",
            ) from exc
        token_data = token_resp.json()
        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")

        if not access_token:
            raise HTTPException(status_code=502, detail="OAuth provider 未返回 access_token。")

        # ── 3. Fetch userinfo ──
        userinfo_resp = await client.get(
            cfg["userinfo_url"],
            headers={"Authorization": f"Bearer {access_token}"},
        )
        try:
            userinfo_resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"获取用户信息失败: {exc.response.status_code}",
            ) from exc
        userinfo = userinfo_resp.json()
        email_address = userinfo.get("email", "")

    if not email_address:
        raise HTTPException(status_code=400, detail="无法从 OAuth provider 获取邮箱地址。")

    # ── 4. Find or create user ──
    user = db.query_one("SELECT * FROM users WHERE email = ?", (email_address,))
    if not user:
        username = email_address.split("@")[0]
        existing = db.query_one("SELECT id FROM users WHERE username = ?", (username,))
        if existing:
            username = f"{username}_{_secrets.token_hex(4)}"
        now = utc_iso()
        cursor = db.execute(
            "INSERT INTO users(username, email, phone, password_hash, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (
                username,
                email_address,
                None,
                hash_password(_secrets.token_urlsafe(32)),
                now,
                now,
            ),
        )
        user_id = int(cursor.lastrowid)
        _seed_user_defaults(user_id)
    else:
        user_id = int(user["id"])

    # ── 5. Look up IMAP/SMTP from provider catalog ──
    cat = discover_provider(email_address)
    if not cat:
        raise HTTPException(
            status_code=400,
            detail=f"未能识别邮箱服务商配置: {email_address}",
        )
    imap_host = cat["imap"]["host"]
    imap_port = int(cat["imap"]["port"])
    imap_ssl = 1 if cat["imap"]["ssl"] else 0
    smtp_host = cat["smtp"]["host"]
    smtp_port = int(cat["smtp"]["port"])
    smtp_ssl = 1 if cat["smtp"]["ssl"] else 0

    # ── 6. Check if mailbox_accounts already exists (idempotent) ──
    existing_mb = db.query_one(
        "SELECT id FROM mailbox_accounts WHERE user_id = ? AND email_address = ?",
        (user_id, email_address),
    )
    if not existing_mb:
        now = utc_iso()
        secret_to_store = refresh_token if refresh_token else access_token
        db.execute(
            """
            INSERT INTO mailbox_accounts(
                user_id, display_name, email_address, provider, imap_host, imap_port, imap_ssl,
                smtp_host, smtp_port, smtp_ssl, auth_type, username, encrypted_secret, sync_enabled,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                user_id,
                f"{cfg['name']} ({email_address})",
                email_address,
                cat["id"],
                imap_host,
                imap_port,
                imap_ssl,
                smtp_host,
                smtp_port,
                smtp_ssl,
                "oauth2",
                email_address,
                encrypt_secret(secret_to_store, settings.secret_key_path),
                now,
                now,
            ),
        )

    # ── 7. Clean up consumed state ──
    db.execute("DELETE FROM oauth_states WHERE state = ?", (state,))

    # ── 8. Create session ──
    token = _create_session(user_id)
    return {"token": token, "email": email_address}
