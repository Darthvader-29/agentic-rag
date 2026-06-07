"""BYOK LLM key CRUD endpoints (Phase 3).

Responses never echo the plaintext API key or its ciphertext — only id + provider.
Log entries never contain key material (see structlog calls below).
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from auth.crypto import encrypt_key
from auth.dependencies import get_current_user
from auth.schemas import KeyIn, KeyListOut, KeyOut
from database.models import User
from database.repository import LLMKeyRepository
from dependencies import get_db_session

router = APIRouter(prefix="/api/keys", tags=["llm-keys"])
logger = structlog.get_logger(__name__)


@router.get("", response_model=list[KeyListOut])
async def list_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[KeyListOut]:
    """List the caller's stored BYOK providers (id + provider + created_at) — never ciphertext."""
    rows = await LLMKeyRepository(db).list_for_user(current_user.id)
    return [KeyListOut.model_validate(r) for r in rows]


@router.post("", status_code=201, response_model=KeyOut)
async def add_key(
    body: KeyIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> KeyOut:
    rec = await LLMKeyRepository(db).upsert(
        user_id=current_user.id,
        provider=body.provider,
        ciphertext=encrypt_key(body.api_key),
    )
    logger.info(
        "llm_key_added", user_id=str(current_user.id), provider=body.provider, key_id=str(rec.id)
    )
    return KeyOut.model_validate(rec)


@router.put("/{provider}", response_model=KeyOut)
async def rotate_key(
    provider: str,
    body: KeyIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> KeyOut:
    rec = await LLMKeyRepository(db).rotate(
        user_id=current_user.id,
        provider=provider,
        ciphertext=encrypt_key(body.api_key),
    )
    logger.info(
        "llm_key_rotated", user_id=str(current_user.id), provider=provider, key_id=str(rec.id)
    )
    return KeyOut.model_validate(rec)


@router.delete("/{provider}", status_code=204)
async def delete_key(
    provider: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    repo = LLMKeyRepository(db)
    existing = await repo.get(user_id=current_user.id, provider=provider)
    if existing is None:
        raise HTTPException(404, f"no key found for provider '{provider}'")
    await repo.delete(user_id=current_user.id, provider=provider)
    logger.info("llm_key_deleted", user_id=str(current_user.id), provider=provider)
