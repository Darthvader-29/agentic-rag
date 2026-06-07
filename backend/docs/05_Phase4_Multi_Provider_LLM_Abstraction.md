# Phase 4 — Multi-Provider LLM Abstraction + Per-Request BYOK Clients

> **For the implementer:** execute the tasks **in order**; each ends with a verification that must
> pass before its commit. The tree stays releasable after every task. Companion to
> [`00_Master_Upgrade_Roadmap.md`](./00_Master_Upgrade_Roadmap.md) §4 (Phase 4) and follows
> [`04_Phase3_User_Accounts_and_Auth.md`](./04_Phase3_User_Accounts_and_Auth.md).
>
> **Doc numbering:** `00` = master roadmap; `01` = Phase 0; `02` = Phase 1; `03` = Phase 2;
> `04` = Phase 3; `05` = this Phase 4 detail doc.

## 1. Objective & scope

Stand up a **provider-agnostic LLM layer** — Gemini, OpenAI, and Anthropic behind one interface —
instantiated **per request** with the **authenticated user's decrypted API key**. This is the phase
the whole roadmap has been building toward: once it lands, no LLM credential or client lives in
process-global state, the provider is chosen per user/request, and **BYOK goes live end-to-end**
(roadmap §7: *"the BYOK capability goes live here"*).

Phase 1 deliberately left **one** import-time singleton untouched — the Gemini
`genai.configure()` + `GenerativeModel(...)` in `components/router.py` and
`components/generation.py` (roadmap §3 ordering constraint 2: per-user keys are unsafe without both
the DI seam *and* the per-call provider clients this abstraction provides). **Phase 4 removes that
last global.**

**In scope:**
- A new **`llm/` package**: an `LLMProvider` **Protocol** with three **async** methods —
  `route()`, `generate()`, `stream()` — plus three concrete adapters shipped at launch
  (`GeminiProvider`, `OpenAIProvider`, `AnthropicProvider`) and a factory
  `build_provider(provider_name, api_key, model) -> LLMProvider`.
- **Removal** of the global `genai.configure()` and the module-level `GenerativeModel` from
  `components/router.py` and `components/generation.py`; both functions take an **injected provider**.
- A **provider-neutral error taxonomy** in `exceptions.py` (`LLMAuthError`, `LLMRateLimitError`,
  `LLMUnavailableError`, `LLMResponseError`), replacing the Gemini-specific `GoogleAPIError`
  HTTP-code mapping currently duplicated in `router.py` and `generation.py`. Each adapter maps its own
  SDK exceptions inward.
- A DI dependency `get_llm_provider` that: resolves the authenticated user (Phase 3) → loads that
  user's `user_llm_keys` row → **decrypts the key in memory only** (Phase 3 Fernet helper) →
  `build_provider(...)` → yields a **per-request** provider, with an optional server fallback key.
- Wiring of the `app.py` `/api/chat` path to resolve the provider via `Depends` and pass it down to
  `route_query` and `generate_final_response`.
- Provider-adapter **contract tests** (mocked SDKs) + a **concurrent multi-user/multi-provider
  isolation** test.

**Explicitly deferred** (do **not** do here):
- Redis, rate limiting, Celery queue-based ingestion, S3 presigned uploads — **Phase 5**.
- LangGraph orchestration and SSE streaming endpoints — **Phase 6**. *Note:* the provider `stream()`
  method is **defined** here (it belongs on the provider interface), but it is **not yet consumed by
  an HTTP endpoint** until Phase 6.
- 3-layer memory and OpenTelemetry/LangSmith tracing — **Phase 7**.

## 2. Decisions & rationale

| Decision | Rationale |
|---|---|
| **`typing.Protocol` (`@runtime_checkable`) for `LLMProvider`** | Structural typing decouples adapters from a base import; mypy verifies conformance without inheritance coupling. `@runtime_checkable` lets the factory/tests assert membership. |
| **One provider instance built *per request*** | A user's decrypted key must never bleed into another request. Per-request construction is the isolation guarantee; nothing about the key or client is cached across requests. |
| **Single factory `build_provider(name, key, model)`** | One construction site; trivial to add a 4th provider; testable in isolation. Avoids `if/elif` drift across call sites. |
| **Neutral taxonomy in `exceptions.py`; adapters map inward** | Call sites and the HTTP handler never branch on SDK-specific exceptions. Replaces the two **duplicated** `GoogleAPIError`→HTTP blocks in `router.py`/`generation.py` with one mapping owned by each adapter. |
| **`route()`/`generate()`/`stream()` all `async`** | Phase 1 made the call sites coroutines (`generate_content_async`); the interface must match. `stream()` returns an `AsyncIterator[str]` so Phase 6 SSE plugs straight in. |
| **All three adapters at launch** | The contract-test harness is only meaningful with ≥2 real adapters; Anthropic completes the "big three" named in roadmap §2.3. Gemini-only would defeat the abstraction. |
| **Default provider/model in `Settings` + optional server fallback key** | Lets the app boot and smoke-test without a per-user key; production prefers BYOK. A user with no stored key falls back only if the operator configured one, else gets a clean `LLMAuthError`. |
| **`LLM*` errors subclass the existing `AppException`** | The app already centralises error→HTTP via `app_exception_handler` (`exceptions.py`). Carrying a `status_code` keeps that one handler authoritative; no new handler plumbing. |
| **Add `anthropic`; `openai` already present** | Per roadmap §6: `openai`, `langgraph`, `httpx`, `tenacity` are installed; `anthropic` is the only new dep this phase. |

