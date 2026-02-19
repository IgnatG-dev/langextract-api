"""Tests for application settings and configuration.

Validates:
- Default values
- Environment variable parsing
- Derived properties (REDIS_URL, CELERY_*)
- CORS origins parsing
- New security, batch, and SSRF settings
"""

from __future__ import annotations

from app.core.config import Settings, get_version


class TestSettingsDefaults:
    """Verify that Settings defaults are correct."""

    def test_app_name(self):
        """Default app name."""
        s = Settings(_env_file=None, REDIS_HOST="localhost")
        assert s.APP_NAME == "LangExtract API"

    def test_api_prefix(self):
        """Default API prefix."""
        s = Settings(_env_file=None, REDIS_HOST="localhost")
        assert s.API_V1_STR == "/api/v1"

    def test_debug_default(self):
        """Debug is off by default."""
        s = Settings(_env_file=None, REDIS_HOST="localhost")
        assert s.DEBUG is False

    def test_redis_defaults(self):
        """Default Redis settings."""
        s = Settings(_env_file=None, REDIS_HOST="myhost")
        assert s.REDIS_HOST == "myhost"
        assert s.REDIS_PORT == 6379
        assert s.REDIS_DB == 0

    def test_default_provider(self):
        """Default provider is gpt-4o."""
        s = Settings(_env_file=None, REDIS_HOST="localhost")
        assert s.DEFAULT_PROVIDER == "gpt-4o"

    def test_default_max_workers(self):
        """Default max workers."""
        s = Settings(_env_file=None, REDIS_HOST="localhost")
        assert s.DEFAULT_MAX_WORKERS == 10

    def test_default_max_char_buffer(self):
        """Default max char buffer."""
        s = Settings(_env_file=None, REDIS_HOST="localhost")
        assert s.DEFAULT_MAX_CHAR_BUFFER == 1000

    def test_task_time_limits(self):
        """Default task time limits."""
        s = Settings(_env_file=None, REDIS_HOST="localhost")
        assert s.TASK_TIME_LIMIT == 3600
        assert s.TASK_SOFT_TIME_LIMIT == 3300

    def test_result_expires(self):
        """Default result expiry."""
        s = Settings(_env_file=None, REDIS_HOST="localhost")
        assert s.RESULT_EXPIRES == 86400

    def test_ssrf_defaults(self):
        """Security-related defaults."""
        s = Settings(_env_file=None, REDIS_HOST="localhost")
        assert s.ALLOWED_URL_DOMAINS == []
        assert s.WEBHOOK_SECRET == ""
        assert s.DOC_DOWNLOAD_TIMEOUT == 30
        assert s.DOC_DOWNLOAD_MAX_BYTES == 50_000_000

    def test_batch_concurrency_default(self):
        """Default batch concurrency."""
        s = Settings(_env_file=None, REDIS_HOST="localhost")
        assert s.BATCH_CONCURRENCY == 4


class TestSettingsDerivedProperties:
    """Test computed properties."""

    def test_redis_url(self):
        """REDIS_URL is composed from host, port, db."""
        s = Settings(
            _env_file=None,
            REDIS_HOST="myredis",
            REDIS_PORT=6380,
            REDIS_DB=2,
        )
        assert s.REDIS_URL == "redis://myredis:6380/2"

    def test_celery_broker_url(self):
        """CELERY_BROKER_URL matches REDIS_URL."""
        s = Settings(_env_file=None, REDIS_HOST="localhost")
        assert s.CELERY_BROKER_URL == s.REDIS_URL

    def test_celery_result_backend(self):
        """CELERY_RESULT_BACKEND matches REDIS_URL."""
        s = Settings(_env_file=None, REDIS_HOST="localhost")
        assert s.CELERY_RESULT_BACKEND == s.REDIS_URL


class TestSettingsCorsParser:
    """Test CORS_ORIGINS parsing."""

    def test_parses_json_string(self):
        """JSON-encoded string is parsed into a list."""
        s = Settings(
            _env_file=None,
            REDIS_HOST="localhost",
            CORS_ORIGINS=('["http://localhost:3000","https://app.example.com"]'),
        )
        assert s.CORS_ORIGINS == [
            "http://localhost:3000",
            "https://app.example.com",
        ]

    def test_accepts_list(self):
        """A list value is accepted as-is."""
        s = Settings(
            _env_file=None,
            REDIS_HOST="localhost",
            CORS_ORIGINS=["http://localhost:3000"],
        )
        assert s.CORS_ORIGINS == ["http://localhost:3000"]

    def test_wildcard_default(self):
        """Default CORS origins is ["*"]."""
        s = Settings(_env_file=None, REDIS_HOST="localhost")
        assert s.CORS_ORIGINS == ["*"]


class TestSettingsAllowedDomains:
    """Test ALLOWED_URL_DOMAINS parsing."""

    def test_empty_string_gives_empty_list(self):
        """An empty string produces an empty list."""
        s = Settings(
            _env_file=None,
            REDIS_HOST="localhost",
            ALLOWED_URL_DOMAINS="",
        )
        assert s.ALLOWED_URL_DOMAINS == []

    def test_comma_separated_string(self):
        """Comma-separated domains are split into a list."""
        s = Settings(
            _env_file=None,
            REDIS_HOST="localhost",
            ALLOWED_URL_DOMAINS="a.com, b.com , c.com",
        )
        assert s.ALLOWED_URL_DOMAINS == [
            "a.com",
            "b.com",
            "c.com",
        ]

    def test_list_passthrough(self):
        """A list is accepted as-is."""
        s = Settings(
            _env_file=None,
            REDIS_HOST="localhost",
            ALLOWED_URL_DOMAINS=["x.com"],
        )
        assert s.ALLOWED_URL_DOMAINS == ["x.com"]


class TestSettingsApiKeys:
    """Test API key fields."""

    def test_api_keys_default_empty(self):
        """All API keys default to empty strings."""
        s = Settings(_env_file=None, REDIS_HOST="localhost")
        assert s.OPENAI_API_KEY == ""
        assert s.GEMINI_API_KEY == ""
        assert s.LANGEXTRACT_API_KEY == ""

    def test_api_keys_from_env(self):
        """API keys can be set via constructor."""
        s = Settings(
            _env_file=None,
            REDIS_HOST="localhost",
            OPENAI_API_KEY="sk-test",
            GEMINI_API_KEY="gm-test",
            LANGEXTRACT_API_KEY="lx-test",
        )
        assert s.OPENAI_API_KEY == "sk-test"
        assert s.GEMINI_API_KEY == "gm-test"
        assert s.LANGEXTRACT_API_KEY == "lx-test"


class TestGetVersion:
    """Test the get_version helper."""

    def test_returns_string(self):
        """get_version always returns a string."""
        v = get_version()
        assert isinstance(v, str)
