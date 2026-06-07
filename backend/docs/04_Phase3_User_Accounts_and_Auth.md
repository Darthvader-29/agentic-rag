# Phase 3 — User Accounts, Auth & Encrypted BYOK Key Storage

> **For the implementer:** execute the tasks **in order**; each ends with a verification that must
> pass before its commit. The tree stays releasable after every task. Companion to
> [`00_Master_Upgrade_Roadmap.md`](./00_Master_Upgrade_Roadmap.md) §4 (Phase 3) and follows
> [`03_Phase2_PostgreSQL_and_State_Migration.md`](./03_Phase2_PostgreSQL_and_State_Migration.md).
> The mandatory ordering constraint (roadmap §3.1) — **Postgres (P2) before Auth (P3)** — holds: auth
> needs the user table and persistent ownership that P2's database/DI seam now makes possible.
>
> **Doc numbering:** `00` = master roadmap; `01` = Phase 0; `02` = Phase 1; `03` = Phase 2; `04` = this
> Phase 3 detail doc.

## 1. Objective & scope

Give the backend **multi-tenant identity** and **encrypted-at-rest, per-user LLM keys**. Phase 2 made
Postgres the operational source of truth behind the Phase 1 DI seam; Phase 3 adds *who* is calling on
top of it. After this phase the three state-mutating API routes require a valid bearer token,
session/document ownership is bound to a real user (closing the forgeable-`session_id` hole from
roadmap §1), and each user's BYOK LLM key is stored as **ciphertext** that never appears in the database
or the logs in plaintext.

**In scope:**
- **JWT bearer auth, stateless** (`pyjwt`): short-lived **access** tokens + longer-lived **refresh**
  tokens, signed/verified with a shared secret. **No server-side session store** — any instance
  validates any token with the secret, which is exactly what the P5 horizontal-scale goal needs.
- **Password hashing** with `passlib[bcrypt]`.
- **`users` table** (id, email + username unique, `hashed_password`, timestamps) added to the P2
  `database/models.py`, with a repository and an Alembic migration.
- **Auth endpoints:** `POST /auth/register`, `POST /auth/login` (issues access + refresh),
  `POST /auth/refresh` (exchanges a refresh token for a fresh access token) — new `auth/router.py`.
- **A `get_current_user` dependency** (`auth/dependencies.py`) that validates the bearer token and loads
  the user via the P2 async session dependency.
- **Protect `/api/chat`, `/api/upload`, `/api/cleanup`** with that dependency (**401** if unauthenticated).
- **Rebind `session_id` ownership** — add a `user_id` FK to the P2 `sessions` table; reject cross-user
  session/document access with **403**.
- **`user_llm_keys` table** storing provider + **ciphertext**; endpoints to **add / rotate / delete** a
  user's LLM key(s) (`auth/keys_router.py`).
- **Encryption with `cryptography` Fernet, a single master key** read from `Settings`
  (`LLM_KEY_ENCRYPTION_KEY`) — never stored in the DB, never logged (`auth/crypto.py`).
- **Tighten CORS** (`app.py:35-41`) off `*`-with-credentials to an explicit allow-list from `Settings`
  (`CORS_ALLOWED_ORIGINS`).

**Explicitly deferred** (do **not** do here):
- **Per-request BYOK *consumption*** (decrypt the user key → build a provider client → run inference
  with it) → **Phase 4.** P3 only *stores* keys securely and exposes the CRUD surface; the provider
  abstraction that consumes them does not exist yet, and `genai.configure()` is still process-global
  (roadmap §3.2). The chat path keeps the Phase 1 Gemini global.
- **Redis, rate limiting, account lockout, token revocation/blocklist** → **Phase 5.** Stateless JWT
  means a stolen access token is valid until expiry; the short access TTL is the only mitigation until
  P5 adds a Redis-backed revocation list.
- **Email verification, password reset, OAuth/social login, refresh-token rotation/reuse detection** —
  out of scope; recorded as known gaps in the auth matrix (Appendix B).

## 2. Decisions & rationale

