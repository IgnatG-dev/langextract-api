"""
Document download service with timeout and size enforcement.

Downloads documents from user-supplied URLs, respecting
``DOC_DOWNLOAD_TIMEOUT`` and ``DOC_DOWNLOAD_MAX_BYTES`` settings.
The SSRF validation in ``app.core.security`` must be run before
calling any function in this module.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class DownloadTooLargeError(Exception):
    """Raised when the downloaded content exceeds the size limit."""


def download_document(url: str) -> str:
    """Download document text from *url* with safety limits.

    Streams the response and aborts early if the body
    exceeds ``DOC_DOWNLOAD_MAX_BYTES``.

    Args:
        url: The document URL to fetch (already SSRF-validated).

    Returns:
        The decoded document text.

    Raises:
        DownloadTooLargeError: If the response exceeds the
            configured max bytes.
        httpx.HTTPStatusError: On non-2xx responses.
        httpx.TimeoutException: On timeout.
    """
    settings = get_settings()
    timeout = settings.DOC_DOWNLOAD_TIMEOUT
    max_bytes = settings.DOC_DOWNLOAD_MAX_BYTES

    with (
        httpx.Client(timeout=timeout, follow_redirects=True) as client,
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
