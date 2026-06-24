"""WuYou IMAP/SMTP 邮件客户端。

提供：
- 收件箱全量同步（``sync_inbox``）：拉取最近 N 封邮件及其附件
- 文件夹增量同步（``sync_folder_incremental``）：基于 UID 的增量拉取
- 邮件发送（``send_email``）：支持 TLS/SSL 自动协商、Markdown 渲染、PGP 端到端加密

内部辅助函数负责：
- 邮件正文/附件提取（``_extract_body``）
- IMAP FLAGS 解析（``_extract_flags``）
- 地址头解码（``_decode_addresses``）
- 附件文件名安全化（``_safe_filename``）
"""

from __future__ import annotations

import email
import imaplib
import json
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.policy import default
from email.utils import getaddresses
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

from app.core.security import utc_iso
from app.models import SendMailRequest
from app.services.sync.sync_engine import build_uid_range

_md_renderer: MarkdownIt | None = None
# 附件文件名安全化：仅保留字母数字、点号、下划线、连字符，其余替换为下划线
_FILENAME_ILLEGAL_RE = re.compile(r"[^a-zA-Z0-9._-]")


def _get_md() -> MarkdownIt:
    """延迟初始化 MarkdownIt 渲染器（CommonMark 规范）并缓存。"""
    global _md_renderer
    if _md_renderer is None:
        _md_renderer = MarkdownIt("commonmark")
    return _md_renderer


def _safe_filename(value: str) -> str:
    """将任意字符串转为安全的文件名。

    规则：
    - 替换非法字符为下划线
    - 去除首尾的点号和下划线
    - 截断至 160 字符
    - 空字符串兜底为 "attachment"

    Args:
        value: 原始文件名（可能来自邮件附件的 filename 头）。

    Returns:
        安全的文件名。
    """
    safe = _FILENAME_ILLEGAL_RE.sub("_", value)
    return safe.strip("._")[:160] or "attachment"


