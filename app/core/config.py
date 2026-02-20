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
        return importlib.metadata.version("langextract-api")
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
    APP_NAME: str = "LangExtract API"
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
    LANGEXTRACT_API_KEY: str = ""

    # Provider / model defaults (overridable per-request)
    DEFAULT_PROVIDER: str = "gpt-4o"

    # LangExtract extraction defaults
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
