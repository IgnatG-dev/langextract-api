"""Unit tests for task helpers and integration tests for extraction tasks.

Tests cover:
- ``_build_examples()`` — dict → ExampleData conversion
- ``_resolve_api_key()`` — API key selection logic
- ``_is_openai_model()`` — OpenAI model detection
- ``_convert_extractions()`` — AnnotatedDocument → dict conversion
- ``_extract_token_usage()`` — token usage extraction
- ``fire_webhook()`` — webhook delivery with HMAC signing
- ``run_extraction()`` — full extraction pipeline (mocked)
- ``extract_document`` / ``finalize_batch`` Celery tasks
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import (
    FakeAnnotatedDocument,
    FakeCharInterval,
    FakeExtraction,
)

# ── _build_examples ─────────────────────────────────────────


class TestBuildExamples:
    """Tests for the ``_build_examples`` helper."""

    def test_converts_single_example(self):
        """A single example dict is converted to ExampleData."""
        from app.services.extractor import _build_examples

        raw = [
            {
                "text": "Contract text here",
                "extractions": [
                    {
                        "extraction_class": "party",
                        "extraction_text": "Acme",
                        "attributes": {"role": "Buyer"},
                    },
                ],
            },
        ]
        result = _build_examples(raw)

        assert len(result) == 1
        assert result[0].text == "Contract text here"
        assert len(result[0].extractions) == 1
        assert result[0].extractions[0].extraction_class == "party"
        assert result[0].extractions[0].extraction_text == "Acme"

    def test_converts_multiple_examples(self):
        """Multiple example dicts produce multiple objects."""
        from app.services.extractor import _build_examples

        raw = [
            {"text": "First", "extractions": []},
            {"text": "Second", "extractions": []},
        ]
        result = _build_examples(raw)
        assert len(result) == 2
        assert result[0].text == "First"
        assert result[1].text == "Second"

    def test_empty_list_returns_empty(self):
        """An empty input list returns an empty output list."""
        from app.services.extractor import _build_examples

        assert _build_examples([]) == []

    def test_missing_extractions_key_defaults_to_empty(self):
        """If 'extractions' key is absent, default to []."""
        from app.services.extractor import _build_examples

        raw = [{"text": "No extractions key here"}]
        result = _build_examples(raw)
        assert len(result) == 1
        assert result[0].extractions == []

    def test_attributes_are_optional(self):
        """Extraction dicts without 'attributes' still work."""
        from app.services.extractor import _build_examples

        raw = [
            {
                "text": "Test",
                "extractions": [
                    {
                        "extraction_class": "date",
                        "extraction_text": "Jan 1",
                    },
                ],
            },
        ]
        result = _build_examples(raw)
        assert result[0].extractions[0].attributes is None


# ── _resolve_api_key ────────────────────────────────────────


class TestResolveApiKey:
    """Tests for the ``_resolve_api_key`` helper."""

    def test_returns_openai_key_for_gpt_models(
        self,
        mock_settings,
    ):
        """GPT model names resolve to OPENAI_API_KEY."""
        from app.services.extractor import _resolve_api_key

        with patch(
            "app.services.extractor.get_settings",
            return_value=mock_settings,
        ):
            assert _resolve_api_key("gpt-4o") == "test-openai-key"
            assert _resolve_api_key("GPT-4-turbo") == "test-openai-key"
            assert _resolve_api_key("openai/gpt-4o") == "test-openai-key"

    def test_returns_langextract_key_for_gemini(
        self,
        mock_settings,
    ):
        """Gemini models prefer LANGEXTRACT_API_KEY."""
        from app.services.extractor import _resolve_api_key

        mock_settings.LANGEXTRACT_API_KEY = "lx-key"
        with patch(
            "app.services.extractor.get_settings",
            return_value=mock_settings,
        ):
            assert _resolve_api_key("gemini-2.5-flash") == "lx-key"

    def test_falls_back_to_gemini_key(
        self,
        mock_settings,
    ):
        """If no LANGEXTRACT_API_KEY, fall back to GEMINI."""
        from app.services.extractor import _resolve_api_key

        mock_settings.LANGEXTRACT_API_KEY = ""
        with patch(
            "app.services.extractor.get_settings",
            return_value=mock_settings,
        ):
            assert _resolve_api_key("gemini-2.5-flash") == "test-gemini-key"

    def test_returns_none_when_no_keys(
        self,
        mock_settings,
    ):
        """If no keys configured, return None."""
        from app.services.extractor import _resolve_api_key

        mock_settings.LANGEXTRACT_API_KEY = ""
        mock_settings.GEMINI_API_KEY = ""
        mock_settings.OPENAI_API_KEY = ""
        with patch(
            "app.services.extractor.get_settings",
            return_value=mock_settings,
        ):
            assert _resolve_api_key("gemini-2.5-flash") is None
            assert _resolve_api_key("gpt-4o") is None


# ── _is_openai_model ────────────────────────────────────────


class TestIsOpenaiModel:
    """Tests for the ``_is_openai_model`` helper."""

    def test_detects_gpt_models(self):
        """Model IDs containing 'gpt' are OpenAI."""
        from app.services.extractor import _is_openai_model

        assert _is_openai_model("gpt-4o") is True
        assert _is_openai_model("GPT-4-turbo") is True
        assert _is_openai_model("gpt-4o-mini") is True

    def test_detects_openai_prefix(self):
        """Model IDs containing 'openai' are OpenAI."""
        from app.services.extractor import _is_openai_model

        assert _is_openai_model("openai/gpt-4o") is True

    def test_rejects_non_openai_models(self):
        """Gemini and other models are not OpenAI."""
        from app.services.extractor import _is_openai_model

        assert _is_openai_model("gemini-2.5-flash") is False
        assert _is_openai_model("claude-3-opus") is False
        assert _is_openai_model("llama-3") is False


# ── _convert_extractions ────────────────────────────────────


class TestConvertExtractions:
    """Tests for the ``_convert_extractions`` helper."""

    def test_converts_annotated_document(
        self,
        fake_annotated_document,
    ):
        """A populated AnnotatedDocument produces expected dicts."""
        from app.services.extractor import (
            _convert_extractions,
        )

        entities = _convert_extractions(
            fake_annotated_document,
        )

        assert len(entities) == 2

        party = entities[0]
        assert party["extraction_class"] == "party"
        assert party["extraction_text"] == "Acme Corp"
        assert party["attributes"] == {"role": "Seller"}
        assert party["char_start"] == 14
        assert party["char_end"] == 23

        date = entities[1]
        assert date["extraction_class"] == "date"
        assert date["extraction_text"] == "January 1, 2025"

    def test_empty_document_returns_empty_list(self):
        """An AnnotatedDocument with no extractions returns []."""
        from app.services.extractor import (
            _convert_extractions,
        )

        empty_doc = FakeAnnotatedDocument(
            text="nothing",
            extractions=[],
        )
        assert _convert_extractions(empty_doc) == []

    def test_handles_none_extractions(self):
        """If extractions is None, return an empty list."""
        from app.services.extractor import (
            _convert_extractions,
        )

        doc = FakeAnnotatedDocument(
            text="test",
            extractions=None,
        )
        assert _convert_extractions(doc) == []

    def test_handles_missing_char_interval(self):
        """Extractions without char_interval get None offsets."""
        from app.services.extractor import (
            _convert_extractions,
        )

        doc = FakeAnnotatedDocument(
            text="test",
            extractions=[
                FakeExtraction(
                    extraction_class="term",
                    extraction_text="30 days",
                    char_interval=None,
                ),
            ],
        )
        entities = _convert_extractions(doc)
        assert entities[0]["char_start"] is None
        assert entities[0]["char_end"] is None

    def test_handles_none_attributes(self):
        """Extractions with attributes=None get an empty dict."""
        from app.services.extractor import (
            _convert_extractions,
        )

        doc = FakeAnnotatedDocument(
            text="test",
            extractions=[
                FakeExtraction(
                    extraction_class="party",
                    extraction_text="Corp",
                    attributes=None,
                    char_interval=FakeCharInterval(0, 4),
                ),
            ],
        )
        entities = _convert_extractions(doc)
        assert entities[0]["attributes"] == {}


# ── _extract_token_usage ────────────────────────────────────


class TestExtractTokenUsage:
    """Tests for the ``_extract_token_usage`` helper."""

    def test_returns_none_for_no_usage(self):
        """Documents without usage info return None."""
        from app.services.extractor import (
            _extract_token_usage,
        )

        doc = FakeAnnotatedDocument(text="test")
        assert _extract_token_usage(doc) is None

    def test_extracts_from_object_attribute(self):
        """If usage.total_tokens exists, extract it."""
        from app.services.extractor import (
            _extract_token_usage,
        )

        doc = FakeAnnotatedDocument(text="test")
        usage = MagicMock()
        usage.total_tokens = 42
        doc.usage = usage  # type: ignore[attr-defined]
        assert _extract_token_usage(doc) == 42

    def test_extracts_from_dict_usage(self):
        """If usage is a dict with total_tokens, extract it."""
        from app.services.extractor import (
            _extract_token_usage,
        )

        doc = FakeAnnotatedDocument(text="test")
        doc.usage = {  # type: ignore[attr-defined]
            "total_tokens": 99,
        }
        assert _extract_token_usage(doc) == 99


# ── fire_webhook ────────────────────────────────────────────


class TestFireWebhook:
    """Tests for the ``fire_webhook`` helper."""

    @patch("app.services.webhook.httpx.Client")
    @patch(
        "app.services.webhook.validate_url",
        return_value="ok",
    )
    @patch("app.services.webhook.get_settings")
    def test_successful_delivery(
        self,
        mock_gs,
        mock_validate,
        mock_client_cls,
    ):
        """Webhook is delivered via POST with JSON payload."""
        from app.services.webhook import fire_webhook

        mock_gs.return_value.WEBHOOK_SECRET = ""

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__ = lambda s: mock_client
        mock_client_cls.return_value.__exit__ = MagicMock(
            return_value=False,
        )

        fire_webhook(
            "https://example.com/hook",
            {"task_id": "abc"},
        )

        mock_client.post.assert_called_once()
        mock_resp.raise_for_status.assert_called_once()

    @patch("app.services.webhook.httpx.Client")
    @patch(
        "app.services.webhook.validate_url",
        return_value="ok",
    )
    @patch("app.services.webhook.get_settings")
    def test_failure_does_not_raise(
        self,
        mock_gs,
        mock_validate,
        mock_client_cls,
    ):
        """Webhook failures are logged but never re-raised."""
        from app.services.webhook import fire_webhook

        mock_gs.return_value.WEBHOOK_SECRET = ""

        mock_client = MagicMock()
        mock_client.post.side_effect = Exception(
            "Connection refused",
        )
        mock_client_cls.return_value.__enter__ = lambda s: mock_client
        mock_client_cls.return_value.__exit__ = MagicMock(
            return_value=False,
        )

        # Should not raise
        fire_webhook(
            "https://example.com/hook",
            {"task_id": "abc"},
        )

    @patch("app.services.webhook.validate_url")
    def test_ssrf_blocked_url_is_not_sent(
        self,
        mock_validate,
    ):
        """Webhook to SSRF-blocked URL is not delivered."""
        from app.services.webhook import fire_webhook

        mock_validate.side_effect = ValueError("blocked")

        # Should not raise
        fire_webhook(
            "http://127.0.0.1/hook",
            {"task_id": "abc"},
        )

    @patch("app.services.webhook.httpx.Client")
    @patch(
        "app.services.webhook.validate_url",
        return_value="ok",
    )
    @patch("app.services.webhook.get_settings")
    def test_hmac_headers_added_when_secret_set(
        self,
        mock_gs,
        mock_validate,
        mock_client_cls,
    ):
        """HMAC signature headers are added when WEBHOOK_SECRET is set."""
        from app.services.webhook import fire_webhook

        mock_gs.return_value.WEBHOOK_SECRET = "my-secret"

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__ = lambda s: mock_client
        mock_client_cls.return_value.__exit__ = MagicMock(
            return_value=False,
        )

        fire_webhook(
            "https://example.com/hook",
            {"task_id": "abc"},
        )

        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get(
            "headers",
            call_kwargs[1].get("headers", {}),
        )
        assert "X-Webhook-Signature" in headers
        assert "X-Webhook-Timestamp" in headers

    @patch("app.services.webhook.httpx.Client")
    @patch(
        "app.services.webhook.validate_url",
        return_value="ok",
    )
    @patch("app.services.webhook.get_settings")
    def test_extra_headers_are_merged(
        self,
        mock_gs,
        mock_validate,
        mock_client_cls,
    ):
        """Caller-supplied extra_headers appear in the request."""
        from app.services.webhook import fire_webhook

        mock_gs.return_value.WEBHOOK_SECRET = ""

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__ = lambda s: mock_client
        mock_client_cls.return_value.__exit__ = MagicMock(
            return_value=False,
        )

        fire_webhook(
            "https://example.com/hook",
            {"task_id": "abc"},
            extra_headers={"Authorization": "Bearer tok-xyz"},
        )

        call_kwargs = mock_client.post.call_args
        headers = call_kwargs.kwargs.get(
            "headers",
            call_kwargs[1].get("headers", {}),
        )
        assert headers["Authorization"] == "Bearer tok-xyz"
        assert headers["Content-Type"] == "application/json"


# ── run_extraction (integration with mocked lx.extract) ────


class TestRunExtraction:
    """Integration tests for ``run_extraction``."""

    def test_returns_completed_result(
        self,
        mock_settings,
        mock_lx_extract,
    ):
        """Successful extraction returns a result dict."""
        from app.services.extractor import run_extraction

        with patch(
            "app.services.extractor.get_settings",
            return_value=mock_settings,
        ):
            result = run_extraction(
                task_self=None,
                raw_text=("Agreement by Acme Corp dated January 1, 2025"),
                provider="gpt-4o",
                passes=1,
            )

        assert result["status"] == "completed"
        assert result["source"] == "<raw_text>"
        assert len(result["data"]["entities"]) == 2
        assert result["data"]["metadata"]["provider"] == "gpt-4o"
        assert result["data"]["metadata"]["processing_time_ms"] >= 0

    def test_tokens_used_is_none_when_unavailable(
        self,
        mock_settings,
        mock_lx_extract,
    ):
        """tokens_used is None when lx result has no usage info."""
        from app.services.extractor import run_extraction

        with patch(
            "app.services.extractor.get_settings",
            return_value=mock_settings,
        ):
            result = run_extraction(
                task_self=None,
                raw_text="test",
            )

        assert result["data"]["metadata"]["tokens_used"] is None

    def test_uses_default_prompt_and_examples(
        self,
        mock_settings,
        mock_lx_extract,
    ):
        """Without extraction_config, defaults are used."""
        from app.core.defaults import (
            DEFAULT_PROMPT_DESCRIPTION,
        )
        from app.services.extractor import run_extraction

        with patch(
            "app.services.extractor.get_settings",
            return_value=mock_settings,
        ):
            run_extraction(
                task_self=None,
                raw_text="some text",
            )

        call_kwargs = mock_lx_extract.call_args.kwargs
        assert call_kwargs["prompt_description"] == DEFAULT_PROMPT_DESCRIPTION
        assert call_kwargs["text_or_documents"] == "some text"

    def test_custom_extraction_config(
        self,
        mock_settings,
        mock_lx_extract,
    ):
        """Custom prompt, temp, etc. are forwarded to lx.extract."""
        from app.services.extractor import run_extraction

        cfg: dict[str, Any] = {
            "prompt_description": "Custom prompt",
            "examples": [
                {"text": "x", "extractions": []},
            ],
            "temperature": 0.5,
            "additional_context": "Extra info",
            "max_workers": 5,
        }

        with patch(
            "app.services.extractor.get_settings",
            return_value=mock_settings,
        ):
            run_extraction(
                task_self=None,
                raw_text="test",
                extraction_config=cfg,
            )

        call_kwargs = mock_lx_extract.call_args.kwargs
        assert call_kwargs["prompt_description"] == "Custom prompt"
        assert call_kwargs["temperature"] == 0.5
        assert call_kwargs["additional_context"] == "Extra info"
        assert call_kwargs["max_workers"] == 5

    def test_openai_flags_set_for_gpt(
        self,
        mock_settings,
        mock_lx_extract,
    ):
        """GPT models get fence_output=True."""
        from app.services.extractor import run_extraction

        with patch(
            "app.services.extractor.get_settings",
            return_value=mock_settings,
        ):
            run_extraction(
                task_self=None,
                raw_text="test",
                provider="gpt-4o",
            )

        call_kwargs = mock_lx_extract.call_args.kwargs
        assert call_kwargs["fence_output"] is True
        assert call_kwargs["use_schema_constraints"] is False

    def test_gemini_does_not_set_openai_flags(
        self,
        mock_settings,
        mock_lx_extract,
    ):
        """Gemini models do NOT get OpenAI-specific flags."""
        from app.services.extractor import run_extraction

        with patch(
            "app.services.extractor.get_settings",
            return_value=mock_settings,
        ):
            run_extraction(
                task_self=None,
                raw_text="test",
                provider="gemini-2.5-flash",
            )

        call_kwargs = mock_lx_extract.call_args.kwargs
        assert "fence_output" not in call_kwargs
        assert "use_schema_constraints" not in call_kwargs

    def test_document_url_preferred_over_raw_text(
        self,
        mock_settings,
        mock_lx_extract,
    ):
        """When document_url is provided, it is downloaded and used."""
        from app.services.extractor import run_extraction

        with (
            patch(
                "app.services.extractor.get_settings",
                return_value=mock_settings,
            ),
            patch(
                "app.services.extractor.download_document",
                return_value="downloaded content",
            ),
        ):
            result = run_extraction(
                task_self=None,
                document_url=("https://example.com/doc.pdf"),
                raw_text="fallback text",
            )

        call_kwargs = mock_lx_extract.call_args.kwargs
        assert call_kwargs["text_or_documents"] == "downloaded content"
        assert result["source"] == "https://example.com/doc.pdf"

    def test_progress_updates_with_task_self(
        self,
        mock_settings,
        mock_lx_extract,
    ):
        """When task_self is provided, update_state is called."""
        from app.services.extractor import run_extraction

        mock_task = MagicMock()
        with patch(
            "app.services.extractor.get_settings",
            return_value=mock_settings,
        ):
            run_extraction(
                task_self=mock_task,
                raw_text="test",
            )

        assert mock_task.update_state.call_count >= 3
        steps = [
            c.kwargs["meta"]["step"] for c in mock_task.update_state.call_args_list
        ]
        assert "preparing" in steps
        assert "extracting" in steps
        assert "post_processing" in steps

    def test_handles_list_result_from_lx(
        self,
        mock_settings,
    ):
        """If lx.extract returns a list, first element is used."""
        from app.services.extractor import run_extraction

        doc = FakeAnnotatedDocument(
            text="test",
            extractions=[
                FakeExtraction(
                    extraction_class="party",
                    extraction_text="Corp",
                ),
            ],
        )
        with (
            patch(
                "app.services.extractor.get_settings",
                return_value=mock_settings,
            ),
            patch(
                "app.services.extractor.lx.extract",
                return_value=[doc],
            ),
        ):
            result = run_extraction(
                task_self=None,
                raw_text="test",
            )

        assert len(result["data"]["entities"]) == 1

    def test_handles_empty_list_result(
        self,
        mock_settings,
    ):
        """If lx.extract returns an empty list, no entities."""
        from app.services.extractor import run_extraction

        with (
            patch(
                "app.services.extractor.get_settings",
                return_value=mock_settings,
            ),
            patch(
                "app.services.extractor.lx.extract",
                return_value=[],
            ),
            patch(
                "app.services.extractor.lx.data.AnnotatedDocument",
            ) as mock_ad,
        ):
            mock_ad.return_value = FakeAnnotatedDocument()
            result = run_extraction(
                task_self=None,
                raw_text="test",
            )

        assert result["data"]["entities"] == []


# ── extract_document task ──────────────────────────────────


class TestExtractDocumentTask:
    """Tests for the ``extract_document`` Celery task."""

    def test_calls_run_extraction(self, mock_settings):
        """The task delegates to run_extraction."""
        from app.workers.tasks import extract_document

        mock_result = {
            "status": "completed",
            "source": "<raw_text>",
            "data": {"entities": []},
        }

        extract_document.push_request(id="task-id-123")
        try:
            with patch(
                "app.workers.tasks.run_extraction",
                return_value=mock_result,
            ) as mock_run:
                result = extract_document.run(
                    raw_text="test contract text",
                    provider="gpt-4o",
                )
        finally:
            extract_document.pop_request()

        mock_run.assert_called_once()
        assert result["status"] == "completed"

    def test_fires_webhook_on_success(
        self,
        mock_settings,
    ):
        """Webhook triggered when callback_url is provided."""
        from app.workers.tasks import extract_document

        mock_result = {
            "status": "completed",
            "source": "<raw_text>",
            "data": {"entities": []},
        }

        extract_document.push_request(id="task-id-456")
        try:
            with (
                patch(
                    "app.workers.tasks.run_extraction",
                    return_value=mock_result,
                ),
                patch(
                    "app.workers.tasks.fire_webhook",
                ) as mock_webhook,
            ):
                extract_document.run(
                    raw_text="test",
                    callback_url=("https://hook.example.com/done"),
                )
        finally:
            extract_document.pop_request()

        mock_webhook.assert_called_once()
        webhook_url = mock_webhook.call_args[0][0]
        assert webhook_url == "https://hook.example.com/done"

    def test_retries_on_failure(self, mock_settings):
        """The task retries on exception."""
        from app.workers.tasks import extract_document

        extract_document.push_request(id="task-id-789")
        try:
            with (
                patch(
                    "app.workers.tasks.run_extraction",
                    side_effect=RuntimeError("API error"),
                ),
                pytest.raises(
                    RuntimeError,
                    match="API error",
                ),
            ):
                extract_document.run(raw_text="test")
        finally:
            extract_document.pop_request()

    def test_does_not_record_failure_on_retry(
        self,
        mock_settings,
    ):
        """Metric failure is NOT recorded when retries remain."""
        from app.workers.tasks import extract_document

        extract_document.push_request(
            id="task-retry-1",
            retries=0,
        )
        try:
            with (
                patch(
                    "app.workers.tasks.run_extraction",
                    side_effect=RuntimeError("API error"),
                ),
                patch(
                    "app.workers.tasks.record_task_completed",
                ) as mock_metric,
                pytest.raises(RuntimeError),
            ):
                extract_document.run(raw_text="test")
        finally:
            extract_document.pop_request()

        # Not yet final → no failure metric
        mock_metric.assert_not_called()

    def test_records_failure_on_final_retry(
        self,
        mock_settings,
    ):
        """Metric failure IS recorded when retries exhausted."""
        from app.workers.tasks import extract_document

        extract_document.push_request(
            id="task-final-1",
            retries=extract_document.max_retries,
        )
        try:
            with (
                patch(
                    "app.workers.tasks.run_extraction",
                    side_effect=RuntimeError("final"),
                ),
                patch(
                    "app.workers.tasks.record_task_completed",
                ) as mock_metric,
                pytest.raises(RuntimeError),
            ):
                extract_document.run(raw_text="test")
        finally:
            extract_document.pop_request()

        mock_metric.assert_called_once()
        assert mock_metric.call_args.kwargs["success"] is False
        assert mock_metric.call_args.kwargs["duration_s"] > 0

    def test_stores_result_in_redis(self, mock_settings):
        """Successful extraction stores result under Redis key."""
        from app.workers.tasks import extract_document

        mock_result = {
            "status": "completed",
            "source": "<raw_text>",
            "data": {"entities": []},
        }

        extract_document.push_request(id="task-redis-1")
        try:
            with (
                patch(
                    "app.workers.tasks.run_extraction",
                    return_value=mock_result,
                ),
                patch(
                    "app.workers.tasks._store_result_in_redis",
                ) as mock_store,
            ):
                extract_document.run(raw_text="test")
        finally:
            extract_document.pop_request()

        mock_store.assert_called_once_with(
            "task-redis-1",
            mock_result,
        )

    def test_passes_callback_headers_to_webhook(
        self,
        mock_settings,
    ):
        """callback_headers are forwarded to fire_webhook."""
        from app.workers.tasks import extract_document

        mock_result = {
            "status": "completed",
            "source": "<raw_text>",
            "data": {"entities": []},
        }
        headers = {"Authorization": "Bearer tok123"}

        extract_document.push_request(id="task-hdr-1")
        try:
            with (
                patch(
                    "app.workers.tasks.run_extraction",
                    return_value=mock_result,
                ),
                patch(
                    "app.workers.tasks.fire_webhook",
                ) as mock_wh,
            ):
                extract_document.run(
                    raw_text="test",
                    callback_url="https://hook.example.com/done",
                    callback_headers=headers,
                )
        finally:
            extract_document.pop_request()

        mock_wh.assert_called_once()
        assert mock_wh.call_args.kwargs["extra_headers"] == headers


# ── finalize_batch task ─────────────────────────────────────


def _make_mock_children(
    results: list[dict],
    errors: list[int] | None = None,
) -> list[MagicMock]:
    """Build a list of mock ``AsyncResult`` objects.

    Args:
        results: Per-child result dicts (for successful children).
        errors: Zero-based indices of children that should fail.

    Returns:
        A list of ``MagicMock`` instances mimicking
        ``AsyncResult``.
    """
    errors = errors or []
    children = []
    for i, res in enumerate(results):
        child = MagicMock()
        child.id = f"child-{i}"
        if i in errors:
            child.successful.return_value = False
            child.ready.return_value = True
            child.result = RuntimeError(
                res.get("error", "fail"),
            )
        else:
            child.successful.return_value = True
            child.ready.return_value = True
            child.result = res
        children.append(child)
    return children


class TestFinalizeBatchTask:
    """Tests for the ``finalize_batch`` Celery task."""

    def test_aggregates_all_successful(
        self,
        mock_settings,
    ):
        """All children succeed → full aggregated result."""
        from app.workers.tasks import finalize_batch

        ok = {
            "status": "completed",
            "source": "<raw_text>",
            "data": {"entities": []},
        }
        docs = [
            {"raw_text": "Doc A"},
            {"raw_text": "Doc B"},
            {"raw_text": "Doc C"},
        ]
        children = _make_mock_children([ok, ok, ok])

        finalize_batch.push_request(id="fin-task-1")
        try:
            with (
                patch(
                    "app.workers.tasks.AsyncResult",
                    side_effect=children,
                ),
                patch(
                    "celery.app.task.Task.update_state",
                ),
            ):
                result = finalize_batch.run(
                    batch_id="batch-001",
                    child_task_ids=[
                        "child-0",
                        "child-1",
                        "child-2",
                    ],
                    documents=docs,
                )
        finally:
            finalize_batch.pop_request()

        assert result["status"] == "completed"
        assert result["batch_id"] == "batch-001"
        assert result["total"] == 3
        assert result["successful"] == 3
        assert result["failed"] == 0
        assert len(result["results"]) == 3
        assert result["document_task_ids"] == [
            "child-0",
            "child-1",
            "child-2",
        ]

    def test_handles_partial_failure(
        self,
        mock_settings,
    ):
        """Failed children are captured in errors list."""
        from app.workers.tasks import finalize_batch

        ok = {
            "status": "completed",
            "source": "<raw_text>",
            "data": {"entities": []},
        }
        fail = {"error": "Extraction failed"}
        children = _make_mock_children(
            [ok, fail],
            errors=[1],
        )

        docs = [
            {"raw_text": "Good doc"},
            {"raw_text": "Bad doc"},
        ]

        finalize_batch.push_request(id="fin-task-2")
        try:
            with (
                patch(
                    "app.workers.tasks.AsyncResult",
                    side_effect=children,
                ),
                patch(
                    "celery.app.task.Task.update_state",
                ),
            ):
                result = finalize_batch.run(
                    batch_id="batch-003",
                    child_task_ids=[
                        "child-0",
                        "child-1",
                    ],
                    documents=docs,
                )
        finally:
            finalize_batch.pop_request()

        assert result["successful"] == 1
        assert result["failed"] == 1
        assert len(result["errors"]) == 1

    def test_fires_batch_webhook(self, mock_settings):
        """Batch-level webhook is triggered on completion."""
        from app.workers.tasks import finalize_batch

        ok = {
            "status": "completed",
            "source": "<raw_text>",
            "data": {"entities": []},
        }
        children = _make_mock_children([ok])

        finalize_batch.push_request(id="fin-task-3")
        try:
            with (
                patch(
                    "app.workers.tasks.AsyncResult",
                    side_effect=children,
                ),
                patch(
                    "celery.app.task.Task.update_state",
                ),
                patch(
                    "app.workers.tasks.fire_webhook",
                ) as mock_webhook,
            ):
                finalize_batch.run(
                    batch_id="batch-004",
                    child_task_ids=["child-0"],
                    documents=[{"raw_text": "Doc"}],
                    callback_url=("https://hook.example.com/batch"),
                )
        finally:
            finalize_batch.pop_request()

        mock_webhook.assert_called_once()
        assert mock_webhook.call_args[0][0] == ("https://hook.example.com/batch")

    def test_returns_document_task_ids(
        self,
        mock_settings,
    ):
        """Batch result includes per-document child task IDs."""
        from app.workers.tasks import finalize_batch

        ok = {
            "status": "completed",
            "source": "<raw_text>",
            "data": {"entities": []},
        }
        docs = [{"raw_text": "A"}, {"raw_text": "B"}]
        children = _make_mock_children([ok, ok])

        finalize_batch.push_request(id="fin-task-4")
        try:
            with (
                patch(
                    "app.workers.tasks.AsyncResult",
                    side_effect=children,
                ),
                patch(
                    "celery.app.task.Task.update_state",
                ),
            ):
                result = finalize_batch.run(
                    batch_id="batch-005",
                    child_task_ids=[
                        "child-0",
                        "child-1",
                    ],
                    documents=docs,
                )
        finally:
            finalize_batch.pop_request()

        assert result["document_task_ids"] == [
            "child-0",
            "child-1",
        ]

    def test_retries_when_children_pending(
        self,
        mock_settings,
    ):
        """Task retries itself when children are not ready."""
        from celery.exceptions import Retry

        from app.workers.tasks import finalize_batch

        pending = MagicMock()
        pending.id = "child-0"
        pending.ready.return_value = False

        finalize_batch.push_request(
            id="fin-task-5",
            retries=0,
        )
        try:
            with (
                patch(
                    "app.workers.tasks.AsyncResult",
                    return_value=pending,
                ),
                patch(
                    "celery.app.task.Task.update_state",
                ),
                pytest.raises(Retry),
            ):
                finalize_batch.run(
                    batch_id="batch-006",
                    child_task_ids=["child-0"],
                    documents=[{"raw_text": "A"}],
                )
        finally:
            finalize_batch.pop_request()


# ── _run_lx_extract_with_retry ─────────────────────────────────────────


class TestLxExtractRetry:
    """Tests for the ``_run_lx_extract_with_retry`` helper."""

    def test_succeeds_on_first_attempt(self):
        """Returns result immediately when no error occurs."""
        from app.services.extractor import (
            _run_lx_extract_with_retry,
        )

        expected = MagicMock()
        with patch(
            "app.services.extractor.lx.extract",
            return_value=expected,
        ):
            result = _run_lx_extract_with_retry(
                {"text_or_documents": "hi"},
                "<test>",
                2,
            )

        assert result is expected

    def test_retries_on_value_error_then_succeeds(self):
        """Retries once and succeeds on the second attempt."""
        from app.services.extractor import (
            _run_lx_extract_with_retry,
        )

        expected = MagicMock()
        with patch(
            "app.services.extractor.lx.extract",
            side_effect=[
                ValueError("Found: <class 'dict'>"),
                expected,
            ],
        ):
            result = _run_lx_extract_with_retry(
                {"text_or_documents": "hi"},
                "<test>",
                2,
            )

        assert result is expected

    def test_raises_after_all_retries_exhausted(self):
        """Re-raises ValueError after max_retries + 1 attempts."""
        from app.services.extractor import (
            _run_lx_extract_with_retry,
        )

        with (
            patch(
                "app.services.extractor.lx.extract",
                side_effect=ValueError("bad output"),
            ),
            pytest.raises(ValueError, match="bad output"),
        ):
            _run_lx_extract_with_retry(
                {"text_or_documents": "hi"},
                "<test>",
                2,
            )

    def test_non_value_error_not_retried(self):
        """Non-ValueError exceptions propagate immediately."""
        from app.services.extractor import (
            _run_lx_extract_with_retry,
        )

        with (
            patch(
                "app.services.extractor.lx.extract",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError, match="boom"),
        ):
            _run_lx_extract_with_retry(
                {"text_or_documents": "hi"},
                "<test>",
                2,
            )
