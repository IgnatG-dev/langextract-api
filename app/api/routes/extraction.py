"""Extraction submission routes (single & batch)."""

from __future__ import annotations

import logging

from celery import group
from fastapi import APIRouter, HTTPException

from app.core.config import get_redis_client, get_settings
from app.core.constants import (
    REDIS_PREFIX_IDEMPOTENCY,
    STATUS_SUBMITTED,
)
from app.core.metrics import record_task_submitted
from app.core.security import validate_url
from app.schemas import (
    BatchExtractionRequest,
    BatchTaskSubmitResponse,
    ExtractionRequest,
    TaskSubmitResponse,
)
from app.workers.tasks import extract_document, finalize_batch

logger = logging.getLogger(__name__)

router = APIRouter(tags=["extraction"])


def _validate_request_urls(
    request: ExtractionRequest,
) -> None:
    """Validate document_url and callback_url against SSRF.

    Args:
        request: The extraction request to validate.

    Raises:
        HTTPException: If any URL fails validation.
    """
    if request.document_url:
        try:
            validate_url(
                str(request.document_url),
                purpose="document_url",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    if request.callback_url:
        try:
            validate_url(
                str(request.callback_url),
                purpose="callback_url",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc


@router.post("/extract", response_model=TaskSubmitResponse)
def submit_extraction(
    request: ExtractionRequest,
) -> TaskSubmitResponse:
    """Submit a single document for extraction.

    Accepts either a ``document_url`` or ``raw_text`` (or both).
    Optionally include a ``callback_url`` to receive a webhook
    when the extraction completes.

    When an ``idempotency_key`` is provided, repeat submissions
    return the original task ID without creating a new task.

    Returns a task ID that can be polled via ``GET /tasks/{id}``.
    """
    _validate_request_urls(request)

    # ── Idempotency check ───────────────────────────────────
    if request.idempotency_key:
        redis_client = get_redis_client()
        try:
            idem_key = f"{REDIS_PREFIX_IDEMPOTENCY}{request.idempotency_key}"
            existing_task_id = redis_client.get(idem_key)
            if existing_task_id:
                logger.info(
                    "Idempotent hit: key=%s → task=%s",
                    request.idempotency_key,
                    existing_task_id,
                )
                return TaskSubmitResponse(
                    task_id=existing_task_id,
                    status=STATUS_SUBMITTED,
                    message=("Duplicate request — returning existing task"),
                )
        finally:
            redis_client.close()

    # ── Submit task ─────────────────────────────────────────
    extraction_config = request.extraction_config.to_flat_dict()

    task = extract_document.delay(
        document_url=(str(request.document_url) if request.document_url else None),
        raw_text=request.raw_text,
        provider=request.provider,
        passes=request.passes,
        callback_url=(str(request.callback_url) if request.callback_url else None),
        extraction_config=extraction_config,
        callback_headers=request.callback_headers,
    )

    # Store idempotency mapping
    if request.idempotency_key:
        settings = get_settings()
        redis_client = get_redis_client()
        try:
            idem_key = f"{REDIS_PREFIX_IDEMPOTENCY}{request.idempotency_key}"
            redis_client.setex(
                idem_key,
                settings.RESULT_EXPIRES,
                task.id,
            )
        finally:
            redis_client.close()

    record_task_submitted()

    source = str(request.document_url) if request.document_url else "<raw_text>"
    return TaskSubmitResponse(
        task_id=task.id,
        status=STATUS_SUBMITTED,
        message=f"Extraction submitted for {source}",
    )


@router.post(
    "/extract/batch",
    response_model=BatchTaskSubmitResponse,
)
def submit_batch_extraction(
    request: BatchExtractionRequest,
) -> BatchTaskSubmitResponse:
    """Submit a batch of documents for extraction.

    Dispatches per-document tasks via a Celery ``group()`` at
    the API level so that child task IDs are available
    immediately.  A lightweight ``finalize_batch`` task monitors
    the children (via non-blocking retry-based polling) and
    aggregates results once all documents are done.

    Returns a batch-level task ID plus per-document task IDs so
    callers can retry or poll individual documents independently.
    If a batch-level ``callback_url`` is supplied the aggregated
    result is POSTed there on completion.
    """
    # Validate all URLs up-front
    for doc in request.documents:
        _validate_request_urls(doc)

    if request.callback_url:
        try:
            validate_url(
                str(request.callback_url),
                purpose="batch callback_url",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    documents = [doc.model_dump(mode="json") for doc in request.documents]
    # Convert nested ExtractionConfig → flat dicts
    for doc_dict in documents:
        cfg = doc_dict.get("extraction_config")
        if isinstance(cfg, dict) and cfg:
            doc_dict["extraction_config"] = {
                k: v for k, v in cfg.items() if v is not None
            }

    # ── Fan-out: dispatch group directly to get child IDs ───
    signatures = [
        extract_document.s(
            document_url=doc_dict.get("document_url"),
            raw_text=doc_dict.get("raw_text"),
            provider=doc_dict.get("provider", "gpt-4o"),
            passes=doc_dict.get("passes", 1),
            extraction_config=doc_dict.get("extraction_config", {}),
        )
        for doc_dict in documents
    ]
    group_result = group(signatures).apply_async()
    child_ids = [r.id for r in group_result.children]

    # ── Aggregation: non-blocking finalize task ─────────────
    task = finalize_batch.apply_async(
        kwargs={
            "batch_id": request.batch_id,
            "child_task_ids": child_ids,
            "documents": documents,
            "callback_url": (
                str(request.callback_url) if request.callback_url else None
            ),
            "callback_headers": request.callback_headers,
        },
        countdown=2,
    )

    record_task_submitted()

    return BatchTaskSubmitResponse(
        batch_task_id=task.id,
        document_task_ids=child_ids,
        status=STATUS_SUBMITTED,
        message=(
            f"Batch '{request.batch_id}' submitted "
            f"with {len(request.documents)} document(s)"
        ),
    )
