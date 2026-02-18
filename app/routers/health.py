"""Health-check routes (liveness & readiness probes)."""

from fastapi import APIRouter

from app.schemas import CeleryHealthResponse, HealthResponse
from app.worker import celery_app

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """Liveness probe — returns OK if the web process is running."""
    return HealthResponse(status="ok", version="0.1.0")


@router.get("/health/celery", response_model=CeleryHealthResponse)
def celery_health_check() -> CeleryHealthResponse:
    """Readiness probe — checks Celery worker availability."""
    try:
        inspect = celery_app.control.inspect()
        stats = inspect.stats()
        active = inspect.active()

        if stats is None:
            return CeleryHealthResponse(
                status="unhealthy",
                message="No Celery workers available",
                workers=[],
            )

        workers = [
            {
                "name": name,
                "status": "online",
                "active_tasks": (len(active.get(name, [])) if active else 0),
            }
            for name in stats
        ]

        return CeleryHealthResponse(
            status="healthy",
            message=f"{len(workers)} worker(s) online",
            workers=workers,
        )
    except Exception as exc:
        return CeleryHealthResponse(
            status="unhealthy",
            message=f"Error connecting to Celery: {exc}",
            workers=[],
        )
