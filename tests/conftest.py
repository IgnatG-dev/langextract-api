"""Shared pytest fixtures for the LangExtract API test suite."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

# ── HTTP client ─────────────────────────────────────────────


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Yield an async HTTP client bound to the FastAPI app.

    Usage::

        async def test_something(client: AsyncClient):
            response = await client.get("/api/v1/health")
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as ac:
        yield ac


# ── Settings override ──────────────────────────────────────


@pytest.fixture
def mock_settings():
    """Return a mock Settings object with sensible test defaults."""
    settings = MagicMock()
    settings.APP_NAME = "LangExtract API"
    settings.API_V1_STR = "/api/v1"
    settings.ROOT_PATH = ""
    settings.DEBUG = False
    settings.LOG_LEVEL = "DEBUG"
    settings.CORS_ORIGINS = ["*"]
    settings.REDIS_HOST = "localhost"
    settings.REDIS_PORT = 6379
    settings.REDIS_DB = 0
    settings.REDIS_URL = "redis://localhost:6379/0"
    settings.CELERY_BROKER_URL = "redis://localhost:6379/0"
    settings.CELERY_RESULT_BACKEND = "redis://localhost:6379/0"
    settings.OPENAI_API_KEY = "test-openai-key"
    settings.GEMINI_API_KEY = "test-gemini-key"
    settings.LANGEXTRACT_API_KEY = ""
    settings.DEFAULT_PROVIDER = "gpt-4o"
    settings.DEFAULT_MAX_WORKERS = 10
    settings.DEFAULT_MAX_CHAR_BUFFER = 1000
    settings.TASK_TIME_LIMIT = 3600
    settings.TASK_SOFT_TIME_LIMIT = 3300
    settings.RESULT_EXPIRES = 86400
    # Security settings
    settings.ALLOWED_URL_DOMAINS = []
    settings.WEBHOOK_SECRET = ""
    settings.DOC_DOWNLOAD_TIMEOUT = 30
    settings.DOC_DOWNLOAD_MAX_BYTES = 50_000_000
    # Batch settings
    settings.BATCH_CONCURRENCY = 4
    return settings


# ── LangExtract mock dataclasses ──────────────────────────


@dataclass
class FakeCharInterval:
    """Stand-in for ``lx.data.CharInterval``."""

    start_pos: int = 0
    end_pos: int = 10


@dataclass
class FakeExtraction:
    """Stand-in for ``lx.data.Extraction``."""

    extraction_class: str = "party"
    extraction_text: str = "Acme Corp"
    attributes: dict[str, Any] | None = None
    char_interval: FakeCharInterval | None = None


@dataclass
class FakeAnnotatedDocument:
    """Stand-in for ``lx.data.AnnotatedDocument``."""

    text: str = ""
    extractions: list[FakeExtraction] = field(
        default_factory=list,
    )


@pytest.fixture
def fake_annotated_document() -> FakeAnnotatedDocument:
    """Return a realistic mock AnnotatedDocument."""
    return FakeAnnotatedDocument(
        text="Agreement by Acme Corp dated January 1, 2025",
        extractions=[
            FakeExtraction(
                extraction_class="party",
                extraction_text="Acme Corp",
                attributes={"role": "Seller"},
                char_interval=FakeCharInterval(
                    start_pos=14,
                    end_pos=23,
                ),
            ),
            FakeExtraction(
                extraction_class="date",
                extraction_text="January 1, 2025",
                attributes={"type": "effective_date"},
                char_interval=FakeCharInterval(
                    start_pos=30,
                    end_pos=45,
                ),
            ),
        ],
    )


@pytest.fixture
def mock_lx_extract(fake_annotated_document):
    """Patch ``lx.extract()`` in the extractor service."""
    with patch(
        "app.services.extractor.lx.extract",
        return_value=fake_annotated_document,
    ) as m:
        yield m
