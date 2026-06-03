"""Central application configuration: one pydantic-settings Settings object.

Reads env (and a local .env), fails fast if a required secret is missing. Module-level `settings`
singleton; Phase 1 moves this behind dependency injection.
"""

from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )
    # Required (startup fails fast if absent)
    GOOGLE_API_KEY: str
    PINECONE_API_KEY: str
    HUGGINGFACE_TOKEN: str
    AWS_REGION: str
    S3_BUCKET_NAME: str
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str
    DATABASE_URL: str  # e.g. postgresql+asyncpg://user:pass@host/db (or postgresql:// — transformed at engine build)

    # Optional
    UPLOADTHING_API_KEY: str | None = None
    PINECONE_INDEX_NAME: str = "rag-knowledge-base"
    LOG_JSON: bool = Field(default=False)
    ENVIRONMENT: Literal["development", "production"] = "development"
    S3_ENDPOINT_URL: str | None = None  # set for MinIO/dev; None → real AWS S3

    # --- Auth (Phase 3) ---
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MINUTES: int = 15
    REFRESH_TOKEN_TTL_DAYS: int = 7

    # --- BYOK key encryption (Phase 3) ---
    LLM_KEY_ENCRYPTION_KEY: str  # url-safe base64, 32 bytes — Fernet master key

    # --- CORS (Phase 3) ---
    CORS_ALLOWED_ORIGINS: list[str] = []

    # --- LLM provider (Phase 4) ---
    DEFAULT_LLM_PROVIDER: Literal["gemini", "openai", "anthropic"] = "gemini"
    DEFAULT_LLM_MODEL: str = "gemini-2.5-flash"
    LLM_FALLBACK_API_KEY: SecretStr = SecretStr("")  # optional server-side fallback; BYOK preferred

    # --- Phase 5: Redis / Celery / rate limiting ---
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str | None = None  # falls back to REDIS_URL (see celery_broker_url)
    RATE_LIMIT_STORAGE_URI: str | None = None  # falls back to REDIS_URL; tests set "memory://"
    RATE_LIMIT_CHAT: str = "30/minute"
    RATE_LIMIT_UPLOAD: str = "10/minute"
    RATE_LIMIT_DEFAULT: str = "120/minute"

    # --- Phase 6: agentic graph, conversation memory, freemium ladder ---
    HISTORY_MAX_TURNS: int = 6  # last-N turns (verbatim) fed to supervisor + synthesis
    # Free-tier guards (operator's shared Google key): per-user fairness AND a global ceiling.
    FREE_TIER_DAILY_USER_QUERIES: int = 10
    FREE_TIER_GLOBAL_DAILY_CALLS: int = 1200
    FREE_TIER_MODEL: str = "gemini-2.5-flash"
    # BYOK cheap/strong model tiers (route -> cheap classifier, synth -> strong writer).
    TIER_ROUTE_MODEL_GEMINI: str = "gemini-2.5-flash"
    TIER_SYNTH_MODEL_GEMINI: str = "gemini-2.5-pro"
    TIER_ROUTE_MODEL_OPENAI: str = "gpt-4o-mini"
    TIER_SYNTH_MODEL_OPENAI: str = "gpt-4o"
    TIER_ROUTE_MODEL_ANTHROPIC: str = "claude-3-5-haiku-latest"
    TIER_SYNTH_MODEL_ANTHROPIC: str = "claude-3-5-sonnet-latest"

    # --- Phase 7: Observability (OpenTelemetry + Langfuse) ---
    OTEL_ENABLED: bool = False
    OTEL_SERVICE_NAME: str = "agentic-rag"
    OTEL_EXPORTER_ENDPOINT: str | None = None  # OTLP gRPC endpoint; None → console exporter
    OTEL_SAMPLE_RATIO: float = 1.0
    LANGFUSE_ENABLED: bool = False
    LANGFUSE_PUBLIC_KEY: SecretStr | None = None
    LANGFUSE_SECRET_KEY: SecretStr | None = None
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # --- Phase 7: 3-layer memory ---
    MEMORY_MARKDOWN_MAX_CHARS: int = 8_000
    GRAPH_STORAGE: Literal["postgres", "s3"] = "postgres"
    # None → auto: entity extraction is ON iff an operator fallback (Gemini) key is configured.
    ENTITY_EXTRACTION_ENABLED: bool | None = None
    # Ordered fallback chain (operator Gemini key); tried in order on transient/quota failure.
    ENTITY_EXTRACTION_MODELS: list[str] = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ]
    HYBRID_WEIGHTS_VECTOR: float = 0.6
    HYBRID_WEIGHTS_GRAPH: float = 0.25
    HYBRID_WEIGHTS_MARKDOWN: float = 0.15

    @property
    def celery_broker_url(self) -> str:
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def rate_limit_storage_uri(self) -> str:
        return self.RATE_LIMIT_STORAGE_URI or self.REDIS_URL

    @property
    def entity_extraction_active(self) -> bool:
        """Whether the ingestion task runs the entity-extraction pass (Phase 7).

        Explicit ``ENTITY_EXTRACTION_ENABLED`` wins; otherwise auto-on iff an operator fallback
        (Gemini) key is configured — a keyless deploy silently skips extraction (no LLM cost).
        """
        if self.ENTITY_EXTRACTION_ENABLED is not None:
            return self.ENTITY_EXTRACTION_ENABLED
        return bool(self.LLM_FALLBACK_API_KEY.get_secret_value())

    def tier_route_model(self, provider: str) -> str:
        """BYOK cheap-tier model id for routing, by provider (falls back to DEFAULT_LLM_MODEL)."""
        return {
            "gemini": self.TIER_ROUTE_MODEL_GEMINI,
            "openai": self.TIER_ROUTE_MODEL_OPENAI,
            "anthropic": self.TIER_ROUTE_MODEL_ANTHROPIC,
        }.get(provider.lower(), self.DEFAULT_LLM_MODEL)

    def tier_synth_model(self, provider: str) -> str:
        """BYOK strong-tier model id for synthesis, by provider (falls back to DEFAULT_LLM_MODEL)."""
        return {
            "gemini": self.TIER_SYNTH_MODEL_GEMINI,
            "openai": self.TIER_SYNTH_MODEL_OPENAI,
            "anthropic": self.TIER_SYNTH_MODEL_ANTHROPIC,
        }.get(provider.lower(), self.DEFAULT_LLM_MODEL)

    @field_validator("LLM_KEY_ENCRYPTION_KEY")
    @classmethod
    def _validate_fernet_key(cls, v: str) -> str:
        Fernet(v.encode())  # raises ValueError if not a valid 32-byte url-safe base64 key
        return v


settings = Settings()  # raises ValidationError on missing required vars
