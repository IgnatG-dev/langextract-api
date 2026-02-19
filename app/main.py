"""
FastAPI application factory.

Creates the ``app`` instance with middleware, lifespan hooks,
and versioned API routers.  Route handlers live in
``app.api.routes.*``.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)

from app.api.routes import extraction, health, tasks
from app.core.config import get_settings, get_version
from app.core.logging import setup_logging

# ── Logging ─────────────────────────────────────────────────────────────────

settings = get_settings()
setup_logging(level=settings.LOG_LEVEL, json_format=not settings.DEBUG)
logger = logging.getLogger(__name__)


# ── Request-ID middleware ───────────────────────────────────────────────────


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Inject a unique ``X-Request-ID`` into every request/response.

    If the client provides an ``X-Request-ID`` header it is reused;
    otherwise a new UUID-4 is generated.  The value is stored on
    ``request.state.request_id`` for downstream handlers and logged
    with every request.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Process the request, attaching a request ID."""
        request_id = request.headers.get(
            "x-request-id",
            str(uuid.uuid4()),
        )
        request.state.request_id = request_id

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        response.headers["X-Request-ID"] = request_id

        logger.info(
            "%s %s %d %.1fms [rid=%s]",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
        )
        return response


# ── Lifespan ────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application startup / shutdown hooks."""
    logger.info("Starting %s", settings.APP_NAME)
    yield
    logger.info("Shutting down %s", settings.APP_NAME)


# ── App factory ─────────────────────────────────────────────────────────────

_version = get_version()

app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "Queue-based document extraction API powered by "
        "FastAPI, Celery, and LangExtract."
    ),
    version=_version,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    root_path=settings.ROOT_PATH,
    lifespan=lifespan,
)

# Middleware (order matters — outermost first)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Register routers ───────────────────────────────────────────────────────
app.include_router(health.router, prefix=settings.API_V1_STR)
app.include_router(extraction.router, prefix=settings.API_V1_STR)
app.include_router(tasks.router, prefix=settings.API_V1_STR)
