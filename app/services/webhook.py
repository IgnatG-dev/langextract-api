"""
Webhook delivery service.

Handles POSTing extraction results to caller-supplied callback
URLs with optional HMAC-SHA256 signing and automatic retries
via ``tenacity``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.core.security import (
    compute_webhook_signature,
    validate_url,
)

logger = logging.getLogger(__name__)

# Retry transient HTTP errors with exponential back-off:
#   attempt 1 → immediate
#   attempt 2 → wait ~1 s
#   attempt 3 → wait ~2 s
#   attempt 4 → wait ~4 s  (capped at 10 s)
_MAX_WEBHOOK_ATTEMPTS: int = 4
_WAIT_MIN: int = 1
_WAIT_MAX: int = 10


@retry(
    retry=retry_if_exception_type(
        (httpx.HTTPStatusError, httpx.TransportError),
    ),
    stop=stop_after_attempt(_MAX_WEBHOOK_ATTEMPTS),
    wait=wait_exponential(min=_WAIT_MIN, max=_WAIT_MAX),
    reraise=True,
)
def _deliver(
    url: str,
    body_bytes: bytes,
    headers: dict[str, str],
) -> None:
    """POST *body_bytes* to *url* and raise on failure.

    This internal helper is wrapped by ``tenacity`` so that
    transient failures (network errors, 5xx responses) are
    retried automatically.

    Args:
        url: The destination URL.
        body_bytes: JSON-encoded request body.
        headers: HTTP headers to include.
    """
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            url,
            content=body_bytes,
            headers=headers,
        )
        resp.raise_for_status()


def fire_webhook(
    callback_url: str,
    payload: dict[str, Any],
    *,
    extra_headers: dict[str, str] | None = None,
) -> None:
    """POST *payload* to *callback_url*, logging but never raising.

    Validates the URL against SSRF rules before sending.
    When ``WEBHOOK_SECRET`` is configured, an HMAC-SHA256
    signature is attached via ``X-Webhook-Signature`` and
    ``X-Webhook-Timestamp`` headers so receivers can verify
    authenticity.

    Callers can supply *extra_headers* (e.g. an
    ``Authorization`` bearer token) that will be merged into
    the outgoing request.

    Delivery is retried up to ``_MAX_WEBHOOK_ATTEMPTS`` times
    with exponential back-off for transient HTTP errors.

    Args:
        callback_url: The URL to POST to.
        payload: JSON-serialisable dict to send.
        extra_headers: Optional additional HTTP headers to
            include in the request.
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
    headers: dict[str, str] = {"Content-Type": "application/json"}

    body_bytes = json.dumps(payload).encode()

    if settings.WEBHOOK_SECRET:
        sig, ts = compute_webhook_signature(
            body_bytes,
            settings.WEBHOOK_SECRET,
        )
        headers["X-Webhook-Signature"] = sig
        headers["X-Webhook-Timestamp"] = str(ts)

    if extra_headers:
        headers.update(extra_headers)

    try:
        _deliver(callback_url, body_bytes, headers)
        logger.info(
            "Webhook delivered to %s",
            callback_url,
        )
    except RetryError:
        logger.error(
            "Webhook delivery to %s failed after %d attempts",
            callback_url,
            _MAX_WEBHOOK_ATTEMPTS,
        )
    except Exception as exc:
        logger.error(
            "Webhook delivery to %s failed: %s",
            callback_url,
            exc,
        )