## 3. Current-state snapshot (verified)

> **Assume Phases 1–3 are complete and merged.** That means: the async + DI seam exists
> (`lifespan` + `Depends`-injected clients, `dependencies.py`), Postgres is the operational store,
> and auth is in place with an encrypted `user_llm_keys` table plus a working Fernet decrypt helper.
> **The one thing Phase 1 deliberately left behind is the Gemini global** — this phase removes it.

Confirm the starting point before touching anything:

```bash
uv run python -c "import app"          # boots under conftest dummies
uv run pytest -q
uv run mypy .
```

- **`components/router.py`** calls `genai.configure(api_key=settings.GOOGLE_API_KEY)` at **module
  import time** (`router.py:15`) and builds a module-level `gemini_model = genai.GenerativeModel(
  model_name="gemini-2.5-flash", ...)` (`router.py:17-23`). `route_query(query, session_id,
  web_search_allowed)` calls `await gemini_model.generate_content_async(prompt)` and maps
  `GoogleAPIError` via `code.value` (HTTP status) into `AppException(status_code, detail)` — the
  403/404/429/500/503/504 ladder at `router.py:57-88`. A bare `except Exception` falls back to
  `"RAG" if has_documents else "DIRECT"`.
- **`components/generation.py`** mirrors it: its own `genai.configure(...)` (`generation.py:17`) +
  module-level `gemini_model` (`generation.py:20-27`); `generate_final_response(query, context,
  decision)` dispatches to `_generate_rag_response` / `_generate_web_response` /
  `_generate_direct_response`, each calling `await gemini_model.generate_content_async(prompt)`. The
  **identical** `GoogleAPIError`→HTTP ladder is duplicated at `generation.py:51-87`.
- **`exceptions.py`** is minimal: one `AppException(status_code, detail)` class and one
  `app_exception_handler` returning `JSONResponse`. (Phases 1–3 add `AuthError`/`EncryptionError` and
  the Fernet helpers; **there is no neutral LLM taxonomy yet** — this phase adds it.)
- **`config.py`** has a `pydantic-settings` `Settings` with required `GOOGLE_API_KEY: str` and the
  Pinecone/S3/HF vars. (Phase 1 adds `ENVIRONMENT`/`S3_ENDPOINT_URL`; Phase 3 adds
  `FERNET_KEY`/JWT settings.) **No `DEFAULT_LLM_PROVIDER`/`DEFAULT_LLM_MODEL`/fallback key yet.**
- **`app.py`** `/api/chat` (`app.py:172-246`) calls `await route_query(message, session_id,
  web_search_allowed)` and `await generate_final_response(message, context, final_route)` — the
  **module-global** Gemini model, **no provider injection**. By Phase 3 this endpoint is auth-guarded
  and `session_id` is rebound to the authenticated user.
- **`openai` is already installed**; **`anthropic` is not.**

## 4. Risks & gotchas (with resolutions)

1. **A decrypted key leaking into another request.** **Resolution:** build a **fresh** provider per
   request inside `get_llm_provider`; never cache the provider or the plaintext key across requests;
   never stash plaintext on `app.state` or a module global. The provider holds only the configured
   SDK client, never the raw key string beyond the client constructor.

2. **The decrypted key showing up in logs or a traceback.** The Phase 3 exit explicitly forbids keys
   in logs. **Resolution:** the plaintext is a **local variable** in `get_llm_provider`; never
   `logger.info(key)`, never put it in an exception `detail`. Each adapter defines a `__repr__` that
   renders only the model name. A unit test asserts the key string appears in neither `repr(provider)`
   nor any captured log record.

3. **Concurrency cross-talk between two users on different providers.** **Resolution:** adapters hold
   **no shared mutable state** — each owns its own configured SDK client. The CI
   **concurrent-isolation** test (Task 10) runs an OpenAI-key user and a Gemini-key user under
   `asyncio.gather` and asserts each receives only its own provider's output.

4. **The Gemini SDK's `genai.configure()` is process-global.** Calling it per-request would race
   across concurrent users. **Resolution:** do **not** call the module-global configure. Use the
   per-client constructor path (`genai.Client(api_key=...)`) so the key is **instance-scoped**, not
   process-scoped. This is the single change that finally retires the Phase 1 deferral.

5. **SDK exception taxonomies differ wildly across providers.** Gemini raises
   `google.api_core.exceptions.*`; OpenAI and Anthropic raise their own `AuthenticationError` /
   `RateLimitError` / `APIStatusError`. **Resolution:** each adapter owns a private `_map_error(exc)`
   translating its SDK's exceptions into the neutral taxonomy (Appendix B), ending in a catch-all →
   `LLMResponseError` so **no raw SDK exception escapes the `llm/` package**.

