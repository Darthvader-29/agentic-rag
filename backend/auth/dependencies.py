"""FastAPI dependency that validates a bearer token and loads the current user (Phase 3)."""

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from auth.security import decode_token, require_token_type
from database.models import User
from database.repository import UserRepository
from dependencies import get_db_session
from exceptions import InvalidTokenTypeError

# auto_error=False so we can return 401 instead of FastAPI's default 403
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db_session),  # shares the per-request session (no double-open)
) -> User:
    if creds is None:
        raise HTTPException(401, "missing authorization header")
    try:
        claims = require_token_type(decode_token(creds.credentials), expected="access")
    except (jwt.PyJWTError, InvalidTokenTypeError) as exc:
        raise HTTPException(401, "invalid or expired token") from exc
    user = await UserRepository(db).get(claims["sub"])
    if user is None:
        raise HTTPException(401, "user no longer exists")
    return user
