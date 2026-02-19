"""
Webhook delivery and task-result persistence service.

Handles:
- Persisting extraction results to Redis under a predictable key.
- POSTing results to caller-supplied callback URLs with optional
  HMAC-SHA256 signing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.core.config import get_redis_client, get_settings
from app.core.security import compute_webhook_signature, validate_url

logger = logging.getLogger(__name__)

# Redis key prefix for persisted task results
_RESULT_PREFIX = "task_result:"


def store_result(
    task_id: str,
    result: dict[str, Any],
) -> None:
    """Persist *result* under a predictable Redis key.

    The TTL is driven by ``Settings.RESULT_EXPIRES`` so it stays
    consistent with Celery's own result backend expiry.

    Args:
        task_id: The Celery task identifier.
        result: JSON-serialisable result dict.
    """
    try:
        settings = get_settings()
        client = get_redis_client()
        try:
            key = f"{_RESULT_PREFIX}{task_id}"
            client.setex(
                key,
                settings.RESULT_EXPIRES,
                json.dumps(result),
            )
        finally:
            client.close()
    except Exception as exc:
        logger.warning(
            "Failed to persist result for %s: %s",
            task_id,
            exc,
        )


def fire_webhook(
    callback_url: str,
    payload: dict[str, Any],
) -> None:
    """POST *payload* to *callback_url*, logging but never raising.

    Validates the URL against SSRF rules before sending.
    When ``WEBHOOK_SECRET`` is configured, an HMAC-SHA256
    signature is attached via ``X-Webhook-Signature`` and
    ``X-Webhook-Timestamp`` headers so receivers can verify
    authenticity.

    Args:
        callback_url: The URL to POST to.
        payload: JSON-serialisable dict to send.
    """
    try:
        validate_url(callback_url, purpose="callback_url")
    except ValueError as exc:
        logger.error(
            "Webhook URL blocked by SSRF check (%s): %s",
            callback_url,
            exc,
        )
        return

    settings = get_settings()
    headers: dict[str, str] = {}

    body_bytes = json.dumps(payload).encode()

    if settings.WEBHOOK_SECRET:
        sig, ts = compute_webhook_signature(
            body_bytes,
            settings.WEBHOOK_SECRET,
        )
        headers["X-Webhook-Signature"] = sig
        headers["X-Webhook-Timestamp"] = str(ts)

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                callback_url,
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    **headers,
                },
            )
            resp.raise_for_status()
        logger.info(
            "Webhook delivered to %s (status %s)",
            callback_url,
            resp.status_code,
        )
    except Exception as exc:
        logger.error(
            "Webhook delivery to %s failed: %s",
            callback_url,
            exc,
        )