| Decision | Rationale |
|---|---|
| **Stateless JWT bearer auth (`pyjwt`), no server session store** | Roadmap §2.4 / §3.3 target multiple stateless instances behind a load balancer. A shared signing secret lets any instance validate any token with zero shared session state — no Redis dependency at P3. |
| **Separate access + refresh tokens (short access TTL, longer refresh TTL)** | Limits the blast radius of a leaked access token without forcing constant re-login. Revocation arrives in P5; until then the small access TTL is the control. A `type` claim distinguishes them. |
| **`passlib[bcrypt]` for passwords** | Industry-standard adaptive hash; `passlib` handles salting, the cost factor, and the verify/`needs_update` flow for us. Never store or log the plaintext password. |
| **`cryptography` Fernet, single master key from `Settings`** | Roadmap §4 P3 names Fernet/envelope encryption explicitly. Fernet is authenticated (AES-128-CBC + HMAC) and timestamped — simple and hard to misuse. A single master key keeps P3 tractable; per-user envelope DEKs are a later enhancement. The key lives only in `Settings` (env/secret manager), never in the DB. |
| **Decrypt in memory only; never persist or log plaintext keys** | The entire point of encryption-at-rest. Ciphertext is the only form that touches disk or logs. The CI gate (roadmap §5 P3) asserts this. |
| **`user_id` FK on the existing P2 `sessions` table (extend, don't duplicate)** | Ownership belongs on the session row P2 already delivers. Adding the column **nullable** keeps the migration online-safe on a non-empty table (roadmap §3.1 ordering means the table already exists). |
| **Cross-user access → 403, missing/bad token → 401** | Standard HTTP semantics: 401 = "who are you?"; 403 = "I know who you are, you may not touch this". Both are tested explicitly; a genuinely missing id stays **404**. |
| **CORS allow-list from `Settings`** | `*` + `allow_credentials=True` is rejected by browsers and is unsafe (roadmap §1, §4) — the current config never actually worked for credentialed requests. An explicit origin list is the only correct configuration. |
| **New `auth/` package, not endpoints in `app.py`** | Keeps `app.py` the thin composition root the P1 DI seam established. Security primitives, the dependency, and the routers are independently testable. |

## 3. Current-state snapshot (verified — post Phase 2)

- **DI + Postgres seam exists.** P1 built clients in `app.py` `lifespan` and injects them via `Depends`
  from `dependencies.py`; P2 added the async engine/`async_sessionmaker` the same way and a
  `get_db_session` dependency yielding an `AsyncSession`. **The new `get_current_user` dependency plugs
  straight into this seam.**
- **Postgres is the operational source of truth.** `database/models.py` has `sessions`, `documents`,
  `ingestion_jobs`; `database/repository.py` serves session/document state; Alembic migrations run in
  CI. Pinecone is back to pure vector search (no `top_k=1000` state hack).
- **`session_id` is still client-generated and unauthenticated** (`app.py:182` —
  `session_id = request.session_id or str(uuid.uuid4())`). Any caller can claim any `session_id`;
  ownership is implicit and **forgeable**. (P2 left a nullable `user_id` placeholder on `sessions`
  *only if free*; this phase adds the FK + enforcement either way.)
- **No `users` table, no auth dependency.** `/api/chat` (`app.py:172`), `/api/upload` (`app.py:140`),
  `/api/cleanup` (`app.py:250`) are wide open — no `Depends` guarding them.
- **CORS is still `*` with credentials** (`app.py:35-41`, with the `# tighten for prod` comment) — the
  unsafe/invalid combination flagged in roadmap §1.
- **Config** (`config.py`) is a single `pydantic-settings` `Settings`; no JWT/encryption/CORS fields yet.
  `conftest.py` injects dummy secrets via `os.environ.setdefault` before import.
- **No mechanism to store a per-user LLM key.** BYOK has nowhere to live; `exceptions.py` has a single
  `AppException` + handler we can extend.

## 4. Risks & gotchas (with resolutions)

1. **Plaintext key leaking into logs** (the headline CI gate). `structlog` serializes whatever you pass
   it; one careless `logger.info("added key", key=plaintext)` defeats encryption entirely.
   **Resolution:** never pass the plaintext (or the master key) to a logger — log only `provider`,
   `key_id`, `user_id`. Keep decryption strictly in-memory and short-lived. Add a log-scan assertion
   (Task 7) that fails if the plaintext string ever appears in captured logs.

2. **Master key absent / malformed at boot.** Fernet requires a 32-byte url-safe base64 key; a wrong
   value otherwise raises only on first encrypt — deep inside a request. **Resolution:** validate
   `LLM_KEY_ENCRYPTION_KEY` in `Settings` (construct a `Fernet(...)` in a `field_validator`) so the app
   **fails fast at startup**, consistent with the P0 fail-fast principle.

3. **`bcrypt` 72-byte truncation.** bcrypt silently ignores bytes past 72; long passwords become weaker
   than they look. **Resolution:** `passlib`'s bcrypt handler manages this; document the limit and do
   not pre-hash unless you adopt a documented `bcrypt_sha256` scheme.

4. **Online migration of `sessions.user_id`.** Existing P2 rows have no owner; a `NOT NULL` FK added in
   one step fails on a non-empty table. **Resolution:** add the column **nullable**, deploy, assign on
   first use / backfill, then (optionally, a later migration) tighten to `NOT NULL`. The migration must
   stay reversible.

5. **CORS `*` + credentials is silently broken.** Browsers reject `Access-Control-Allow-Origin: *` when
   credentials are sent, so the current config never worked for authenticated requests. **Resolution:**
   replace with an explicit `CORS_ALLOWED_ORIGINS` list; keep `allow_credentials=True` only alongside
   concrete origins.

6. **Refresh tokens treated like access tokens.** Calling an API with a refresh token, or accepting an
   access token at `/auth/refresh`, widens the attack surface. **Resolution:** put a `type` claim
   (`"access"`/`"refresh"`) in the payload and reject the wrong type in each path (`require_token_type`).

7. **403 vs 404 ownership leak.** Returning 404 for "not yours" leaks nothing but is misleading; 403 for
   "doesn't exist" leaks existence. **Resolution:** be deliberate and consistent — **403** for a
   session/document that exists but is owned by another user; **404** for a genuinely missing id. Test
   both.

8. **Clock skew on JWT `exp`/`iat`.** Multi-instance deployments can drift slightly. **Resolution:**
   pass a small `leeway` to `jwt.decode`; keep instances NTP-synced (ops note).

9. **`get_current_user` must not double-open a DB session.** It needs the user row but runs alongside
   handlers that also take `get_db_session`. **Resolution:** depend on the **same** P2 `get_db_session`
   dependency so FastAPI shares one `AsyncSession` per request; do not build a new engine/session inside
   the auth dependency.

10. **Test secrets bleeding into prod.** Dummy `JWT_SECRET`/`LLM_KEY_ENCRYPTION_KEY` in `conftest.py`
    must never be real. **Resolution:** generate obviously-fake fixtures (a freshly generated Fernet key
    is fine — it is random and local); the P0 required-secret fail-fast guarantees prod won't boot
    without real values.

## 5. Tasks (ordered)

> Conventional-commit message per task. `uv run <cmd>` runs inside the project venv. Each task leaves
> the suite green and the tree releasable. TDD **RED → GREEN** where it is natural (security primitives,
> isolation, ciphertext-at-rest).

### Task 1 — Auth dependencies + Settings (TDD on config)
**Files:** `pyproject.toml`; `config.py`; `test/test_config.py`; `conftest.py`.

Add the runtime deps (roadmap §6):
```bash
uv add cryptography pyjwt "passlib[bcrypt]"
```
**RED** — extend `test/test_config.py`: assert `JWT_SECRET` and `LLM_KEY_ENCRYPTION_KEY` are required on
`Settings`, and that a **malformed** `LLM_KEY_ENCRYPTION_KEY` raises `ValidationError` (fail-fast).

**GREEN** — add to `Settings` in `config.py` and validate the Fernet key at construction:
```python
from cryptography.fernet import Fernet
from pydantic import field_validator

class Settings(BaseSettings):
    # ... existing P0/P1/P2 fields ...

    # --- Auth (Phase 3) ---
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MINUTES: int = 15
    REFRESH_TOKEN_TTL_DAYS: int = 7

    # --- BYOK key encryption (Phase 3) ---
    LLM_KEY_ENCRYPTION_KEY: str           # url-safe base64, 32 bytes — Fernet master key

    # --- CORS (Phase 3) ---
    CORS_ALLOWED_ORIGINS: list[str] = []

    @field_validator("LLM_KEY_ENCRYPTION_KEY")
    @classmethod
    def _validate_fernet_key(cls, v: str) -> str:
        Fernet(v.encode())               # raises if not a valid 32-byte url-safe base64 key
        return v
```
Add dummies to `conftest.py` `_DUMMY` (a real-shaped-but-local Fernet key) so the whole suite can build
`Settings`:
```python
from cryptography.fernet import Fernet
_DUMMY = {
    # ... existing dummies ...
    "JWT_SECRET": "test-jwt-secret-not-for-production",
    "LLM_KEY_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "CORS_ALLOWED_ORIGINS": '["http://localhost:3000"]',
}
```
**Verify:** `uv run pytest test/test_config.py -q` green; `uv run python -c "import app"` exits 0 under
conftest dummies.
**Commit:** `chore(deps): add cryptography/pyjwt/passlib; auth+crypto+CORS settings`

### Task 2 — `users` table + repository + migration
**Files:** `database/models.py`; `database/repository.py`; `migrations/versions/*`;
`test/test_repository.py`.

Add a `User` model alongside the P2 `Session`/`Document`/`IngestionJob`:
```python
# database/models.py
from sqlalchemy import String

class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```
Add a `UserRepository` (`create`, `get`, `get_by_email`, `get_by_username`) following the P2 repository
pattern. Autogenerate the migration (do not hand-write the revision id):
```bash
uv run alembic revision --autogenerate -m "add users table"
```
**Verify:** `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
clean on the compose Postgres; `uv run pytest test/test_repository.py -q` green; `uv run mypy database/`.
**Commit:** `feat(db): users table + repository + migration`

### Task 3 — `auth/security.py`: hashing + JWT primitives (TDD)
**Files:** create `auth/__init__.py`, `auth/security.py`; `exceptions.py` (add `InvalidTokenTypeError`);
`test/test_auth_security.py`.

**RED** — write the assertions first and watch them fail:
```python
def test_password_roundtrip():
    h = hash_password("hunter2")
    assert h != "hunter2"
    assert verify_password("hunter2", h)
    assert not verify_password("wrong", h)

def test_access_token_roundtrip():
    claims = decode_token(create_access_token(subject="user-123"))
    assert claims["sub"] == "user-123" and claims["type"] == "access"

def test_refresh_token_rejected_as_access():
    refresh = create_refresh_token(subject="user-123")
    with pytest.raises(InvalidTokenTypeError):
        require_token_type(decode_token(refresh), expected="access")

def test_expired_token_rejected():
    tok = create_access_token(subject="u", ttl=timedelta(seconds=-1))
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(tok)
```
**GREEN:**
```python
# auth/security.py
from datetime import datetime, timedelta, timezone
import jwt
from passlib.context import CryptContext
from config import settings
from exceptions import InvalidTokenTypeError

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(raw: str) -> str:
    return _pwd.hash(raw)

def verify_password(raw: str, hashed: str) -> bool:
    return _pwd.verify(raw, hashed)

def _create_token(subject: str, token_type: str, ttl: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": subject, "type": token_type, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

def create_access_token(subject: str, ttl: timedelta | None = None) -> str:
    return _create_token(subject, "access",
                         ttl or timedelta(minutes=settings.ACCESS_TOKEN_TTL_MINUTES))

def create_refresh_token(subject: str, ttl: timedelta | None = None) -> str:
    return _create_token(subject, "refresh",
                         ttl or timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS))

def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET,
                      algorithms=[settings.JWT_ALGORITHM], leeway=10)  # small skew tolerance

