"""
Extraction orchestrator — coordinates download, LLM, and formatting.

Delegates provider resolution to ``app.services.providers`` and
data conversion to ``app.services.converters``.  All LangCore
interaction is isolated here so it can be unit-tested without
Celery and reused from both the single-document and batch task
flows.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import langcore as lx
from langcore.core.base_model import BaseLanguageModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_none,
)

from app.core.config import get_settings
from app.core.constants import STATUS_COMPLETED
from app.core.defaults import (
    DEFAULT_EXAMPLES,
    DEFAULT_PROMPT_DESCRIPTION,
)
from app.core.metrics import record_cache_hit, record_cache_miss
from app.core.security import validate_url
from app.schemas.enums import TaskState
from app.services.consensus_model import ConsensusLanguageModel
from app.services.converters import (
    build_examples,
    convert_extractions,
    extract_token_usage,
)
from app.services.downloader import download_document
from app.services.extraction_cache import (
    ExtractionCache,
    build_cache_key,
)
from app.services.model_wrappers import apply_model_wrappers
from app.services.provider_manager import ProviderManager
from app.services.providers import is_openai_model, resolve_api_key
from app.core.url_utils import mask_url as _mask_url
from app.services.structured_output import (
    build_response_format,
    supports_structured_output,
)

logger = logging.getLogger(__name__)


# Maximum immediate retries when the LLM returns malformed
# extraction_text (e.g. a dict instead of a string).  Each retry
# re-invokes the LLM, which is non-deterministic and usually
# self-corrects on the next attempt.
MAX_LLM_RETRIES: int = 2


def _build_model(
    provider: str,
    extraction_config: dict[str, Any],
    manager: ProviderManager,
    examples: Any = None,
    response_format: dict[str, Any] | None = None,
) -> tuple[BaseLanguageModel, str]:
    """Build a language model, optionally wrapped for consensus.

    When ``extraction_config`` contains a ``consensus_providers``
    list, a :class:`ConsensusLanguageModel` is returned that
    dispatches to all specified providers.  Otherwise a single
    cached model is returned.

    Args:
        provider: Primary LLM model ID.
        extraction_config: Job-level overrides — may include
            ``consensus_providers`` (list of model ID strings).
        manager: The global ``ProviderManager`` singleton.
        examples: Example data for schema generation.
        response_format: Optional ``response_format`` dict to
            pass through to the LiteLLM provider.  When set,
            ``fence_output`` is forced to ``False`` because the
            LLM returns raw JSON (no code fences).

    Returns:
        A ``(model, label)`` tuple where *label* is a
        human-readable description for log messages.
    """
    consensus_providers: list[str] | None = extraction_config.get("consensus_providers")

    if consensus_providers and len(consensus_providers) >= 2:
        models: list[BaseLanguageModel] = []
        for model_id in consensus_providers:
            api_key = resolve_api_key(model_id)
            extra: dict[str, Any] = {}
            if is_openai_model(model_id):
                extra["fence_output"] = True
            # When response_format is active, override fence_output
            # because the LLM returns raw JSON instead of fenced
            # code blocks.
            if response_format is not None:
                extra["fence_output"] = False
            models.append(
                manager.get_or_create_model(
                    model_id=model_id,
                    api_key=api_key,
                    fence_output=extra.get("fence_output"),
                    use_schema_constraints=not is_openai_model(model_id),
                    examples=examples,
                    response_format=response_format,
                )
            )

        threshold = float(extraction_config.get("consensus_threshold", 0.6))
        consensus = ConsensusLanguageModel(
            models=models,
            similarity_threshold=threshold,
        )
        label = f"consensus({', '.join(consensus_providers)})"
        logger.info(
            "Built consensus model with %d providers: %s (threshold=%.2f)",
            len(consensus_providers),
            label,
            threshold,
        )
        return consensus, label

    # ── Single-provider path (default) ──────────────────────
    api_key = resolve_api_key(provider)
    extra_kwargs: dict[str, Any] = {}
    if is_openai_model(provider):
        extra_kwargs["fence_output"] = True
    # When response_format is active, override fence_output to
    # False — the LLM returns raw JSON, not fenced code blocks.
    if response_format is not None:
        extra_kwargs["fence_output"] = False

    model = manager.get_or_create_model(
        model_id=provider,
        api_key=api_key,
        fence_output=extra_kwargs.get("fence_output"),
        use_schema_constraints=not is_openai_model(provider),
        examples=examples,
        response_format=response_format,
    )
    return model, provider


# ── LLM retry wrapper ──────────────────────────────────────


@retry(
    retry=retry_if_exception_type(ValueError),
    stop=stop_after_attempt(MAX_LLM_RETRIES + 1),
    wait=wait_none(),
    reraise=True,
)
def _run_lx_extract_with_retry(
    extract_kwargs: dict[str, Any],
    source: str,
) -> Any:
    """Call ``lx.extract()`` with automatic retries on ValueError.

    LLMs occasionally return malformed output where
    ``extraction_text`` is a dict instead of a string.  Because
    the output is non-deterministic, an immediate re-invocation
    usually succeeds.

    Retries are handled by ``tenacity`` with up to
    ``MAX_LLM_RETRIES`` attempts.

    Args:
        extract_kwargs: Keyword arguments for ``lx.extract()``.
        source: Human-readable source label for logs.

    Returns:
        The result of ``lx.extract()``.

    Raises:
        ValueError: If all attempts fail with the same error.
    """
    return lx.extract(**extract_kwargs)


# ── Core extraction logic ───────────────────────────────────


def run_extraction(
    task_self: Any | None,
    document_url: str | None = None,
    raw_text: str | None = None,
    provider: str = "gpt-4o",
    passes: int = 1,
    extraction_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Core extraction logic shared by single and batch tasks.

    Delegates to ``langcore.extract()`` for the heavy lifting.

    Args:
        task_self: Bound Celery task instance (for progress
            updates).  ``None`` when called from batch items.
        document_url: URL to the source document.
        raw_text: Raw text blob to process directly.
        provider: LLM model ID (e.g. ``gpt-4o``).
        passes: Number of extraction passes.
        extraction_config: Optional overrides for prompt,
            examples, and LangCore parameters.

    Returns:
        A dict containing the extraction result and metadata.
    """
    settings = get_settings()
    extraction_config = extraction_config or {}
    source = document_url or "<raw_text>"
    start_ms = int(time.time() * 1000)

    log_source = _mask_url(source)
    logger.info(
        "Starting extraction for %s (model=%s, passes=%d)",
        log_source,
        provider,
        passes,
    )

    # ── Step 1: Determine input ─────────────────────────────
    if task_self:
        task_self.update_state(
            state=TaskState.PROGRESS,
            meta={
                "step": "preparing",
                "source": source,
                "percent": 5,
            },
        )

    if document_url:
        # Defence-in-depth: re-validate the URL in the worker
        # in case a task was enqueued by something other than
        # the API route (e.g. management command, direct Celery
        # call).
        validate_url(document_url, purpose="document_url")
        logger.info("Downloading document from %s", log_source)
        text_input: str = download_document(document_url)
    else:
        text_input = raw_text or ""

    # ── Step 2: Build prompt & examples ─────────────────────
    prompt_description: str = extraction_config.get(
        "prompt_description",
        DEFAULT_PROMPT_DESCRIPTION,
    )

    raw_examples: list[dict[str, Any]] = extraction_config.get(
        "examples", DEFAULT_EXAMPLES
    )
    examples = build_examples(raw_examples)

    # ── Step 2b: Check extraction cache ─────────────────────
    no_cache = bool(extraction_config.get("no_cache", False))
    ext_cache = ExtractionCache.instance()
    cache_key: str | None = None

    if ext_cache.enabled and not no_cache:
        cache_key = build_cache_key(
            text=text_input,
            prompt_description=prompt_description,
            examples=extraction_config.get("examples", DEFAULT_EXAMPLES),
            model_id=provider,
            temperature=extraction_config.get("temperature"),
            passes=passes,
            consensus_providers=extraction_config.get(
                "consensus_providers",
            ),
            consensus_threshold=extraction_config.get(
                "consensus_threshold",
            ),
        )
        cached = ext_cache.get(cache_key)
        if cached is not None:
            record_cache_hit()
            elapsed_ms = int(time.time() * 1000) - start_ms
            cached["data"]["metadata"]["processing_time_ms"] = elapsed_ms
            cached["data"]["metadata"]["cache_hit"] = True
            # Update source to the current URL — the cached entry may
            # have been written with a different (now-stale) URL for
            # the same document content.
            cached["source"] = source
            logger.info(
                "Extraction cache HIT for %s — returning in %d ms",
                log_source,
                elapsed_ms,
            )
            return cached
        record_cache_miss()
    elif no_cache:
        logger.info("Cache bypassed (no_cache=true) for %s", log_source)

    # ── Step 3: Assemble lx.extract() kwargs ────────────────
    if task_self:
        task_self.update_state(
            state=TaskState.PROGRESS,
            meta={
                "step": "extracting",
                "source": source,
                "percent": 10,
            },
        )

    # Initialise provider manager (singleton) — enables litellm
    # Redis cache and model instance reuse across batches/jobs.
    manager = ProviderManager.instance()
    # Only enable the litellm prompt-level cache for single-pass
    # jobs.  Multi-pass jobs need non-deterministic LLM responses
    # per pass; the litellm cache would return identical results
    # for every pass (same prompt → same cache key), defeating
    # cross-pass diversity.  The ExtractionCache (semantic layer)
    # still covers cross-job caching for multi-pass results.
    if passes <= 1:
        manager.ensure_cache()

    # ── Step 3a: Resolve structured output ──────────────────
    # When the caller has not explicitly opted out AND the
    # provider advertises JSON Schema support, build a
    # ``response_format`` dict so the LLM is constrained to
    # valid JSON matching the extraction schema.
    structured_output_flag: bool | None = extraction_config.get(
        "structured_output",
    )
    response_format: dict[str, Any] | None = None
    if structured_output_flag is not False and (
        structured_output_flag is True or supports_structured_output(provider)
    ):
        response_format = build_response_format(
            extraction_config.get("examples", DEFAULT_EXAMPLES),
        )
        logger.info(
            "Structured output enabled for %s (response_format type=%s)",
            provider,
            response_format.get("type"),
        )

    cached_model, model_label = _build_model(
        provider,
        extraction_config,
        manager,
        examples=examples,
        response_format=response_format,
    )

    # ── Step 3b: Apply guardrails & audit wrappers ──────────
    cached_model = apply_model_wrappers(
        cached_model,
        provider,
        extraction_config,
    )

    extract_kwargs: dict[str, Any] = {
        "text_or_documents": text_input,
        "prompt_description": prompt_description,
        "examples": examples,
        "model": cached_model,
        "extraction_passes": passes,
        "max_workers": extraction_config.get(
            "max_workers",
            settings.DEFAULT_MAX_WORKERS,
        ),
        "max_char_buffer": extraction_config.get(
            "max_char_buffer",
            settings.DEFAULT_MAX_CHAR_BUFFER,
        ),
        "show_progress": False,
        # Model is pre-configured; suppress the langcore
        # UserWarning that fires when model + use_schema_constraints
        # (which defaults to True) are passed together.
        "use_schema_constraints": False,
    }

    # Optional overrides
    if "additional_context" in extraction_config:
        extract_kwargs["additional_context"] = extraction_config["additional_context"]
    if "temperature" in extraction_config:
        extract_kwargs["temperature"] = extraction_config["temperature"]
    if "context_window_chars" in extraction_config:
        extract_kwargs["context_window_chars"] = extraction_config[
            "context_window_chars"
        ]

    # ── Step 4: Run LangCore ─────────────────────────────
    logger.info(
        "Calling lx.extract() for %s (model=%s, passes=%d)",
        log_source,
        model_label,
        passes,
    )

    lx_result = _run_lx_extract_with_retry(
        extract_kwargs,
        source,
    )

    if isinstance(lx_result, list):
        lx_result = lx_result[0] if lx_result else lx.data.AnnotatedDocument()

    # ── Step 5: Convert to response schema ──────────────────
    if task_self:
        task_self.update_state(
            state=TaskState.PROGRESS,
            meta={
                "step": "post_processing",
                "source": source,
                "percent": 90,
            },
        )

    entities = convert_extractions(lx_result)
    elapsed_ms = int(time.time() * 1000) - start_ms
    tokens = extract_token_usage(lx_result)

    result: dict[str, Any] = {
        "status": STATUS_COMPLETED,
        "source": source,
        "data": {
            "entities": entities,
            "metadata": {
                "provider": model_label,
                "tokens_used": tokens,
                "processing_time_ms": elapsed_ms,
            },
        },
    }

    logger.info(
        "Extraction completed for %s — %d entities in %d ms",
        log_source,
        len(entities),
        elapsed_ms,
    )

    # ── Step 6: Populate extraction cache ───────────────────
    if cache_key is not None:
        ext_cache.put(cache_key, result)

    return result


