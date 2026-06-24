"""WuYou PGP 风格端到端加密（基于 cryptography 库，无需 GPG 二进制）。

混合加密方案：
    - RSA-2048 (OAEP-SHA256) 用于密钥交换
    - AES-256-GCM 用于报文加密
    - 传输格式: base64(RSA_密文 || 12字节nonce || AES_密文 || 16字节tag)

所有依赖均来自 ``cryptography`` 库，不依赖外部 GPG 二进制。

密钥生成：
    - ``generate_keypair()`` — 生成 RSA-2048 密钥对（PEM 格式）

加密/解密：
    - ``encrypt_message(plaintext, recipient_public_pem)`` — 混合加密
    - ``decrypt_message(ciphertext_b64, private_pem)`` — 混合解密

安全特性：
    - 每次加密使用随机 AES 密钥（256 位）+ 随机 nonce（96 位）
    - AES-GCM 提供认证加密（防篡改）
    - RSA-OAEP 填充防选择密文攻击
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ── constants ──────────────────────────────────────────────────────────────
_RSA_KEY_SIZE = 2048          # RSA 密钥长度（位）
_RSA_PUBLIC_EXPONENT = 65537  # RSA 公钥指数（标准值）
_AES_KEY_BYTES = 32           # AES-256（32 字节 = 256 位）
_GCM_NONCE_BYTES = 12         # GCM nonce（96 位，NIST 推荐）
_GCM_TAG_BYTES = 16           # GCM 认证标签（128 位）


# ── key generation ────────────────────────────────────────────────────────

def generate_keypair() -> tuple[str, str]:
    """Generate an RSA-2048 keypair.

    Returns:
        (private_pem, public_pem)  – both are UTF-8 strings.
    """
    private_key = rsa.generate_private_key(
        public_exponent=_RSA_PUBLIC_EXPONENT,
        key_size=_RSA_KEY_SIZE,
    )

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )

    return private_pem, public_pem


# ── hybrid encrypt / decrypt ──────────────────────────────────────────────

def encrypt_message(plaintext: str, recipient_public_pem: str) -> str:
    """Encrypt *plaintext* for a recipient identified by their public key.

    1. Generate a random 256-bit AES session key.
    2. Encrypt *plaintext* with AES-256-GCM (random nonce).
    3. Encrypt the AES session key with RSA-2048 OAEP-SHA256.
    4. Return base64-encoded concatenation of:
       ``RSA_ciphertext || nonce || AES_ciphertext || tag``
    """
    public_key = serialization.load_pem_public_key(
        recipient_public_pem.encode("utf-8")
    )

    # Step 1 – random AES key
    aes_key = os.urandom(_AES_KEY_BYTES)

    # Step 2 – AES-256-GCM encrypt
    aesgcm = AESGCM(aes_key)
    nonce = os.urandom(_GCM_NONCE_BYTES)
    # AESGCM.encrypt returns ``ciphertext || 16-byte-tag``
    aes_output = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    aes_ciphertext = aes_output[:-_GCM_TAG_BYTES]
    aes_tag = aes_output[-_GCM_TAG_BYTES:]

    # Step 3 – RSA-OAEP encrypt the AES key
    rsa_ciphertext = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    # Step 4 – pack and encode
    payload = rsa_ciphertext + nonce + aes_ciphertext + aes_tag
    return base64.b64encode(payload).decode("ascii")


def decrypt_message(ciphertext_b64: str, private_pem: str) -> str:
    """Decrypt a message produced by :func:`encrypt_message` using the
    recipient's private key.

    Raises:
        ValueError: if decryption or authentication fails.
    """
    private_key = serialization.load_pem_private_key(
        private_pem.encode("utf-8"), password=None
    )

    payload = base64.b64decode(ciphertext_b64)

    # Unpack the fixed-size fields first
    rsa_size = _RSA_KEY_SIZE // 8  # 256 bytes for RSA-2048
    if len(payload) < rsa_size + _GCM_NONCE_BYTES + _GCM_TAG_BYTES:
        raise ValueError("Ciphertext too short for expected PGP wire format.")

    rsa_ciphertext = payload[:rsa_size]
    nonce = payload[rsa_size : rsa_size + _GCM_NONCE_BYTES]
    remainder = payload[rsa_size + _GCM_NONCE_BYTES :]

    # Last 16 bytes = GCM tag, rest = AES ciphertext
    aes_ciphertext = remainder[:-_GCM_TAG_BYTES]
    aes_tag = remainder[-_GCM_TAG_BYTES:]

    # Step 1 – RSA-OAEP decrypt the AES session key
    try:
        aes_key = private_key.decrypt(
            rsa_ciphertext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except Exception as exc:
        raise ValueError(f"RSA decryption failed (wrong private key?): {exc}") from exc

    # Step 2 – AES-256-GCM decrypt
    aesgcm = AESGCM(aes_key)
    combined = aes_ciphertext + aes_tag
    try:
        plaintext_bytes = aesgcm.decrypt(nonce, combined, None)
    except Exception as exc:
        raise ValueError(f"AES-GCM decryption/authentication failed: {exc}") from exc

    return plaintext_bytes.decode("utf-8")
