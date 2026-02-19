# LangExtract API

Queue-based document extraction API powered by **FastAPI**, **Celery**, and [**LangExtract**](https://github.com/google/langextract).

Submit a document URL or raw text, get back structured entities — asynchronously.

---

## Architecture

```
┌──────────┐      ┌──────────┐      ┌──────────┐
│   API    │─────▶│ FastAPI  │─────▶│  Redis   │
│  Client  │◀──── │  (web)   │      │ (broker) │
└────▲─────┘      └──────────┘      └────┬─────┘
     │                                   │
     │                              ┌────▼─────┐
     │          Webhook / Poll      │  Celery  │
     └──────────────────────────────┤  Worker  │
                                    └──────────┘
```

1. Client submits via `POST /api/v1/extract` (or `/extract/batch`)
2. FastAPI validates, enqueues a Celery task in Redis, returns a **task ID**
3. A Celery worker runs the LangExtract pipeline
4. Results are stored in Redis (TTL via `RESULT_EXPIRES`)
5. Client **polls** `GET /api/v1/tasks/{task_id}` or receives a **webhook** callback

---

## Quick Start

### Docker (recommended)

```bash
cp .env.example .env          # add your GEMINI_API_KEY or OPENAI_API_KEY
docker compose up --build      # API on :8000, Flower on :5555
```

### Local Development

```bash
uv sync                                        # install deps
docker run -d -p 6379:6379 redis:8-alpine      # start Redis
export REDIS_HOST=localhost

# Terminal 1 — API
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2 — Worker
uv run celery -A app.workers.celery_app worker --loglevel=info
```

### Production

```bash
docker compose --profile production up --build -d
```

Multi-worker Uvicorn (4 procs), multiple Celery replicas, resource limits, health checks.

---

## API Reference

| Method   | Path                      | Description                                    |
|----------|---------------------------|------------------------------------------------|
| `POST`   | `/api/v1/extract`         | Submit single extraction                       |
| `POST`   | `/api/v1/extract/batch`   | Submit batch of extractions                    |
| `GET`    | `/api/v1/tasks/{task_id}` | Poll task status / result                      |
| `DELETE` | `/api/v1/tasks/{task_id}` | Revoke a running task                          |
| `GET`    | `/api/v1/health`          | Liveness probe                                 |
| `GET`    | `/api/v1/health/celery`   | Worker readiness probe                         |
| `GET`    | `/api/v1/metrics`         | Task counters (submitted / completed / failed) |

Interactive docs at **<http://localhost:8000/api/v1/docs>** (Swagger UI).

### Submit Extraction (URL)

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "document_url": "https://example.com/contract.pdf",
    "callback_url": "https://my-app.com/webhooks/done",
    "provider": "gpt-4o",
    "passes": 2
  }'
```

```json
{
  "task_id": "a1b2c3d4-...",
  "status": "submitted",
  "message": "Extraction submitted for https://example.com/contract.pdf"
}
```

### Submit Extraction (Raw Text)

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "AGREEMENT between Acme Corp and ...",
    "provider": "gpt-4o"
  }'
```

### Poll Status

```bash
curl http://localhost:8000/api/v1/tasks/{task_id}
```

### Idempotency

Pass an `idempotency_key` to prevent duplicate tasks:

```json
{
  "raw_text": "...",
  "idempotency_key": "my-unique-key-123"
}
```

Repeat submissions with the same key return the original task ID.

---

