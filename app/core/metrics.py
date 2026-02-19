"""
Redis-backed metrics counters.

Stores counters in Redis using atomic ``INCR`` / ``INCRBYFLOAT``
so that values are consistent across FastAPI and Celery worker
processes (and across multiple Uvicorn workers).

Both the Celery worker tasks and the FastAPI health/metrics
endpoint import from this module, avoiding circular dependencies
between the API and worker layers.
"""

from __future__ import annotations

import logging

from app.core.config import get_redis_client

logger = logging.getLogger(__name__)

# Redis key prefix for all metric counters.
_PREFIX = "metrics:"

_SUBMITTED_KEY = f"{_PREFIX}tasks_submitted_total"
_SUCCEEDED_KEY = f"{_PREFIX}tasks_succeeded_total"
_FAILED_KEY = f"{_PREFIX}tasks_failed_total"
_DURATION_KEY = f"{_PREFIX}task_duration_seconds_sum"


def record_task_submitted() -> None:
    """Increment the submitted-task counter.

    Called from the extraction router on every
    ``POST /extract``.
    """
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
    """Record a task completion event.

    Called from Celery task wrappers after ``run_extraction``
    finishes (success or failure).

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


def get_metrics() -> dict[str, float | int]:
    """Return a snapshot of current metrics from Redis.

    Returns:
        A dict with counter names as keys.  Missing keys
        default to ``0``.
    """
    defaults: dict[str, float | int] = {
        "tasks_submitted_total": 0,
        "tasks_succeeded_total": 0,
        "tasks_failed_total": 0,
        "task_duration_seconds_sum": 0.0,
    }
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
        return {
            "tasks_submitted_total": int(vals[0] or 0),
            "tasks_succeeded_total": int(vals[1] or 0),
            "tasks_failed_total": int(vals[2] or 0),
            "task_duration_seconds_sum": float(vals[3] or 0),
        }
    except Exception:
        logger.warning(
            "Failed to read metrics from Redis",
            exc_info=True,
        )
        return defaults
