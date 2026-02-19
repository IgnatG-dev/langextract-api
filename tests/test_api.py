"""Tests for the LangExtract API endpoints."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_health_check():
    """Test basic health endpoint returns OK."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_health_check_returns_request_id():
    """Response includes X-Request-ID header from middleware."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/health")

    assert "x-request-id" in response.headers


@pytest.mark.asyncio
async def test_health_check_echoes_custom_request_id():
    """Client-supplied X-Request-ID is echoed back."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/v1/health",
            headers={"X-Request-ID": "custom-rid-42"},
        )

    assert response.headers["x-request-id"] == "custom-rid-42"


@pytest.mark.asyncio
async def test_submit_extraction_with_url():
    """Test submitting an extraction task with a document URL."""
    with (
        patch(
            "app.api.routes.extraction.extract_document",
        ) as mock_task,
        patch(
            "app.api.routes.extraction.validate_url",
            return_value="https://example.com/doc.pdf",
        ),
    ):
        mock_result = MagicMock()
        mock_result.id = "task-id-abc-123"
        mock_task.delay.return_value = mock_result

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/extract",
                json={
                    "document_url": ("https://example.com/doc.pdf"),
                    "provider": "gpt-4o",
                    "passes": 2,
                    "callback_url": ("https://nestjs.example.com/webhooks/done"),
                    "extraction_config": {
                        "temperature": 0.5,
                    },
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "task-id-abc-123"
        assert data["status"] == "submitted"
        mock_task.delay.assert_called_once()


@pytest.mark.asyncio
async def test_submit_extraction_with_raw_text():
    """Test submitting an extraction task with raw text."""
    with patch(
        "app.api.routes.extraction.extract_document",
    ) as mock_task:
        mock_result = MagicMock()
        mock_result.id = "task-id-text-456"
        mock_task.delay.return_value = mock_result

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/extract",
                json={
                    "raw_text": ("AGREEMENT between Acme Corp and ..."),
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "task-id-text-456"
        mock_task.delay.assert_called_once()
        call_kwargs = mock_task.delay.call_args.kwargs
        assert call_kwargs["document_url"] is None
        assert call_kwargs["raw_text"] == ("AGREEMENT between Acme Corp and ...")


@pytest.mark.asyncio
async def test_submit_extraction_requires_input():
    """Test that providing neither URL nor text returns 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/extract",
            json={"provider": "gpt-4o"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_provider_validation_rejects_bad_input():
    """Provider with invalid characters is rejected (422)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/extract",
            json={
                "raw_text": "test",
                "provider": "!invalid provider!",
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submit_batch_extraction():
    """Test submitting a batch extraction task."""
    # Mock the group dispatch — returns a GroupResult with
    # two children whose IDs are known.
    mock_child_0 = MagicMock()
    mock_child_0.id = "child-0"
    mock_child_1 = MagicMock()
    mock_child_1.id = "child-1"
    mock_group_result = MagicMock()
    mock_group_result.children = [mock_child_0, mock_child_1]

    mock_group_instance = MagicMock()
    mock_group_instance.apply_async.return_value = mock_group_result

    mock_finalize_result = MagicMock()
    mock_finalize_result.id = "batch-task-id-789"

    with (
        patch(
            "app.api.routes.extraction.group",
            return_value=mock_group_instance,
        ),
        patch(
            "app.api.routes.extraction.finalize_batch",
        ) as mock_finalize,
        patch(
            "app.api.routes.extraction.validate_url",
            return_value="ok",
        ),
    ):
        mock_finalize.apply_async.return_value = mock_finalize_result

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/extract/batch",
                json={
                    "batch_id": "batch-001",
                    "callback_url": ("https://nestjs.example.com/webhooks/batch"),
                    "documents": [
                        {
                            "document_url": ("https://example.com/a.pdf"),
                        },
                        {
                            "raw_text": ("Some contract text here ..."),
                            "provider": "gpt-4o",
                        },
                    ],
                },
            )

    assert response.status_code == 200
    data = response.json()
    assert data["batch_task_id"] == "batch-task-id-789"
    assert data["document_task_ids"] == ["child-0", "child-1"]
    assert data["status"] == "submitted"
    # finalize_batch should receive the child IDs
    call_kwargs = mock_finalize.apply_async.call_args.kwargs
    assert call_kwargs["kwargs"]["batch_id"] == "batch-001"
    assert call_kwargs["kwargs"]["child_task_ids"] == [
        "child-0",
        "child-1",
    ]
    assert call_kwargs["kwargs"]["callback_url"] == (
        "https://nestjs.example.com/webhooks/batch"
    )


@pytest.mark.asyncio
async def test_get_task_status_pending():
    """Test polling a pending task."""
    with (
        patch(
            "app.api.routes.tasks.AsyncResult",
        ) as mock_ar,
        patch(
            "app.api.routes.tasks._fetch_redis_result",
            return_value=None,
        ),
    ):
        mock_instance = MagicMock()
        mock_instance.state = "PENDING"
        mock_instance.info = None
        mock_ar.return_value = mock_instance

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v1/tasks/task-id-abc-123",
            )

        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "PENDING"
        assert data["progress"] is not None


@pytest.mark.asyncio
async def test_get_task_status_pending_falls_back_to_redis():
    """When Celery says PENDING but Redis has a result, return it."""
    stored = {"status": "completed", "data": {"entities": []}}

    with (
        patch(
            "app.api.routes.tasks.AsyncResult",
        ) as mock_ar,
        patch(
            "app.api.routes.tasks._fetch_redis_result",
            return_value=stored,
        ),
    ):
        mock_instance = MagicMock()
        mock_instance.state = "PENDING"
        mock_instance.info = None
        mock_ar.return_value = mock_instance

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/v1/tasks/task-id-expired",
            )

    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "SUCCESS"
    assert data["result"] == stored


