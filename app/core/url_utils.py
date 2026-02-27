"""URL utility functions for safe logging and display."""

from __future__ import annotations


def mask_url(url: str) -> str:
    """Strip query-string (signed tokens) from a URL for safe logging.

    Signed URLs from Supabase storage contain JWT tokens in the
    query string.  This helper replaces everything after ``?``
    with ``<token>`` so that log output never leaks secrets.

    Args:
        url: The full URL, possibly containing query parameters.

    Returns:
        The URL with query parameters replaced by ``<token>``,
        or the original URL if it has no query string.
    """
    idx = url.find("?")
    return f"{url[:idx]}?<token>" if idx != -1 else url