6. **The `stream()` interface differs across providers.** Gemini yields chunk objects with `.text`,
   OpenAI yields `choices[0].delta.content`, Anthropic exposes `stream.text_stream`. **Resolution:**
   normalise all three to an `AsyncIterator[str]` of plain text deltas; the contract test asserts each
   adapter yields `str` chunks. (No HTTP consumer until Phase 6.)

7. **Adapters drifting apart over time.** **Resolution:** a **single parametrized contract test**
   (Task 3) runs the *same* assertions against all three adapters with mocked SDKs — same routing
   outputs, same neutral error mapping.

8. **The Gemini SDK's async surface.** Today's code uses `generate_content_async`. The newer
   `genai.Client(...)` per-instance API is sync; to stay non-blocking (Phase 1 invariant) wrap its
   calls in `anyio.to_thread.run_sync` (or `asyncio.to_thread`) exactly as the other sync SDKs are
   wrapped. **Resolution:** the `GeminiProvider` offloads its blocking SDK calls; OpenAI and Anthropic
   ship native async clients and need no offload.

9. **A user with no stored key (or a disabled one).** **Resolution:** `get_llm_provider` uses the
   server fallback key if the operator configured one; otherwise it raises `LLMAuthError`
   (→ 401), which `app_exception_handler` renders as a clean envelope. No silent default.

10. **`GOOGLE_API_KEY` still required at import is removed.** Once the Gemini globals are deleted,
    `import app`/pytest collection no longer needs `GOOGLE_API_KEY`. **Resolution:** keep it in
    `conftest.py` dummies harmlessly, but it is no longer load-bearing for import; the new
    `DEFAULT_LLM_*`/fallback settings have safe defaults.

## 5. Tasks (ordered)

> Conventional-commit message per task. `uv run <cmd>` runs inside the project venv. TDD where natural
> (RED → GREEN). Each task leaves the suite green and the tree releasable. Run `uv run pytest -q` and
> `uv run mypy .` before every commit.

### Task 1 — Add `anthropic` dep + extend `Settings` (TDD)
**Files:** `pyproject.toml`/lock; `config.py`; `test/test_config.py`; `conftest.py`; `.env.example`.

Add the dependency (`openai` is already present — confirm with `uv pip show openai`):
```bash
uv add anthropic
```
**RED** — extend `test/test_config.py`:
```python
def test_default_provider_settings(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.DEFAULT_LLM_PROVIDER == "gemini"
    assert c.settings.DEFAULT_LLM_MODEL              # non-empty
    assert c.settings.LLM_FALLBACK_API_KEY.get_secret_value() == ""   # optional, empty default
```
**GREEN** — add to `Settings` in `config.py`:
```python
from typing import Literal
from pydantic import SecretStr
...
    DEFAULT_LLM_PROVIDER: Literal["gemini", "openai", "anthropic"] = "gemini"
    DEFAULT_LLM_MODEL: str = "gemini-2.5-flash"
    LLM_FALLBACK_API_KEY: SecretStr = SecretStr("")   # optional server key; BYOK preferred
```
**Verify:** `uv run pytest test/test_config.py -q` green; `uv lock`.
**Commit:** `feat(config): default LLM provider/model + optional fallback key; add anthropic dep`

### Task 2 — Provider-neutral error taxonomy in `exceptions.py` (TDD)
**Files:** `exceptions.py`; `test/test_exceptions.py`.

**RED** — assert the taxonomy and its HTTP mapping:
```python
from exceptions import (AppException, LLMError, LLMAuthError,
                        LLMRateLimitError, LLMUnavailableError, LLMResponseError)

def test_llm_taxonomy_carries_http_status():
    assert issubclass(LLMAuthError, LLMError) and issubclass(LLMError, AppException)
    assert LLMAuthError().status_code == 401
    assert LLMRateLimitError().status_code == 429
    assert LLMUnavailableError().status_code == 503
    assert LLMResponseError().status_code == 502
```
**GREEN** — add to `exceptions.py` (subclassing the existing `AppException` so the one
`app_exception_handler` still renders them):
```python
class LLMError(AppException):
    """Base for provider-neutral LLM failures."""
    status_code = 502
    default_detail = "The AI provider returned an error. Please try again."
    def __init__(self, detail: str | None = None):
        super().__init__(status_code=self.status_code, detail=detail or self.default_detail)

class LLMAuthError(LLMError):
    status_code = 401
    default_detail = "The AI provider rejected the API key. Check the key and permissions."

class LLMRateLimitError(LLMError):
    status_code = 429
    default_detail = "The AI provider rate limit was reached. Please retry later."

class LLMUnavailableError(LLMError):
    status_code = 503
    default_detail = "The AI provider is temporarily unavailable. Please retry later."

class LLMResponseError(LLMError):
    status_code = 502
    default_detail = "The AI provider returned an unusable response."
```
> The existing `GoogleAPIError`→HTTP ladders in `router.py`/`generation.py` are **deleted** in Task 8
> once the adapters own the mapping; they are not touched yet (keeps this task green in isolation).

