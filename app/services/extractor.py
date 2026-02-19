"""
Extraction service — pure business logic for LangExtract calls.

All LangExtract interaction is isolated here so it can be
unit-tested without Celery and reused from both the single-
document and batch task flows.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import langextract as lx

from app.core.config import get_settings
from app.core.defaults import (
    DEFAULT_EXAMPLES,
    DEFAULT_PROMPT_DESCRIPTION,
)

logger = logging.getLogger(__name__)

# Maximum immediate retries when the LLM returns malformed
# extraction_text (e.g. a dict instead of a string).  Each retry
# re-invokes the LLM, which is non-deterministic and usually
# self-corrects on the next attempt.
MAX_LLM_RETRIES: int = 2


# ── Helpers ─────────────────────────────────────────────────────────────────


def _build_examples(
    raw_examples: list[dict[str, Any]],
) -> list[lx.data.ExampleData]:
    """Convert plain-dict examples into ``lx.data.ExampleData``.

    Args:
        raw_examples: List of dicts, each with ``text`` and
            ``extractions`` keys.

    Returns:
        A list of ``ExampleData`` ready for ``lx.extract()``.
    """
    return [
        lx.data.ExampleData(
            text=ex["text"],
            extractions=[
                lx.data.Extraction(
                    extraction_class=e["extraction_class"],
                    extraction_text=e["extraction_text"],
                    attributes=e.get("attributes"),
                )
                for e in ex.get("extractions", [])
            ],
        )
        for ex in raw_examples
    ]


def _resolve_api_key(provider: str) -> str | None:
    """Pick the correct API key for *provider* from settings.

    Args:
        provider: Model ID string (e.g. ``gpt-4o``).

    Returns:
        An API key string, or ``None`` if nothing is configured.
    """
    settings = get_settings()
    lower = provider.lower()
    if "gpt" in lower or "openai" in lower:
        return settings.OPENAI_API_KEY or None
    return settings.LANGEXTRACT_API_KEY or settings.GEMINI_API_KEY or None


def _is_openai_model(provider: str) -> bool:
    """Return ``True`` if *provider* is an OpenAI model.

    Args:
        provider: Model ID string.

    Returns:
        Boolean indicating whether OpenAI-specific flags apply.
    """
    lower = provider.lower()
    return "gpt" in lower or "openai" in lower


def _convert_extractions(
    result: lx.data.AnnotatedDocument,
) -> list[dict[str, Any]]:
    """Flatten ``AnnotatedDocument.extractions`` into dicts.

    Args:
        result: The annotated document from ``lx.extract()``.

    Returns:
        A list of entity dicts matching ``ExtractedEntity``
        schema.
    """
    entities: list[dict[str, Any]] = []
    for ext in result.extractions or []:
        # Defensive coercion: the LLM occasionally returns a
        # dict for extraction_text — stringify it so
        # downstream consumers always receive a string.
        raw_text = ext.extraction_text
        if not isinstance(raw_text, (str, int, float)):
            logger.warning(
                "Coercing non-scalar extraction_text (%s) to str for class '%s'",
                type(raw_text).__name__,
                ext.extraction_class,
            )
            raw_text = str(raw_text)

        entity: dict[str, Any] = {
            "extraction_class": ext.extraction_class,
            "extraction_text": str(raw_text),
            "attributes": ext.attributes or {},
            "char_start": (ext.char_interval.start_pos if ext.char_interval else None),
            "char_end": (ext.char_interval.end_pos if ext.char_interval else None),
        }
        entities.append(entity)
    return entities


def _extract_token_usage(
    lx_result: lx.data.AnnotatedDocument,
) -> int | None:
    """Attempt to extract token usage from a LangExtract result.

    Args:
        lx_result: The annotated document from ``lx.extract()``.

    Returns:
        Token count if available, ``None`` otherwise.
    """
    usage = getattr(lx_result, "usage", None)
    if usage and hasattr(usage, "total_tokens"):
        return int(usage.total_tokens)
    if isinstance(usage, dict) and "total_tokens" in usage:
        return int(usage["total_tokens"])
    return None


def _run_lx_extract_with_retry(
    extract_kwargs: dict[str, Any],
    source: str,
    max_retries: int,
) -> Any:
    """Call ``lx.extract()`` with automatic retries on ValueError.

    LLMs occasionally return malformed output where
    ``extraction_text`` is a dict instead of a string.  Because
    the output is non-deterministic, an immediate re-invocation
    usually succeeds.

    Args:
        extract_kwargs: Keyword arguments for ``lx.extract()``.
        source: Human-readable source label for logs.
        max_retries: How many extra attempts after the initial
            call.

    Returns:
        The result of ``lx.extract()``.

    Raises:
        ValueError: If all attempts fail with the same error.
    """
    last_exc: ValueError | None = None

    for attempt in range(1, max_retries + 2):  # +2 = 1 initial + retries
        try:
            return lx.extract(**extract_kwargs)
        except ValueError as exc:
            last_exc = exc
            if attempt <= max_retries:
                logger.warning(
                    "LLM returned malformed output for %s "
                    "(attempt %d/%d): %s — retrying",
                    source,
                    attempt,
                    max_retries + 1,
                    exc,
                )
            else:
                logger.error(
                    "LLM returned malformed output for %s after %d attempt(s): %s",
                    source,
                    attempt,
                    exc,
                )

    raise last_exc  # type: ignore[misc]


# ── Core extraction logic ───────────────────────────────────────────────────


def run_extraction(
    task_self: Any | None,
    document_url: str | None = None,
    raw_text: str | None = None,
    provider: str = "gpt-4o",
    passes: int = 1,
    extraction_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Core extraction logic shared by single and batch tasks.

    Delegates to ``langextract.extract()`` for the heavy lifting.

    Args:
        task_self: Bound Celery task instance (for progress
            updates).  ``None`` when called from batch items.
        document_url: URL to the source document.
        raw_text: Raw text blob to process directly.
        provider: LLM model ID (e.g. ``gpt-4o``).
        passes: Number of extraction passes.
        extraction_config: Optional overrides for prompt,
            examples, and LangExtract parameters.

    Returns:
        A dict containing the extraction result and metadata.
    """
    settings = get_settings()
    extraction_config = extraction_config or {}
    source = document_url or "<raw_text>"
    start_ms = int(time.time() * 1000)

    logger.info(
        "Starting extraction for %s (model=%s, passes=%d)",
        source,
        provider,
        passes,
    )

    # ── Step 1: Determine input ─────────────────────────────────
    if task_self:
        task_self.update_state(
            state="PROGRESS",
            meta={
                "step": "preparing",
                "source": source,
                "percent": 5,
            },
        )

    text_input: str = document_url if document_url else (raw_text or "")

    # ── Step 2: Build prompt & examples ─────────────────────────
    prompt_description: str = extraction_config.get(
        "prompt_description",
        DEFAULT_PROMPT_DESCRIPTION,
    )

    raw_examples: list[dict[str, Any]] = extraction_config.get(
        "examples",
        DEFAULT_EXAMPLES,
    )
    examples = _build_examples(raw_examples)

    # ── Step 3: Assemble lx.extract() kwargs ────────────────────
    if task_self:
        task_self.update_state(
            state="PROGRESS",
            meta={
                "step": "extracting",
                "source": source,
                "percent": 10,
            },
        )

    extract_kwargs: dict[str, Any] = {
        "text_or_documents": text_input,
        "prompt_description": prompt_description,
        "examples": examples,
        "model_id": provider,
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

    # API key
    api_key = _resolve_api_key(provider)
    if api_key:
        extract_kwargs["api_key"] = api_key

    # OpenAI-specific flags
    if _is_openai_model(provider):
        extract_kwargs["fence_output"] = True
        extract_kwargs["use_schema_constraints"] = False

    # ── Step 4: Run LangExtract ─────────────────────────────────
    logger.info(
        "Calling lx.extract() for %s (model_id=%s, passes=%d)",
        source,
        provider,
        passes,
    )

    lx_result = _run_lx_extract_with_retry(
        extract_kwargs,
        source,
        MAX_LLM_RETRIES,
    )

    if isinstance(lx_result, list):
        lx_result = lx_result[0] if lx_result else lx.data.AnnotatedDocument()

    # ── Step 5: Convert to response schema ──────────────────────
    if task_self:
        task_self.update_state(
            state="PROGRESS",
            meta={
                "step": "post_processing",
                "source": source,
                "percent": 90,
            },
        )

    entities = _convert_extractions(lx_result)
    elapsed_ms = int(time.time() * 1000) - start_ms
    tokens = _extract_token_usage(lx_result)

    result: dict[str, Any] = {
        "status": "completed",
        "source": source,
        "data": {
            "entities": entities,
            "metadata": {
                "provider": provider,
                "tokens_used": tokens,
                "processing_time_ms": elapsed_ms,
            },
        },
    }

    logger.info(
        "Extraction completed for %s — %d entities in %d ms",
        source,
        len(entities),
        elapsed_ms,
    )
    return result
