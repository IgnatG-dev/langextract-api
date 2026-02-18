"""
Celery application configuration.

Instantiates and configures the Celery app used by both the worker
process and the FastAPI application (for ``task.delay()`` calls).

Usage — start a worker::

    celery -A app.worker.celery_app worker --loglevel=info
"""

from celery import Celery

from app.dependencies import get_settings

settings = get_settings()

celery_app = Celery(
    "langextract-worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks"],
)

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # Reliability
    task_track_started=True,
    task_time_limit=settings.TASK_TIME_LIMIT,
    task_soft_time_limit=settings.TASK_SOFT_TIME_LIMIT,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,

    # Results
    result_expires=settings.RESULT_EXPIRES,

    # Retry policy for broker connection
    broker_connection_retry_on_startup=True,
)
