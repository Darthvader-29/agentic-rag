"""JWT + bcrypt primitives for Phase 3 auth.

Uses the `bcrypt` library directly (passlib 1.7.4 is incompatible with bcrypt ≥ 5.0).
bcrypt silently truncates passwords at 72 bytes — keep passwords under that limit.
"""

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from config import settings
from exceptions import InvalidTokenTypeError

# bcrypt silently truncates at 72 bytes; enforce the same limit on input bytes
_BCRYPT_MAX_BYTES = 72


def hash_password(raw: str) -> str:
    pwd_bytes = raw.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(pwd_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        pwd_bytes = raw.encode("utf-8")[:_BCRYPT_MAX_BYTES]
        return bcrypt.checkpw(pwd_bytes, hashed.encode("utf-8"))
    except ValueError:
        return False


def _create_token(subject: str, token_type: str, ttl: timedelta, is_guest: bool) -> str:
    now = datetime.now(UTC)
    # is_guest lets the client tell an anonymous session from a registered one after a reload.
    payload = {
        "sub": subject,
        "type": token_type,
        "is_guest": is_guest,
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(
    subject: str, ttl: timedelta | None = None, *, is_guest: bool = False
) -> str:
    return _create_token(
        subject,
        "access",
        ttl or timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES),
        is_guest,
    )


def create_refresh_token(
    subject: str, ttl: timedelta | None = None, *, is_guest: bool = False
) -> str:
    return _create_token(
        subject,
        "refresh",
        ttl or timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS),
        is_guest,
    )


def decode_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.JWT_SECRET,
        algorithms=[settings.JWT_ALGORITHM],
        leeway=10,  # small skew tolerance for multi-instance deployments
    )


def require_token_type(claims: dict, expected: str) -> dict:
    if claims.get("type") != expected:
        raise InvalidTokenTypeError(expected, claims.get("type"))
    return claims
