"""
Centralized logging configuration powered by structlog.

Provides structured JSON logging for production and
human-readable coloured output for local development.
Import ``setup_logging`` early in the application
lifecycle (e.g. in ``main.py`` or ``celery_app.py``).

Context variables (``request_id``, etc.) are injected
via ``structlog.contextvars`` — see the
``RequestIDMiddleware`` in ``app.main``.
"""

from __future__ import annotations

import logging
import sys

import structlog


def setup_logging(
    level: str = "INFO",
    *,
    json_format: bool = False,
) -> None:
    """Configure structlog + stdlib root logger.

    structlog is configured as a **wrapper** around stdlib
    ``logging`` so that every existing ``logging.getLogger()``
    call throughout the codebase automatically benefits from
    structured output and context-variable injection.

    Args:
        level: Logging level name (e.g. ``"INFO"``,
            ``"DEBUG"``).
        json_format: If ``True``, emit structured JSON lines.
            Recommended for containerised / production
            environments.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Shared processors used by both structlog-native loggers
    # and stdlib loggers that pass through structlog.
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_format:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # ── Configure structlog ─────────────────────────────────
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # ── Configure stdlib root logger ────────────────────────
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()
    root.addHandler(handler)

    # Quiet noisy third-party loggers
    _silence_noisy_loggers(log_level)


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
        # LiteLLM stdlib loggers (various capitalisation)
        "LiteLLM",
        "litellm",
        "LiteLLM Proxy",
        "LiteLLM Router",
    ]
    for name in noisy:
        logging.getLogger(name).setLevel(
            max(app_level, logging.WARNING),
        )

    # ── Suppress per-call noise ─────────────────────────────
    # "Provider List" is a print() in litellm, not a logger.
    # Setting suppress_debug_info silences it.
    try:
        import litellm as _litellm

        _litellm.suppress_debug_info = True
    except ImportError:
        pass

    # Prompt alignment warnings use absl.logging (logger name "absl").
    # These repeat per-pass for few-shot examples and aren't diagnostic.
    logging.getLogger("absl").setLevel(logging.ERROR)

    # Per-chunk audit JSON (langcore_audit.LoggingSink) is INFO-level.
    # Suppress to WARNING so only errors/warnings from audit are shown.
    logging.getLogger("langcore.audit").setLevel(logging.WARNING)
