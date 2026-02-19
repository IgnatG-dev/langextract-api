"""
Extraction orchestrator — coordinates download, LLM, and formatting.

Delegates provider resolution to ``app.services.providers`` and
data conversion to ``app.services.converters``.  All LangExtract
interaction is isolated here so it can be unit-tested without
Celery and reused from both the single-document and batch task
flows.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import langextract as lx
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
from app.core.security import validate_url
from app.schemas.enums import TaskState
from app.services.converters import (
    build_examples,
    convert_extractions,
    extract_token_usage,
)
from app.services.downloader import download_document
from app.services.providers import is_openai_model, resolve_api_key

logger = logging.getLogger(__name__)

# Maximum immediate retries when the LLM returns malformed
# extraction_text (e.g. a dict instead of a string).  Each retry
# re-invokes the LLM, which is non-deterministic and usually
# self-corrects on the next attempt.
MAX_LLM_RETRIES: int = 2


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
    max_retries: int,
) -> Any:
    """Call ``lx.extract()`` with automatic retries on ValueError.

    LLMs occasionally return malformed output where
    ``extraction_text`` is a dict instead of a string.  Because
    the output is non-deterministic, an immediate re-invocation
    usually succeeds.

    Retries are handled by ``tenacity``; the ``max_retries``
    parameter is kept for backward compatibility but the actual
    attempt count is governed by ``MAX_LLM_RETRIES``.

    Args:
        extract_kwargs: Keyword arguments for ``lx.extract()``.
        source: Human-readable source label for logs.
        max_retries: Kept for API compatibility (unused).

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
        logger.info(
            "Downloading document from %s",
            document_url,
        )
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
    api_key = resolve_api_key(provider)
    if api_key:
        extract_kwargs["api_key"] = api_key

    # OpenAI-specific flags
    if is_openai_model(provider):
        extract_kwargs["fence_output"] = True
        extract_kwargs["use_schema_constraints"] = False

    # ── Step 4: Run LangExtract ─────────────────────────────
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