@pytest.mark.asyncio
async def test_revoke_task():
    """Test revoking a task."""
    with patch(
        "app.api.routes.tasks.celery_app",
    ) as mock_celery:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.delete(
                "/api/v1/tasks/task-id-abc-123",
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "revoked"
        mock_celery.control.revoke.assert_called_once_with(
            "task-id-abc-123",
            terminate=False,
        )


@pytest.mark.asyncio
async def test_metrics_endpoint():
    """Test that /metrics returns Prometheus-format text."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/metrics")

    assert response.status_code == 200
    assert "tasks_submitted_total" in response.text
    assert "tasks_succeeded_total" in response.text
    assert "tasks_failed_total" in response.text


@pytest.mark.asyncio
async def test_idempotency_key_returns_existing_task():
    """Duplicate idempotency_key returns existing task ID."""
    mock_redis = MagicMock()
    mock_redis.get.return_value = "existing-task-id"

    with patch(
        "app.api.routes.extraction.get_redis_client",
        return_value=mock_redis,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/extract",
                json={
                    "raw_text": "test",
                    "idempotency_key": "my-key-123",
                },
            )

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "existing-task-id"
    assert "Duplicate" in data["message"]


@pytest.mark.asyncio
async def test_submit_extraction_with_callback_headers():
    """callback_headers are forwarded to the task."""
    with (
        patch(
            "app.api.routes.extraction.extract_document",
        ) as mock_task,
        patch(
            "app.api.routes.extraction.validate_url",
            return_value="ok",
        ),
    ):
        mock_result = MagicMock()
        mock_result.id = "task-hdr-api"
        mock_task.delay.return_value = mock_result

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/extract",
                json={
                    "raw_text": "test",
                    "callback_url": ("https://hook.example.com/done"),
                    "callback_headers": {
                        "Authorization": "Bearer tok-abc",
                    },
                },
            )

    assert response.status_code == 200
    call_kwargs = mock_task.delay.call_args.kwargs
    assert call_kwargs["callback_headers"] == {
        "Authorization": "Bearer tok-abc",
    }
