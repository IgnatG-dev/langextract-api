"""Tests for the LangExtract API endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_check():
    """Test basic health endpoint returns OK."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_submit_extraction_with_url():
    """Test submitting an extraction task with a document URL."""
    with patch("app.routers.extraction.extract_document") as mock_task:
        mock_result = MagicMock()
        mock_result.id = "task-id-abc-123"
        mock_task.delay.return_value = mock_result

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/extract",
                json={
                    "document_url": "https://example.com/doc.pdf",
                    "provider": "gpt-4o",
                    "passes": 2,
                    "callback_url": "https://nestjs.example.com/webhooks/done",
                    "extraction_config": {"model": "gpt-4"},
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "task-id-abc-123"
        assert data["status"] == "submitted"
        mock_task.delay.assert_called_once_with(
            document_url="https://example.com/doc.pdf",
            raw_text=None,
            provider="gpt-4o",
            passes=2,
            callback_url="https://nestjs.example.com/webhooks/done",
            extraction_config={"model": "gpt-4"},
        )


@pytest.mark.asyncio
async def test_submit_extraction_with_raw_text():
    """Test submitting an extraction task with raw text."""
    with patch("app.routers.extraction.extract_document") as mock_task:
        mock_result = MagicMock()
        mock_result.id = "task-id-text-456"
        mock_task.delay.return_value = mock_result

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/extract",
                json={
                    "raw_text": "AGREEMENT between Acme Corp and ...",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "task-id-text-456"
        mock_task.delay.assert_called_once()
        call_kwargs = mock_task.delay.call_args.kwargs
        assert call_kwargs["document_url"] is None
        assert call_kwargs["raw_text"] == "AGREEMENT between Acme Corp and ..."


@pytest.mark.asyncio
async def test_submit_extraction_requires_input():
    """Test that providing neither URL nor text returns 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/extract",
            json={"provider": "gpt-4o"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submit_batch_extraction():
    """Test submitting a batch extraction task with callback_url."""
    with patch("app.routers.extraction.extract_batch") as mock_task:
        mock_result = MagicMock()
        mock_result.id = "batch-task-id-789"
        mock_task.delay.return_value = mock_result

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/extract/batch",
                json={
                    "batch_id": "batch-001",
                    "callback_url": "https://nestjs.example.com/webhooks/batch",
                    "documents": [
                        {
                            "document_url": "https://example.com/a.pdf",
                        },
                        {
                            "raw_text": "Some contract text here ...",
                            "provider": "gpt-4o",
                        },
                    ],
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "batch-task-id-789"
        assert data["status"] == "submitted"
        mock_task.delay.assert_called_once()
        call_kwargs = mock_task.delay.call_args.kwargs
        assert call_kwargs["batch_id"] == "batch-001"
        assert (
            call_kwargs["callback_url"] == "https://nestjs.example.com/webhooks/batch"
        )
        assert len(call_kwargs["documents"]) == 2


@pytest.mark.asyncio
async def test_get_task_status_pending():
    """Test polling a pending task."""
    with patch("app.routers.tasks.AsyncResult") as mock_ar:
        mock_instance = MagicMock()
        mock_instance.state = "PENDING"
        mock_instance.info = None
        mock_ar.return_value = mock_instance

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/tasks/task-id-abc-123",
            )

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "PENDING"
        assert data["progress"] is not None


@pytest.mark.asyncio
async def test_revoke_task():
    """Test revoking a task."""
    with patch("app.routers.tasks.celery_app") as mock_celery:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(
                "/api/v1/tasks/task-id-abc-123",
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "revoked"
        mock_celery.control.revoke.assert_called_once_with(
            "task-id-abc-123", terminate=False
        )
