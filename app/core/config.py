"""
Application configuration and settings.

Centralises all external configuration (env-vars / .env) and
version discovery.  Redis connectivity lives in
``app.core.redis`` and FastAPI dependency injection in
``app.api.deps``.
"""

from __future__ import annotations

import importlib.metadata
import logging
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


# ── Package version (single source of truth from pyproject.toml) ────────


def get_version() -> str:
    """Return the installed package version.

    Falls back to ``"0.0.0-dev"`` when the package metadata
    is not available (e.g. during editable / source installs).

    Returns:
        Semantic version string.
    """
    try:
        return importlib.metadata.version("langcore-api")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0-dev"


# ── Settings ────────────────────────────────────────────────────────────────


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    # General
    APP_NAME: str = "LangCore API"
    API_V1_STR: str = "/api/v1"
    ROOT_PATH: str = ""
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # CORS
    CORS_ORIGINS: list[str] = ["*"]

    # Redis / Celery
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0

    # API Keys (populated via .env)
    OPENAI_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    MISTRAL_API_KEY: str = ""
    LANGCORE_API_KEY: str = ""

    # Provider / model defaults (overridable per-request)
    DEFAULT_PROVIDER: str = "gpt-4o"

    # LangCore extraction defaults
    DEFAULT_MAX_WORKERS: int = 10
    DEFAULT_MAX_CHAR_BUFFER: int = 1000

    # Task defaults
    TASK_TIME_LIMIT: int = 3600  # seconds
    TASK_SOFT_TIME_LIMIT: int = 3300  # seconds
    RESULT_EXPIRES: int = 86400  # seconds

    # ── Security / SSRF ─────────────────────────────────────────────
    ALLOWED_URL_DOMAINS: list[str] = []
    WEBHOOK_SECRET: str = ""
    DOC_DOWNLOAD_TIMEOUT: int = 30  # seconds
    DOC_DOWNLOAD_MAX_BYTES: int = 50_000_000  # 50 MB

    # ── Batch concurrency ───────────────────────────────────────────
    BATCH_CONCURRENCY: int = 4

    # ── Extraction-result cache ─────────────────────────────────────
    EXTRACTION_CACHE_ENABLED: bool = True
    EXTRACTION_CACHE_TTL: int = 86400  # seconds (24 h)
    EXTRACTION_CACHE_BACKEND: str = "redis"  # redis | disk | none

    # ── Audit logging ───────────────────────────────────────────────
    AUDIT_ENABLED: bool = True
    AUDIT_SINK: str = "logging"  # logging | jsonfile | otel
    AUDIT_LOG_PATH: str = "audit.jsonl"
    AUDIT_SAMPLE_LENGTH: int | None = None

    # ── Guardrails (output validation) ──────────────────────────────
    GUARDRAILS_ENABLED: bool = True
    GUARDRAILS_MAX_RETRIES: int = 3
    GUARDRAILS_MAX_CONCURRENCY: int | None = None
    GUARDRAILS_INCLUDE_OUTPUT_IN_CORRECTION: bool = True
    GUARDRAILS_MAX_CORRECTION_PROMPT_LENGTH: int | None = None
    GUARDRAILS_MAX_CORRECTION_OUTPUT_LENGTH: int | None = None

    # ── Hybrid rule-based extraction (langcore-hybrid) ──────────────
    HYBRID_ENABLED: bool = False
    HYBRID_MIN_CONFIDENCE: float = 0.8

    # ── DSPy prompt optimization ────────────────────────────────────
    DSPY_ENABLED: bool = False
    DSPY_MODEL_ID: str = "gemini/gemini-2.5-flash"
    DSPY_OPTIMIZER: str = "miprov2"  # miprov2 | gepa
    DSPY_NUM_CANDIDATES: int = 7
    DSPY_MAX_BOOTSTRAPPED_DEMOS: int = 3
    DSPY_MAX_LABELED_DEMOS: int = 4
    DSPY_NUM_THREADS: int = 4
    DSPY_CONFIG_DIR: str = ".dspy_configs"

    # ── RAG query parsing ───────────────────────────────────────────
    RAG_ENABLED: bool = False
    RAG_MODEL_ID: str = "gpt-4o"
    RAG_TEMPERATURE: float = 0.0
    RAG_MAX_TOKENS: int = 1024
    RAG_MAX_RETRIES: int = 2

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors(cls, v: str | list[str]) -> list[str]:
        """Accept a JSON-encoded string or a list."""
        if isinstance(v, str):
            import json

            return json.loads(v)
        return v

    @field_validator("ALLOWED_URL_DOMAINS", mode="before")
    @classmethod
    def _parse_allowed_domains(
        cls,
        v: str | list[str],
    ) -> list[str]:
        """Accept comma-separated string or a list."""
        if isinstance(v, str):
            if not v.strip():
                return []
            return [d.strip() for d in v.split(",") if d.strip()]
        return v

    # Derived URLs
    @property
    def REDIS_URL(self) -> str:  # noqa: N802
        """Full Redis connection URL."""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def CELERY_BROKER_URL(self) -> str:  # noqa: N802
        """Celery broker URL (backed by Redis)."""
        return self.REDIS_URL

    @property
    def CELERY_RESULT_BACKEND(self) -> str:  # noqa: N802
        """Celery result backend URL (backed by Redis)."""
        return self.REDIS_URL


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings singleton.

    Using ``lru_cache`` ensures the .env file is read exactly
    once.  Override in tests via ``app.dependency_overrides``.
    """
    return Settings()