**Verify:** `uv run pytest test/test_exceptions.py -q` green.
**Commit:** `feat(exceptions): provider-neutral LLM error taxonomy on AppException`

### Task 3 — `llm/base.py` Protocol + parametrized contract-test harness
**Files:** create `llm/__init__.py`, `llm/base.py`; create `test/llm/conftest.py`,
`test/llm/test_provider_contract.py`.

`llm/base.py`:
```python
from __future__ import annotations
from typing import AsyncIterator, Literal, Protocol, runtime_checkable

Route = Literal["RAG", "WEB", "DIRECT"]   # matches the existing router vocabulary

@runtime_checkable
class LLMProvider(Protocol):
    """Provider-agnostic LLM interface. Exactly one instance per request."""

    async def route(self, query: str, *, has_documents: bool, web_allowed: bool) -> Route:
        """Classify a query into RAG / WEB / DIRECT."""
        ...

    async def generate(self, query: str, context: str, decision: Route) -> str:
        """Produce the final answer for the decided route."""
        ...

    def stream(self, query: str, context: str, decision: Route) -> AsyncIterator[str]:
        """Yield answer text deltas (consumed by SSE in Phase 6)."""
        ...
```
Write the **single contract test** parametrized over a `provider_case` fixture that later tasks fill
in (one case per adapter, each with its SDK mocked):
```python
# test/llm/test_provider_contract.py
import pytest
from exceptions import LLMAuthError, LLMRateLimitError, LLMUnavailableError

@pytest.mark.asyncio
async def test_route_returns_known_label(provider_case):
    provider, _ = provider_case
    decision = await provider.route("hi", has_documents=False, web_allowed=True)
    assert decision in ("RAG", "WEB", "DIRECT")

@pytest.mark.asyncio
async def test_generate_returns_text(provider_case):
    provider, _ = provider_case
    out = await provider.generate("Q?", "ctx", "DIRECT")
    assert isinstance(out, str) and out

@pytest.mark.asyncio
async def test_stream_yields_str_chunks(provider_case):
    provider, _ = provider_case
    chunks = [c async for c in provider.stream("Q?", "ctx", "DIRECT")]
    assert chunks and all(isinstance(c, str) for c in chunks)

@pytest.mark.parametrize("kind, neutral", [
    ("auth", LLMAuthError), ("rate", LLMRateLimitError), ("unavailable", LLMUnavailableError),
])
@pytest.mark.asyncio
async def test_error_mapping(provider_case, kind, neutral):
    provider, mock_sdk = provider_case
    mock_sdk.raise_next(kind)                 # harness helper makes the SDK throw
    with pytest.raises(neutral):
        await provider.generate("Q?", "ctx", "DIRECT")
```
`test/llm/conftest.py` defines a `params=[]`-style `provider_case` fixture that Tasks 4–6 extend.
Run now → **red** (no adapters yet); that is expected.
**Verify:** `uv run mypy llm/base.py` clean.
**Commit:** `feat(llm): LLMProvider protocol + parametrized contract-test harness`

### Task 4 — `GeminiProvider` (port existing logic; instance-scoped key)
**Files:** create `llm/gemini.py`; register the Gemini case in `test/llm/conftest.py`.

Port the existing routing/generation prompts and the RAG/WEB/DIRECT dispatch from `router.py` /
`generation.py`, but with an **instance-scoped client** — no `genai.configure()`:
```python
# llm/gemini.py
import anyio
import google.generativeai as genai
from google.api_core import exceptions as gexc
from llm.base import Route
from exceptions import LLMAuthError, LLMRateLimitError, LLMUnavailableError, LLMResponseError

class GeminiProvider:
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self._client = genai.Client(api_key=api_key)   # instance-scoped, NOT genai.configure()
        self._model = model

    def __repr__(self) -> str:                          # never render the key
        return f"GeminiProvider(model={self._model!r})"

    def _map_error(self, exc: Exception) -> Exception:
        if isinstance(exc, (gexc.PermissionDenied, gexc.Unauthenticated)):
            return LLMAuthError() 
        if isinstance(exc, gexc.ResourceExhausted):
            return LLMRateLimitError()
        if isinstance(exc, (gexc.ServiceUnavailable, gexc.DeadlineExceeded)):
            return LLMUnavailableError()
        return LLMResponseError()

    async def _complete(self, prompt: str) -> str:
        try:
            resp = await anyio.to_thread.run_sync(
                lambda: self._client.models.generate_content(model=self._model, contents=prompt)
            )
            return resp.text.strip()
        except Exception as e:                          # noqa: BLE001 — neutralised below
            raise self._map_error(e) from e

    async def route(self, query, *, has_documents, web_allowed) -> Route:
        text = await self._complete(_routing_prompt(query, has_documents, web_allowed))
        return _normalize_decision(text)                # reuse the existing normaliser

    async def generate(self, query, context, decision) -> str:
        return await self._complete(_prompt_for(decision, query, context))

    async def stream(self, query, context, decision):
        prompt = _prompt_for(decision, query, context)
        try:
            stream = await anyio.to_thread.run_sync(
                lambda: self._client.models.generate_content_stream(model=self._model, contents=prompt)
            )
            for chunk in stream:                        # SDK stream is sync-iterable
                if chunk.text:
                    yield chunk.text
        except Exception as e:                          # noqa: BLE001
            raise self._map_error(e) from e
```
- Move the `_build_routing_prompt` / `_normalize_decision` helpers and the three generation prompts
  (`_generate_rag_response`/`_web_/_direct_`) into `llm/gemini.py` (or a shared `llm/_prompts.py`) so
  the routing vocabulary and prompts are preserved verbatim — behavior unchanged.
