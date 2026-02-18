"""
FastAPI entry point — all routes are defined here.

The application exposes:
* ``POST /api/v1/extract``         — submit a single extraction task
* ``POST /api/v1/extract/batch``   — submit a batch of extraction tasks
* ``GET  /api/v1/tasks/{task_id}`` — poll task status / result
* ``DELETE /api/v1/tasks/{task_id}`` — revoke a running task
* ``GET  /api/v1/health``          — liveness probe
* ``GET  /api/v1/health/celery``   — Celery worker readiness probe
"""

import logging
from contextlib import asynccontextmanager

from celery.result import AsyncResult
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.dependencies import get_settings
from app.schemas import (
    BatchExtractionRequest,
    CeleryHealthResponse,
    ExtractionRequest,
    HealthResponse,
    TaskRevokeResponse,
    TaskState,
    TaskStatusResponse,
    TaskSubmitResponse,
)
from app.tasks import extract_batch, extract_document
from app.worker import celery_app

# ── Logging ─────────────────────────────────────────────────────────────────

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan ────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application startup / shutdown hooks."""
    logger.info("Starting %s", settings.APP_NAME)
    yield
    logger.info("Shutting down %s", settings.APP_NAME)


# ── App factory ─────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "Queue-based document extraction API powered by "
        "FastAPI, Celery, and LangExtract."
    ),
    version="0.1.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    root_path=settings.ROOT_PATH,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health endpoints ────────────────────────────────────────────────────────


@app.get(
    f"{settings.API_V1_STR}/health",
    response_model=HealthResponse,
    tags=["health"],
)
def health_check() -> HealthResponse:
    """Liveness probe — returns OK if the web process is running."""
    return HealthResponse(status="ok", version="0.1.0")


@app.get(
    f"{settings.API_V1_STR}/health/celery",
    response_model=CeleryHealthResponse,
    tags=["health"],
)
def celery_health_check() -> CeleryHealthResponse:
    """Readiness probe — checks Celery worker availability."""
    try:
        inspect = celery_app.control.inspect()
        stats = inspect.stats()
        active = inspect.active()

        if stats is None:
            return CeleryHealthResponse(
                status="unhealthy",
                message="No Celery workers available",
                workers=[],
            )

        workers = [
            {
                "name": name,
                "status": "online",
                "active_tasks": (len(active.get(name, [])) if active else 0),
            }
            for name in stats
        ]

        return CeleryHealthResponse(
            status="healthy",
            message=f"{len(workers)} worker(s) online",
            workers=workers,
        )
    except Exception as exc:
        return CeleryHealthResponse(
            status="unhealthy",
            message=f"Error connecting to Celery: {exc}",
            workers=[],
        )


# ── Extraction endpoints ───────────────────────────────────────────────────


@app.post(
    f"{settings.API_V1_STR}/extract",
    response_model=TaskSubmitResponse,
    tags=["extraction"],
)
def submit_extraction(request: ExtractionRequest) -> TaskSubmitResponse:
    """
    Submit a single document for extraction.

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


@app.post(
    f"{settings.API_V1_STR}/extract/batch",
    response_model=TaskSubmitResponse,
    tags=["extraction"],
)
def submit_batch_extraction(
    request: BatchExtractionRequest,
) -> TaskSubmitResponse:
    """
    Submit a batch of documents for extraction.

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


# ── Task management endpoints ──────────────────────────────────────────────


@app.get(
    f"{settings.API_V1_STR}/tasks/{{task_id}}",
    response_model=TaskStatusResponse,
    tags=["tasks"],
)
def get_task_status(task_id: str) -> TaskStatusResponse:
    """
    Poll the current status and result of a previously submitted task.
    """
    result = AsyncResult(task_id, app=celery_app)

    response = TaskStatusResponse(
        task_id=task_id,
        state=TaskState(result.state),
    )

    if result.state == "PENDING":
        response.progress = {
            "status": "Task is waiting to be processed",
        }
    elif result.state == "PROGRESS":
        response.progress = result.info
    elif result.state == "SUCCESS":
        response.result = result.result
    elif result.state == "FAILURE":
        response.error = str(result.info)

    return response


@app.delete(
    f"{settings.API_V1_STR}/tasks/{{task_id}}",
    response_model=TaskRevokeResponse,
    tags=["tasks"],
)
def revoke_task(
    task_id: str,
    terminate: bool = False,
) -> TaskRevokeResponse:
    """
    Revoke a pending or running task.

    Set ``terminate=true`` to send SIGTERM to a running worker process.
    """
    celery_app.control.revoke(task_id, terminate=terminate)
    return TaskRevokeResponse(
        task_id=task_id,
        status="revoked",
        message=(f"Task revocation signal sent (terminate={terminate})"),
    )
