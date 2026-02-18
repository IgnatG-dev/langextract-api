"""
URL validation and SSRF protection utilities.

Prevents Server-Side Request Forgery by blocking requests to
private IP ranges, link-local addresses, and loopback addresses.
Also provides domain allow-listing for outbound URL fetching
and webhook delivery.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import socket
import time
from urllib.parse import urlparse

from app.dependencies import get_settings

logger = logging.getLogger(__name__)

# ── Private / dangerous IP ranges ───────────────────────────────────────────

_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    # IPv4 private ranges
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    # IPv4 loopback
    ipaddress.IPv4Network("127.0.0.0/8"),
    # IPv4 link-local
    ipaddress.IPv4Network("169.254.0.0/16"),
    # IPv4 carrier-grade NAT
    ipaddress.IPv4Network("100.64.0.0/10"),
    # IPv6 loopback
    ipaddress.IPv6Network("::1/128"),
    # IPv6 link-local
    ipaddress.IPv6Network("fe80::/10"),
    # IPv6 unique local
    ipaddress.IPv6Network("fc00::/7"),
    # IPv4-mapped IPv6
    ipaddress.IPv6Network("::ffff:0:0/96"),
]


def _is_private_ip(host: str) -> bool:
    """Check whether *host* resolves to a blocked IP range.

    Args:
        host: Hostname or IP address string.

    Returns:
        ``True`` if the resolved address falls within a blocked
        network, ``False`` otherwise.
    """
    try:
        addr_infos = socket.getaddrinfo(
            host, None, socket.AF_UNSPEC, socket.SOCK_STREAM,
        )
    except socket.gaierror:
        # If DNS resolution fails, block the request
        logger.warning("DNS resolution failed for host: %s", host)
        return True

    for family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for network in _BLOCKED_NETWORKS:
            if addr in network:
                logger.warning(
                    "Blocked SSRF attempt: %s resolved to %s "
                    "(%s)",
                    host,
                    ip_str,
                    network,
                )
                return True
    return False


def validate_url(url: str, *, purpose: str = "request") -> str:
    """Validate that *url* is safe for server-side fetching.

    Checks:
    - Scheme is ``http`` or ``https``.
    - Host does not resolve to a private / link‑local address.
    - Host is on the domain allow-list (when configured).

    Args:
        url: The URL string to validate.
        purpose: Human‑readable label for log messages
            (e.g. ``"document_url"``, ``"callback_url"``).

    Returns:
        The validated URL string (unchanged).

    Raises:
        ValueError: If the URL fails any safety check.
    """
    parsed = urlparse(url)

    # Scheme check
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"Invalid scheme '{parsed.scheme}' for {purpose}. "
            "Only http and https are allowed."
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(
            f"Cannot extract hostname from {purpose} URL."
        )

    # Domain allow-list
    settings = get_settings()
    allowed = settings.ALLOWED_URL_DOMAINS
    if allowed:
        if hostname not in allowed:
            raise ValueError(
                f"Domain '{hostname}' is not in the "
                f"allowed domains list for {purpose}."
            )

    # SSRF protection — resolve and check IPs
    if _is_private_ip(hostname):
        raise ValueError(
            f"URL for {purpose} resolves to a "
            "private/reserved IP address."
        )

    return url


# ── HMAC webhook signing ────────────────────────────────────────────────────


def compute_webhook_signature(
    payload_bytes: bytes,
    secret: str,
    *,
    timestamp: int | None = None,
) -> tuple[str, int]:
    """Compute an HMAC-SHA256 signature for a webhook payload.

    The signature covers ``{timestamp}.{payload_bytes}`` to prevent
    replay attacks.

    Args:
        payload_bytes: The raw JSON body bytes.
        secret: The shared HMAC secret string.
        timestamp: Unix epoch seconds. Defaults to ``int(time.time())``.

    Returns:
        A ``(signature_hex, timestamp)`` tuple.
    """
    if timestamp is None:
        timestamp = int(time.time())
    message = f"{timestamp}.".encode() + payload_bytes
    sig = hmac.new(
        secret.encode(),
        message,
        hashlib.sha256,
    ).hexdigest()
    return sig, timestamp
