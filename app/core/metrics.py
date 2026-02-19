"""
In-process metrics counters.

Provides simple thread-safe counters for task-level observability.
Both the Celery worker tasks and the FastAPI health/metrics
endpoint import from this module, avoiding circular dependencies
between the API and worker layers.
"""

from __future__ import annotations

import threading

# Thread lock protects counter mutations from concurrent
# Celery worker threads and the FastAPI event loop.
_lock = threading.Lock()

_metrics: dict[str, float | int] = {
    "tasks_submitted_total": 0,
    "tasks_succeeded_total": 0,
    "tasks_failed_total": 0,
    "task_duration_seconds_sum": 0.0,
}


def record_task_submitted() -> None:
    """Increment the submitted-task counter.

    Called from the extraction router on every
    ``POST /extract``.
    """
    with _lock:
        _metrics["tasks_submitted_total"] += 1


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
    with _lock:
        if success:
            _metrics["tasks_succeeded_total"] += 1
        else:
            _metrics["tasks_failed_total"] += 1
        _metrics["task_duration_seconds_sum"] += duration_s


def get_metrics() -> dict[str, float | int]:
    """Return a snapshot of current metrics.

    Returns:
        A copy of the metrics dict (safe to read without
        holding the lock).
    """
    with _lock:
        return dict(_metrics)
