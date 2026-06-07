"""Auth endpoints: register / login / refresh (Phase 3) + guest mint / upgrade (Phase 6)."""

import secrets
import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import get_current_user
from auth.schemas import (
    AuthTokenPair,
    GuestTokenPair,
    LoginIn,
    RefreshIn,
    RegisterIn,
    TokenPair,
    UpgradeIn,
)
from auth.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    require_token_type,
    verify_password,
)
from database.models import User
from database.repository import UserRepository
from dependencies import get_db_session
from exceptions import InvalidTokenTypeError

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", status_code=201, response_model=AuthTokenPair)
async def register(
    body: RegisterIn,
    db: AsyncSession = Depends(get_db_session),
) -> AuthTokenPair:
    repo = UserRepository(db)
    if await repo.get_by_email(body.email):
        raise HTTPException(409, "email already registered")
    if await repo.get_by_username(body.username):
        raise HTTPException(409, "username already taken")
    user = await repo.create(
        email=body.email,
        username=body.username,
        hashed_password=hash_password(body.password),
    )
    # Phase 6: registration mints a NON-guest token pair (the frontend signs the user straight in
    # and validates a TokenPair). user_id lets the client persist the new identity.
    sub = str(user.id)
    return AuthTokenPair(
        access_token=create_access_token(sub),
        refresh_token=create_refresh_token(sub),
        user_id=sub,
    )


@router.post("/guest", status_code=201, response_model=GuestTokenPair)
async def guest(
    db: AsyncSession = Depends(get_db_session),
) -> GuestTokenPair:
    """Mint an anonymous guest account + token pair (Phase 6).

    The placeholder email/username are namespaced + uuid-suffixed so they never collide with a
    real signup, and the password hash is over random bytes (no one can log in as a guest). The
    tokens carry ``is_guest=True`` so the client can tell an anonymous session after a reload.
    """
    # Collision-proof placeholder identity + an unusable (random) password — no one logs in as guest.
    marker = uuid.uuid4().hex
    user = await UserRepository(db).create_guest(
        email=f"guest+{marker}@guest.local",
        username=f"guest_{marker}",
        hashed_password=hash_password(secrets.token_urlsafe(32)),
    )
    sub = str(user.id)
    return GuestTokenPair(
        access_token=create_access_token(sub, is_guest=True),
        refresh_token=create_refresh_token(sub, is_guest=True),
        user_id=sub,
    )


@router.post("/upgrade", status_code=200, response_model=AuthTokenPair)
async def upgrade(
    body: UpgradeIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AuthTokenPair:
    """Promote the bearer's guest account to a registered one, in place (Phase 6).

    Preserves the user_id, their sessions, and their BYOK keys. Rejects (409) if the caller is
    already registered, or if the requested email/username is taken by a different user. Returns a
    fresh NON-guest token pair so the client can swap its guest tokens for registered ones.
    """
    if not current_user.is_guest:
        raise HTTPException(409, "account is already registered")
    repo = UserRepository(db)
    existing_email = await repo.get_by_email(body.email)
    if existing_email and existing_email.id != current_user.id:
        raise HTTPException(409, "email already registered")
    existing_username = await repo.get_by_username(body.username)
    if existing_username and existing_username.id != current_user.id:
        raise HTTPException(409, "username already taken")
    user = await repo.upgrade_guest(
        current_user,
        email=body.email,
        username=body.username,
        hashed_password=hash_password(body.password),
    )
    sub = str(user.id)
    return AuthTokenPair(
        access_token=create_access_token(sub),
        refresh_token=create_refresh_token(sub),
        user_id=sub,
    )


@router.post("/login", response_model=TokenPair)
async def login(
    body: LoginIn,
    db: AsyncSession = Depends(get_db_session),
) -> TokenPair:
    user = await UserRepository(db).get_by_email(body.email)
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(401, "invalid credentials")  # generic on purpose
    return TokenPair(
        access_token=create_access_token(str(user.id)),
        refresh_token=create_refresh_token(str(user.id)),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshIn) -> TokenPair:
    try:
        claims = require_token_type(decode_token(body.refresh_token), expected="refresh")
    except (jwt.PyJWTError, InvalidTokenTypeError) as exc:
        raise HTTPException(401, "invalid or expired refresh token") from exc
    sub = claims["sub"]
    return TokenPair(
        access_token=create_access_token(sub),
        refresh_token=create_refresh_token(sub),
    )