# ── Async extraction path ───────────────────────────────────


@retry(
    retry=retry_if_exception_type(ValueError),
    stop=stop_after_attempt(MAX_LLM_RETRIES + 1),
    wait=wait_none(),
    reraise=True,
)
async def _run_lx_async_extract_with_retry(
    extract_kwargs: dict[str, Any],
    source: str,
) -> Any:
    """Call ``lx.async_extract()`` with automatic retries on ValueError.

    Mirrors ``_run_lx_extract_with_retry`` but uses the async
    extraction path for I/O-CPU overlap.

    Retries are handled by ``tenacity`` with up to
    ``MAX_LLM_RETRIES`` attempts.

    Args:
        extract_kwargs: Keyword arguments for ``lx.async_extract()``.
        source: Human-readable source label for logs.

    Returns:
        The result of ``lx.async_extract()``.

    Raises:
        ValueError: If all attempts fail with the same error.
    """
    return await lx.async_extract(**extract_kwargs)


async def async_run_extraction(
    task_self: Any | None,
    document_url: str | None = None,
    raw_text: str | None = None,
    provider: str = "gpt-4o",
    passes: int = 1,
    extraction_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Async extraction logic using ``lx.async_extract()``.

    Mirrors ``run_extraction`` but awaits the native async
    LangCore path, enabling I/O-CPU overlap for 20-40%
    wall-time improvement when providers support native
    ``async_infer`` (e.g. LiteLLM via ``litellm.acompletion``).

    Args:
        task_self: Bound Celery task instance (for progress
            updates).  ``None`` when called from batch items.
        document_url: URL to the source document.
        raw_text: Raw text blob to process directly.
        provider: LLM model ID (e.g. ``gpt-4o``).
        passes: Number of extraction passes.
        extraction_config: Optional overrides for prompt,
            examples, and LangCore parameters.

    Returns:
        A dict containing the extraction result and metadata.
    """
    settings = get_settings()
    extraction_config = extraction_config or {}
    source = document_url or "<raw_text>"
    log_source = _mask_url(source)
    start_ms = int(time.time() * 1000)

    logger.info(
        "Starting async extraction for %s (model=%s, passes=%d)",
        log_source,
        provider,
        passes,
    )

    # ── Step 1: Determine input ─────────────────────────────
    if task_self:
        task_self.update_state(
            state=TaskState.PROGRESS,
            meta={
                "step": "preparing",
                "source": source,
                "percent": 5,
            },
        )

    if document_url:
        validate_url(document_url, purpose="document_url")
        logger.info("Downloading document from %s", log_source)
        # download_document is I/O-bound but short; run in a
        # thread to avoid blocking the event loop.
        text_input: str = await asyncio.to_thread(
            download_document,
            document_url,
        )
    else:
        text_input = raw_text or ""

    # ── Step 2: Build prompt & examples ─────────────────────
    prompt_description: str = extraction_config.get(
        "prompt_description",
        DEFAULT_PROMPT_DESCRIPTION,
    )

    raw_examples: list[dict[str, Any]] = extraction_config.get(
        "examples", DEFAULT_EXAMPLES
    )
    examples = build_examples(raw_examples)

    # ── Step 2b: Check extraction cache ─────────────────────
    no_cache = bool(extraction_config.get("no_cache", False))
    ext_cache = ExtractionCache.instance()
    cache_key: str | None = None

    if ext_cache.enabled and not no_cache:
        cache_key = build_cache_key(
            text=text_input,
            prompt_description=prompt_description,
            examples=extraction_config.get("examples", DEFAULT_EXAMPLES),
            model_id=provider,
            temperature=extraction_config.get("temperature"),
            passes=passes,
            consensus_providers=extraction_config.get(
                "consensus_providers",
            ),
            consensus_threshold=extraction_config.get(
                "consensus_threshold",
            ),
        )
        cached = ext_cache.get(cache_key)
        if cached is not None:
            record_cache_hit()
            elapsed_ms = int(time.time() * 1000) - start_ms
            cached["data"]["metadata"]["processing_time_ms"] = elapsed_ms
            cached["data"]["metadata"]["cache_hit"] = True
            # Update source to the current URL — the cached entry may
            # have been written with a different (now-stale) URL for
            # the same document content.
            cached["source"] = source
            logger.info(
                "Extraction cache HIT for %s — returning in %d ms",
                log_source,
                elapsed_ms,
            )
            return cached
        record_cache_miss()
    elif no_cache:
        logger.info("Cache bypassed (no_cache=true) for %s", log_source)

    # ── Step 3: Assemble lx.async_extract() kwargs ──────────
    if task_self:
        task_self.update_state(
            state=TaskState.PROGRESS,
            meta={
                "step": "extracting",
                "source": source,
                "percent": 10,
            },
        )

    manager = ProviderManager.instance()
    # Only enable the litellm prompt-level cache for single-pass
    # jobs.  Multi-pass jobs need non-deterministic LLM responses
    # per pass; the litellm cache would return identical results
    # for every pass (same prompt → same cache key), defeating
    # cross-pass diversity.  The ExtractionCache (semantic layer)
    # still covers cross-job caching for multi-pass results.
    if passes <= 1:
        manager.ensure_cache()

    # ── Step 3a: Resolve structured output (async) ──────────
    structured_output_flag_async: bool | None = extraction_config.get(
        "structured_output",
    )
    response_format_async: dict[str, Any] | None = None
    if structured_output_flag_async is not False and (
        structured_output_flag_async is True or supports_structured_output(provider)
    ):
        response_format_async = build_response_format(
            extraction_config.get("examples", DEFAULT_EXAMPLES),
        )
        logger.info(
            "Structured output enabled (async) for %s (response_format type=%s)",
            provider,
            response_format_async.get("type"),
        )

    cached_model, model_label = _build_model(
        provider,
        extraction_config,
        manager,
        examples=examples,
        response_format=response_format_async,
    )

    # ── Step 3b: Apply guardrails & audit wrappers (async) ───
    cached_model = apply_model_wrappers(
        cached_model,
        provider,
        extraction_config,
    )

    extract_kwargs: dict[str, Any] = {
        "text_or_documents": text_input,
        "prompt_description": prompt_description,
        "examples": examples,
        "model": cached_model,
        "extraction_passes": passes,
        "max_workers": extraction_config.get(
            "max_workers",
            settings.DEFAULT_MAX_WORKERS,
        ),
        "max_char_buffer": extraction_config.get(
            "max_char_buffer",
            settings.DEFAULT_MAX_CHAR_BUFFER,
        ),
        "show_progress": False,
        # Model is pre-configured; suppress the langcore
        # UserWarning that fires when model + use_schema_constraints
        # (which defaults to True) are passed together.
        "use_schema_constraints": False,
    }

    if "additional_context" in extraction_config:
        extract_kwargs["additional_context"] = extraction_config["additional_context"]
    if "temperature" in extraction_config:
        extract_kwargs["temperature"] = extraction_config["temperature"]
    if "context_window_chars" in extraction_config:
        extract_kwargs["context_window_chars"] = extraction_config[
            "context_window_chars"
        ]

    # ── Step 4: Run LangCore (async) ─────────────────────
    logger.info(
        "Calling lx.async_extract() for %s (model=%s, passes=%d)",
        log_source,
        model_label,
        passes,
    )

    lx_result = await _run_lx_async_extract_with_retry(
        extract_kwargs,
        source,
    )

    if isinstance(lx_result, list):
        lx_result = lx_result[0] if lx_result else lx.data.AnnotatedDocument()

    # ── Step 5: Convert to response schema ──────────────────
    if task_self:
        task_self.update_state(
            state=TaskState.PROGRESS,
            meta={
                "step": "post_processing",
                "source": source,
                "percent": 90,
            },
        )

    entities = convert_extractions(lx_result)
    elapsed_ms = int(time.time() * 1000) - start_ms
    tokens = extract_token_usage(lx_result)

    result: dict[str, Any] = {
        "status": STATUS_COMPLETED,
        "source": source,
        "data": {
            "entities": entities,
            "metadata": {
                "provider": model_label,
                "tokens_used": tokens,
                "processing_time_ms": elapsed_ms,
            },
        },
    }

    logger.info(
        "Async extraction completed for %s — %d entities in %d ms",
        log_source,
        len(entities),
        elapsed_ms,
    )

    # ── Step 6: Populate extraction cache ───────────────────
    if cache_key is not None:
        ext_cache.put(cache_key, result)

    return result