- Add a Gemini-specific test: `assert api_key not in repr(GeminiProvider(api_key="sk-leak"))`.

**Verify:** `uv run pytest test/llm -q -k gemini` green (the Gemini contract case now passes).
**Commit:** `feat(llm): GeminiProvider with instance-scoped key and neutral error mapping`

### Task 5 — `OpenAIProvider`
**Files:** create `llm/openai.py`; register the OpenAI case in `test/llm/conftest.py`.
```python
# llm/openai.py
from openai import (AsyncOpenAI, AuthenticationError, PermissionDeniedError,
                    RateLimitError, APIStatusError)
from llm.base import Route
from exceptions import LLMAuthError, LLMRateLimitError, LLMUnavailableError, LLMResponseError

class OpenAIProvider:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    def __repr__(self) -> str:
        return f"OpenAIProvider(model={self._model!r})"

    def _map_error(self, exc: Exception) -> Exception:
        if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
            return LLMAuthError()
        if isinstance(exc, RateLimitError):
            return LLMRateLimitError()
        if isinstance(exc, APIStatusError) and exc.status_code in (500, 502, 503):
            return LLMUnavailableError()
        return LLMResponseError()

    async def _chat(self, system: str, user: str) -> str:
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:                          # noqa: BLE001
            raise self._map_error(e) from e

    async def route(self, query, *, has_documents, web_allowed) -> Route:
        text = await self._chat("You are a routing classifier. Reply with ONE word.",
                                _routing_prompt(query, has_documents, web_allowed))
        return _normalize_decision(text)

    async def generate(self, query, context, decision) -> str:
        sys, usr = _system_and_user_for(decision, query, context)
        return await self._chat(sys, usr)

    # stream(): chat.completions.create(..., stream=True); `async for event in stream` and yield
    #           event.choices[0].delta.content when non-empty; wrap in the same _map_error.
```
**Verify:** `uv run pytest test/llm -q -k openai` green.
**Commit:** `feat(llm): OpenAIProvider adapter with neutral error mapping`

### Task 6 — `AnthropicProvider`
**Files:** create `llm/anthropic.py`; register the Anthropic case in `test/llm/conftest.py`.
```python
# llm/anthropic.py
from anthropic import (AsyncAnthropic, AuthenticationError, PermissionDeniedError,
                       RateLimitError, APIStatusError)
from llm.base import Route
from exceptions import LLMAuthError, LLMRateLimitError, LLMUnavailableError, LLMResponseError

class AnthropicProvider:
    def __init__(self, api_key: str, model: str = "claude-3-5-haiku-latest") -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    def __repr__(self) -> str:
        return f"AnthropicProvider(model={self._model!r})"

    def _map_error(self, exc: Exception) -> Exception:
        if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
            return LLMAuthError()
        if isinstance(exc, RateLimitError):
            return LLMRateLimitError()
        if isinstance(exc, APIStatusError) and exc.status_code in (500, 503, 529):  # 529 = overloaded
            return LLMUnavailableError()
        return LLMResponseError()

    async def _message(self, prompt: str, max_tokens: int = 1024) -> str:
        try:
            msg = await self._client.messages.create(
                model=self._model, max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:                          # noqa: BLE001
            raise self._map_error(e) from e

    async def route(self, query, *, has_documents, web_allowed) -> Route:
        text = await self._message(_routing_prompt(query, has_documents, web_allowed), max_tokens=8)
        return _normalize_decision(text)

    async def generate(self, query, context, decision) -> str:
        return await self._message(_prompt_for(decision, query, context))

    # stream(): `async with self._client.messages.stream(...) as stream:` then
    #           `async for text in stream.text_stream: yield text`; wrap in the same _map_error.
```
**Verify:** `uv run pytest test/llm -q` green — the full contract suite now passes across all three.
**Commit:** `feat(llm): AnthropicProvider adapter with neutral error mapping`

### Task 7 — `build_provider` factory + `get_llm_provider` DI (TDD)
**Files:** create `llm/factory.py`; extend `dependencies.py`; create `test/llm/test_factory.py`,
`test/test_get_llm_provider.py`.

