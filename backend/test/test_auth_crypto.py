"""Unit tests for auth/crypto.py — Fernet encryption.

No DB required; tests verify ciphertext-at-rest and key isolation.
"""

import pytest
from cryptography.fernet import Fernet, InvalidToken

from auth.crypto import decrypt_key, encrypt_key


def test_encrypt_decrypt_roundtrip():
    plaintext = "sk-test-api-key-12345"
    ciphertext = encrypt_key(plaintext)
    assert decrypt_key(ciphertext) == plaintext


def test_ciphertext_differs_from_plaintext():
    plaintext = "my-secret-api-key"
    ciphertext = encrypt_key(plaintext)
    assert ciphertext != plaintext
    assert plaintext not in ciphertext


def test_two_encryptions_differ():
    """Fernet uses random IV so two encryptions of the same value differ."""
    k = "same-key"
    c1 = encrypt_key(k)
    c2 = encrypt_key(k)
    assert c1 != c2


def test_wrong_key_fails_to_decrypt(monkeypatch):
    plaintext = "original-key"
    ciphertext = encrypt_key(plaintext)

    other_fernet_key = Fernet.generate_key().decode()
    monkeypatch.setenv("LLM_KEY_ENCRYPTION_KEY", other_fernet_key)

    import importlib

    import config

    importlib.reload(config)
    import auth.crypto

    importlib.reload(auth.crypto)

    with pytest.raises((InvalidToken, Exception)):
        auth.crypto.decrypt_key(ciphertext)

    # Restore
    importlib.reload(config)
    importlib.reload(auth.crypto)


def test_ciphertext_is_fernet_token():
    """Verify the stored value is a Fernet token (base64url bytes)."""
    import base64

    ciphertext = encrypt_key("test-key")
    # Fernet tokens are base64url-encoded and start with 0x80 when decoded
    raw = base64.urlsafe_b64decode(ciphertext + "==")  # pad if needed
    assert raw[0] == 0x80  # Fernet version byte
