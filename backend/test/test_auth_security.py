"""Unit tests for auth/security.py — bcrypt hashing + JWT primitives.

No DB required; all operations are pure in-memory.
"""

from datetime import timedelta

import jwt
import pytest

from auth.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    require_token_type,
    verify_password,
)
from exceptions import InvalidTokenTypeError


def test_password_roundtrip():
    h = hash_password("hunter2")
    assert h != "hunter2"
    assert verify_password("hunter2", h)
    assert not verify_password("wrong", h)


def test_hash_is_different_each_time():
    h1 = hash_password("same")
    h2 = hash_password("same")
    assert h1 != h2  # bcrypt salts each hash


def test_verify_password_malformed_hash_returns_false():
    """A corrupt/non-bcrypt hash must return False, not raise (bcrypt raises ValueError)."""
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_access_token_roundtrip():
    token = create_access_token(subject="user-123")
    claims = decode_token(token)
    assert claims["sub"] == "user-123"
    assert claims["type"] == "access"


def test_refresh_token_roundtrip():
    token = create_refresh_token(subject="user-456")
    claims = decode_token(token)
    assert claims["sub"] == "user-456"
    assert claims["type"] == "refresh"


def test_refresh_token_rejected_as_access():
    refresh = create_refresh_token(subject="user-123")
    claims = decode_token(refresh)
    with pytest.raises(InvalidTokenTypeError):
        require_token_type(claims, expected="access")


def test_access_token_rejected_as_refresh():
    access = create_access_token(subject="user-123")
    claims = decode_token(access)
    with pytest.raises(InvalidTokenTypeError):
        require_token_type(claims, expected="refresh")


def test_expired_token_rejected():
    # Use -30s to exceed the 10s leeway in decode_token
    tok = create_access_token(subject="u", ttl=timedelta(seconds=-30))
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(tok)


def test_invalid_token_rejected():
    with pytest.raises(jwt.PyJWTError):
        decode_token("not.a.valid.token")


def test_wrong_secret_rejected():
    import jwt as pyjwt

    token = pyjwt.encode({"sub": "x", "type": "access"}, "wrong-secret", algorithm="HS256")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(token)


# ── Phase 6: is_guest claim ───────────────────────────────────────────────────


def test_tokens_default_is_guest_false():
    """Registered/normal tokens carry is_guest=False so the client doesn't mistake them."""
    access = decode_token(create_access_token(subject="u-1"))
    refresh = decode_token(create_refresh_token(subject="u-1"))
    assert access["is_guest"] is False
    assert refresh["is_guest"] is False


def test_guest_access_token_carries_is_guest_true():
    token = create_access_token(subject="guest-1", is_guest=True)
    claims = decode_token(token)
    assert claims["is_guest"] is True
    assert claims["type"] == "access"
    assert claims["sub"] == "guest-1"


def test_guest_refresh_token_carries_is_guest_true():
    token = create_refresh_token(subject="guest-1", is_guest=True)
    claims = decode_token(token)
    assert claims["is_guest"] is True
    assert claims["type"] == "refresh"
