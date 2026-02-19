"""
Document download service with timeout and size enforcement.

Downloads documents from user-supplied URLs, respecting
``DOC_DOWNLOAD_TIMEOUT`` and ``DOC_DOWNLOAD_MAX_BYTES`` settings.

Each redirect hop is re-validated against the SSRF rules in
``app.core.security`` so that a "safe" URL cannot 302-redirect
the worker to a private IP or metadata endpoint.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings
from app.core.security import validate_url

logger = logging.getLogger(__name__)

# Maximum number of redirects to follow per download request.
_MAX_REDIRECTS: int = 5


class DownloadTooLargeError(Exception):
    """Raised when the downloaded content exceeds the size limit."""


class UnsafeRedirectError(Exception):
    """Raised when a redirect target fails SSRF validation."""


def _ssrf_safe_redirect_handler(
    request: httpx.Request,
    response: httpx.Response,
) -> None:
    """Validate each redirect target against SSRF rules.

    This is used as an httpx *response* event hook.  When the
    server returns a 3xx redirect, httpx resolves the
    ``Location`` header *before* calling this hook on the
    redirect response.  We intercept and validate the next
    URL that httpx will follow.

    Args:
        request: The outgoing request that produced *response*.
        response: The HTTP response (may be a 3xx redirect).

    Raises:
        UnsafeRedirectError: If the redirect target fails SSRF
            validation.
    """
    if response.next_request is not None:
        target = str(response.next_request.url)
        try:
            validate_url(target, purpose="redirect target")
        except ValueError as exc:
            raise UnsafeRedirectError(
                f"Redirect to {target} blocked by SSRF check: {exc}"
            ) from exc


def download_document(url: str) -> str:
    """Download document text from *url* with safety limits.

    Follows redirects (up to ``_MAX_REDIRECTS``), re-validating
    every hop against the SSRF rules so that a "safe" initial URL
    cannot 302-redirect the worker to a private IP.

    Streams the response and aborts early if the body exceeds
    ``DOC_DOWNLOAD_MAX_BYTES``.

    Args:
        url: The document URL to fetch (already SSRF-validated
            at the API layer).

    Returns:
        The decoded document text.

    Raises:
        UnsafeRedirectError: If any redirect target fails the
            SSRF check.
        DownloadTooLargeError: If the response exceeds the
            configured max bytes.
        httpx.HTTPStatusError: On non-2xx responses.
        httpx.TimeoutException: On timeout.
    """
    settings = get_settings()
    timeout = settings.DOC_DOWNLOAD_TIMEOUT
    max_bytes = settings.DOC_DOWNLOAD_MAX_BYTES

    with (
        httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
            event_hooks={
                "response": [_ssrf_safe_redirect_handler],
            },
        ) as client,
        client.stream("GET", url) as response,
    ):
        response.raise_for_status()

        # Check Content-Length header first (untrusted but
        # allows an early exit without reading the body).
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > max_bytes:
            raise DownloadTooLargeError(
                f"Content-Length ({content_length}) exceeds limit of {max_bytes} bytes."
            )

        chunks: list[bytes] = []
        received = 0
        for chunk in response.iter_bytes(chunk_size=65_536):
            received += len(chunk)
            if received > max_bytes:
                raise DownloadTooLargeError(
                    f"Download exceeded {max_bytes} bytes (received {received} so far)."
                )
            chunks.append(chunk)

    body = b"".join(chunks)

    # Best-effort decode: honour the response charset, fall
    # back to UTF-8 with replacement for binary-heavy docs.
    charset = response.charset_encoding or "utf-8"
    text = body.decode(charset, errors="replace")

    logger.info(
        "Downloaded %d bytes from %s",
        len(body),
        url,
    )
    return text
