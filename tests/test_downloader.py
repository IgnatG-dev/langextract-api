"""Tests for the document download service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.downloader import (
    DownloadTooLargeError,
    UnsafeRedirectError,
    _ssrf_safe_redirect_handler,
    download_document,
)


class TestDownloadDocument:
    """Tests for ``download_document``."""

    @patch("app.services.downloader.get_settings")
    @patch("app.services.downloader.httpx.Client")
    def test_successful_download(
        self,
        mock_client_cls,
        mock_gs,
    ):
        """Downloads text content and returns decoded string."""
        mock_gs.return_value.DOC_DOWNLOAD_TIMEOUT = 30
        mock_gs.return_value.DOC_DOWNLOAD_MAX_BYTES = 1_000_000

        mock_response = MagicMock()
        mock_response.headers = {"content-length": "11"}
        mock_response.charset_encoding = "utf-8"
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_bytes.return_value = [b"hello world"]
        mock_response.__enter__ = lambda s: mock_response
        mock_response.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream.return_value = mock_response
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = download_document("https://example.com/doc.txt")

        assert result == "hello world"

    @patch("app.services.downloader.get_settings")
    @patch("app.services.downloader.httpx.Client")
    def test_rejects_oversized_content_length(
        self,
        mock_client_cls,
        mock_gs,
    ):
        """Rejects early when Content-Length exceeds limit."""
        mock_gs.return_value.DOC_DOWNLOAD_TIMEOUT = 30
        mock_gs.return_value.DOC_DOWNLOAD_MAX_BYTES = 100

        mock_response = MagicMock()
        mock_response.headers = {"content-length": "5000"}
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_bytes.return_value = []
        mock_response.__enter__ = lambda s: mock_response
        mock_response.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream.return_value = mock_response
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(
            DownloadTooLargeError,
            match=r"Content-Length",
        ):
            download_document("https://example.com/big.bin")

    @patch("app.services.downloader.get_settings")
    @patch("app.services.downloader.httpx.Client")
    def test_rejects_oversized_streaming_body(
        self,
        mock_client_cls,
        mock_gs,
    ):
        """Rejects when streamed bytes exceed limit."""
        mock_gs.return_value.DOC_DOWNLOAD_TIMEOUT = 30
        mock_gs.return_value.DOC_DOWNLOAD_MAX_BYTES = 10

        mock_response = MagicMock()
        mock_response.headers = {}  # no content-length
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_bytes.return_value = [
            b"A" * 20,
        ]
        mock_response.__enter__ = lambda s: mock_response
        mock_response.__exit__ = MagicMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream.return_value = mock_response
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(
            DownloadTooLargeError,
            match=r"exceeded",
        ):
            download_document("https://example.com/big.bin")


class TestSsrfSafeRedirectHandler:
    """Tests for the ``_ssrf_safe_redirect_handler`` hook."""

    def test_allows_safe_redirect(self):
        """Redirect to a public URL passes without error."""
        request = MagicMock(spec=httpx.Request)
        response = MagicMock(spec=httpx.Response)
        next_req = MagicMock(spec=httpx.Request)
        next_req.url = httpx.URL(
            "https://cdn.example.com/doc.pdf",
        )
        response.next_request = next_req

        with patch(
            "app.services.downloader.validate_url",
            return_value="ok",
        ):
            # Should not raise
            _ssrf_safe_redirect_handler(request, response)

    def test_blocks_redirect_to_private_ip(self):
        """Redirect to a private IP raises UnsafeRedirectError."""
        request = MagicMock(spec=httpx.Request)
        response = MagicMock(spec=httpx.Response)
        next_req = MagicMock(spec=httpx.Request)
        next_req.url = httpx.URL("http://169.254.169.254/meta")
        response.next_request = next_req

        with (
            patch(
                "app.services.downloader.validate_url",
                side_effect=ValueError("private IP"),
            ),
            pytest.raises(
                UnsafeRedirectError,
                match="blocked by SSRF",
            ),
        ):
            _ssrf_safe_redirect_handler(request, response)

    def test_no_redirect_is_noop(self):
        """Non-redirect response (next_request is None) passes."""
        request = MagicMock(spec=httpx.Request)
        response = MagicMock(spec=httpx.Response)
        response.next_request = None

        # Should not raise
        _ssrf_safe_redirect_handler(request, response)
