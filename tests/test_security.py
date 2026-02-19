"""Unit tests for ``app.core.security`` — SSRF protection and HMAC."""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
from unittest.mock import patch

import pytest

from app.core.security import (
    _is_private_ip,
    compute_webhook_signature,
    validate_url,
)

# ── _is_private_ip ──────────────────────────────────────────


class TestIsPrivateIp:
    """Tests for the ``_is_private_ip`` helper."""

    @patch("app.core.security.socket.getaddrinfo")
    def test_blocks_loopback(self, mock_gai):
        """Loopback addresses (127.x) are blocked."""
        mock_gai.return_value = [
            (2, 1, 6, "", ("127.0.0.1", 0)),
        ]
        assert _is_private_ip("localhost") is True

    @patch("app.core.security.socket.getaddrinfo")
    def test_blocks_private_10(self, mock_gai):
        """10.x.x.x addresses are blocked."""
        mock_gai.return_value = [
            (2, 1, 6, "", ("10.0.0.5", 0)),
        ]
        assert _is_private_ip("internal.corp") is True

    @patch("app.core.security.socket.getaddrinfo")
    def test_blocks_private_172(self, mock_gai):
        """172.16.x.x addresses are blocked."""
        mock_gai.return_value = [
            (2, 1, 6, "", ("172.16.0.1", 0)),
        ]
        assert _is_private_ip("internal.corp") is True

    @patch("app.core.security.socket.getaddrinfo")
    def test_blocks_private_192(self, mock_gai):
        """192.168.x.x addresses are blocked."""
        mock_gai.return_value = [
            (2, 1, 6, "", ("192.168.1.1", 0)),
        ]
        assert _is_private_ip("internal.corp") is True

    @patch("app.core.security.socket.getaddrinfo")
    def test_blocks_link_local(self, mock_gai):
        """169.254.x.x (link-local) addresses are blocked."""
        mock_gai.return_value = [
            (2, 1, 6, "", ("169.254.1.1", 0)),
        ]
        assert _is_private_ip("link-local.host") is True

    @patch("app.core.security.socket.getaddrinfo")
    def test_blocks_ipv6_loopback(self, mock_gai):
        """IPv6 loopback (::1) is blocked."""
        mock_gai.return_value = [
            (10, 1, 6, "", ("::1", 0, 0, 0)),
        ]
        assert _is_private_ip("localhost") is True

    @patch("app.core.security.socket.getaddrinfo")
    def test_allows_public_ip(self, mock_gai):
        """Public IP addresses are allowed."""
        mock_gai.return_value = [
            (2, 1, 6, "", ("93.184.216.34", 0)),
        ]
        assert _is_private_ip("example.com") is False

    @patch("app.core.security.socket.getaddrinfo")
    def test_dns_failure_blocks(self, mock_gai):
        """DNS resolution failure is treated as blocked."""
        import socket

        mock_gai.side_effect = socket.gaierror(
            "No such host",
        )
        assert _is_private_ip("nonexistent.invalid") is True


# ── validate_url ────────────────────────────────────────────