**RED** — factory test:
```python
import pytest
from llm.factory import build_provider
from llm.gemini import GeminiProvider
from llm.openai import OpenAIProvider
from llm.anthropic import AnthropicProvider
from exceptions import LLMError

@pytest.mark.parametrize("name, cls", [
    ("gemini", GeminiProvider), ("openai", OpenAIProvider), ("anthropic", AnthropicProvider)])
def test_dispatch(name, cls):
    assert isinstance(build_provider(name, "k", model="m"), cls)

def test_unknown_provider():
    with pytest.raises(LLMError):
        build_provider("bedrock", "k")
```
**GREEN** — `llm/factory.py`:
```python
from llm.base import LLMProvider
from llm.gemini import GeminiProvider
from llm.openai import OpenAIProvider
from llm.anthropic import AnthropicProvider
from exceptions import LLMResponseError

_REGISTRY = {"gemini": GeminiProvider, "openai": OpenAIProvider, "anthropic": AnthropicProvider}

def build_provider(provider_name: str, api_key: str, model: str | None = None) -> LLMProvider:
    cls = _REGISTRY.get(provider_name.lower())
    if cls is None:
        raise LLMResponseError(f"unknown LLM provider: {provider_name!r}")
    return cls(api_key=api_key, model=model) if model else cls(api_key=api_key)
```
**RED** — DI test (override the Phase 3 auth dep + key lookup; assert per-request build, no leak):
```python
import pytest
from dependencies import get_llm_provider
from exceptions import LLMAuthError

@pytest.mark.asyncio
async def test_provider_built_from_user_key(fake_user, fake_session_with_key):
    # fake key row: provider="openai", ciphertext decrypts to "sk-test"
    provider = await get_llm_provider(user=fake_user, session=fake_session_with_key)
    assert provider.__class__.__name__ == "OpenAIProvider"
    assert "sk-test" not in repr(provider)

@pytest.mark.asyncio
async def test_no_key_no_fallback_raises(fake_user, fake_session_without_key):
    with pytest.raises(LLMAuthError):
        await get_llm_provider(user=fake_user, session=fake_session_without_key)
```
**GREEN** — extend `dependencies.py` (uses Phase 3's `get_current_user`, the async session provider,
the Fernet `decrypt_key` helper, and a `user_llm_keys` repository query):
```python
from fastapi import Depends
from config import settings
from llm.base import LLMProvider
from llm.factory import build_provider
from exceptions import LLMAuthError
# Phase 3 imports:
from auth.dependencies import get_current_user, User
from database.session import get_session
from auth.crypto import decrypt_key            # Fernet decrypt — in-memory only
from database.repositories import get_user_llm_key

async def get_llm_provider(
    user: "User" = Depends(get_current_user),
    session = Depends(get_session),
) -> LLMProvider:
    row = await get_user_llm_key(session, user_id=user.id)
    if row is not None and row.enabled:
        api_key = decrypt_key(row.ciphertext)            # plaintext: local, brief, never logged
        return build_provider(
            row.provider or settings.DEFAULT_LLM_PROVIDER,
            api_key,
            model=row.model or settings.DEFAULT_LLM_MODEL,
        )
    fallback = settings.LLM_FALLBACK_API_KEY.get_secret_value()
    if fallback:
        return build_provider(settings.DEFAULT_LLM_PROVIDER, fallback,
                              model=settings.DEFAULT_LLM_MODEL)
    raise LLMAuthError("No LLM key on file and no server fallback configured.")
    # NOTE: `api_key` is a local only — never logged, never returned, never on app.state.
```
**Verify:** `uv run pytest test/llm/test_factory.py test/test_get_llm_provider.py -q` green;
`uv run mypy llm dependencies.py` clean.
**Commit:** `feat(llm): build_provider factory + per-request get_llm_provider DI`

### Task 8 — Rewrite `router.py`/`generation.py` to take an injected provider; delete Gemini globals
**Files:** rewrite `components/router.py`, `components/generation.py`; update `test/test_router.py`,
`test/test_generation*.py`; prune the legacy `GoogleAPIError` ladders.

