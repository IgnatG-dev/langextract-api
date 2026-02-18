"""Shared pytest fixtures for the LangExtract API test suite."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    """
    Yield an async HTTP client bound to the FastAPI app.

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
