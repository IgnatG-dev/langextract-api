"""
Centralized logging configuration.

Provides structured JSON logging for production and human-readable
output for local development.  Import ``setup_logging`` early in
the application lifecycle (e.g. in ``main.py`` or ``celery_app.py``).
"""

from __future__ import annotations

import logging
import sys


def setup_logging(
    level: str = "INFO",
    *,
    json_format: bool = False,
) -> None:
    """Configure the root logger for the application.

    The JSON format includes a ``request_id`` placeholder that is
    populated by the ``RequestIDMiddleware`` when running inside
    the FastAPI process.

    Args:
        level: Logging level name (e.g. ``"INFO"``, ``"DEBUG"``).
        json_format: If ``True``, emit structured JSON lines.
            Recommended for containerised / production
            environments.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    if json_format:
        fmt = (
            '{"time":"%(asctime)s",'
            '"level":"%(levelname)s",'
            '"logger":"%(name)s",'
            '"request_id":"%(request_id)s",'
            '"message":"%(message)s"}'
        )
    else:
        fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt))

    # Add a filter that injects a default ``request_id`` so the
    # formatter never fails on a missing key.
    handler.addFilter(_RequestIDFilter())

    root = logging.getLogger()
    root.setLevel(log_level)

    # Avoid duplicate handlers on repeated calls
    root.handlers.clear()
    root.addHandler(handler)

    # Quiet noisy third-party loggers
    _silence_noisy_loggers(log_level)


class _RequestIDFilter(logging.Filter):
    """Inject ``request_id`` into every log record.

    Defaults to ``"-"`` when no request context is available
    (e.g. in Celery workers or CLI scripts).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Add ``request_id`` attribute to *record*.

        Args:
            record: The log record to augment.

        Returns:
            Always ``True`` (never suppress records).
        """
        if not hasattr(record, "request_id"):
            record.request_id = "-"  # type: ignore[attr-defined]
        return True


def _silence_noisy_loggers(app_level: int) -> None:
    """Reduce verbosity of third-party libraries.

    Args:
        app_level: The application's configured log level.
    """
    noisy = [
        "urllib3",
        "httpcore",
        "httpx",
        "celery.redirected",
        "celery.worker.strategy",
    ]
    for name in noisy:
        logging.getLogger(name).setLevel(
            max(app_level, logging.WARNING),
        )