def require_token_type(claims: dict, expected: str) -> dict:
    if claims.get("type") != expected:
        raise InvalidTokenTypeError(expected, claims.get("type"))
    return claims
```
Add `InvalidTokenTypeError` to `exceptions.py` (a plain `Exception` subclass; reuse the module).
**Verify:** `uv run pytest test/test_auth_security.py -q`; `uv run mypy auth/security.py`.
**Commit:** `feat(auth): bcrypt hashing + JWT access/refresh token primitives`

### Task 4 — `auth/router.py`: register / login / refresh
**Files:** create `auth/schemas.py`, `auth/router.py`; `app.py` (`include_router`);
`test/test_auth_router.py`.

```python
# auth/router.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from dependencies import get_db_session            # P2 dependency
from auth.security import hash_password, verify_password, \
    create_access_token, create_refresh_token, decode_token, require_token_type
from auth.schemas import RegisterIn, LoginIn, RefreshIn, TokenPair, UserOut
from database.repository import UserRepository

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register", status_code=201, response_model=UserOut)
async def register(body: RegisterIn, db: AsyncSession = Depends(get_db_session)):
    repo = UserRepository(db)
    if await repo.get_by_email(body.email):
        raise HTTPException(409, "email already registered")
    user = await repo.create(email=body.email, username=body.username,
                             hashed_password=hash_password(body.password))
    return UserOut.model_validate(user)

