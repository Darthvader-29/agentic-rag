"""Fernet symmetric encryption for BYOK LLM keys (Phase 3).

The master key lives only in Settings; decrypted key material never touches
the database or logs — it stays in memory for the lifetime of a single request.
"""

from cryptography.fernet import Fernet

from config import settings


def _fernet() -> Fernet:
    return Fernet(settings.LLM_KEY_ENCRYPTION_KEY.encode())


def encrypt_key(plaintext: str) -> str:
    """Encrypt a plaintext API key and return the Fernet token (ciphertext)."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_key(ciphertext: str) -> str:
    """Decrypt a stored Fernet token — result is in-memory only, never persisted or logged."""
    return _fernet().decrypt(ciphertext.encode()).decode()