class TestValidateUrl:
    """Tests for the ``validate_url`` function."""

    @patch(
        "app.core.security._is_private_ip",
        return_value=False,
    )
    @patch("app.core.security.get_settings")
    def test_accepts_valid_https_url(
        self,
        mock_gs,
        mock_priv,
    ):
        """A valid HTTPS URL passes validation."""
        mock_gs.return_value.ALLOWED_URL_DOMAINS = []
        result = validate_url(
            "https://example.com/doc.pdf",
        )
        assert result == "https://example.com/doc.pdf"

    @patch(
        "app.core.security._is_private_ip",
        return_value=False,
    )
    @patch("app.core.security.get_settings")
    def test_accepts_valid_http_url(
        self,
        mock_gs,
        mock_priv,
    ):
        """A valid HTTP URL passes validation."""
        mock_gs.return_value.ALLOWED_URL_DOMAINS = []
        assert validate_url("http://example.com/doc")

    def test_rejects_ftp_scheme(self):
        """FTP scheme is rejected."""
        with pytest.raises(ValueError, match="Invalid scheme"):
            validate_url("ftp://example.com/file")

    def test_rejects_file_scheme(self):
        """file:// scheme is rejected."""
        with pytest.raises(ValueError, match="Invalid scheme"):
            validate_url("file:///etc/passwd")

    def test_rejects_empty_hostname(self):
        """URLs without a hostname are rejected."""
        with pytest.raises(
            ValueError,
            match="Cannot extract hostname",
        ):
            validate_url("https:///path")

    @patch(
        "app.core.security._is_private_ip",
        return_value=False,
    )
    @patch("app.core.security.get_settings")
    def test_domain_allowlist_accepts_listed(
        self,
        mock_gs,
        mock_priv,
    ):
        """Domains on the allow-list pass validation."""
        mock_gs.return_value.ALLOWED_URL_DOMAINS = [
            "trusted.com",
        ]
        result = validate_url("https://trusted.com/doc")
        assert result == "https://trusted.com/doc"

    @patch("app.core.security.get_settings")
    def test_domain_allowlist_rejects_unlisted(
        self,
        mock_gs,
    ):
        """Domains not on the allow-list are rejected."""
        mock_gs.return_value.ALLOWED_URL_DOMAINS = [
            "trusted.com",
        ]
        with pytest.raises(
            ValueError,
            match="not in the allowed domains",
        ):
            validate_url("https://evil.com/doc")

    @patch(
        "app.core.security._is_private_ip",
        return_value=True,
    )
    @patch("app.core.security.get_settings")
    def test_rejects_private_ip(self, mock_gs, mock_priv):
        """URLs resolving to private IPs are rejected."""
        mock_gs.return_value.ALLOWED_URL_DOMAINS = []
        with pytest.raises(
            ValueError,
            match="private/reserved IP",
        ):
            validate_url("https://internal.corp/doc")

    def test_rejects_localhost_hostname(self):
        """Explicit 'localhost' hostname is blocked."""
        with pytest.raises(
            ValueError,
            match=r"[Ll]ocalhost",
        ):
            validate_url("https://localhost/secret")

    def test_rejects_overly_long_url(self):
        """URLs exceeding the max length are rejected."""
        long = "https://example.com/" + "a" * 2100
        with pytest.raises(ValueError, match="too long"):
            validate_url(long)

    @patch(
        "app.core.security._is_private_ip",
        return_value=False,
    )
    @patch("app.core.security.get_settings")
    def test_subdomain_matching(self, mock_gs, mock_priv):
        """Subdomain ``sub.trusted.com`` matches allow-list."""
        mock_gs.return_value.ALLOWED_URL_DOMAINS = [
            "trusted.com",
        ]
        result = validate_url(
            "https://sub.trusted.com/doc",
        )
        assert result == "https://sub.trusted.com/doc"


# ── compute_webhook_signature ───────────────────────────────


class TestComputeWebhookSignature:
    """Tests for ``compute_webhook_signature``."""

    def test_returns_hex_and_timestamp(self):
        """Returns a (hex_string, timestamp) tuple."""
        sig, ts = compute_webhook_signature(
            b'{"key": "value"}',
            "secret",
            timestamp=1000,
        )
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA-256 hex = 64 chars
        assert ts == 1000

    def test_deterministic_with_fixed_timestamp(self):
        """Same inputs produce the same signature."""
        payload = b'{"task_id": "abc"}'
        sig1, _ = compute_webhook_signature(
            payload,
            "mysecret",
            timestamp=9999,
        )
        sig2, _ = compute_webhook_signature(
            payload,
            "mysecret",
            timestamp=9999,
        )
        assert sig1 == sig2

    def test_different_secret_produces_different_sig(self):
        """Different secrets produce different signatures."""
        payload = b'{"x": 1}'
        sig1, _ = compute_webhook_signature(
            payload,
            "secret-a",
            timestamp=1,
        )
        sig2, _ = compute_webhook_signature(
            payload,
            "secret-b",
            timestamp=1,
        )
        assert sig1 != sig2

    def test_signature_matches_manual_hmac(self):
        """Signature matches a manually computed HMAC-SHA256."""
        payload = b'{"test": true}'
        secret = "my-key"
        ts = 12345
        expected_msg = f"{ts}.".encode() + payload
        expected = hmac_mod.new(
            secret.encode(),
            expected_msg,
            hashlib.sha256,
        ).hexdigest()

        sig, _ = compute_webhook_signature(
            payload,
            secret,
            timestamp=ts,
        )
        assert sig == expected

    def test_auto_timestamp_when_not_provided(self):
        """Timestamp is auto-generated when omitted."""
        sig, ts = compute_webhook_signature(
            b"payload",
            "secret",
        )
        assert ts > 0
        assert isinstance(sig, str)
