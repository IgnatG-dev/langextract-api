# LangExtract API

Queue-based document extraction API powered by **FastAPI**, **Celery**, and [**LangExtract**](https://github.com/google/langextract).

## Architecture

```
┌──────────┐      ┌──────────┐      ┌──────────┐
│   API    │────▶ │  FastAPI │────▶│  Redis   │
│  Client  │◀──── │   (API)  │      │ (Broker) │
└────▲─────┘      └──────────┘      └────┬─────┘
     │                                   │
     │                              ┌────▼─────┐
     │                              │  Celery  │
     └──────────────────────────────┤  Worker  │
                 Webhook            └──────────┘
```

1. **API client** submits a document URL (or raw text) via `POST /api/v1/extract`
2. FastAPI validates the request, enqueues a Celery task in **Redis**, and returns a **task ID**
3. A **Celery worker** picks up the task and runs the LangExtract pipeline
4. Results are stored in **Redis** (configurable TTL via `RESULT_EXPIRES`)
5. The client either **polls** `GET /api/v1/tasks/{task_id}` or receives a **webhook** callback

## Project Structure

```
langextract-api/
├── app/
│   ├── __init__.py            # Package marker
│   ├── main.py                # App factory, middleware, lifespan
│   ├── worker.py              # Celery app configuration
│   ├── tasks.py               # Long-running extraction tasks (langextract)
│   ├── schemas.py             # Pydantic request/response models
│   ├── dependencies.py        # Settings, Redis client, singletons
│   ├── extraction_defaults.py # Default prompt & few-shot examples
│   ├── logging_config.py      # Structured logging setup
│   └── routers/
│       ├── __init__.py
│       ├── health.py          # GET /health, GET /health/celery
│       ├── extraction.py      # POST /extract, POST /extract/batch
│       └── tasks.py           # GET/DELETE /tasks/{task_id}
├── tests/
│   ├── conftest.py            # Shared pytest fixtures
│   ├── test_api.py            # API endpoint tests
│   ├── test_tasks.py          # Task & helper unit tests
│   ├── test_schemas.py        # Schema validation tests
│   └── test_settings.py       # Configuration tests
├── docker/
│   ├── Dockerfile             # Multi-stage build (dev + production)
│   └── entrypoint.sh          # Switches between web / worker / flower / beat
├── .github/
│   ├── workflows/ci.yml       # Lint, test, Docker build CI pipeline
│   ├── CODEOWNERS
│   └── pull_request_template.md
├── docker-compose.yml         # API + Worker + Redis + Flower
├── pyproject.toml             # Project metadata & dependencies (uv)
├── uv.lock                    # Lock file for reproducible installs
├── Makefile                   # Common development commands
├── .ruff.toml                 # Ruff linter/formatter configuration
├── .env.example               # Template for environment variables
├── .dockerignore              # Docker build context exclusions
├── .gitignore
└── README.md
```

## Getting Started

### Prerequisites

- Docker & Docker Compose
- Python 3.12+ (for local development)
- [uv](https://github.com/astral-sh/uv) package manager (recommended)
- A **Gemini** or **OpenAI** API key (for running extractions)

### Quick Start (Docker)

```bash
# 1. Create .env from template
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY or OPENAI_API_KEY

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
    "callback_url": "https://api-client.com/webhooks/extraction-complete",
    "provider": "gpt-4o",
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

| Variable                  | Default            | Description                                                |
|---------------------------|--------------------|------------------------------------------------------------|
| `APP_NAME`                | LangExtract API    | Application display name                                   |
| `API_V1_STR`              | /api/v1            | API version prefix                                         |
| `ROOT_PATH`               | (empty)            | ASGI root path (reverse proxy)                             |
| `DEBUG`                   | false              | Enable debug mode                                          |
| `LOG_LEVEL`               | info               | Logging level                                              |
| `CORS_ORIGINS`            | ["*"]              | JSON list of allowed origins                               |
| `REDIS_HOST`              | redis              | Redis hostname                                             |
| `REDIS_PORT`              | 6379               | Redis port                                                 |
| `REDIS_DB`                | 0                  | Redis database index                                       |
| `OPENAI_API_KEY`          | (empty)            | OpenAI API key (for GPT models)                            |
| `GEMINI_API_KEY`          | (empty)            | Google Gemini API key                                      |
| `LANGEXTRACT_API_KEY`     | (empty)            | Dedicated LangExtract key (falls back to `GEMINI_API_KEY`) |
| `DEFAULT_PROVIDER`        | gpt-4o   | Default LLM model (overridable per-request)                |
| `DEFAULT_MAX_WORKERS`     | 10                 | LangExtract parallel worker count                          |
| `DEFAULT_MAX_CHAR_BUFFER` | 1000               | LangExtract character buffer size                          |
| `TASK_TIME_LIMIT`         | 3600               | Hard task timeout (seconds)                                |
| `TASK_SOFT_TIME_LIMIT`    | 3300               | Soft task timeout (seconds)                                |
| `RESULT_EXPIRES`          | 86400              | Result TTL in Redis (seconds)                              |

> **Multi-provider note:** The `DEFAULT_PROVIDER` env var sets the fallback.
> Every request can override it via the `provider` field in the request body
> (e.g. `"provider": "gpt-4o"`). OpenAI models automatically get
> `fence_output=True` and `use_schema_constraints=False`.

## Data Models

### Request Schema (`ExtractionRequest`)

| Field               | Type            | Required | Default            | Description |
|---------------------|-----------------|----------|--------------------|-------------|
| `document_url`      | `string (URL)`  | *        |                    | URL to the document to extract from |
| `raw_text`          | `string`        | *        |                    | Raw text blob to process directly |
| `provider`          | `string`        | No       | `gpt-4o` | LLM model ID |
| `passes`            | `integer (1-5)` | No       | `1`                | Number of extraction passes |
| `callback_url`      | `string (URL)`  | No       |                    | Webhook URL for completion notification |
| `extraction_config` | `object`        | No       | `{}`               | LangExtract overrides (see below) |

> \* At least one of `document_url` or `raw_text` must be provided.

#### `extraction_config` Keys

| Key                    | Type          | Description |
|------------------------|---------------|-------------|
| `prompt_description`   | `string`      | Custom extraction prompt |
| `examples`             | `list[dict]`  | Few-shot examples (`text` + `extractions`) |
| `max_workers`          | `int`         | Override parallel worker count |
| `max_char_buffer`      | `int`         | Override character buffer size |
| `additional_context`   | `string`      | Extra context for the LLM |
| `temperature`          | `float`       | LLM temperature |
| `context_window_chars` | `int`         | Context window size in characters |

### Response Schema (Success)

```json
{
  "status": "completed",
  "source": "https://example.com/contract.pdf",
  "data": {
    "entities": [
      {
        "extraction_class": "party",
        "extraction_text": "Acme Corporation",
        "attributes": { "role": "Seller", "jurisdiction": "Delaware" },
        "char_start": 52,
        "char_end": 68
      },
      {
        "extraction_class": "monetary_amount",
        "extraction_text": "$2,500,000",
        "attributes": { "type": "purchase_price" },
        "char_start": 180,
        "char_end": 190
      }
    ],
    "metadata": {
      "provider": "gpt-4o",
      "tokens_used": 0,
      "processing_time_ms": 1200
    }
  }
}
```

### Entity Schema (`ExtractedEntity`)

| Field              | Type     | Description |
|--------------------|----------|-------------|
| `extraction_class` | `string` | Entity class (e.g. `party`, `date`, `monetary_amount`, `term`) |
| `extraction_text`  | `string` | Exact text extracted from the source |
| `attributes`       | `object` | Key-value pairs providing context |
| `char_start`       | `int?`   | Start character offset in the source |
| `char_end`         | `int?`   | End character offset in the source |

## LangExtract Integration

This API uses [Google's LangExtract](https://github.com/google/langextract) library for LLM-based structured extraction. The default configuration extracts contract entities (parties, dates, monetary amounts, terms) using few-shot prompting.

Customise extraction behaviour per-request via the `extraction_config` field,
or modify the defaults in `app/extraction_defaults.py`.

### Supported Models

| Provider | Models | Notes |
|----------|--------|-------|
| Google   | `gpt-4o`, `gemini-2.5-pro`, `gemini-2.0-flash` | Default; uses `GEMINI_API_KEY` or `LANGEXTRACT_API_KEY` |
| OpenAI   | `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo` | Uses `OPENAI_API_KEY`; auto-sets `fence_output=True` |

## Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=app --cov-report=term-missing

# Run a specific test file
uv run pytest tests/test_tasks.py -v
```

## Development

```bash
# Lint
uv run ruff check .

# Format
uv run ruff format .

# Lint + auto-fix
uv run ruff check --fix .
```

Or use the Makefile shortcuts:

```bash
make lint      # ruff check + format check
make format    # auto-format
make test      # pytest with coverage
make dev       # docker compose up --build
make clean     # docker compose down -v
```

## License

Apache License 2.0