@router.post("/login", response_model=TokenPair)
async def login(body: LoginIn, db: AsyncSession = Depends(get_db_session)):
    user = await UserRepository(db).get_by_email(body.email)
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(401, "invalid credentials")   # generic message on purpose
    return TokenPair(access_token=create_access_token(str(user.id)),
                     refresh_token=create_refresh_token(str(user.id)))

@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshIn):
    claims = require_token_type(decode_token(body.refresh_token), expected="refresh")
    sub = claims["sub"]
    return TokenPair(access_token=create_access_token(sub),
                     refresh_token=create_refresh_token(sub))
```
`auth/schemas.py` holds `RegisterIn`/`LoginIn`/`RefreshIn`/`TokenPair`/`UserOut` (Pydantic;
`UserOut` exposes only `id`, `email`, `username` — never `hashed_password`). Wire
`app.include_router(auth_router)` in `app.py`.
**Verify (RED→GREEN):** `uv run pytest test/test_auth_router.py -q` — register 201; duplicate email 409;
login good → 200 + two tokens; login bad password → 401; refresh with an **access** token → 401/400;
refresh with a valid refresh token → new access token.
**Commit:** `feat(auth): register/login/refresh endpoints issuing JWT pairs`

### Task 5 — `get_current_user` + protect the three API endpoints
**Files:** create `auth/dependencies.py`; `app.py` (add the dependency to chat/upload/cleanup);
`test/test_auth_protected.py`.

```python
# auth/dependencies.py
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from dependencies import get_db_session
from auth.security import decode_token, require_token_type
from database.repository import UserRepository
from database.models import User
from exceptions import InvalidTokenTypeError

