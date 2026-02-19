"""
Prometheus metrics with Redis-backed cross-process counters.

FastAPI and Celery workers run in separate containers, so
in-process ``prometheus_client`` counters cannot be shared.
This module keeps Redis as the cross-process backing store
and exposes a ``CeleryTaskCollector`` custom Collector that
bridges Redis values into proper Prometheus metric families.

HTTP request metrics (latency, count, size) are handled
separately by ``prometheus-fastapi-instrumentator`` in
``app.main``.

Usage:
    Call ``record_task_submitted()`` / ``record_task_completed()``
    from any process.  On the FastAPI side the
    ``/metrics`` endpoint calls ``generate_latest(REGISTRY)``
    which invokes the custom collector automatically.
"""

from __future__ import annotations

import logging

from prometheus_client import (
    CollectorRegistry,
    generate_latest,
)
from prometheus_client.core import (
    CounterMetricFamily,
    GaugeMetricFamily,
)

from app.core.constants import REDIS_PREFIX_METRICS
from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)

# ── Redis keys (unchanged so existing data carries over) ────

_SUBMITTED_KEY = f"{REDIS_PREFIX_METRICS}tasks_submitted_total"
_SUCCEEDED_KEY = f"{REDIS_PREFIX_METRICS}tasks_succeeded_total"
_FAILED_KEY = f"{REDIS_PREFIX_METRICS}tasks_failed_total"
_DURATION_KEY = f"{REDIS_PREFIX_METRICS}task_duration_seconds_sum"


# ── Record helpers (called from any process) ────────────────


def record_task_submitted() -> None:
    """Increment the submitted-task counter in Redis."""
    try:
        client = get_redis_client()
        try:
            client.incr(_SUBMITTED_KEY)
        finally:
            client.close()
    except Exception:
        logger.warning(
            "Failed to record task_submitted metric",
            exc_info=True,
        )


def record_task_completed(
    *,
    success: bool,
    duration_s: float,
) -> None:
    """Record a task completion event in Redis.

    Args:
        success: ``True`` if the task succeeded.
        duration_s: Wall-clock duration in seconds.
    """
    try:
        client = get_redis_client()
        try:
            key = _SUCCEEDED_KEY if success else _FAILED_KEY
            client.incr(key)
            client.incrbyfloat(_DURATION_KEY, duration_s)
        finally:
            client.close()
    except Exception:
        logger.warning(
            "Failed to record task_completed metric",
            exc_info=True,
        )


# ── Prometheus custom collector ─────────────────────────────


class CeleryTaskCollector:
    """Read task metrics from Redis on each Prometheus scrape.

    Registered on a dedicated ``CollectorRegistry`` so that
    ``generate_latest(REGISTRY)`` automatically invokes
    ``collect()`` and renders proper Prometheus exposition
    format.
    """

    def collect(self):
        """Yield Prometheus metric families from Redis."""
        submitted = 0
        succeeded = 0
        failed = 0
        duration = 0.0

        try:
            client = get_redis_client()
            try:
                vals = client.mget(
                    _SUBMITTED_KEY,
                    _SUCCEEDED_KEY,
                    _FAILED_KEY,
                    _DURATION_KEY,
                )
            finally:
                client.close()
            submitted = int(vals[0] or 0)
            succeeded = int(vals[1] or 0)
            failed = int(vals[2] or 0)
            duration = float(vals[3] or 0)
        except Exception:
            logger.warning(
                "Failed to read metrics from Redis",
                exc_info=True,
            )

        c_sub = CounterMetricFamily(
            "langextract_tasks_submitted",
            "Total extraction tasks submitted.",
        )
        c_sub.add_metric([], submitted)
        yield c_sub

        c_ok = CounterMetricFamily(
            "langextract_tasks_succeeded",
            "Total extraction tasks that succeeded.",
        )
        c_ok.add_metric([], succeeded)
        yield c_ok

        c_fail = CounterMetricFamily(
            "langextract_tasks_failed",
            "Total extraction tasks that failed.",
        )
        c_fail.add_metric([], failed)
        yield c_fail

        g_dur = GaugeMetricFamily(
            "langextract_task_duration_seconds_sum",
            "Cumulative task processing time in seconds.",
        )
        g_dur.add_metric([], duration)
        yield g_dur


# ── Shared registry ─────────────────────────────────────────

#: Dedicated registry that avoids default-registry conflicts
#: with ``prometheus-fastapi-instrumentator`` (which uses the
#: default registry for HTTP metrics).
REGISTRY = CollectorRegistry()
REGISTRY.register(CeleryTaskCollector())


def generate_metrics() -> bytes:
    """Render Prometheus exposition format for task metrics.

    Returns:
        UTF-8 bytes ready to be served on ``/metrics``.
    """
    return generate_latest(REGISTRY)