def _extract_body(
    message: email.message.EmailMessage,
    attachment_dir: Path | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    """从 EmailMessage 中提取正文和附件。

    优先取 text/plain（若存在），其次 text/html。附件若指定了
    ``attachment_dir`` 则保存到磁盘，否则仅记录元数据。

    Args:
        message: 已解析的 email.message.EmailMessage 对象。
        attachment_dir: 附件保存目录（None 表示不保存到磁盘）。

    Returns:
        (body_text, body_html, attachments) 三元组。
        body_text 和 body_html 可能为空字符串；attachments 为 dict 列表。
    """
    text = ""
    html = ""
    attachments: list[dict[str, Any]] = []
    if message.is_multipart():
        for part in message.walk():
            disposition = part.get_content_disposition()
            content_type = part.get_content_type()
            filename = part.get_filename()
            if disposition == "attachment" or filename:
                payload = part.get_payload(decode=True) or b""
                saved_path = None
                if attachment_dir and payload:
                    attachment_dir.mkdir(parents=True, exist_ok=True)
                    target = attachment_dir / _safe_filename(filename or "attachment")
                    if target.exists():
                        target = attachment_dir / f"{target.stem}_{len(list(attachment_dir.iterdir()))}{target.suffix}"
                    target.write_bytes(payload)
                    saved_path = str(target)
                attachments.append(
                    {
                        "filename": filename or "attachment",
                        "content_type": content_type,
                        "size": len(payload),
                        "downloaded": bool(saved_path),
                        "path": saved_path,
                    }
                )
                continue
            try:
                content = part.get_content()
            except Exception:
                continue
            if content_type == "text/plain" and not text:
                text = str(content)
            elif content_type == "text/html" and not html:
                html = str(content)
    else:
        try:
            content = message.get_content()
        except Exception:
            content = ""
        if message.get_content_type() == "text/html":
            html = str(content)
        else:
            text = str(content)
    return text, html, attachments


def _decode_addresses(header_value: str | None) -> list[str]:
    """解码邮件地址头（From/To/Cc），返回地址字符串列表。

    Args:
        header_value: 如 ``"Alice <a@x.com>, Bob <b@x.com>"``，可为 None。

    Returns:
        地址字符串列表，如 ``["a@x.com", "b@x.com"]``。
    """
    if not header_value:
        return []
    return [str(item) for item in getaddresses([header_value])]


def _extract_flags(meta: bytes | str) -> set[str]:
    """从 IMAP FETCH 返回的元数据中解析 FLAGS 集合。

    IMAP 的 meta 通常类似: ``'123 (UID 456 RFC822 {..} FLAGS (\\Seen \\Flagged))'``。
    本函数在 FLAGS 之后的括号内提取 \\Seen、\\Flagged 等标记。

    Args:
        meta: IMAP FETCH 响应项的元数据部分。

    Returns:
        FLAGS 集合，如 ``{"\\Seen", "\\Flagged"}``。
    """
    if isinstance(meta, bytes):
        text = meta.decode("utf-8", errors="ignore")
    else:
        text = str(meta)
    # meta 通常类似: '123 (UID 456 RFC822 {..} FLAGS (\\Seen \\Flagged))'
    start = text.upper().find("FLAGS")
    if start == -1:
        return set()
    frag = text[start:]
    left = frag.find("(")
    right = frag.find(")")
    if left == -1 or right == -1 or right <= left:
        return set()
    return {item for item in frag[left + 1 : right].split() if item}


def sync_inbox(
    account: dict[str, Any],
    secret: str,
    limit: int = 50,
    attachment_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Fetch recent messages from one mailbox account."""

    if account["auth_type"] in {"oauth2", "sms_code"}:
        raise RuntimeError("该登录方式需要接入服务商 OAuth2/验证码网关后才能同步。")

    client = None
    try:
        if account["imap_ssl"]:
            client = imaplib.IMAP4_SSL(account["imap_host"], int(account["imap_port"]))
        else:
            client = imaplib.IMAP4(account["imap_host"], int(account["imap_port"]))
            client.starttls(ssl.create_default_context())

        client.login(account["username"], secret)
        client.select("INBOX")
        status, data = client.search(None, "ALL")
        if status != "OK":
            raise RuntimeError("邮箱服务器未返回可同步邮件列表。")
        ids = data[0].split()[-limit:]
        messages: list[dict[str, Any]] = []
        for message_id in reversed(ids):
            status, fetched = client.fetch(message_id, "(RFC822 FLAGS)")
            if status != "OK" or not fetched:
                continue
            raw = next((item[1] for item in fetched if isinstance(item, tuple)), None)
            if not raw:
                continue
            parsed = email.message_from_bytes(raw, policy=default)
            message_identifier = parsed.get("Message-ID") or message_id.decode("ascii", errors="ignore")
            attachment_dir = None
            if attachment_root:
                attachment_dir = (
                    attachment_root
                    / str(account["user_id"])
                    / str(account["id"])
                    / _safe_filename(str(message_identifier))
                )
            body_text, body_html, attachments = _extract_body(parsed, attachment_dir=attachment_dir)
            received_at = parsed.get("Date")
            messages.append(
                {
                    "external_id": str(message_identifier),
                    "folder": "INBOX",
                    "subject": str(parsed.get("Subject") or "(无主题)"),
                    "sender": str(parsed.get("From") or ""),
                    "recipients": json.dumps(_decode_addresses(parsed.get("To")), ensure_ascii=False),
                    "snippet": (body_text or body_html).replace("\n", " ").strip()[:240],
                    "body_text": body_text,
                    "body_html": body_html,
                    "raw_headers": json.dumps(dict(parsed.items()), ensure_ascii=False),
                    "attachments_json": json.dumps(attachments, ensure_ascii=False),
                    "unread": 1,
                    "starred": 0,
                    "has_attachments": 1 if attachments else 0,
                    "remote_content_allowed": 0,
                    "received_at": str(received_at or utc_iso()),
                }
            )
        return messages
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass


def sync_folder_incremental(
    account: dict[str, Any],
    secret: str,
    imap_folder: str,
    last_uid: int,
    attachment_root: Path | None = None,
) -> tuple[list[dict[str, Any]], int, int | None]:
    """基于 UID 的文件夹增量同步。

    Returns:
        (messages, new_last_uid, uidvalidity)
    """

    if account["auth_type"] in {"oauth2", "sms_code"}:
        raise RuntimeError("该登录方式需要接入服务商 OAuth2/验证码网关后才能同步。")

    uid_range = build_uid_range(last_uid)

    if account["imap_ssl"]:
        client: imaplib.IMAP4 = imaplib.IMAP4_SSL(account["imap_host"], int(account["imap_port"]))
    else:
        client = imaplib.IMAP4(account["imap_host"], int(account["imap_port"]))
        client.starttls(ssl.create_default_context())

    uidvalidity: int | None = None
    try:
        client.login(account["username"], secret)
        status, _ = client.select(imap_folder)
        if status != "OK":
            raise RuntimeError(f"无法选择文件夹：{imap_folder}")

        # UIDVALIDITY：能取则取，不强依赖
        try:
            resp = client.response("UIDVALIDITY")
            if resp and resp[1]:
                raw = resp[1][0]
                text = raw.decode("ascii", errors="ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
                m = next((item for item in text.split() if item.isdigit()), None)
                if m:
                    uidvalidity = int(m)
                else:
                    mm = re.search(r"(\d+)", text)
                    if mm:
                        uidvalidity = int(mm.group(1))
        except Exception:
            uidvalidity = None

        status, data = client.uid("SEARCH", None, f"UID {uid_range}")
        if status != "OK" or not data:
            return ([], int(last_uid), uidvalidity)

        raw_list = data[0] or b""
        if isinstance(raw_list, str):
            raw_list = raw_list.encode("utf-8", errors="ignore")
        uids = [int(item) for item in raw_list.split() if item.isdigit()]
        if not uids:
            return ([], int(last_uid), uidvalidity)

        messages: list[dict[str, Any]] = []
        chunk_size = 50
        for idx in range(0, len(uids), chunk_size):
            chunk = uids[idx : idx + chunk_size]
            uid_set = ",".join(str(uid) for uid in chunk)
            status, fetched = client.uid("FETCH", uid_set, "(RFC822 FLAGS)")
            if status != "OK" or not fetched:
                continue

            for item in fetched:
                if not isinstance(item, tuple) or len(item) < 2:
                    continue
                meta, raw = item[0], item[1]
                if not raw:
                    continue
                flags = _extract_flags(meta)
                unread = 0 if r"\Seen" in flags or "\\Seen" in flags else 1
                starred = 1 if r"\Flagged" in flags or "\\Flagged" in flags else 0

                parsed = email.message_from_bytes(raw, policy=default)
                # 备注：同一封邮件在不同 folder 下 Message-ID 可能相同，这里沿用 Message-ID 作为主键。
                message_identifier = parsed.get("Message-ID") or f"{imap_folder}:{meta!s}"
                attachment_dir = None
                if attachment_root:
                    attachment_dir = (
                        attachment_root
                        / str(account["user_id"])
                        / str(account["id"])
                        / _safe_filename(str(message_identifier))
                    )
                body_text, body_html, attachments = _extract_body(parsed, attachment_dir=attachment_dir)
                received_at = parsed.get("Date")
                messages.append(
                    {
                        "external_id": str(message_identifier),
                        "folder": str(imap_folder),
                        "subject": str(parsed.get("Subject") or "(无主题)"),
                        "sender": str(parsed.get("From") or ""),
                        "recipients": json.dumps(_decode_addresses(parsed.get("To")), ensure_ascii=False),
                        "snippet": (body_text or body_html).replace("\n", " ").strip()[:240],
                        "body_text": body_text,
                        "body_html": body_html,
                        "raw_headers": json.dumps(dict(parsed.items()), ensure_ascii=False),
                        "attachments_json": json.dumps(attachments, ensure_ascii=False),
                        "unread": unread,
                        "starred": starred,
                        "has_attachments": 1 if attachments else 0,
                        "remote_content_allowed": 0,
                        "received_at": str(received_at or utc_iso()),
                    }
                )

        new_last_uid = max(uids) if uids else int(last_uid)
        return (messages, int(new_last_uid), uidvalidity)
    finally:
        try:
            client.close()
        except Exception:
            pass
        try:
            client.logout()
        except Exception:
            pass


def _render_body(request: SendMailRequest) -> tuple[str, str | None]:
    if request.format == "html":
        return "", request.body
    if request.format == "markdown":
        html = _get_md().render(request.body)
        return request.body, html
    return request.body, None


def send_email(account: dict[str, Any], secret: str, request: SendMailRequest, attachments_data: list[dict] | None = None) -> dict[str, Any]:
    """Send one email with TLS-first defaults.

    When ``encryption_mode`` is ``"pgp"`` the function looks up the first
    recipient's public key in the local ``pgp_keys`` table and encrypts the
    plain-text body with hybrid RSA-2048 + AES-256-GCM before handing it off
    to the SMTP server.
    """

    if account["auth_type"] in {"oauth2", "sms_code"}:
        raise RuntimeError("该登录方式需要接入服务商 OAuth2/验证码网关后才能发信。")

    pgp_encrypted = False
    text_body, html_body = _render_body(request)

    if request.encryption_mode == "pgp":
        from app.core.database import db as _db
        from app.services.pgp_crypto import encrypt_message

        # Use the first recipient's email as the PGP key selector.
        recipient_email = str(request.recipients[0])
        key_row = _db.query_one(
            "SELECT public_key_pem FROM pgp_keys WHERE email_address = ? ORDER BY created_at DESC LIMIT 1",
            (recipient_email,),
        )
        if key_row is None:
            raise RuntimeError(
                f"未找到收件人 {recipient_email} 的 PGP 公钥。请先在「PGP 密钥管理」中导入或生成该收件人的密钥对。"
            )

        encrypted_body = encrypt_message(text_body, key_row["public_key_pem"])
        text_body = encrypted_body
        html_body = None  # HTML alternative is not meaningful for PGP-encrypted payloads
        pgp_encrypted = True

    message = EmailMessage()
    message["From"] = account["email_address"]
    message["To"] = ", ".join(str(item) for item in request.recipients)
    if request.cc:
        message["Cc"] = ", ".join(str(item) for item in request.cc)
    message["Subject"] = request.subject
    if pgp_encrypted:
        message["X-WuYou-PGP"] = "encrypted"
        message["X-WuYou-Encryption"] = "pgp-e2e"
    else:
        message["X-WuYou-Encryption"] = "tls-auto"

    if request.in_reply_to:
        from app.core.database import db as _db
        orig = _db.query_one("SELECT external_id, raw_headers FROM messages WHERE id = ?", (request.in_reply_to,))
        if orig:
            message["In-Reply-To"] = orig["external_id"] or ""
            try:
                hdrs = json.loads(orig["raw_headers"]) if isinstance(orig["raw_headers"], str) else (orig["raw_headers"] or {})
            except Exception:
                hdrs = {}
            refs = hdrs.get("References", "") if isinstance(hdrs, dict) else ""
            existing = refs.split() if refs else []
            existing.append(orig["external_id"] or "")
            if len(existing) > 20:
                existing = existing[-20:]
            message["References"] = " ".join(existing)

    sig_text = account.get("signature_text", "") or ""
    sig_html = account.get("signature_html", "") or ""
    if sig_text or sig_html:
        if not pgp_encrypted:
            text_body = text_body + "\n\n-- \n" + sig_text if sig_text else text_body
        if html_body and sig_html:
            html_body = html_body + "<br/><br/>-- <br/>" + sig_html

    message.set_content(text_body or "此邮件包含 HTML 内容。")
    if html_body:
        message.add_alternative(html_body, subtype="html")

    if attachments_data:
        from email.mime.application import MIMEApplication
        for att in attachments_data:
            with open(att["file_path"], "rb") as a_file:
                part = MIMEApplication(a_file.read(), _subtype="octet-stream")
                part.add_header("Content-Disposition", "attachment", filename=att["original_name"])
                message.attach(part)

    recipients = [str(item) for item in request.recipients + request.cc + request.bcc]
    context = ssl.create_default_context()
    if account["smtp_ssl"]:
        with smtplib.SMTP_SSL(account["smtp_host"], int(account["smtp_port"]), context=context) as server:
            server.login(account["username"], secret)
            server.send_message(message, to_addrs=recipients)
    else:
        with smtplib.SMTP(account["smtp_host"], int(account["smtp_port"])) as server:
            try:
                server.starttls(context=context)
            except smtplib.SMTPException as exc:
                raise smtplib.SMTPException(f"SMTP STARTTLS 升级失败: {exc}") from exc
            server.login(account["username"], secret)
            server.send_message(message, to_addrs=recipients)
    return {"message": "邮件已提交至 SMTP 服务器。", "encrypted_transport": True, "pgp_encrypted": pgp_encrypted}
