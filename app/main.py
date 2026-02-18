"""
FastAPI application factory.

Creates the ``app`` instance with middleware, lifespan hooks,
and versioned API routers.  Route handlers live in ``app.routers.*``.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.dependencies import get_settings
from app.logging_config import setup_logging
from app.routers import extraction, health, tasks

# ── Logging ─────────────────────────────────────────────────────────────────

settings = get_settings()
setup_logging(level=settings.LOG_LEVEL, json_format=not settings.DEBUG)
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

# ── Register routers ───────────────────────────────────────────────────────
app.include_router(health.router, prefix=settings.API_V1_STR)
app.include_router(extraction.router, prefix=settings.API_V1_STR)
app.include_router(tasks.router, prefix=settings.API_V1_STR)
