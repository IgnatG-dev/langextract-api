"""Health-check routes (liveness, readiness, metrics)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.core.config import get_version
from app.core.metrics import get_metrics
from app.schemas import CeleryHealthResponse, HealthResponse
from app.workers.celery_app import celery_app

router = APIRouter(tags=["health"])

_version = get_version()


# ── Routes ──────────────────────────────────────────────────────


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """Liveness probe — returns OK if the web process runs."""
    return HealthResponse(status="ok", version=_version)


@router.get(
    "/health/celery",
    response_model=CeleryHealthResponse,
)
def celery_health_check() -> CeleryHealthResponse:
    """Readiness probe — checks Celery worker availability.

    Uses a thread-pool with a 5-second timeout to avoid
    hanging when the broker or workers are unreachable.
    """
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_inspect_workers)
            workers = future.result(timeout=5)

        if not workers:
            return CeleryHealthResponse(
                status="unhealthy",
                message="No Celery workers available",
                workers=[],
            )

        return CeleryHealthResponse(
            status="healthy",
            message=f"{len(workers)} worker(s) online",
            workers=workers,
        )
    except TimeoutError:
        return CeleryHealthResponse(
            status="degraded",
            message=("Celery inspect timed out — workers may be busy"),
            workers=[],
        )
    except Exception as exc:
        return CeleryHealthResponse(
            status="unhealthy",
            message=f"Error connecting to Celery: {exc}",
            workers=[],
        )


def _inspect_workers() -> list[dict[str, object]]:
    """Query Celery for online worker stats.

    Returns:
        A list of worker-info dicts, or an empty list when
        no workers are found.
    """
    inspect = celery_app.control.inspect(timeout=3)
    stats = inspect.stats()
    active = inspect.active()

    if stats is None:
        return []

    return [
        {
            "name": name,
            "status": "online",
            "active_tasks": (len(active.get(name, [])) if active else 0),
        }
        for name in stats
    ]


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    tags=["observability"],
)
def prometheus_metrics() -> str:
    """Expose basic Prometheus-format metrics.

    Returns counters for submitted, succeeded, and failed tasks
    as well as cumulative task duration.
    """
    m = get_metrics()
    lines = [
        "# HELP tasks_submitted_total Total extraction tasks submitted.",
        "# TYPE tasks_submitted_total counter",
        f"tasks_submitted_total {int(m['tasks_submitted_total'])}",
        "",
        "# HELP tasks_succeeded_total Total extraction tasks that succeeded.",
        "# TYPE tasks_succeeded_total counter",
        f"tasks_succeeded_total {int(m['tasks_succeeded_total'])}",
        "",
        "# HELP tasks_failed_total Total extraction tasks that failed.",
        "# TYPE tasks_failed_total counter",
        f"tasks_failed_total {int(m['tasks_failed_total'])}",
        "",
        "# HELP task_duration_seconds_sum Cumulative task processing time.",
        "# TYPE task_duration_seconds_sum counter",
        f"task_duration_seconds_sum {m['task_duration_seconds_sum']:.3f}",
    ]
    return "\n".join(lines) + "\n"
