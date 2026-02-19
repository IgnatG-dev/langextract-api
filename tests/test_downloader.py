"""Tests for the document download service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.downloader import (
    DownloadTooLargeError,
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
