# Deployment

## Docker Compose (development)

```bash
docker compose up --build
```

This starts three services:

| Service   | Port  | Purpose                  |
|-----------|-------|--------------------------|
| `web`     | 8000  | FastAPI (Uvicorn)        |
| `worker`  | —     | Celery worker            |
| `redis`   | 6379  | Broker + result backend  |

## Container Roles

The unified image supports four roles via `APP_ROLE`:

| Role     | Command                                       |
|----------|-----------------------------------------------|
| `web`    | `uvicorn app.main:app`                        |
| `worker` | `celery -A app.workers.celery_app worker`     |
| `flower` | `celery -A app.workers.celery_app flower`     |
| `beat`   | `celery -A app.workers.celery_app beat`       |

## Environment Variables

Copy `.env.example` (if provided) or set the following:

| Variable                | Default              | Description                    |
|-------------------------|----------------------|--------------------------------|
| `REDIS_HOST`            | *(required)*         | Redis hostname                 |
| `REDIS_PORT`            | `6379`               | Redis port                     |
| `REDIS_DB`              | `0`                  | Redis database index           |
| `OPENAI_API_KEY`        | `""`                 | OpenAI API key                 |
| `GEMINI_API_KEY`        | `""`                 | Google Gemini API key          |
| `LANGEXTRACT_API_KEY`   | `""`                 | LangExtract managed key        |
| `DEFAULT_PROVIDER`      | `gpt-4o`             | Default LLM provider           |
| `ALLOWED_URL_DOMAINS`   | `[]`                 | Comma-separated domain list    |
| `WEBHOOK_SECRET`        | `""`                 | HMAC secret for webhooks       |
| `BATCH_CONCURRENCY`     | `4`                  | Max parallel batch extractions |
| `LOG_LEVEL`             | `info`               | Logging level                  |
| `DEBUG`                 | `false`              | Enable debug mode              |

## Health Checks

- **Liveness** — `GET /api/v1/health`
- **Readiness** — `GET /api/v1/health/celery`
- **Metrics** — `GET /api/v1/metrics` (Prometheus text format)
