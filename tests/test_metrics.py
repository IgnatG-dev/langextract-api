"""Tests for Redis-backed metrics counters."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.core.metrics import (
    get_metrics,
    record_task_completed,
    record_task_submitted,
)


class TestRecordTaskSubmitted:
    """Tests for ``record_task_submitted``."""

    @patch("app.core.metrics.get_redis_client")
    def test_increments_submitted_counter(self, mock_grc):
        """INCR is called on the submitted key."""
        mock_client = MagicMock()
        mock_grc.return_value = mock_client

        record_task_submitted()

        mock_client.incr.assert_called_once_with(
            "metrics:tasks_submitted_total",
        )
        mock_client.close.assert_called_once()

    @patch("app.core.metrics.get_redis_client")
    def test_swallows_redis_errors(self, mock_grc):
        """Redis failures are logged but not raised."""
        mock_grc.side_effect = Exception("no redis")

        # Should not raise
        record_task_submitted()


class TestRecordTaskCompleted:
    """Tests for ``record_task_completed``."""

    @patch("app.core.metrics.get_redis_client")
    def test_success_increments_succeeded(self, mock_grc):
        """Success increments the succeeded counter."""
        mock_client = MagicMock()
        mock_grc.return_value = mock_client

        record_task_completed(success=True, duration_s=1.5)

        mock_client.incr.assert_called_once_with(
            "metrics:tasks_succeeded_total",
        )
        mock_client.incrbyfloat.assert_called_once_with(
            "metrics:task_duration_seconds_sum",
            1.5,
        )
        mock_client.close.assert_called_once()

    @patch("app.core.metrics.get_redis_client")
    def test_failure_increments_failed(self, mock_grc):
        """Failure increments the failed counter."""
        mock_client = MagicMock()
        mock_grc.return_value = mock_client

        record_task_completed(success=False, duration_s=0.3)

        mock_client.incr.assert_called_once_with(
            "metrics:tasks_failed_total",
        )
        mock_client.incrbyfloat.assert_called_once_with(
            "metrics:task_duration_seconds_sum",
            0.3,
        )

    @patch("app.core.metrics.get_redis_client")
    def test_swallows_redis_errors(self, mock_grc):
        """Redis failures are logged but not raised."""
        mock_grc.side_effect = Exception("no redis")

        # Should not raise
        record_task_completed(success=True, duration_s=1.0)


class TestGetMetrics:
    """Tests for ``get_metrics``."""

    @patch("app.core.metrics.get_redis_client")
    def test_returns_snapshot(self, mock_grc):
        """Returns a dict of current metric values."""
        mock_client = MagicMock()
        mock_client.mget.return_value = ["10", "7", "3", "45.5"]
        mock_grc.return_value = mock_client

        m = get_metrics()

        assert m["tasks_submitted_total"] == 10
        assert m["tasks_succeeded_total"] == 7
        assert m["tasks_failed_total"] == 3
        assert m["task_duration_seconds_sum"] == 45.5
        mock_client.close.assert_called_once()

    @patch("app.core.metrics.get_redis_client")
    def test_returns_defaults_on_redis_failure(self, mock_grc):
        """Returns zeroed defaults when Redis is unavailable."""
        mock_grc.side_effect = Exception("no redis")

        m = get_metrics()

        assert m["tasks_submitted_total"] == 0
        assert m["tasks_succeeded_total"] == 0
        assert m["tasks_failed_total"] == 0
        assert m["task_duration_seconds_sum"] == 0.0

    @patch("app.core.metrics.get_redis_client")
    def test_handles_none_values(self, mock_grc):
        """Missing keys (None from mget) default to 0."""
        mock_client = MagicMock()
        mock_client.mget.return_value = [None, None, None, None]
        mock_grc.return_value = mock_client

        m = get_metrics()

        assert m["tasks_submitted_total"] == 0
        assert m["tasks_succeeded_total"] == 0
        assert m["tasks_failed_total"] == 0
        assert m["task_duration_seconds_sum"] == 0.0