bearer = HTTPBearer(auto_error=True)   # missing/blank Authorization header → 401

async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db_session),   # shares the per-request session (gotcha 9)
) -> User:
    try:
        claims = require_token_type(decode_token(creds.credentials), expected="access")
    except (jwt.PyJWTError, InvalidTokenTypeError) as exc:
        raise HTTPException(401, "invalid or expired token") from exc
    user = await UserRepository(db).get(claims["sub"])
    if user is None:
        raise HTTPException(401, "user no longer exists")
    return user
```
Protect the three endpoints in `app.py` by adding `user: User = Depends(get_current_user)` to
`chat(...)` (`app.py:172`), `upload(...)` (`app.py:140`), and `cleanup_session(...)` (`app.py:250`).
**Verify:** `uv run pytest test/test_auth_protected.py -q` — each endpoint **without** a token → 401;
with a valid access token → reaches the handler; with an expired/garbage token → 401; with a **refresh**
token → 401.
**Commit:** `feat(auth): get_current_user dependency; require auth on chat/upload/cleanup`

### Task 6 — `sessions.user_id` + cross-user isolation (403)
**Files:** `database/models.py` (extend `Session`); `migrations/versions/*`; `database/repository.py`
(ownership-aware lookups); `app.py` (chat/upload/cleanup ownership checks); `test/test_auth_isolation.py`.

Extend the **P2** `Session` model with an owner FK (nullable for online-migration safety, gotcha 4):
```python
# database/models.py — extend the existing Session
class Session(Base):
    __tablename__ = "sessions"
    # ... existing P2 columns ...
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
```
```bash
uv run alembic revision --autogenerate -m "add sessions.user_id owner fk"
```
In `chat(...)` and `upload(...)`, when a `session_id` is supplied, look up the row and enforce ownership;
on first use bind it to `current_user`. Reject another user's session with **403**:
```python
session = await session_repo.get(session_id)
if session is None:
    session = await session_repo.create(id=session_id, user_id=user.id)
elif session.user_id != user.id:
    raise HTTPException(403, "session does not belong to the current user")
```
Apply the same ownership check to document access in `cleanup_session(...)` (resolve the document →
owning session → compare `user_id`).
**Verify (the core security test):** `uv run pytest test/test_auth_isolation.py -q` —
user A creates session S + uploads a doc; user B calls `/api/chat` with S → **403**; user B calls
`/api/cleanup` on A's doc → **403**; user A → **200**; a non-existent session id → **404** (not 403).
Plus `uv run alembic upgrade head && uv run alembic downgrade -1` clean.
**Commit:** `feat(auth): bind sessions to owner; enforce cross-user 403 isolation`

### Task 7 — `auth/crypto.py` (Fernet) + `user_llm_keys` + key CRUD (ciphertext-at-rest TDD)
**Files:** create `auth/crypto.py`, `auth/keys_router.py`; `database/models.py` (`UserLLMKey`);
`database/repository.py` (`LLMKeyRepository`); `auth/schemas.py` (`KeyIn`/`KeyOut`);
`migrations/versions/*`; `app.py` (`include_router`); `test/test_auth_crypto.py`,
`test/test_auth_keys_router.py`.

Fernet helpers — master key from `Settings` only, decrypt **in memory only**:
```python
# auth/crypto.py
from cryptography.fernet import Fernet
from config import settings

def _fernet() -> Fernet:
    return Fernet(settings.LLM_KEY_ENCRYPTION_KEY.encode())

def encrypt_key(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()

def decrypt_key(ciphertext: str) -> str:
    # in-memory only; the result is never persisted or logged
    return _fernet().decrypt(ciphertext.encode()).decode()
```
Model — store provider + **ciphertext**, never plaintext:
```python
# database/models.py
from sqlalchemy import Text, UniqueConstraint

class UserLLMKey(Base):
    __tablename__ = "user_llm_keys"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))     # gemini | openai | anthropic
    ciphertext: Mapped[str] = mapped_column(Text)          # Fernet token
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    __table_args__ = (UniqueConstraint("user_id", "provider"),)
```
```bash
uv run alembic revision --autogenerate -m "add user_llm_keys table"
```
Key CRUD router (all routes behind `get_current_user`; responses **never** echo plaintext or ciphertext):
```python
# auth/keys_router.py
import structlog
router = APIRouter(prefix="/api/keys", tags=["llm-keys"])
logger = structlog.get_logger(__name__)

@router.post("", status_code=201, response_model=KeyOut)             # add
async def add_key(body: KeyIn, user=Depends(get_current_user),
                  db: AsyncSession = Depends(get_db_session)):
    rec = await LLMKeyRepository(db).upsert(
        user_id=user.id, provider=body.provider, ciphertext=encrypt_key(body.api_key))
    logger.info("llm_key_added", user_id=str(user.id),
                provider=body.provider, key_id=str(rec.id))          # NOTE: no api_key, no ciphertext
    return KeyOut(id=rec.id, provider=rec.provider)

@router.put("/{provider}", response_model=KeyOut)                    # rotate
async def rotate_key(provider: str, body: KeyIn, user=Depends(get_current_user),
                     db: AsyncSession = Depends(get_db_session)):
    rec = await LLMKeyRepository(db).rotate(
        user_id=user.id, provider=provider, ciphertext=encrypt_key(body.api_key))
    return KeyOut(id=rec.id, provider=rec.provider)

@router.delete("/{provider}", status_code=204)                      # delete
async def delete_key(provider: str, user=Depends(get_current_user),
                     db: AsyncSession = Depends(get_db_session)):
    await LLMKeyRepository(db).delete(user_id=user.id, provider=provider)
```
`KeyOut` exposes only `id` + `provider`; `KeyIn` is `{provider, api_key}`. Wire
`app.include_router(keys_router)` in `app.py`.
**Verify — the headline CI gate (roadmap §5 P3):**
```bash
uv run pytest test/test_auth_crypto.py -q
# encrypt→decrypt roundtrip; ciphertext != plaintext; a wrong key fails to decrypt.

uv run pytest test/test_auth_keys_router.py -q
# add key → query the raw user_llm_keys row → assert the stored value is NOT the plaintext api_key
#   (ciphertext-at-rest);
# rotate → ciphertext changes, decrypt yields the NEW key;
# delete → row gone, 204;
# another user's keys are isolated (user scoping);
# LOG SCAN: capture structlog output across the whole flow (capsys / structlog capture) and assert
#   the plaintext api_key string NEVER appears anywhere in the logs.
```
**Commit:** `feat(auth): Fernet key encryption + encrypted user_llm_keys CRUD`

### Task 8 — Tighten CORS
**Files:** `app.py` (`:35-41`); `test/test_cors.py`.

Replace the `*`-with-credentials middleware with the explicit allow-list:
```python
# app.py
from config import settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,   # explicit list, never "*"
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
Delete the `allow_origins=["*"]` line and the `# tighten for prod` comment.
**Verify:** `uv run pytest test/test_cors.py -q` — allowed origin → `Access-Control-Allow-Origin` echoes
that origin; disallowed origin → header absent; `"*"` is never returned alongside credentials.
**Commit:** `fix(cors): restrict origins to CORS_ALLOWED_ORIGINS allow-list`

### Task 9 — Coverage ratchet, mypy, lock & docs
**Files:** `pyproject.toml` (coverage floor); `Jenkinsfile`/CI; this doc; `README.md`.
```bash
uv run pytest --cov --cov-report=term-missing
```
Read `TOTAL`; raise `--cov-fail-under` to `floor(new TOTAL)` (ratchet **upward only**); record the
integer here and in `Jenkinsfile`/CI. Then:
```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy app.py config.py exceptions.py dependencies.py auth database
uv lock        # deps changed in Task 1 → relock; regenerate requirements*.txt if used
```
Document the auth flow + the `LLM_KEY_ENCRYPTION_KEY`/`JWT_SECRET`/`CORS_ALLOWED_ORIGINS` env vars in
`README.md` and `.env.example` (leave them blank/commented in the example — never a real value).
**Verify:** full gate (`ruff`, `ruff format --check`, `mypy`, `pytest --cov --cov-fail-under=<new>`)
all exit 0.
**Commit:** `test: auth/isolation/ciphertext suites; ratchet coverage gate; document auth env`

## 6. Exit criteria (checkable)

Restating the roadmap §4 *Phase 3 Exit* and the §5 P3 CI-gate row:

1. **All three API endpoints require auth.** `/api/chat`, `/api/upload`, `/api/cleanup` return **401**
   without a valid access token (test-asserted).
2. **Ciphertext-at-rest verified.** Querying a `user_llm_keys` row returns a Fernet token, **not** the
   plaintext API key; a full add/rotate/delete flow leaves **no plaintext key in the logs** (log-scan
   assertion). *(roadmap §5 P3: "assert keys are ciphertext at rest and never logged".)*
3. **Cross-user isolation enforced.** User B cannot read/use user A's session or document (**403**);
   genuinely missing ids return **404**.
4. **CORS locked to known origins.** No `*`-with-credentials; `Access-Control-Allow-Origin` only ever
   echoes an allow-listed origin.
5. **Encryption master key from `Settings` only.** `LLM_KEY_ENCRYPTION_KEY` is read from config,
   validated at startup, never written to the DB, never logged.
6. **Migrations apply and reverse.** `alembic upgrade head` then `downgrade` is clean for `users`,
   `sessions.user_id`, and `user_llm_keys`; migration test green in CI.
7. **All prior gates still pass** (lint, `mypy`, full pytest) and the **coverage floor is raised**
   (ratchet recorded in `pyproject.toml` + `Jenkinsfile`/CI); `uv.lock` current.

> Note: the existing CI is **Jenkins** (`Jenkinsfile`); the coverage floor lives in `pyproject.toml`.
> Roadmap §5 references GitHub Actions — use whichever is active (Phase 0 may have introduced it).

## Appendix A — `users` / `sessions` / `user_llm_keys` schema (ERD)

```
┌──────────────────────────────┐
│ users                        │
├──────────────────────────────┤
│ id              UUID  PK      │
│ email           TEXT  UNIQUE  │
│ username        TEXT  UNIQUE  │
│ hashed_password TEXT          │   ← passlib[bcrypt]; never plaintext
│ created_at      TIMESTAMPTZ   │
│ updated_at      TIMESTAMPTZ   │
└──────────────────────────────┘
        │ 1                                   │ 1
        │                                     │
        │ N                                   │ N
┌──────────────────────────────┐   ┌──────────────────────────────────┐
│ sessions  (extends P2)       │   │ user_llm_keys                    │
├──────────────────────────────┤   ├──────────────────────────────────┤
│ id          (P2 PK)          │   │ id          UUID PK              │
│ ...P2 columns...             │   │ user_id     UUID FK → users.id   │
│ user_id  UUID FK → users.id  │   │ provider    TEXT (gemini|openai| │
│          (nullable, ON DEL   │   │              anthropic)          │
│           CASCADE, indexed)  │   │ ciphertext  TEXT  ← Fernet token │
└──────────────────────────────┘   │ created_at  TIMESTAMPTZ          │
        │ 1                          │ updated_at  TIMESTAMPTZ          │
        │ N                          │ UNIQUE (user_id, provider)      │
┌──────────────────────────────┐   └──────────────────────────────────┘
│ documents (P2)               │
│  → reached only through an    │   Notes:
│    owned session; ownership   │   • No plaintext LLM-key column anywhere.
│    checked at the API layer.  │   • Master key lives in Settings only.
└──────────────────────────────┘   • ON DELETE CASCADE drops a user's
                                       sessions + keys atomically.
```

## Appendix B — Endpoint auth matrix

| Route | Method | Auth required | Ownership check | Notes |
|---|---|---|---|---|
| `/auth/register` | POST | no | — | 409 on duplicate email/username |
| `/auth/login` | POST | no | — | 401 on bad credentials (generic message) |
| `/auth/refresh` | POST | refresh token (body) | — | rejects an **access** token (`type` claim) |
| `/api/chat` | POST | **yes** (access) | session `user_id` | 401 unauth · 403 other user's session |
| `/api/upload` | POST | **yes** (access) | session `user_id` | 401 unauth · 403 other user's session |
| `/api/cleanup` | POST | **yes** (access) | document → session owner | 401 unauth · 403 other user's doc |
| `/api/keys` | POST | **yes** (access) | self (`current_user`) | add key; response has no secret |
| `/api/keys/{provider}` | PUT | **yes** (access) | self | rotate key |
| `/api/keys/{provider}` | DELETE | **yes** (access) | self | 204 |

**Known gaps (deferred):** no token revocation/blocklist, no account lockout, no rate limiting (→ P5);
no email verification / password reset / OAuth / refresh-token rotation (out of scope). A stolen access
token is valid until `exp`; the short access TTL is the only mitigation until P5.

## Appendix C — Master-key rotation procedure

The single Fernet master key (`LLM_KEY_ENCRYPTION_KEY`) is the root of confidentiality for every stored
BYOK key. Rotate it on suspected compromise or on a scheduled cadence. The procedure re-encrypts every
stored ciphertext under a new key with **zero plaintext on disk** and a clean rollback point.

1. **Generate a new master key** (operator only — never committed, never logged):
   ```bash
   uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
2. **Provide both keys to the app** during the rotation window. Fernet supports this via `MultiFernet`:
   - `LLM_KEY_ENCRYPTION_KEY` = the **new** key (used for all new encryption),
   - `LLM_KEY_ENCRYPTION_KEYS_OLD` = the previous key(s), accepted for **decryption only**.
   ```python
   # auth/crypto.py — rotation-aware variant
   from cryptography.fernet import Fernet, MultiFernet
   from config import settings

   def _multi() -> MultiFernet:
       keys = [Fernet(settings.LLM_KEY_ENCRYPTION_KEY.encode())]
       keys += [Fernet(k.encode()) for k in settings.LLM_KEY_ENCRYPTION_KEYS_OLD]
       return MultiFernet(keys)   # encrypts with keys[0]; decrypts with any
   ```
3. **Re-encrypt every row** with an idempotent, resumable one-off script (`scripts/rotate_llm_keys.py`):
   decrypt in memory under any old/new key, re-encrypt under the new key, write back in a transaction.
   `MultiFernet.rotate(token)` does exactly this per token:
   ```python
   async for row in LLMKeyRepository(db).iter_all():
       row.ciphertext = _multi().rotate(row.ciphertext.encode()).decode()
   await db.commit()
   ```
   The script **must never** print or log a decrypted key.
4. **Verify:** every row now decrypts under the new key alone; spot-check counts; confirm a fresh
   `decrypt_key` works with only the new key configured.
5. **Drop the old key:** remove `LLM_KEY_ENCRYPTION_KEYS_OLD`, redeploy with the new key only.
6. **Rollback (pre-step-5):** if anything fails mid-rotation the old key is still configured, so every
   row — re-encrypted or not — remains decryptable; re-run the idempotent script.

**Invariants throughout:** the master key(s) come only from `Settings` (env/secret manager); decrypted
key material exists only transiently in memory; nothing plaintext is ever written to the DB or the logs.
