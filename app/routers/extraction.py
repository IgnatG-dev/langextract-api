"""Extraction submission routes (single & batch)."""

from fastapi import APIRouter

from app.schemas import (
    BatchExtractionRequest,
    ExtractionRequest,
    TaskSubmitResponse,
)
from app.tasks import extract_batch, extract_document

router = APIRouter(tags=["extraction"])


@router.post("/extract", response_model=TaskSubmitResponse)
def submit_extraction(request: ExtractionRequest) -> TaskSubmitResponse:
    """Submit a single document for extraction.

    Accepts either a ``document_url`` or ``raw_text`` (or both).
    Optionally include a ``callback_url`` to receive a webhook
    when the extraction completes.

    Returns a task ID that can be polled via ``GET /tasks/{task_id}``.
    """
    task = extract_document.delay(
        document_url=(str(request.document_url) if request.document_url else None),
        raw_text=request.raw_text,
        provider=request.provider,
        passes=request.passes,
        callback_url=(str(request.callback_url) if request.callback_url else None),
        extraction_config=request.extraction_config,
    )
    source = str(request.document_url) if request.document_url else "<raw_text>"
    return TaskSubmitResponse(
        task_id=task.id,
        status="submitted",
        message=f"Extraction submitted for {source}",
    )


@router.post("/extract/batch", response_model=TaskSubmitResponse)
def submit_batch_extraction(
    request: BatchExtractionRequest,
) -> TaskSubmitResponse:
    """Submit a batch of documents for extraction.

    Returns a single task ID that tracks the overall batch progress.
    If a batch-level ``callback_url`` is supplied the aggregated
    result will be POSTed there on completion.
    """
    documents = [doc.model_dump(mode="json") for doc in request.documents]
    task = extract_batch.delay(
        batch_id=request.batch_id,
        documents=documents,
        callback_url=(str(request.callback_url) if request.callback_url else None),
    )
    return TaskSubmitResponse(
        task_id=task.id,
        status="submitted",
        message=(
            f"Batch '{request.batch_id}' submitted "
            f"with {len(request.documents)} document(s)"
        ),
    )