`components/router.py` after rewrite (the prompt-building helper now lives in `llm/`, error mapping is
the adapter's job, and the `has_session_documents` dependency stays as injected by Phase 1/2):
```python
import structlog
from llm.base import LLMProvider, Route

logger = structlog.get_logger(__name__)

async def route_query(
    provider: LLMProvider, query: str, *, has_documents: bool, web_search_allowed: bool
) -> Route:
    decision = await provider.route(query, has_documents=has_documents, web_allowed=web_search_allowed)
    logger.info("router_decision", decision=decision,
                has_documents=has_documents, web_search_allowed=web_search_allowed)
    return decision
```
`components/generation.py` after rewrite:
```python
import structlog
from components.retrieval import format_context
from llm.base import LLMProvider, Route

logger = structlog.get_logger(__name__)

async def generate_final_response(
    provider: LLMProvider, query: str, context: list[str], decision: Route
) -> str:
    answer = await provider.generate(query, format_context(context), decision)
    logger.info("generation_complete", decision=decision, response_chars=len(answer))
    return answer
```
**Delete:** in both modules — `import google.generativeai as genai`, `genai.configure(...)`, the
module-level `gemini_model = GenerativeModel(...)`, and the duplicated `GoogleAPIError`→HTTP ladders
(`router.py:57-88`, `generation.py:51-87`). The neutral errors now bubble straight from the adapter to
`app_exception_handler`. Move the routing/generation prompts and `_normalize_decision` into `llm/`
(done in Tasks 4–6).
- **Tests:** rewrite `test_router.py`/generation tests to pass a fake provider (an object satisfying
  `LLMProvider` with canned returns / a raising `generate`) and assert delegation + that neutral
  errors propagate.
**Verify:**
```bash
grep -rn "genai.configure\|GenerativeModel\|gemini_model" components/ || echo "clean: no LLM globals"
uv run pytest -q
```
**Commit:** `refactor(llm): inject provider into router/generation; remove Gemini process-globals`

### Task 9 — Wire `app.py` `/api/chat`
**Files:** `app.py`; `test/test_chat_provider_di.py`.

**RED** — override `get_llm_provider` with a fake and assert the answer comes from it:
```python
import pytest
from httpx import AsyncClient, ASGITransport
from app import app
from dependencies import get_llm_provider

@pytest.mark.asyncio
async def test_chat_uses_injected_provider(fake_provider, auth_headers):
    app.dependency_overrides[get_llm_provider] = lambda: fake_provider
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/chat", json={"message": "hi"}, headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["answer"] == fake_provider.canned_answer
    finally:
        app.dependency_overrides.clear()
```
**GREEN** — thread the provider through the existing chat flow (auth + session ownership come from
Phase 3; the Pinecone/embedder/web clients from Phase 1):
```python
from fastapi import Depends
from llm.base import LLMProvider
from dependencies import get_llm_provider

@app.post("/api/chat")
async def chat(request: ChatRequest, provider: LLMProvider = Depends(get_llm_provider), ...):
    ...
    base_route = await route_query(
        provider, request.message,
        has_documents=has_docs, web_search_allowed=request.web_search_allowed,
    )
    ...
    answer = await generate_final_response(provider, request.message, context, final_route)
    ...
```
- The `decide_combined_route` / `check_docs_relevant` logic is unchanged; only the calls into
  `route_query`/`generate_final_response` gain the `provider` argument.
**Verify:** `uv run pytest test/test_chat_provider_di.py -q` green;
`grep -rn "genai\." app.py components/ || echo "clean"`.
**Commit:** `feat(app): resolve LLM provider per request and thread it through /api/chat`

### Task 10 — Contract + concurrent-isolation tests; ratchet, mypy, lock
**Files:** confirm `test/llm/test_provider_contract.py`; create
`test/llm/test_concurrent_isolation.py`, `test/llm/test_key_no_leak.py`; `pyproject.toml`/`Jenkinsfile`
(coverage floor); this doc.

1. Confirm the **contract test** runs green identically across all three adapters (Tasks 4–6).
2. **Concurrent multi-user/multi-provider isolation** (roadmap §5 P4 gate):
```python
import asyncio, pytest
from dependencies import get_llm_provider

@pytest.mark.asyncio
async def test_two_users_two_providers_no_crosstalk(make_user_session):
    alice = make_user_session(provider="openai", answer="from-openai")
    bob   = make_user_session(provider="gemini", answer="from-gemini")

    async def ask(u):
        provider = await get_llm_provider(user=u.user, session=u.session)
        return await provider.generate("Q?", "ctx", "DIRECT")

    a, b = await asyncio.gather(ask(alice), ask(bob))
    assert a == "from-openai"      # Alice never sees Gemini output
    assert b == "from-gemini"      # Bob never sees OpenAI output
```
3. **Key-leak guard:** assert the decrypted key appears in no `repr()`, no captured log record
   (`caplog`), and is never set on `app.state`.
4. Ratchet and gate:
```bash
uv run pytest --cov --cov-report=term-missing
# raise --cov-fail-under to floor(new TOTAL) in pyproject.toml + Jenkinsfile (upward only)
uv run mypy .
uv lock
```
5. Update CI to run the contract + isolation suites as part of the P4 gate.
**Verify:** `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
--cov --cov-fail-under=<new>` → all exit 0.
**Commit:** `test(llm): contract + concurrent multi-provider isolation; ratchet coverage gate`

## 6. Exit criteria (checkable)

Restating the roadmap §4 Phase 4 exit clause and the §5 P4 CI-gate row — all must be green in CI:

1. **CI gate met.** Provider-adapter **contract tests** (mocked SDKs) pass identically across
   `GeminiProvider`, `OpenAIProvider`, `AnthropicProvider`; the **concurrent multi-user/multi-provider
   isolation** test passes.
2. **Concurrent BYOK, no cross-talk.** A user with an **OpenAI key** and a user with a **Gemini key**
   run concurrently and each receives output from their own provider only.
3. **No process-global LLM config remains.**
   `grep -rn "genai.configure\|GenerativeModel\|gemini_model" components/ app.py` is empty.
4. **Provider selected per user/request** via `get_llm_provider`; the decrypted key lives in memory
   only and appears in no log, no `repr()`, and not on `app.state`.
5. **BYOK is live.** A user's encrypted `user_llm_keys` row drives their provider calls end-to-end
   (this is the capability the whole roadmap was built toward — roadmap §7).
6. **Behavior preserved.** Same `/api/chat` route, same RAG/WEB/DIRECT vocabulary and combined-route
   logic, same response shape; only the LLM client construction and error mapping changed.
7. `uv run mypy .` clean (Protocol conformance verified); `uv lock` committed; coverage floor
   **raised** (ratchet recorded in `pyproject.toml` + `Jenkinsfile`); only new dep is `anthropic`.

## Appendix A — Provider capability matrix

| Capability | Gemini (`google-generativeai`) | OpenAI (`openai`) | Anthropic (`anthropic`) |
|---|---|---|---|
| Client construction | `genai.Client(api_key=...)` — **instance-scoped**, not `genai.configure()` | `AsyncOpenAI(api_key=...)` | `AsyncAnthropic(api_key=...)` |
| Native async | No (sync SDK → wrap in `anyio.to_thread.run_sync`) | Yes | Yes |
| `route()` call | `client.models.generate_content(...)` → `.text` | `chat.completions.create(...)` → `.choices[0].message.content` | `messages.create(...)` → `.content[0].text` |
| `generate()` call | same as route | same as route | same as route |
| `stream()` call | `generate_content_stream(...)`, chunk `.text` | `create(..., stream=True)`, `event.choices[0].delta.content` | `messages.stream(...)` → `stream.text_stream` |
| Default model | `gemini-2.5-flash` | `gpt-4o-mini` | `claude-3-5-haiku-latest` |
| Auth failure | 403 `PermissionDenied` / `Unauthenticated` | `AuthenticationError` / `PermissionDeniedError` | `AuthenticationError` / `PermissionDeniedError` |
| Rate limit | 429 `ResourceExhausted` | `RateLimitError` | `RateLimitError` |
| Unavailable | 503 `ServiceUnavailable` / `DeadlineExceeded` | `APIStatusError` 500/502/503 | `APIStatusError` 500/503 / **529 overloaded** |

## Appendix B — Error-taxonomy mapping (SDK exception → neutral)

| Neutral (`exceptions.py`) | HTTP | Gemini (`google.api_core.exceptions`) | OpenAI (`openai`) | Anthropic (`anthropic`) |
|---|---|---|---|---|
| `LLMAuthError` | 401 | `PermissionDenied`, `Unauthenticated` | `AuthenticationError`, `PermissionDeniedError` | `AuthenticationError`, `PermissionDeniedError` |
| `LLMRateLimitError` | 429 | `ResourceExhausted` | `RateLimitError` | `RateLimitError` |
| `LLMUnavailableError` | 503 | `ServiceUnavailable`, `DeadlineExceeded` | `APIStatusError` 500/502/503 | `APIStatusError` 500/503/**529** |
| `LLMResponseError` | 502 | any other `Exception` (catch-all) | any other `Exception` (catch-all) | any other `Exception` (catch-all) |

> Every adapter's `_map_error` ends with a catch-all → `LLMResponseError`, so no raw SDK exception
> escapes the `llm/` package. All four subclass `AppException`, so the single existing
> `app_exception_handler` renders them — no new handler wiring.

## Appendix C — Signature-change map

| Symbol | Before (current source) | After (Phase 4) |
|---|---|---|
| `components/router.py` module top | `genai.configure(api_key=settings.GOOGLE_API_KEY)` + `gemini_model = GenerativeModel("gemini-2.5-flash", ...)` | **deleted** (no globals) |
| `route_query` | `async (query, session_id, web_search_allowed)` — uses global `gemini_model`, `GoogleAPIError`→HTTP ladder | `async (provider, query, *, has_documents, web_search_allowed)` — delegates to `provider.route` |
| `components/generation.py` module top | own `genai.configure(...)` + `gemini_model = GenerativeModel(...)` | **deleted** |
| `generate_final_response` | `async (query, context, decision)` — global model, `GoogleAPIError` ladder | `async (provider, query, context, decision)` — delegates to `provider.generate` |
| `_build_routing_prompt` / `_normalize_decision` / `_generate_*_response` | private helpers in `router.py`/`generation.py` | moved into `llm/` (prompts preserved verbatim) |
| `app.py` `/api/chat` | `route_query(message, session_id, web_allowed)` + `generate_final_response(message, context, route)` | `provider = Depends(get_llm_provider)`; both calls take `provider` first |
| `exceptions.py` | `AppException` + `app_exception_handler` only | + `LLMError`/`LLMAuthError`/`LLMRateLimitError`/`LLMUnavailableError`/`LLMResponseError` (all `AppException` subclasses) |
| `config.py` | `GOOGLE_API_KEY`, Pinecone/S3/HF vars | + `DEFAULT_LLM_PROVIDER`, `DEFAULT_LLM_MODEL`, `LLM_FALLBACK_API_KEY` |
| `dependencies.py` | Phase 1–3 client/auth providers | + `get_llm_provider` (auth → `user_llm_keys` row → Fernet decrypt → `build_provider`) |
| `llm/` package | does not exist | `base.py`, `gemini.py`, `openai.py`, `anthropic.py`, `factory.py` |
