"""Pydantic request and response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, model_validator


class PublicUser(BaseModel):
    id: int
    username: str | None = None
    email: EmailStr | None = None
    phone: str | None = None


class RegisterRequest(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=64)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, min_length=6, max_length=32)
    password: str = Field(min_length=8, max_length=256)

    @model_validator(mode="after")
    def require_identifier(self) -> "RegisterRequest":
        if not any([self.username, self.email, self.phone]):
            raise ValueError("请至少填写用户名、邮箱或手机号之一。")
        return self


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=256)
    password: str | None = Field(default=None, min_length=1, max_length=256)
    code: str | None = Field(default=None, min_length=4, max_length=12)


class SessionResponse(BaseModel):
    token: str
    user: PublicUser


class VerificationCodeRequest(BaseModel):
    target_type: Literal["email", "phone"]
    target: str = Field(min_length=3, max_length=256)
    purpose: Literal["login", "change_password", "change_contact", "reset_password"]


class VerificationCodeResponse(BaseModel):
    message: str
    dev_code: str | None = None


class PasswordChangeRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)
    old_password: str | None = None
    code: str | None = None
    target: str | None = None


class PasswordResetRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=4, max_length=12)
    new_password: str = Field(min_length=8, max_length=256)


class ContactChangeRequest(BaseModel):
    target_type: Literal["email", "phone"]
    target: str = Field(min_length=3, max_length=256)
    code: str = Field(min_length=4, max_length=12)


class MailboxCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)
    email_address: EmailStr
    provider: str = "auto"
    auth_type: Literal["app_password", "password", "oauth2", "key", "sms_code"] = "app_password"
    username: str | None = None
    secret: str = Field(min_length=1, max_length=2048)
    imap_host: str | None = None
    imap_port: int | None = None
    imap_ssl: bool = True
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_ssl: bool = True
    signature_html: str = ""
    signature_text: str = ""
    auto_reply_enabled: bool = False
    auto_reply_subject: str = ""
    auto_reply_body: str = ""
    auto_reply_start: str | None = None
    auto_reply_end: str | None = None
    auto_reply_days: int = 0


class MailboxOut(BaseModel):
    id: int
    display_name: str
    email_address: EmailStr
    provider: str
    imap_host: str
    imap_port: int
    imap_ssl: bool
    smtp_host: str
    smtp_port: int
    smtp_ssl: bool
    auth_type: str
    username: str
    sync_enabled: bool
    signature_html: str = ""
    signature_text: str = ""
    auto_reply_enabled: bool = False
    auto_reply_subject: str = ""
    auto_reply_body: str = ""
    auto_reply_start: str | None = None
    auto_reply_end: str | None = None
    auto_reply_days: int = 0
    created_at: str


class MessageOut(BaseModel):
    id: int
    mailbox_id: int | None = None
    folder: str
    subject: str
    sender: str
    recipients: list[str]
    snippet: str
    body_text: str = ""
    body_html: str = ""
    attachments: list[dict[str, Any]] = []
    unread: bool
    starred: bool
    has_attachments: bool
    remote_content_allowed: bool
    received_at: str
    tags: list[dict[str, Any]] = []


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=48)
    color: str = Field(default="#2f7cf6", pattern=r"^#[0-9A-Fa-f]{6}$")
    priority: int = Field(default=0, ge=0, le=9)


class TagOut(TagCreate):
    id: int


class SendMailRequest(BaseModel):
    mailbox_id: int
    recipients: list[EmailStr] = Field(min_length=1)
    cc: list[EmailStr] = []
    bcc: list[EmailStr] = []
    subject: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=2_000_000)
    format: Literal["text", "markdown", "html"] = "text"
    encryption_mode: Literal["auto", "tls_only", "pgp"] = "auto"
    attachment_ids: list[int] = []
    in_reply_to: int | None = None


class ScheduledMailCreate(BaseModel):
    mailbox_id: int
    recipients: list[EmailStr] = Field(min_length=1)
    cc: list[EmailStr] = []
    bcc: list[EmailStr] = []
    subject: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=2_000_000)
    format: Literal["text", "markdown", "html"] = "text"
    attachment_ids: list[int] = []
    scheduled_at: str = Field(min_length=1)


class ScheduledMailOut(BaseModel):
    id: int
    mailbox_id: int
    recipients: str
    cc: str
    subject: str
    body_text: str
    scheduled_at: str
    status: str
    error: str | None = None
    sent_at: str | None = None
    created_at: str


class ReplyRequest(BaseModel):
    reply_mode: Literal["reply", "reply_all", "forward"]


class TranslationRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    source_lang: str = "en"
    target_lang: str = "zh-CN"
    provider: str = "auto"
    custom_url: str | None = None
    api_key: str | None = None


class TranslationResponse(BaseModel):
    provider: str
    translated_text: str


class SettingsUpdate(BaseModel):
    key: str = Field(min_length=1, max_length=96)
    value: Any


class PluginSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=96)
    url: str = Field(min_length=1, max_length=2048)
    kind: Literal["local", "remote"] = "remote"


class PluginInstallRequest(BaseModel):
    manifest: dict[str, Any]


class PluginInstallUrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    manifest: dict[str, Any]


class WebDavBackupRequest(BaseModel):
    url: str = Field(min_length=6, max_length=2048)
    username: str | None = None
    password: str | None = None

