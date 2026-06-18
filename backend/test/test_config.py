import importlib

import pytest
from cryptography.fernet import Fernet

_FERNET_KEY = Fernet.generate_key().decode()

REQUIRED = {
    "GOOGLE_API_KEY": "g",
    "PINECONE_API_KEY": "p",
    "HUGGINGFACE_TOKEN": "h",
    "AWS_REGION": "us-east-1",
    "S3_BUCKET_NAME": "b",
    "AWS_ACCESS_KEY_ID": "ak",
    "AWS_SECRET_ACCESS_KEY": "sk",
    "DATABASE_URL": "postgresql+asyncpg://rag:rag@localhost:5432/rag",
    # Phase 3 required
    "JWT_SECRET": "test-jwt-secret",
    "LLM_KEY_ENCRYPTION_KEY": _FERNET_KEY,
}


def _fresh(monkeypatch, env):
    for k in list(REQUIRED) + [
        "PINECONE_INDEX_NAME",
        "LOG_JSON",
        "ENVIRONMENT",
        "S3_ENDPOINT_URL",
        # Phase 3 optional fields
        "JWT_ALGORITHM",
        "ACCESS_TOKEN_TTL_MINUTES",
        "REFRESH_TOKEN_TTL_DAYS",
        "CORS_ALLOWED_ORIGINS",
        # Phase 4 optional fields
        "DEFAULT_LLM_PROVIDER",
        "DEFAULT_LLM_MODEL",
        "LLM_FALLBACK_API_KEY",
        # Phase 5 optional fields
        "REDIS_URL",
        "CELERY_BROKER_URL",
        "RATE_LIMIT_STORAGE_URI",
        "RATE_LIMIT_CHAT",
        "RATE_LIMIT_UPLOAD",
        "RATE_LIMIT_DEFAULT",
    ]:
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import config

    importlib.reload(config)
    return config


def test_loads_required(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.GOOGLE_API_KEY == "g"


def test_index_name_default(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.PINECONE_INDEX_NAME == "rag-knowledge-base"


def test_optionals_default(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.LOG_JSON is False


def test_missing_required_raises(monkeypatch):
    bad = dict(REQUIRED)
    del bad["GOOGLE_API_KEY"]
    with pytest.raises(Exception):
        _fresh(monkeypatch, bad)


def test_environment_default(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.ENVIRONMENT == "development"


def test_s3_endpoint_optional(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.S3_ENDPOINT_URL is None


def test_database_url_required(monkeypatch, tmp_path):
    # Use a temp dir without a .env file so pydantic-settings can't fall back to the real one
    monkeypatch.chdir(tmp_path)
    bad = {k: v for k, v in REQUIRED.items() if k != "DATABASE_URL"}
    with pytest.raises(Exception):
        _fresh(monkeypatch, bad)


def test_database_url_loaded(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.DATABASE_URL == "postgresql+asyncpg://rag:rag@localhost:5432/rag"


# ── Phase 3 Settings tests ────────────────────────────────────────────────────


def test_jwt_secret_required(monkeypatch):
    bad = {k: v for k, v in REQUIRED.items() if k != "JWT_SECRET"}
    with pytest.raises(Exception):
        _fresh(monkeypatch, bad)


def test_llm_key_encryption_key_required(monkeypatch):
    bad = {k: v for k, v in REQUIRED.items() if k != "LLM_KEY_ENCRYPTION_KEY"}
    with pytest.raises(Exception):
        _fresh(monkeypatch, bad)


def test_invalid_fernet_key_raises_validation_error(monkeypatch):
    bad = dict(REQUIRED)
    bad["LLM_KEY_ENCRYPTION_KEY"] = "not-a-valid-fernet-key"
    with pytest.raises(Exception):
        _fresh(monkeypatch, bad)


def test_jwt_algorithm_default(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.JWT_ALGORITHM == "HS256"


def test_access_token_ttl_default(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.ACCESS_TOKEN_TTL_MINUTES == 15


def test_refresh_token_ttl_default(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.REFRESH_TOKEN_TTL_DAYS == 7


def test_cors_allowed_origins_default_empty(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.CORS_ALLOWED_ORIGINS == []


def test_cors_allowed_origins_parsed_from_json(monkeypatch):
    env = dict(REQUIRED)
    env["CORS_ALLOWED_ORIGINS"] = '["http://localhost:3000", "https://app.example.com"]'
    c = _fresh(monkeypatch, env)
    assert "http://localhost:3000" in c.settings.CORS_ALLOWED_ORIGINS
    assert "https://app.example.com" in c.settings.CORS_ALLOWED_ORIGINS


def test_valid_fernet_key_passes_validation(monkeypatch):
    env = dict(REQUIRED)
    env["LLM_KEY_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    c = _fresh(monkeypatch, env)
    assert c.settings.LLM_KEY_ENCRYPTION_KEY is not None


# ── Phase 4 Settings tests ────────────────────────────────────────────────────


def test_default_provider_settings(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.DEFAULT_LLM_PROVIDER == "gemini"
    assert c.settings.DEFAULT_LLM_MODEL  # non-empty
    assert c.settings.LLM_FALLBACK_API_KEY.get_secret_value() == ""  # optional, empty default


def test_default_llm_provider_can_be_overridden(monkeypatch):
    env = dict(REQUIRED)
    env["DEFAULT_LLM_PROVIDER"] = "openai"
    env["DEFAULT_LLM_MODEL"] = "gpt-4o-mini"
    c = _fresh(monkeypatch, env)
    assert c.settings.DEFAULT_LLM_PROVIDER == "openai"
    assert c.settings.DEFAULT_LLM_MODEL == "gpt-4o-mini"


def test_llm_fallback_key_set(monkeypatch):
    env = dict(REQUIRED)
    env["LLM_FALLBACK_API_KEY"] = "sk-server-key"
    c = _fresh(monkeypatch, env)
    assert c.settings.LLM_FALLBACK_API_KEY.get_secret_value() == "sk-server-key"


# ── Phase 5 Settings tests ────────────────────────────────────────────────────


def test_redis_url_default(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.REDIS_URL == "redis://localhost:6379/0"


def test_rate_limit_defaults(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.RATE_LIMIT_CHAT == "30/minute"
    assert c.settings.RATE_LIMIT_UPLOAD == "10/minute"
    assert c.settings.RATE_LIMIT_DEFAULT == "120/minute"


def test_celery_broker_falls_back_to_redis(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.celery_broker_url == c.settings.REDIS_URL


def test_celery_broker_explicit_overrides(monkeypatch):
    env = dict(REQUIRED)
    env["CELERY_BROKER_URL"] = "redis://broker:6379/1"
    c = _fresh(monkeypatch, env)
    assert c.settings.celery_broker_url == "redis://broker:6379/1"


def test_rate_limit_storage_falls_back_to_redis(monkeypatch):
    c = _fresh(monkeypatch, REQUIRED)
    assert c.settings.rate_limit_storage_uri == c.settings.REDIS_URL
