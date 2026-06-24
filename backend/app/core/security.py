"""WuYou 安全工具模块。

提供密码哈希/验证、会话令牌生成、本地密钥加密存储、验证码生成等基础安全原语。

密码方案：
    - 哈希算法：PBKDF2-SHA256，310,000 次迭代（OWASP 2025 推荐值）
    - 盐：每次哈希随机生成 16 字节，与哈希值一起存储
    - 存储格式：``pbkdf2_sha256${iterations}${salt_b64}${digest_b64}``
    - 验证使用 ``hmac.compare_digest`` 防时序攻击

会话令牌：
    - 生成：``secrets.token_urlsafe(48)``（384 位随机 token）
    - 存储：只存 SHA-256 哈希，从不存明文

本地密钥加密（Fernet）：
    - 用于加密数据库中存储的邮箱密码等敏感字段
    - 密钥文件自动创建于 ``data_dir/secret.key``
    - 通过 lru_cache 缓存 Fernet 实例避免重复 I/O
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

PASSWORD_ITERATIONS = 310_000  # PBKDF2 迭代次数（OWASP 2025 推荐 >= 210,000）


def now_utc() -> datetime:
    """返回当前 UTC 时间（naive datetime + UTC tzinfo）。"""
    return datetime.now(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    """将 datetime 转为 ISO 8601 字符串。不传参数则使用当前 UTC 时间。

    Args:
        value: 要格式化的 datetime，None 表示当前时间。

    Returns:
        ISO 8601 格式字符串，如 ``"2025-06-23T10:30:00+00:00"``。
    """
    return (value or now_utc()).isoformat()


def parse_utc(value: str) -> datetime:
    """从 ISO 8601 字符串解析为 UTC datetime。

    如果输入字符串未带时区信息，默认视为 UTC。

    Args:
        value: ISO 8601 格式的时间字符串。

    Returns:
        带 UTC 时区的 datetime 对象。
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def hash_password(password: str) -> str:
    """对明文密码执行 PBKDF2-SHA256 哈希。

    每次调用生成新的随机盐（16 字节），因此同一密码的两次哈希结果不同。

    Args:
        password: 用户输入的明文密码。

    Returns:
        格式为 ``pbkdf2_sha256${iterations}${salt_b64}${digest_b64}`` 的字符串，
        其中 salt 和 digest 均为 urlsafe base64 编码。
    """
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码是否与存储的哈希匹配。

    使用 ``hmac.compare_digest`` 防时序攻击。任何解析/解码异常均安全地返回 False。

    Args:
        password: 用户输入的明文密码。
        password_hash: ``hash_password()`` 生成的存储哈希。

    Returns:
        True 表示密码匹配。
    """
    try:
        scheme, iterations, salt_b64, digest_b64 = password_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def create_session_token() -> str:
    """生成 384 位（48 字节）的 URL 安全随机令牌，用于 session cookie。

    Returns:
        urlsafe base64 编码的随机字符串。
    """
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """对令牌做 SHA-256 哈希（数据库只存哈希，不存明文）。

    Args:
        token: 原始会话令牌或验证码。

    Returns:
        小写十六进制哈希字符串。
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def make_verification_code() -> str:
    """生成 6 位数字验证码（000000-999999）。

    Returns:
        6 位零填充数字字符串，如 ``"004217"``。
    """
    return f"{secrets.randbelow(1_000_000):06d}"


@lru_cache(maxsize=4)  # 最多缓存 4 个不同路径的 Fernet 实例，避免重复磁盘 I/O
def _cached_fernet(key_path: Path) -> Fernet:
    """按路径缓存 Fernet 加密器。

    首次访问某路径时：若密钥文件不存在则自动生成，然后加载。
    后续相同路径的调用直接返回缓存的 Fernet 实例。

    Args:
        key_path: 密钥文件路径（如 ``data_dir/secret.key``）。

    Returns:
        对应路径的 Fernet 加密器。
    """
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if not key_path.exists():
        key_path.write_bytes(Fernet.generate_key())
    return Fernet(key_path.read_bytes())


def load_or_create_fernet(key_path: Path) -> Fernet:
    """加载或创建 Fernet 加密器（公开接口，内部带缓存）。

    Args:
        key_path: 密钥文件路径。

    Returns:
        Fernet 实例。
    """
    return _cached_fernet(key_path)


def encrypt_secret(secret: str, key_path: Path) -> str:
    """使用本地密钥加密敏感字符串（如邮箱密码）。

    空字符串输入直接返回空字符串，不做加密。

    Args:
        secret: 明文敏感字符串。
        key_path: 密钥文件路径。

    Returns:
        base64 编码的密文字符串。
    """
    if not secret:
        return ""
    return _cached_fernet(key_path).encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_secret(encrypted: str, key_path: Path) -> str:
    """使用本地密钥解密密文。

    空字符串输入直接返回空字符串。密钥不匹配时抛出 ``ValueError``。

    Args:
        encrypted: base64 编码的密文。
        key_path: 密钥文件路径。

    Returns:
        明文字符串。

    Raises:
        ValueError: 密钥不匹配（secret.key 与加密时不同）或密文损坏。
    """
    if not encrypted:
        return ""
    try:
        return _cached_fernet(key_path).decrypt(encrypted.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("密钥解密失败，请确认 secret.key 与数据库来自同一份备份。") from exc


def session_expiry(days: int) -> str:
    """计算会话过期时间的 ISO 字符串。

    Args:
        days: 从当前时间起算的有效天数。

    Returns:
        ISO 8601 格式的过期时间字符串。
    """
    return utc_iso(now_utc() + timedelta(days=days))

