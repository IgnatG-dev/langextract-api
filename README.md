# LangExtract API

Queue-based document extraction API powered by **FastAPI**, **Celery**, and **LangExtract**.

## Architecture

```
┌──────────┐      ┌──────────┐      ┌──────────┐
│  NestJS  │─────▶│  FastAPI │─────▶│  Redis   │
│  Client  │◀─────│   (API)  │      │ (Broker) │
└────▲─────┘      └──────────┘      └────┬─────┘
     │                                   │
     │            ┌──────────┐      ┌────▼─────┐
     │            │ Postgres │◀─────┤  Celery  │
     └────────────┤ (Result) │      │  Worker  │
       Webhook    └──────────┘      └──────────┘
```

1. **NestJS client** submits a document URL (or raw text) via `POST /api/v1/extract`
2. FastAPI validates the request, enqueues a Celery task in **Redis**, and returns a **task ID**
3. A **Celery worker** picks up the task and runs the LangExtract pipeline
4. Results are stored in Redis (and optionally **Postgres** for long-term persistence)
5. The client either **polls** `GET /api/v1/tasks/{task_id}` — or receives a **webhook** callback

## Project Structure

```
langextract-api/
├── app/
│   ├── main.py            # FastAPI entry point & all routes
│   ├── worker.py          # Celery app configuration
│   ├── tasks.py           # Long-running extraction tasks
│   ├── schemas.py         # Pydantic request/response models
│   └── dependencies.py    # Settings, Redis client, singletons
├── docker/
│   ├── Dockerfile         # Multi-stage build (dev + production)
│   └── entrypoint.sh      # Switches between web / worker / flower
├── tests/
│   └── test_api.py        # API endpoint tests
├── docker-compose.yml     # API + Worker + Redis + Flower
├── pyproject.toml         # Project metadata & dependencies (uv)
├── requirements.txt       # Pip-compatible dependency list
├── .env.example           # Template for environment variables
└── README.md
```

## Getting Started

### Prerequisites

- Docker & Docker Compose
- Python 3.12+ (for local development)
- [uv](https://github.com/astral-sh/uv) package manager (recommended)

### Quick Start (Docker)

```bash
# 1. Create .env from template
cp .env.example .env

# 2. Start all services
docker compose up --build

# 3. Access
#    API Docs  → http://localhost:8000/api/v1/docs
#    Flower    → http://localhost:5555
```

### Local Development (without Docker)

```bash
# Install dependencies
uv sync

# Start Redis
docker run -d -p 6379:6379 redis:8-alpine

# Set Redis host to localhost
export REDIS_HOST=localhost

# Start API (with hot-reload)
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Start worker (separate terminal)
uv run celery -A app.worker.celery_app worker --loglevel=info
```

### Production

```bash
docker compose --profile production up --build -d
```

This starts production-optimised API and worker containers with:

- Multi-worker Uvicorn (4 processes)
- Multiple Celery worker replicas
- CPU / memory resource limits
- Health checks and automatic restart

## API Endpoints

| Method   | Path                       | Description                     |
|----------|----------------------------|---------------------------------|
| `POST`   | `/api/v1/extract`          | Submit a single extraction task |
| `POST`   | `/api/v1/extract/batch`    | Submit a batch of extractions   |
| `GET`    | `/api/v1/tasks/{task_id}`  | Poll task status & result       |
| `DELETE` | `/api/v1/tasks/{task_id}`  | Revoke a running task           |
| `GET`    | `/api/v1/health`           | Liveness probe                  |
| `GET`    | `/api/v1/health/celery`    | Worker readiness probe          |

### Example — Submit Extraction (URL)

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "document_url": "https://example.com/contract.pdf",
    "callback_url": "https://nestjs-api.com/webhooks/extraction-complete",
    "provider": "gemini-1.5-pro",
    "passes": 2
  }'
```

Response:

```json
{
  "task_id": "a1b2c3d4-...",
  "status": "submitted",
  "message": "Extraction submitted for https://example.com/contract.pdf"
}
```

### Example — Submit Extraction (Raw Text)

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "AGREEMENT between Acme Corp and ...",
    "provider": "gpt-4o",
    "passes": 1
  }'
```

### Example — Poll Status

```bash
curl http://localhost:8000/api/v1/tasks/a1b2c3d4-...
```

## Configuration

All configuration is driven by environment variables (loaded from `.env`):

| Variable               | Default            | Description                          |
|------------------------|--------------------|--------------------------------------|
| `APP_NAME`             | LangExtract API    | Application display name             |
| `API_V1_STR`           | /api/v1            | API version prefix                   |
| `DEBUG`                | false              | Enable debug mode                    |
| `LOG_LEVEL`            | info               | Logging level                        |
| `REDIS_HOST`           | redis              | Redis hostname                       |
| `REDIS_PORT`           | 6379               | Redis port                           |
| `REDIS_DB`             | 0                  | Redis database index                 |
| `OPENAI_API_KEY`       |                    | OpenAI API key (for LangExtract)     |
| `GEMINI_API_KEY`       |                    | Google Gemini API key                |
| `DEFAULT_PROVIDER`     | gemini-1.5-pro     | Default AI provider (per-request override via `provider` field) |
| `TASK_TIME_LIMIT`      | 3600               | Hard task timeout (seconds)          |
| `TASK_SOFT_TIME_LIMIT` | 3300               | Soft task timeout (seconds)          |
| `RESULT_EXPIRES`       | 86400              | Result TTL in Redis (seconds)        |

> **Multi-provider note:** The `DEFAULT_PROVIDER` env var sets the fallback
> provider. Every request can override it via the `provider` field in the
> request body (e.g. `"provider": "gpt-4o"`).

## Data Models

The API returns a **standardised JSON** structure regardless of which AI
provider ran the extraction:

```json
{
  "task_id": "a1b2c3d4-...",
  "status": "completed",
  "data": {
    "entities": [
      { "type": "Company", "value": "Google", "confidence": 0.98 },
      { "type": "Date",    "value": "2026-02-18", "confidence": 1.0 }
    ],
    "metadata": {
      "provider": "gemini-1.5-pro",
      "tokens_used": 450,
      "processing_time_ms": 1200
    }
  }
}
```

### Request Schema (`ExtractionRequest`)

| Field               | Type            | Required | Default          | Description |
|---------------------|-----------------|----------|------------------|-------------|
| `document_url`      | `string (URL)`  | *        |                  | URL to the document to extract from |
| `raw_text`          | `string`        | *        |                  | Raw text blob to process directly |
| `provider`          | `string`        | No       | `gemini-1.5-pro` | AI provider / model |
| `passes`            | `integer (1-5)` | No       | `1`              | Number of extraction passes |
| `callback_url`      | `string (URL)`  | No       |                  | Webhook URL for completion notification |
| `extraction_config` | `object`        | No       | `{}`             | Additional extraction overrides |

> \* At least one of `document_url` or `raw_text` must be provided.

### Response Schema (`ExtractionResult`)

| Field                     | Type       | Description |
|---------------------------|------------|-------------|
| `entities[]`              | `array`    | Extracted entities |
| `entities[].type`         | `string`   | Entity type (e.g. Company, Date, Amount) |
| `entities[].value`        | `string`   | Extracted value |
| `entities[].confidence`   | `float`    | Confidence score (0–1) |
| `metadata.provider`       | `string`   | AI provider that ran the extraction |
| `metadata.tokens_used`    | `integer`  | Total tokens consumed |
| `metadata.processing_time_ms` | `integer` | Wall-clock processing time (ms) |

## Running Tests

```bash
uv run pytest tests/ -v
```