## Response Schema

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
      "tokens_used": 1234,
      "processing_time_ms": 1200
    }
  }
}
```

> `tokens_used` is `null` when the provider does not report usage.

---

## Configuration

All settings are driven by environment variables (`.env` file supported):

### General

| Variable       | Default         | Description                       |
|----------------|-----------------|-----------------------------------|
| `APP_NAME`     | LangExtract API | Display name                      |
| `API_V1_STR`   | /api/v1         | API version prefix                |
| `ROOT_PATH`    | _(empty)_       | ASGI root path (reverse proxy)    |
| `DEBUG`        | false           | Enable debug mode                 |
| `LOG_LEVEL`    | info            | Logging level                     |
| `CORS_ORIGINS` | ["*"]           | JSON list of allowed CORS origins |

### Redis / Celery

| Variable               | Default | Description                   |
|------------------------|---------|-------------------------------|
| `REDIS_HOST`           | redis   | Redis hostname                |
| `REDIS_PORT`           | 6379    | Redis port                    |
| `REDIS_DB`             | 0       | Redis database index          |
| `RESULT_EXPIRES`       | 86400   | Result TTL in Redis (seconds) |
| `TASK_TIME_LIMIT`      | 3600    | Hard task timeout (seconds)   |
| `TASK_SOFT_TIME_LIMIT` | 3300    | Soft task timeout (seconds)   |

### LLM / Extraction

| Variable                 | Default   | Description                                      |
|--------------------------|-----------|--------------------------------------------------|
| `DEFAULT_PROVIDER`       | gpt-4o    | Default model (overridable per-request)          |
| `DEFAULT_MAX_WORKERS`    | 10        | LangExtract parallel workers                     |
| `DEFAULT_MAX_CHAR_BUFFER`| 1000      | LangExtract character buffer                     |
| `OPENAI_API_KEY`         | _(empty)_ | OpenAI key (for GPT models)                      |
| `GEMINI_API_KEY`         | _(empty)_ | Google Gemini key                                |
| `LANGEXTRACT_API_KEY`    | _(empty)_ | Dedicated key (falls back to `GEMINI_API_KEY`)   |

### Security

| Variable                 | Default   | Description                                    |
|--------------------------|-----------|------------------------------------------------|
| `ALLOWED_URL_DOMAINS`    | _(empty)_ | Comma-separated allow-list for document URLs   |
| `WEBHOOK_SECRET`         | _(empty)_ | HMAC-SHA256 key for signing webhook payloads   |
| `DOC_DOWNLOAD_TIMEOUT`   | 30        | Document download timeout (seconds)            |
| `DOC_DOWNLOAD_MAX_BYTES` | 50000000  | Max document size (bytes)                      |

### Batch

| Variable           | Default | Description                    |
|--------------------|---------|--------------------------------|
| `BATCH_CONCURRENCY`| 4       | Max parallel batch extractions |

> **Multi-provider:** every request can override the model via `"provider": "gemini-2.5-flash"`.
> OpenAI models automatically get `fence_output=True` and `use_schema_constraints=False`.

---

## Customising Extraction

The default prompt extracts contract entities (parties, dates, monetary amounts, terms).
Override per-request via `extraction_config`:

```json
{
  "raw_text": "Take Aspirin 81 mg daily.",
  "extraction_config": {
    "prompt_description": "Extract medication names and dosages.",
    "examples": [
      {
        "text": "Take Aspirin 81 mg daily.",
        "extractions": [
          {
            "extraction_class": "medication",
            "extraction_text": "Aspirin 81 mg",
            "attributes": { "dosage": "81 mg", "frequency": "daily" }
          }
        ]
      }
    ],
    "temperature": 0.2
  }
}
```

| `extraction_config` key | Type         | Description                       |
|-------------------------|--------------|-----------------------------------|
| `prompt_description`    | `string`     | Custom extraction prompt          |
| `examples`              | `list[dict]` | Few-shot examples                 |
| `max_workers`           | `int`        | Parallel worker count             |
| `max_char_buffer`       | `int`        | Character buffer size             |
| `additional_context`    | `string`     | Extra context for the LLM        |
| `temperature`           | `float`      | LLM temperature (0.0–2.0)        |
| `context_window_chars`  | `int`        | Context window size in characters |

To change the **global** defaults, edit `app/core/defaults.py`.

### Supported Models

| Provider | Models                               | Key variable     |
|----------|--------------------------------------|------------------|
| Google   | `gemini-2.5-pro`, `gemini-2.0-flash` | `GEMINI_API_KEY` |
| OpenAI   | `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo` | `OPENAI_API_KEY` |

---

## Security

- **SSRF protection** — private IP / localhost blocking, subdomain matching, URL length limit (2 048 chars), DNS resolution timeout (5 s)
- **Domain allow-list** — set `ALLOWED_URL_DOMAINS` to restrict accepted document URLs
- **Webhook HMAC signing** — set `WEBHOOK_SECRET` to sign outbound webhooks (`X-Webhook-Signature` / `X-Webhook-Timestamp` headers, HMAC-SHA256)
- **Provider validation** — model IDs are validated against a strict regex pattern

See [docs/security.md](docs/security.md) for full details.

---

## Project Structure

```
langextract-api/
├── app/
│   ├── main.py                    # App factory, middleware, lifespan
│   ├── core/
│   │   ├── config.py              # Settings, Redis pool
│   │   ├── defaults.py            # Default prompt & few-shot examples
│   │   ├── logging.py             # Structured logging setup
│   │   └── security.py            # SSRF protection, URL validation
│   ├── services/
│   │   ├── extractor.py           # Extraction business logic
│   │   └── webhook.py             # Result persistence & webhook delivery
│   ├── workers/
│   │   ├── celery_app.py          # Celery app configuration
│   │   └── tasks.py               # Celery task definitions
│   ├── api/
│   │   ├── deps.py                # FastAPI dependencies (Redis, etc.)
│   │   └── routes/
│   │       ├── extraction.py      # POST /extract, POST /extract/batch
│   │       ├── health.py          # GET /health, GET /metrics
│   │       └── tasks.py           # GET/DELETE /tasks/{task_id}
│   └── schemas/
│       └── extraction.py          # Pydantic request/response models
├── tests/                         # pytest suite (147 tests)
├── docs/                          # security.md, deployment.md, recipes.md
├── examples/                      # curl scripts, JSON config samples
├── docker/
│   ├── Dockerfile                 # Multi-stage build
│   └── entrypoint.sh              # web / worker / flower / beat
├── docker-compose.yml
├── pyproject.toml
├── Makefile
└── README.md
```

---

## Development

```bash
make install   # uv sync
make lint      # ruff check + format check
make format    # auto-format
make test      # pytest -v
make test-cov  # pytest with coverage
make dev       # docker compose up --build
make clean     # docker compose down -v
```

### Running Tests

```bash
uv run pytest -v                           # all tests
uv run pytest --cov=app --cov-report=term  # with coverage
uv run pytest tests/test_tasks.py -v       # single file
```

---

## Further Reading

- [docs/security.md](docs/security.md) — SSRF protection, HMAC webhooks, domain allow-lists
- [docs/deployment.md](docs/deployment.md) — Production deployment guide
- [docs/recipes.md](docs/recipes.md) — Common usage patterns and examples

## License

Apache License 2.0
