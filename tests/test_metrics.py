"""Tests for Prometheus metrics with Redis-backed counters."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from prometheus_client.core import (
    CounterMetricFamily,
    GaugeMetricFamily,
)

from app.core.metrics import (
    CeleryTaskCollector,
    generate_metrics,
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


class TestCeleryTaskCollector:
    """Tests for the custom Prometheus collector."""

    @patch("app.core.metrics.get_redis_client")
    def test_collect_returns_metric_families(self, mock_grc):
        """Collector yields proper metric families from Redis."""
        mock_client = MagicMock()
        mock_client.mget.return_value = [
            "10",
            "7",
            "3",
            "45.5",
        ]
        mock_grc.return_value = mock_client

        collector = CeleryTaskCollector()
        families = list(collector.collect())

        assert len(families) == 4

        # Counter families
        assert isinstance(families[0], CounterMetricFamily)
        assert families[0].name == "langextract_tasks_submitted"
        assert families[0].samples[0].value == 10

        assert isinstance(families[1], CounterMetricFamily)
        assert families[1].samples[0].value == 7

        assert isinstance(families[2], CounterMetricFamily)
        assert families[2].samples[0].value == 3

        # Duration gauge
        assert isinstance(families[3], GaugeMetricFamily)
        assert families[3].samples[0].value == 45.5

        mock_client.close.assert_called_once()

    @patch("app.core.metrics.get_redis_client")
    def test_collect_defaults_on_redis_failure(self, mock_grc):
        """Returns zeroed metrics when Redis is unavailable."""
        mock_grc.side_effect = Exception("no redis")

        collector = CeleryTaskCollector()
        families = list(collector.collect())

        assert len(families) == 4
        for family in families:
            assert family.samples[0].value == 0

    @patch("app.core.metrics.get_redis_client")
    def test_collect_handles_none_values(self, mock_grc):
        """Missing keys (None from mget) default to 0."""
        mock_client = MagicMock()
        mock_client.mget.return_value = [
            None,
            None,
            None,
            None,
        ]
        mock_grc.return_value = mock_client

        collector = CeleryTaskCollector()
        families = list(collector.collect())

        for family in families:
            assert family.samples[0].value == 0


class TestGenerateMetrics:
    """Tests for ``generate_metrics`` exposition."""

    @patch("app.core.metrics.get_redis_client")
    def test_returns_prometheus_format(self, mock_grc):
        """Output contains Prometheus HELP/TYPE lines."""
        mock_client = MagicMock()
        mock_client.mget.return_value = [
            "5",
            "3",
            "1",
            "12.0",
        ]
        mock_grc.return_value = mock_client

        output = generate_metrics().decode()

        assert "langextract_tasks_submitted_total" in output
        assert "langextract_tasks_succeeded_total" in output
        assert "langextract_tasks_failed_total" in output
        assert "langextract_task_duration_seconds_sum" in output
        assert "# HELP" in output
        assert "# TYPE" in output

    def test_registry_has_collector_registered(self):
        """The shared REGISTRY contains our custom collector."""
        # Verify the registry can produce output without error
        data = generate_metrics()
        assert isinstance(data, bytes)
        assert len(data) > 0
