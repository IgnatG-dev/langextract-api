"""
Provider singletons, configuration, and session management.

Centralises all external configuration (env-vars / .env) and
provides FastAPI dependency-injection helpers for Redis and
Celery connectivity.
"""

import logging
from functools import lru_cache
from typing import Generator

import redis
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


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

    # Provider defaults (overridable per-request)
    DEFAULT_PROVIDER: str = "gemini-1.5-pro"

    # Task defaults
    TASK_TIME_LIMIT: int = 3600  # seconds
    TASK_SOFT_TIME_LIMIT: int = 3300  # seconds
    RESULT_EXPIRES: int = 86400  # seconds

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors(cls, v: str | list[str]) -> list[str]:
        """Accept a JSON-encoded string or a list."""
        if isinstance(v, str):
            import json

            return json.loads(v)
        return v

    # Derived URLs
    @property
    def REDIS_URL(self) -> str:
        """Full Redis connection URL."""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def CELERY_BROKER_URL(self) -> str:
        """Celery broker URL (backed by Redis)."""
        return self.REDIS_URL

    @property
    def CELERY_RESULT_BACKEND(self) -> str:
        """Celery result backend URL (backed by Redis)."""
        return self.REDIS_URL


@lru_cache
def get_settings() -> Settings:
    """
    Return a cached Settings singleton.

    Using ``lru_cache`` ensures the .env file is read exactly once.
    Override in tests via ``app.dependency_overrides``.
    """
    return Settings()


# ── Redis client ────────────────────────────────────────────────────────────


def get_redis() -> Generator[redis.Redis, None, None]:
    """
    Yield a Redis client for the duration of a request.

    Usage as a FastAPI dependency::

        @router.get("/ping")
        def ping(r: redis.Redis = Depends(get_redis)):
            return r.ping()
    """
    settings = get_settings()
    pool = redis.ConnectionPool.from_url(
        settings.REDIS_URL,
        decode_responses=True,
    )
    client = redis.Redis(connection_pool=pool)
    try:
        yield client
    finally:
        client.close()
