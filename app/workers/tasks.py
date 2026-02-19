"""
Celery tasks — thin wrappers around service functions.

Each task follows the pattern:
1. Accept a serialisable payload.
2. Delegate real work to ``app.services.*``.
3. Report progress via ``self.update_state()``.
4. Persist results and optionally fire a webhook.

Business logic lives in ``app.services.extractor`` so it can be
tested and reused independently of Celery.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from celery.exceptions import Retry
from celery.result import AsyncResult

from app.core.config import get_redis_client, get_settings
from app.core.constants import (
    REDIS_PREFIX_TASK_RESULT,
    STATUS_COMPLETED,
)
from app.core.metrics import record_task_completed
from app.schemas.extraction import TaskState
from app.services.extractor import run_extraction
from app.services.webhook import fire_webhook
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ── Single-document extraction ──────────────────────────────────────────────


def _store_result_in_redis(
    task_id: str,
    result: dict[str, Any],
) -> None:
    """Persist *result* under a predictable Redis key.

    Stored separately from Celery's result backend so the
    task-status endpoint can fall back to this key when
    Celery metadata has expired or is unavailable.

    Args:
        task_id: The Celery task identifier.
        result: JSON-serialisable result dict.
    """
    try:
        settings = get_settings()
        client = get_redis_client()
        try:
            client.setex(
                f"{REDIS_PREFIX_TASK_RESULT}{task_id}",
                settings.RESULT_EXPIRES,
                json.dumps(result),
            )
        finally:
            client.close()
    except Exception:
        logger.warning(
            "Failed to persist result for task %s",
            task_id,
            exc_info=True,
        )


@celery_app.task(
    bind=True,
    name="tasks.extract_document",
    max_retries=3,
    default_retry_delay=60,
)
def extract_document(
    self,
    document_url: str | None = None,
    raw_text: str | None = None,
    provider: str = "gpt-4o",
    passes: int = 1,
    callback_url: str | None = None,
    extraction_config: dict[str, Any] | None = None,
    callback_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Extract structured data from a single document.

    Args:
        document_url: URL to the source document.
        raw_text: Raw text blob to process directly.
        provider: AI provider / model to use.
        passes: Number of extraction passes.
        callback_url: Optional webhook URL.
        extraction_config: Optional overrides for the extraction
            pipeline.
        callback_headers: Optional extra HTTP headers to send
            with the webhook request (e.g. Authorization).

    Returns:
        A dict containing the extraction result and metadata.
    """
    start_s = time.monotonic()
    try:
        result = run_extraction(
            task_self=self,
            document_url=document_url,
            raw_text=raw_text,
            provider=provider,
            passes=passes,
            extraction_config=extraction_config,
        )
        elapsed_s = time.monotonic() - start_s

        # Persist result under a predictable Redis key
        _store_result_in_redis(self.request.id, result)

        # Fire webhook if requested
        if callback_url:
            fire_webhook(
                callback_url,
                {"task_id": self.request.id, **result},
                extra_headers=callback_headers,
            )

        record_task_completed(success=True, duration_s=elapsed_s)
        return result

    except Retry:
        # Celery retry — do not record as a final failure.
        raise

    except Exception as exc:
        elapsed_s = time.monotonic() - start_s
        is_final = self.request.retries >= self.max_retries
        if is_final:
            record_task_completed(
                success=False,
                duration_s=elapsed_s,
            )
        logger.exception(
            "Extraction failed (attempt %d/%d) for %s: %s",
            self.request.retries + 1,
            self.max_retries + 1,
            document_url or "<raw_text>",
            exc,
        )
        raise self.retry(exc=exc) from exc


# ── Batch finalisation (non-blocking) ───────────────────────────────────

# Maximum time (in seconds) to wait for child tasks before
# giving up and reporting a partial result.  With a 5-second
# retry countdown this allows for roughly 1 hour of waiting.
_FINALIZE_MAX_RETRIES: int = 720
_FINALIZE_COUNTDOWN_S: int = 5


@celery_app.task(
    bind=True,
    name="tasks.finalize_batch",
    max_retries=_FINALIZE_MAX_RETRIES,
    default_retry_delay=_FINALIZE_COUNTDOWN_S,
)
def finalize_batch(
    self,
    *,
    batch_id: str,
    child_task_ids: list[str],
    documents: list[dict[str, Any]],
    callback_url: str | None = None,
    callback_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Aggregate results from child extraction tasks.

    The batch API route dispatches per-document tasks via a
    Celery ``group()`` and then schedules this task.
    ``finalize_batch`` polls the children using Celery's retry
    mechanism so that no worker slot is blocked while children
    are still running.

    Once all children are ready (or the retry budget is
    exhausted), it aggregates success/failure results, fires
    an optional webhook, and persists the batch result in Redis.

    Args:
        batch_id: Unique identifier for this batch.
        child_task_ids: Celery task IDs of the per-document
            extraction tasks.
        documents: The original document dicts (for error
            source attribution).
        callback_url: Optional batch-level webhook URL.
        callback_headers: Optional extra HTTP headers for the
            webhook request.

    Returns:
        Aggregated batch result with per-document outcomes.
    """
    total = len(child_task_ids)
    children = [AsyncResult(tid, app=celery_app) for tid in child_task_ids]

    # ── Poll: re-schedule if children are still running ─────
    if not all(c.ready() for c in children):
        completed = sum(1 for c in children if c.ready())

        self.update_state(
            state=TaskState.PROGRESS,
            meta={
                "batch_id": batch_id,
                "document_task_ids": child_task_ids,
                "total": total,
                "completed": completed,
            },
        )

        if self.request.retries < self.max_retries:
            raise self.retry(countdown=_FINALIZE_COUNTDOWN_S)

        logger.warning(
            "Batch %s: timed out after %d retries — finalising with partial results",
            batch_id,
            self.request.retries,
        )

    # ── Aggregate results ───────────────────────────────────
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for child, doc in zip(children, documents, strict=True):
        source = doc.get("document_url") or "<raw_text>"
        if child.successful():
            results.append(child.result)
        else:
            err_msg = str(child.result) if child.result else "Unknown error"
            errors.append({"source": source, "error": err_msg})

    batch_result: dict[str, Any] = {
        "status": STATUS_COMPLETED,
        "batch_id": batch_id,
        "total": total,
        "successful": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
        "document_task_ids": child_task_ids,
    }

    if callback_url:
        fire_webhook(
            callback_url,
            {"task_id": self.request.id, **batch_result},
            extra_headers=callback_headers,
        )

    # Persist batch result under a predictable Redis key
    _store_result_in_redis(self.request.id, batch_result)

    return batch_result
