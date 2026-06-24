"""Tests for the PGP end-to-end encryption layer."""

from __future__ import annotations

import pytest

from app.services.pgp_crypto import decrypt_message, encrypt_message, generate_keypair


# ── test_generate_keypair ──────────────────────────────────────────────────

def test_generate_keypair() -> None:
    """Generated keypair must contain non-empty PEM strings."""
    priv, pub = generate_keypair()

    assert isinstance(priv, str)
    assert isinstance(pub, str)
    assert priv.startswith("-----BEGIN PRIVATE KEY-----")
    assert pub.startswith("-----BEGIN PUBLIC KEY-----")
    assert len(priv) > 300
    assert len(pub) > 200


# ── test_encrypt_decrypt_roundtrip ─────────────────────────────────────────

def test_encrypt_decrypt_roundtrip() -> None:
    """Encrypt then decrypt should return the original plaintext."""
    priv, pub = generate_keypair()
    original = "Hello PGP — 你好，端到端加密！"

    encrypted = encrypt_message(original, pub)
    assert encrypted != original
    assert isinstance(encrypted, str)
    assert len(encrypted) > 0

    decrypted = decrypt_message(encrypted, priv)
    assert decrypted == original


# ── test_encrypt_decrypt_wrong_key ─────────────────────────────────────────

def test_encrypt_decrypt_wrong_key() -> None:
    """Decrypting with a different keypair must raise ValueError."""
    priv_alice, pub_alice = generate_keypair()
    _priv_bob, pub_bob = generate_keypair()

    encrypted = encrypt_message("secret for Alice", pub_alice)

    # Attempt to decrypt with Bob's key
    priv_bob, _pub_bob2 = generate_keypair()
    with pytest.raises(ValueError):
        decrypt_message(encrypted, priv_bob)


# ── test_empty_message ─────────────────────────────────────────────────────

def test_empty_message() -> None:
    """Encrypting and decrypting an empty string should work."""
    priv, pub = generate_keypair()
    encrypted = encrypt_message("", pub)
    decrypted = decrypt_message(encrypted, priv)
    assert decrypted == ""


# ── test_long_message ─────────────────────────────────────────────────────

def test_long_message() -> None:
    """Encrypt then decrypt a multi-kilobyte message."""
    priv, pub = generate_keypair()
    original = "PGP 密文测试 " * 500  # ~7.5 KB

    encrypted = encrypt_message(original, pub)
    decrypted = decrypt_message(encrypted, priv)
    assert decrypted == original


# ── test_tampered_ciphertext ───────────────────────────────────────────────

def test_tampered_ciphertext() -> None:
    """Modifying the ciphertext must cause AES-GCM authentication to fail."""
    priv, pub = generate_keypair()
    encrypted = encrypt_message("tamper me", pub)

    import base64

    raw = bytearray(base64.b64decode(encrypted))
    # Flip a byte in the AES ciphertext portion (just past the RSA + nonce)
    raw[-20] ^= 0x01
    tampered = base64.b64encode(bytes(raw)).decode("ascii")

    with pytest.raises(ValueError):
        decrypt_message(tampered, priv)
