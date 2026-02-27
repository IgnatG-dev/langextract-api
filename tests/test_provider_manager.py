"""Tests for the ProviderManager singleton."""

from __future__ import annotations

from unittest import mock

import pytest

from app.services.provider_manager import ProviderManager


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure a pristine singleton for every test."""
    ProviderManager.reset()
    yield
    ProviderManager.reset()


@pytest.fixture()
def _mock_settings(monkeypatch):
    """Patch ``get_settings`` so Redis config is deterministic."""
    fake = mock.MagicMock()
    fake.REDIS_HOST = "localhost"
    fake.REDIS_PORT = 6379
    fake.REDIS_DB = 0
    monkeypatch.setattr(
        "app.services.provider_manager.get_settings",
        lambda: fake,
    )
    return fake


class TestProviderManagerSingleton:
    def test_singleton_identity(self):
        a = ProviderManager.instance()
        b = ProviderManager.instance()
        assert a is b

    def test_reset_creates_new_instance(self):
        a = ProviderManager.instance()
        ProviderManager.reset()
        b = ProviderManager.instance()
        assert a is not b


class TestModelCaching:
    def test_same_key_returns_same_model(self):
        manager = ProviderManager.instance()
        with mock.patch(
            "app.services.provider_manager.factory.create_model"
        ) as mock_create:
            sentinel = mock.MagicMock()
            mock_create.return_value = sentinel

            m1 = manager.get_or_create_model("gpt-4o", api_key="k1")
            m2 = manager.get_or_create_model("gpt-4o", api_key="k1")

        assert m1 is m2
        assert mock_create.call_count == 1

    def test_different_key_creates_new_model(self):
        manager = ProviderManager.instance()
        with mock.patch(
            "app.services.provider_manager.factory.create_model"
        ) as mock_create:
            mock_create.side_effect = [mock.MagicMock(), mock.MagicMock()]

            m1 = manager.get_or_create_model("gpt-4o", api_key="k1")
            m2 = manager.get_or_create_model("gpt-4o", api_key="k2")

        assert m1 is not m2
        assert mock_create.call_count == 2

    def test_different_fence_output_creates_new_model(self):
        """Different ``fence_output`` should produce distinct cache entries."""
        manager = ProviderManager.instance()
        with mock.patch(
            "app.services.provider_manager.factory.create_model"
        ) as mock_create:
            mock_create.side_effect = [mock.MagicMock(), mock.MagicMock()]

            m1 = manager.get_or_create_model(
                "gpt-4o",
                api_key="k1",
                fence_output=True,
            )
            m2 = manager.get_or_create_model(
                "gpt-4o",
                api_key="k1",
                fence_output=False,
            )

        assert m1 is not m2
        assert mock_create.call_count == 2

    def test_different_schema_constraints_creates_new_model(self):
        """Different ``use_schema_constraints`` should produce distinct entries."""
        manager = ProviderManager.instance()
        with mock.patch(
            "app.services.provider_manager.factory.create_model"
        ) as mock_create:
            mock_create.side_effect = [mock.MagicMock(), mock.MagicMock()]

            m1 = manager.get_or_create_model(
                "gpt-4o",
                api_key="k1",
                use_schema_constraints=True,
            )
            m2 = manager.get_or_create_model(
                "gpt-4o",
                api_key="k1",
                use_schema_constraints=False,
            )

        assert m1 is not m2
        assert mock_create.call_count == 2

    def test_response_format_creates_distinct_entry(self):
        """Passing ``response_format`` should create a distinct cache entry."""
        manager = ProviderManager.instance()
        rf = {"type": "json_schema", "json_schema": {"name": "test"}}
        with mock.patch(
            "app.services.provider_manager.factory.create_model"
        ) as mock_create:
            mock_create.side_effect = [mock.MagicMock(), mock.MagicMock()]

            m1 = manager.get_or_create_model("gpt-4o", api_key="k1")
            m2 = manager.get_or_create_model(
                "gpt-4o",
                api_key="k1",
                response_format=rf,
            )

        assert m1 is not m2
        assert mock_create.call_count == 2

    def test_different_response_formats_create_distinct_entries(self):
        """Different ``response_format`` dicts must produce distinct entries.

        Regression test: previously the cache key only tracked *whether*
        ``response_format`` was set (bool), not *what* it contained.
        This caused all extraction groups to share the first group's
        schema, producing 0 entities for subsequent groups.
        """
        manager = ProviderManager.instance()
        rf_red_flags = {
            "type": "json_schema",
            "json_schema": {"name": "red_flags_schema", "schema": {"type": "object"}},
        }
        rf_key_dates = {
            "type": "json_schema",
            "json_schema": {"name": "key_dates_schema", "schema": {"type": "object"}},
        }
        with mock.patch(
            "app.services.provider_manager.factory.create_model"
        ) as mock_create:
            mock_create.side_effect = [mock.MagicMock(), mock.MagicMock()]

            m1 = manager.get_or_create_model(
                "gpt-4o",
                api_key="k1",
                response_format=rf_red_flags,
            )
            m2 = manager.get_or_create_model(
                "gpt-4o",
                api_key="k1",
                response_format=rf_key_dates,
            )

        assert m1 is not m2
        assert mock_create.call_count == 2

    def test_response_format_forwarded_to_provider_kwargs(self):
        """``response_format`` should appear in the model config."""
        manager = ProviderManager.instance()
        rf = {"type": "json_schema", "json_schema": {"name": "test"}}
        with mock.patch(
            "app.services.provider_manager.factory.create_model"
        ) as mock_create:
            mock_create.return_value = mock.MagicMock()
            manager.get_or_create_model(
                "gpt-4o",
                api_key="k1",
                response_format=rf,
            )

        call_args = mock_create.call_args
        config = call_args.kwargs.get("config") or call_args[1].get("config")
        assert config.provider_kwargs["response_format"] == rf

    def test_clear_empties_cache(self):
        manager = ProviderManager.instance()
        with mock.patch(
            "app.services.provider_manager.factory.create_model"
        ) as mock_create:
            mock_create.return_value = mock.MagicMock()
            manager.get_or_create_model("gpt-4o")
            assert len(manager._models) == 1
            manager.clear()
            assert len(manager._models) == 0


class TestLiteLLMCache:
    def test_ensure_cache_initialises_once(self, _mock_settings, monkeypatch):
        monkeypatch.setenv("EXTRACTION_CACHE_ENABLED", "true")
        manager = ProviderManager.instance()

        with mock.patch("app.services.provider_manager.litellm") as mock_litellm:
            mock_litellm.Cache.return_value = mock.MagicMock()
            manager.ensure_cache()
            manager.ensure_cache()  # second call should be a no-op

        assert mock_litellm.Cache.call_count == 1

    def test_cache_disabled_via_env(self, _mock_settings, monkeypatch):
        monkeypatch.setenv("EXTRACTION_CACHE_ENABLED", "false")
        manager = ProviderManager.instance()

        with mock.patch("app.services.provider_manager.litellm") as mock_litellm:
            manager.ensure_cache()

        mock_litellm.Cache.assert_not_called()
