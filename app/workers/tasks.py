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

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.core.metrics import record_task_completed
from app.services.extractor import run_extraction
from app.services.webhook import fire_webhook, store_result
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ── Single-document extraction ──────────────────────────────────────────────


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

    Returns:
        A dict containing the extraction result and metadata.
    """
    try:
        start_s = time.monotonic()
        result = run_extraction(
            task_self=self,
            document_url=document_url,
            raw_text=raw_text,
            provider=provider,
            passes=passes,
            extraction_config=extraction_config,
        )
        elapsed_s = time.monotonic() - start_s

        # Persist under a predictable Redis key
        store_result(self.request.id, result)

        # Fire webhook if requested
        if callback_url:
            fire_webhook(
                callback_url,
                {"task_id": self.request.id, **result},
            )

        record_task_completed(success=True, duration_s=elapsed_s)
        return result

    except Exception as exc:
        record_task_completed(success=False, duration_s=0.0)
        logger.exception(
            "Extraction failed for %s: %s",
            document_url or "<raw_text>",
            exc,
        )
        raise self.retry(exc=exc) from exc


# ── Batch extraction ────────────────────────────────────────────────────────


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
    concurrency: int = 4,
) -> dict[str, Any]:
    """Process a batch of documents with bounded parallelism.

    Args:
        batch_id: Unique identifier for this batch.
        documents: List of extraction request dicts.
        callback_url: Optional batch-level webhook URL.
        concurrency: Max parallel extractions (default 4).

    Returns:
        Aggregated batch result with per-document outcomes.
    """
    total = len(documents)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    logger.info(
        "Starting batch %s with %d documents (concurrency=%d)",
        batch_id,
        total,
        concurrency,
    )

    def _process_doc(
        idx: int,
        doc: dict[str, Any],
    ) -> tuple[int, dict[str, Any] | None, str | None]:
        """Extract a single document.

        Args:
            idx: Zero-based document index.
            doc: Extraction request dict.

        Returns:
            Tuple of (index, result_or_None, error_or_None).
        """
        source = doc.get("document_url") or "<raw_text>"
        try:
            outcome = run_extraction(
                task_self=None,
                document_url=doc.get("document_url"),
                raw_text=doc.get("raw_text"),
                provider=doc.get("provider", "gpt-4o"),
                passes=doc.get("passes", 1),
                extraction_config=doc.get(
                    "extraction_config",
                    {},
                ),
            )
            return idx, outcome, None
        except Exception as exc:
            logger.error(
                "Batch %s — document %s failed: %s",
                batch_id,
                source,
                exc,
            )
            return idx, None, str(exc)

    # ── Parallel execution with concurrency limit ───────────
    completed = 0
    with ThreadPoolExecutor(
        max_workers=min(concurrency, total),
    ) as pool:
        futures = {
            pool.submit(_process_doc, i, doc): i for i, doc in enumerate(documents)
        }

        for future in as_completed(futures):
            idx, outcome, error_msg = future.result()
            source = documents[idx].get("document_url") or "<raw_text>"

            if outcome:
                results.append(outcome)
            else:
                errors.append(
                    {"source": source, "error": error_msg},
                )

            completed += 1
            self.update_state(
                state="PROGRESS",
                meta={
                    "batch_id": batch_id,
                    "current": completed,
                    "total": total,
                    "successful": len(results),
                    "failed": len(errors),
                    "percent": int(completed / total * 100),
                },
            )

            # Partial-success webhook (every 25 %)
            if (
                callback_url
                and total >= 4
                and completed < total
                and completed % max(1, total // 4) == 0
            ):
                fire_webhook(
                    callback_url,
                    {
                        "task_id": self.request.id,
                        "status": "in_progress",
                        "batch_id": batch_id,
                        "current": completed,
                        "total": total,
                        "successful": len(results),
                        "failed": len(errors),
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

    store_result(self.request.id, batch_result)

    if callback_url:
        fire_webhook(
            callback_url,
            {"task_id": self.request.id, **batch_result},
        )

    return batch_result
