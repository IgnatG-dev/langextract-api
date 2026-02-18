"""
Celery tasks — long-running extraction jobs processed by workers.

Each task follows the pattern:
1. Accept a serialisable payload.
2. Report progress via ``self.update_state(state="PROGRESS", ...)``.
3. Return a JSON-serialisable result dict.
4. Optionally POST the result to a ``callback_url`` (webhook).

LangExtract integration hooks are marked with ``# TODO: langextract``
so they are easy to find once the library is wired in.
"""

import logging
import time
from typing import Any

import httpx

from app.worker import celery_app

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _fire_webhook(
    callback_url: str,
    payload: dict[str, Any],
) -> None:
    """POST *payload* to *callback_url*, logging but never raising."""
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(callback_url, json=payload)
            resp.raise_for_status()
        logger.info(
            "Webhook delivered to %s (status %s)",
            callback_url,
            resp.status_code,
        )
    except Exception as exc:
        logger.error(
            "Webhook delivery to %s failed: %s",
            callback_url,
            exc,
        )


# ── Core extraction logic ───────────────────────────────────────────────────


def _run_extraction(
    task_self: Any | None,
    document_url: str | None = None,
    raw_text: str | None = None,
    provider: str = "gemini-1.5-pro",
    passes: int = 1,
    extraction_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Core extraction logic shared by single and batch tasks.

    Args:
        task_self: Bound Celery task instance (for progress updates).
        document_url: URL to the source document.
        raw_text: Raw text blob to process directly.
        provider: AI provider / model to use.
        passes: Number of extraction passes.
        extraction_config: Optional pipeline configuration overrides.

    Returns:
        A dict containing the extraction result and metadata.
    """
    extraction_config = extraction_config or {}
    source = document_url or "<raw_text>"
    start_ms = int(time.time() * 1000)

    logger.info(
        "Starting extraction for %s (provider=%s, passes=%d)",
        source,
        provider,
        passes,
    )

    # Step 1 — Download / access document (or use raw_text)
    if task_self:
        task_self.update_state(
            state="PROGRESS",
            meta={
                "step": "downloading",
                "source": source,
                "percent": 10,
            },
        )
    if raw_text:
        logger.info("Processing raw text (%d chars)", len(raw_text))
    # TODO: langextract — load document via langextract loader
    time.sleep(1)  # placeholder

    # Step 2 — Run extraction pipeline (N passes)
    for pass_num in range(1, passes + 1):
        if task_self:
            task_self.update_state(
                state="PROGRESS",
                meta={
                    "step": "extracting",
                    "source": source,
                    "pass": pass_num,
                    "total_passes": passes,
                    "percent": 10 + int(70 * pass_num / passes),
                },
            )
        # TODO: langextract — call extraction pipeline with
        #       provider=provider, config=extraction_config
        time.sleep(2)  # placeholder

    # Step 3 — Post-process / validate results
    if task_self:
        task_self.update_state(
            state="PROGRESS",
            meta={
                "step": "post_processing",
                "source": source,
                "percent": 90,
            },
        )
    # TODO: langextract — validate / normalise output
    time.sleep(0.5)  # placeholder

    elapsed_ms = int(time.time() * 1000) - start_ms

    result: dict[str, Any] = {
        "status": "completed",
        "source": source,
        "data": {
            "entities": [],  # TODO: replace with real output
            "metadata": {
                "provider": provider,
                "tokens_used": 0,
                "processing_time_ms": elapsed_ms,
            },
        },
    }

    logger.info(
        "Extraction completed for %s in %d ms",
        source,
        elapsed_ms,
    )
    return result


# ── Extraction task ─────────────────────────────────────────────────────────


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
    provider: str = "gemini-1.5-pro",
    passes: int = 1,
    callback_url: str | None = None,
    extraction_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Extract structured data from a single document.

    Args:
        document_url: URL to the source document.
        raw_text: Raw text blob to process directly.
        provider: AI provider / model to use.
        passes: Number of extraction passes.
        callback_url: Optional webhook URL to POST the result to.
        extraction_config: Optional overrides for the extraction
            pipeline (prompt template, chunking strategy, etc.).

    Returns:
        A dict containing the extraction result and metadata.
    """
    try:
        result = _run_extraction(
            task_self=self,
            document_url=document_url,
            raw_text=raw_text,
            provider=provider,
            passes=passes,
            extraction_config=extraction_config,
        )

        # Fire webhook if requested
        if callback_url:
            _fire_webhook(
                callback_url,
                {"task_id": self.request.id, **result},
            )

        return result

    except Exception as exc:
        logger.exception(
            "Extraction failed for %s: %s",
            document_url or "<raw_text>",
            exc,
        )
        raise self.retry(exc=exc)


# ── Batch extraction task ──────────────────────────────────────────────────


@celery_app.task(
    bind=True,
    name="tasks.extract_batch",
    max_retries=1,
    default_retry_delay=120,
)
def extract_batch(
    self,
    batch_id: str,
    documents: list[dict[str, Any]],
    callback_url: str | None = None,
) -> dict[str, Any]:
    """
    Process a batch of documents sequentially with progress tracking.

    Args:
        batch_id: Unique identifier for this batch.
        documents: List of dicts, each with ``document_url`` and/or
            ``raw_text``, plus optional ``provider``, ``passes``,
            ``callback_url``, and ``extraction_config``.
        callback_url: Optional batch-level webhook URL. Overrides
            any per-document ``callback_url``.

    Returns:
        Aggregated batch result with per-document outcomes.
    """
    total = len(documents)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    logger.info("Starting batch %s with %d documents", batch_id, total)

    for idx, doc in enumerate(documents):
        source = doc.get("document_url") or "<raw_text>"

        try:
            outcome = _run_extraction(
                task_self=self,
                document_url=doc.get("document_url"),
                raw_text=doc.get("raw_text"),
                provider=doc.get("provider", "gemini-1.5-pro"),
                passes=doc.get("passes", 1),
                extraction_config=doc.get("extraction_config", {}),
            )
            results.append(outcome)
        except Exception as exc:
            logger.error(
                "Batch %s — document %s failed: %s",
                batch_id,
                source,
                exc,
            )
            errors.append({"source": source, "error": str(exc)})

        self.update_state(
            state="PROGRESS",
            meta={
                "batch_id": batch_id,
                "current": idx + 1,
                "total": total,
                "successful": len(results),
                "failed": len(errors),
                "percent": int((idx + 1) / total * 100),
            },
        )

    batch_result: dict[str, Any] = {
        "status": "completed",
        "batch_id": batch_id,
        "total": total,
        "successful": len(results),
        "failed": len(errors),
        "results": results,
        "errors": errors,
    }

    if callback_url:
        _fire_webhook(
            callback_url,
            {"task_id": self.request.id, **batch_result},
        )

    return batch_result
