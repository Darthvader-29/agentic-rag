"""Async data-access layer for session, document, user, LLM key, and message state.

Each function/method accepts an AsyncSession and performs a single focused query.
The caller (endpoint or background task) owns the transaction boundary.
"""

import uuid

from sqlalchemy import delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Document, DocumentStatus, Message, Session, User, UserLLMKey

# ── Session ──────────────────────────────────────────────────────────────────


async def get_or_create_session(db: AsyncSession, session_id: str, user_id: uuid.UUID) -> None:
    """Idempotent upsert of an OWNED session — safe to call repeatedly for the same session_id.

    ``user_id`` is required: sessions are never unowned (tenant-isolation invariant — see the
    NOT NULL ``sessions.user_id`` migration). An existing row is left untouched (owner stands).
    """
    stmt = (
        pg_insert(Session)
        .values(id=session_id, user_id=user_id)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    await db.execute(stmt)


async def get_session(db: AsyncSession, session_id: str) -> Session | None:
    """Return the Session row for session_id, or None if it doesn't exist."""
    return await db.get(Session, session_id)


async def create_session(db: AsyncSession, session_id: str, user_id: uuid.UUID) -> Session:
    """Create a new session owned by user_id."""
    session = Session(id=session_id, user_id=user_id)
    db.add(session)
    await db.flush()
    return session


# ── Document ─────────────────────────────────────────────────────────────────


async def create_document(
    db: AsyncSession, *, session_id: str, s3_key: str, filename: str
) -> Document:
    doc = Document(
        session_id=session_id,
        s3_key=s3_key,
        filename=filename,
        status=DocumentStatus.PENDING,
    )
    db.add(doc)
    await db.flush()  # populate doc.id without ending the request transaction
    return doc


async def get_document(db: AsyncSession, document_id: str) -> Document | None:
    """Return the Document row for document_id, or None if it doesn't exist."""
    return await db.get(Document, document_id)


async def set_document_status(db: AsyncSession, *, s3_key: str, status: DocumentStatus) -> None:
    await db.execute(update(Document).where(Document.s3_key == s3_key).values(status=status))


async def set_document_status_by_id(
    db: AsyncSession, *, document_id: str, status: DocumentStatus
) -> None:
    """Scope the status write by primary key, not the globally-unique s3_key.

    Keying on document_id stops a confirmed (document_id, s3_key) mismatch from landing the UPDATE
    on another user's row (s3_key is unique across tenants) — see the confirm_upload fix.
    """
    await db.execute(update(Document).where(Document.id == document_id).values(status=status))


async def session_has_documents(db: AsyncSession, session_id: str) -> bool:
    stmt = select(exists().where(Document.session_id == session_id))
    return bool(await db.scalar(stmt))


async def list_s3_keys_for_session(db: AsyncSession, session_id: str) -> list[str]:
    stmt = (
        select(Document.s3_key).where(Document.session_id == session_id).order_by(Document.s3_key)
    )
    return list(await db.scalars(stmt))


async def delete_session(db: AsyncSession, session_id: str) -> None:
    # FK ON DELETE CASCADE removes the session's documents atomically
    await db.execute(delete(Session).where(Session.id == session_id))


# ── UserRepository ───────────────────────────────────────────────────────────


class UserRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, *, email: str, username: str, hashed_password: str) -> User:
        user = User(email=email, username=username, hashed_password=hashed_password)
        self.db.add(user)
        await self.db.flush()
        return user

    async def create_guest(self, *, email: str, username: str, hashed_password: str) -> User:
        """Create an anonymous guest user (is_guest=True) with placeholder credentials (Phase 6)."""
        user = User(email=email, username=username, hashed_password=hashed_password, is_guest=True)
        self.db.add(user)
        await self.db.flush()
        return user

    async def upgrade_guest(
        self, user: User, *, email: str, username: str, hashed_password: str
    ) -> User:
        """Promote a guest to a registered account in place — same id, sessions, and BYOK keys."""
        user.email = email
        user.username = username
        user.hashed_password = hashed_password
        user.is_guest = False
        await self.db.flush()
        return user

    async def get(self, user_id: str | uuid.UUID) -> User | None:
        if isinstance(user_id, str):
            try:
                user_id = uuid.UUID(user_id)
            except ValueError:
                return None
        return await self.db.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()


# ── LLMKeyRepository ─────────────────────────────────────────────────────────


class LLMKeyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert(self, *, user_id: uuid.UUID, provider: str, ciphertext: str) -> UserLLMKey:
        """Insert or update (rotate) the ciphertext for the given user+provider."""
        stmt = (
            pg_insert(UserLLMKey)
            .values(user_id=user_id, provider=provider, ciphertext=ciphertext)
            .on_conflict_do_update(
                index_elements=["user_id", "provider"],
                set_={"ciphertext": ciphertext},
            )
            .returning(UserLLMKey)
        )
        result = await self.db.execute(stmt)
        await self.db.flush()
        row = result.scalar_one()
        return row

    async def rotate(self, *, user_id: uuid.UUID, provider: str, ciphertext: str) -> UserLLMKey:
        return await self.upsert(user_id=user_id, provider=provider, ciphertext=ciphertext)

    async def delete(self, *, user_id: uuid.UUID, provider: str) -> None:
        await self.db.execute(
            delete(UserLLMKey).where(UserLLMKey.user_id == user_id, UserLLMKey.provider == provider)
        )

    async def list_for_user(self, user_id: uuid.UUID) -> list[UserLLMKey]:
        result = await self.db.execute(select(UserLLMKey).where(UserLLMKey.user_id == user_id))
        return list(result.scalars())

    async def get(self, *, user_id: uuid.UUID, provider: str) -> UserLLMKey | None:
        result = await self.db.execute(
            select(UserLLMKey).where(UserLLMKey.user_id == user_id, UserLLMKey.provider == provider)
        )
        return result.scalar_one_or_none()


# ── Phase 4: per-request key lookup ──────────────────────────────────────────


async def get_user_llm_key(db: AsyncSession, *, user_id: uuid.UUID) -> UserLLMKey | None:
    """Return any active LLM key for the user (first row; provider field names which adapter)."""
    result = await db.execute(select(UserLLMKey).where(UserLLMKey.user_id == user_id).limit(1))
    return result.scalar_one_or_none()


# ── Phase 6: conversation history ────────────────────────────────────────────


async def save_message(db: AsyncSession, *, session_id: str, role: str, content: str) -> Message:
    """Persist one conversation turn (role: "user" | "assistant")."""
    msg = Message(session_id=session_id, role=role, content=content)
    db.add(msg)
    await db.flush()
    return msg


async def load_recent_messages(db: AsyncSession, *, session_id: str, limit: int) -> list[Message]:
    """Return the last `limit` messages for the session in chronological (oldest-first) order."""
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(limit)
    )
    rows = list(await db.scalars(stmt))
    rows.reverse()
    return rows
