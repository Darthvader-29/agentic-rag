"""Pydantic request/response schemas for Phase 3 auth endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class UpgradeIn(BaseModel):
    """Claim a guest account: same fields as register; the guest's bearer identifies the user."""

    email: EmailStr
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class GuestTokenPair(TokenPair):
    """Guest mint response — a token pair plus the new user_id so the client can persist it."""

    user_id: str


class AuthTokenPair(TokenPair):
    """Register/upgrade response — a token pair plus the user_id.

    Phase 6 mints tokens on register and on guest->registered upgrade (the frontend validates a
    TokenPair from both), and carries user_id so the client can persist the (possibly new) identity.
    """

    user_id: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    username: str

    model_config = {"from_attributes": True}


class KeyIn(BaseModel):
    provider: str = Field(pattern=r"^(gemini|openai|anthropic)$")
    api_key: str = Field(min_length=1)


class KeyOut(BaseModel):
    id: uuid.UUID
    provider: str

    model_config = {"from_attributes": True}


class KeyListOut(BaseModel):
    """GET /api/keys row — id + provider + created_at. NEVER the ciphertext (no key material)."""

    id: uuid.UUID
    provider: str
    created_at: datetime

    model_config = {"from_attributes": True}
