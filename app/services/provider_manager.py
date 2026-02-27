"""
Singleton provider manager for LLM model instance caching.

Caches ``LiteLLMLanguageModel`` instances by ``(model_id, api_key)``
so that the same model is reused across batches and jobs.  This
eliminates the overhead of re-creating model objects for every
``lx.extract()`` call.

Optionally configures ``litellm.cache`` with Redis for LLM
response caching, providing near-zero-cost re-runs of identical
documents.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any

import litellm
from langcore import factory
from langcore.core.base_model import BaseLanguageModel

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class ProviderManager:
    """Thread-safe singleton that caches language model instances.

    Models are keyed by ``(model_id, api_key)`` so that distinct
    credentials produce distinct instances.  The cache lives for the
    lifetime of the process (Celery worker).

    Usage::

        manager = ProviderManager.instance()
        model = manager.get_or_create_model("gpt-4o", api_key="sk-...")
    """

    _instance: ProviderManager | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._models: dict[str, BaseLanguageModel] = {}
        self._model_lock = threading.Lock()
        self._cache_initialized = False

    # ── Singleton accessor ──────────────────────────────────

    @classmethod
    def instance(cls) -> ProviderManager:
        """Return the global ``ProviderManager`` singleton."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── LiteLLM Redis cache ─────────────────────────────────

    def ensure_cache(self) -> None:
        """Initialise ``litellm.cache`` with Redis if not already done.

        Reads ``REDIS_HOST``, ``REDIS_PORT``, ``REDIS_DB`` from
        settings and ``EXTRACTION_CACHE_TTL`` from the environment
        (default 24 h).

        The cache is automatically keyed by the full completion
        request parameters so any change in prompt, model, or
        temperature produces a new cache key.
        """
        if self._cache_initialized:
            return

        settings = get_settings()
        cache_ttl = settings.EXTRACTION_CACHE_TTL
        cache_enabled = settings.EXTRACTION_CACHE_ENABLED

        if not cache_enabled:
            logger.info("LiteLLM response cache disabled via env")
            self._cache_initialized = True
            return

        try:
            litellm.cache = litellm.Cache(
                type="redis",
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                ttl=cache_ttl,
                namespace="lx-cache",
            )
            self._cache_initialized = True
            logger.info(
                "LiteLLM Redis cache enabled (host=%s, ttl=%ds)",
                settings.REDIS_HOST,
                cache_ttl,
            )
        except Exception:
            logger.warning(
                "Failed to initialise LiteLLM Redis cache — continuing without caching",
                exc_info=True,
            )
            self._cache_initialized = True

    # ── Model caching ───────────────────────────────────────

    @staticmethod
    def _cache_key(
        model_id: str,
        api_key: str | None,
        fence_output: bool | None = None,
        use_schema_constraints: bool = True,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """Deterministic cache key for a model configuration.

        Includes ``fence_output``, ``use_schema_constraints``, and
        a hash of the full ``response_format`` content so that the
        same ``(model_id, api_key)`` pair with different structured-
        output schemas (e.g. different extraction groups) produces
        distinct cache entries.
        """
        rf_hash = ""
        if response_format is not None:
            rf_hash = hashlib.sha256(
                json.dumps(response_format, sort_keys=True).encode()
            ).hexdigest()[:16]
        raw = (
            f"{model_id}:{api_key or ''}:{fence_output}"
            f":{use_schema_constraints}:{rf_hash}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def get_or_create_model(
        self,
        model_id: str,
        api_key: str | None = None,
        *,
        fence_output: bool | None = None,
        use_schema_constraints: bool = True,
        examples: Any = None,
        response_format: dict[str, Any] | None = None,
        **extra_kwargs: Any,
    ) -> BaseLanguageModel:
        """Return a cached model or create and cache a new one.

        Args:
            model_id: LLM model identifier.
            api_key: Optional API key.
            fence_output: Whether to force fenced output.
            use_schema_constraints: Whether to apply schema constraints.
            examples: Example data for schema generation.
            response_format: Optional ``response_format`` dict
                to include in calls to ``litellm.completion()``.
                Stored as a provider kwarg on the model.
            **extra_kwargs: Forwarded to ``factory.create_model``.

        Returns:
            A ``BaseLanguageModel`` instance, potentially reused from
            the cache.
        """
        ck = self._cache_key(
            model_id,
            api_key,
            fence_output=fence_output,
            use_schema_constraints=use_schema_constraints,
            response_format=response_format,
        )

        with self._model_lock:
            if ck in self._models:
                logger.debug("Reusing cached model for %s", model_id)
                return self._models[ck]

        provider_kwargs: dict[str, Any] = {}
        if api_key:
            provider_kwargs["api_key"] = api_key
        if response_format is not None:
            provider_kwargs["response_format"] = response_format

        # Propagate concurrency limit to the LiteLLM provider's
        # asyncio.Semaphore so burst request volume is capped.
        max_concurrent = get_settings().LITELLM_MAX_CONCURRENT_REQUESTS
        if max_concurrent and max_concurrent > 0:
            provider_kwargs["max_workers"] = max_concurrent

        provider_kwargs.update(extra_kwargs)

        config = factory.ModelConfig(
            model_id=model_id,
            provider_kwargs={k: v for k, v in provider_kwargs.items() if v is not None},
        )

        model = factory.create_model(
            config=config,
            examples=examples,
            use_schema_constraints=use_schema_constraints,
            fence_output=fence_output,
        )

        with self._model_lock:
            self._models.setdefault(ck, model)

        logger.info(
            "Created and cached model for %s (cache size=%d)",
            model_id,
            len(self._models),
        )
        return self._models[ck]

    def clear(self) -> None:
        """Drop all cached models (useful in tests)."""
        with self._model_lock:
            self._models.clear()

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (useful in tests)."""
        with cls._lock:
            cls._instance = None
