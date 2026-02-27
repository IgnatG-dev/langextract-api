"""
Document download service with timeout, size, and content
enforcement.

Downloads documents from user-supplied URLs, respecting
``DOC_DOWNLOAD_TIMEOUT`` and ``DOC_DOWNLOAD_MAX_BYTES``
settings.

Only plain-text and Markdown content is accepted.  Validation
is three-layered:

1. **Extension guardrail** — reject URLs whose path clearly
   ends with a binary extension (e.g. ``.pdf``, ``.docx``).
2. **Content-Type allowlist** — reject anything outside a
   narrow set of text MIME types.
3. **Byte-sniff** — inspect the first 512 bytes for binary
   signatures (``%PDF-``, ``PK\\x03\\x04``, ``GIF``,
   ``\\x1f\\x8b`` GZIP, null bytes …) so that a lying
   ``Content-Type`` cannot smuggle binary data.

Each redirect hop is re-validated against the SSRF rules in
``app.core.security`` so that a "safe" URL cannot 302-redirect
the worker to a private IP or metadata endpoint.

The URL itself is also re-validated at the start of
``download_document`` as a defence-in-depth measure.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.core.security import validate_url
from app.core.url_utils import mask_url

logger = logging.getLogger(__name__)

# Maximum number of redirects to follow per download request.
_MAX_REDIRECTS: int = 5


class DownloadTooLargeError(Exception):
    """Raised when the downloaded content exceeds the size limit."""


class UnsafeRedirectError(Exception):
    """Raised when a redirect target fails SSRF validation."""


class UnsupportedContentTypeError(Exception):
    """Raised when the response Content-Type is not text-based."""


class BinaryContentError(Exception):
    """Raised when byte-sniffing detects binary content."""


class UnsupportedExtensionError(Exception):
    """Raised when the URL path has a known binary extension."""


# ── Strict allowlist ────────────────────────────────────────
# Only plain-text and Markdown are accepted.  Every other MIME
# type — including application/octet-stream and missing
# Content-Type — is rejected.

_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/x-markdown",
        "text/md",
        "application/markdown",
    }
)

# File extensions accepted by the UX guardrail.  This is *not*
# the security boundary (Content-Type + sniffing handle that),
# but it gives callers an immediate, descriptive error when the
# URL clearly points to a binary file.
_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".text",
    }
)


def _is_allowed_content_type(
    content_type: str | None,
) -> bool:
    """Check whether *content_type* is in the strict allowlist.

    Args:
        content_type: Raw ``Content-Type`` header value,
            possibly including parameters (e.g.
            ``text/plain; charset=utf-8``).

    Returns:
        ``True`` only when the base MIME type is in
        ``_ALLOWED_CONTENT_TYPES``.
    """
    if not content_type:
        return False
    base = content_type.split(";")[0].strip().lower()
    return base in _ALLOWED_CONTENT_TYPES


def _looks_like_text(data: bytes) -> bool:
    """Return ``True`` if *data* appears to be genuine text.

    Rejects well-known binary signatures and the presence of
    null bytes, which are a strong indicator of binary content.

    Args:
        data: The first N bytes of the downloaded content
            (typically 512).

    Returns:
        ``True`` if no binary indicators were found.
    """
    # Well-known binary file signatures
    if data.startswith(b"%PDF-"):
        return False
    if data.startswith(b"PK\x03\x04"):  # ZIP/DOCX/XLSX
        return False
    if data.startswith(b"\x89PNG"):  # PNG
        return False
    if data[:2] == b"\xff\xd8":  # JPEG
        return False
    if data[:4] == b"\x7fELF":  # ELF executable
        return False
    if data[:3] == b"GIF":  # GIF (GIF87a / GIF89a)
        return False
    if data[:2] == b"\x1f\x8b":  # GZIP
        return False

    # Null bytes are a reliable binary indicator
    return b"\x00" not in data


def _ssrf_safe_redirect_handler(
    response: httpx.Response,
) -> None:
    """Validate each redirect target against SSRF rules.

    This is used as an httpx *response* event hook.  When the
    server returns a 3xx redirect, httpx resolves the
    ``Location`` header *before* calling this hook on the
    redirect response.  We intercept and validate the next
    URL that httpx will follow.

    Args:
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
        url: The document URL to fetch.  Re-validated against
            SSRF rules here as a defence-in-depth measure.

    Returns:
        The decoded document text.

    Raises:
        ValueError: If the URL fails SSRF validation.
        UnsupportedExtensionError: If the URL path has a
            known binary extension.
        UnsafeRedirectError: If any redirect target fails the
            SSRF check.
        UnsupportedContentTypeError: If the Content-Type is
            outside the allowed set.
        DownloadTooLargeError: If the response exceeds the
            configured max bytes.
        BinaryContentError: If byte-sniffing detects binary
            content or decoding fails.
        httpx.HTTPStatusError: On non-2xx responses.
        httpx.TimeoutException: On timeout.
    """
    # ── Defence-in-depth URL re-validation ──────────────────
    # The API layer already validates URLs, but re-check here
    # in case a task was enqueued by other means (misconfig,
    # internal call, future queue consumer).
    validate_url(url, purpose="document_url")

    # ── UX guardrail: reject obvious binary extensions ──────
    # This is not the security boundary (Content-Type +
    # sniffing handle that), but it saves a round-trip and
    # gives the caller a clear error message.
    path = urlparse(url).path
    dot_idx = path.rfind(".")
    ext = path[dot_idx:].lower() if dot_idx != -1 else ""
    if not ext or ext not in _ALLOWED_EXTENSIONS:
        raise UnsupportedExtensionError(
            f"URL extension '{ext or '<none>'}' is not accepted. "
            "Only .txt and .md URLs are allowed. "
            "Convert other files to text before "
            "submitting."
        )

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

        # ── Content-Type strict allowlist ───────────────────
        # Reject everything outside the narrow set of text
        # MIME types.  Missing Content-Type and
        # application/octet-stream are no longer tolerated.
        raw_ct = response.headers.get("content-type", "")
        if not _is_allowed_content_type(raw_ct):
            mime = raw_ct.split(";")[0].strip().lower() if raw_ct else "<missing>"
            raise UnsupportedContentTypeError(
                f"Unsupported Content-Type '{mime}'. "
                "Only plain-text and Markdown "
                "documents are accepted."
            )

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

    # ── Byte-sniff: reject binary content ───────────────────
    # Content-Type can lie, so inspect the first 512 bytes for
    # binary signatures (PDF, ZIP/DOCX, PNG, JPEG, ELF) and
    # null bytes.
    if not _looks_like_text(body[:512]):
        raise BinaryContentError(
            "Document appears to be binary, not text. "
            "Only plain-text and Markdown content is accepted."
        )

    # Strict decode — refuse garbled / binary-heavy bodies.
    charset = response.charset_encoding or "utf-8"
    try:
        text = body.decode(charset, errors="strict")
    except (UnicodeDecodeError, LookupError) as exc:
        raise BinaryContentError(
            f"Failed to decode document as {charset}. "
            "Only valid UTF-8 / ASCII text is accepted."
        ) from exc

    logger.info(
        "Downloaded %d bytes from %s",
        len(body),
        mask_url(url),
    )
    return text
