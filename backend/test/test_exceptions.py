from exceptions import (
    AppException,
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMUnavailableError,
)


def test_llm_taxonomy_carries_http_status():
    assert issubclass(LLMAuthError, LLMError)
    assert issubclass(LLMError, AppException)
    assert LLMAuthError().status_code == 401
    assert LLMRateLimitError().status_code == 429
    assert LLMUnavailableError().status_code == 503
    assert LLMResponseError().status_code == 502


def test_llm_error_default_details():
    assert "AI provider" in LLMAuthError().detail
    assert "rate limit" in LLMRateLimitError().detail.lower()
    assert "unavailable" in LLMUnavailableError().detail.lower()


def test_llm_error_custom_detail():
    err = LLMAuthError("bad key")
    assert err.detail == "bad key"
    assert err.status_code == 401


def test_llm_error_is_app_exception():
    # Ensures the single app_exception_handler renders all LLM errors
    assert isinstance(LLMAuthError(), AppException)
    assert isinstance(LLMRateLimitError(), AppException)
    assert isinstance(LLMUnavailableError(), AppException)
    assert isinstance(LLMResponseError(), AppException)
