from fastapi import Request
from fastapi.responses import JSONResponse


class AppException(Exception):
    def __init__(self, status_code: int, detail: str, code: str | None = None):
        self.status_code = status_code
        self.detail = detail
        self.code = code  # optional machine-readable code (e.g. "free_tier_exhausted")


async def app_exception_handler(request: Request, exc: AppException):
    content: dict[str, str] = {"detail": exc.detail}
    code = getattr(exc, "code", None)
    if code:
        content["code"] = code
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
    )


class InvalidTokenTypeError(Exception):
    """Raised when a JWT token has the wrong `type` claim."""

    def __init__(self, expected: str, got: str | None):
        self.expected = expected
        self.got = got
        super().__init__(f"Expected token type '{expected}', got '{got}'")


# ── Phase 4: Provider-neutral LLM error taxonomy ─────────────────────────────


class LLMError(AppException):
    """Base for all provider-neutral LLM failures."""

    status_code = 502
    default_detail = "The AI provider returned an error. Please try again."

    def __init__(self, detail: str | None = None) -> None:
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


# ── Phase 6: freemium ladder ─────────────────────────────────────────────────


class KeyDecryptionError(AppException):
    """A stored BYOK key can't be decrypted — the master key (LLM_KEY_ENCRYPTION_KEY) was rotated
    or the ciphertext is corrupt. Actionable: re-enter the key (it re-encrypts under the current
    master key). Surfaced instead of a bare 500 so the client can prompt the user.
    """

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(
            status_code=400,
            detail=detail or "Your stored API key could not be decrypted. Please re-enter it.",
            code="key_decryption_failed",
        )


class FreeTierExhaustedError(AppException):
    """Raised when a keyless user has used up the free allowance (per-user or global guard).

    Carries a stable ``code`` so the frontend can show the BYOK call-to-action rather than a
    generic 429/402. See docs/09_Phase6_Agentic_Architecture.md §3.
    """

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(
            status_code=402,
            detail=detail or "Free tier exhausted — add your own API key to continue.",
            code="free_tier_exhausted",
        )
