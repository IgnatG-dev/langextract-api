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
3. A Celery worker downloads the document text and runs the LangExtract pipeline
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
    "document_url": "https://example.com/contract.txt",
    "callback_url": "https://my-app.com/webhooks/done",
    "callback_headers": {
      "Authorization": "Bearer eyJhbGciOi..."
    },
    "provider": "gpt-4o",
    "passes": 2
  }'
```

```json
{
  "task_id": "a1b2c3d4-...",
  "status": "submitted",
  "message": "Extraction submitted for https://example.com/contract.txt"
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

### Webhook Headers

Use `callback_headers` to attach custom headers (e.g. `Authorization`) to the
webhook POST that fires when extraction completes:

```json
{
  "raw_text": "...",
  "callback_url": "https://my-app.com/webhooks/done",
  "callback_headers": {
    "Authorization": "Bearer <token>"
  }
}
```

These headers are merged with the default `Content-Type` and HMAC signature
headers.  The same field is available on batch requests.

---

## Response Schema

```json
{
  "status": "completed",
  "source": "https://example.com/contract.txt",
  "data": {
    "entities": [
      {
        "extraction_class": "party",
        "extraction_text": "Acme Corporation",
        "attributes": { "role": "Seller", "jurisdiction": "Delaware" },
        "char_start": 52,
        "char_end": 68,
        "confidence_score": 1.0
      },
      {
        "extraction_class": "monetary_amount",
        "extraction_text": "$2,500,000",
        "attributes": { "type": "purchase_price" },
        "char_start": 180,
        "char_end": 190,
        "confidence_score": 0.67
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
>
> `confidence_score` is present when `passes > 1`. It indicates how
> consistently the entity was found across passes (1.0 = every pass,
> 0.5 = half the passes). When consensus mode is active the `provider`
> field reads `"consensus(gpt-4o, claude-3-opus)"` etc.

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

| Variable                   | Default   | Description                                      |
|----------------------------|-----------|--------------------------------------------------|
| `DEFAULT_PROVIDER`         | gpt-4o    | Default model (overridable per-request)          |
| `DEFAULT_MAX_WORKERS`      | 10        | LangExtract parallel workers                     |
| `DEFAULT_MAX_CHAR_BUFFER`  | 1000      | LangExtract character buffer                     |
| `OPENAI_API_KEY`           | _(empty)_ | OpenAI key (for GPT models)                      |
| `GEMINI_API_KEY`           | _(empty)_ | Google Gemini key                                |
| `ANTHROPIC_API_KEY`        | _(empty)_ | Anthropic key (for Claude models)                |
| `MISTRAL_API_KEY`          | _(empty)_ | Mistral AI key                                   |
| `LANGEXTRACT_API_KEY`      | _(empty)_ | Dedicated key (falls back to `GEMINI_API_KEY`)   |
| `OLLAMA_API_BASE`          | _(empty)_ | Ollama base URL (e.g. `http://localhost:11434`)  |
| `EXTRACTION_CACHE_ENABLED` | true      | Enable LLM response caching via Redis            |
| `EXTRACTION_CACHE_TTL`     | 86400     | Cache TTL in seconds (default 24 h)              |
| `EXTRACTION_CACHE_BACKEND` | redis     | Cache backend: `redis`, `disk`, or `none`        |
| `EXTRACTION_CACHE_DIR`     | .extraction_cache | Directory for disk backend (dev only)    |

### Security

| Variable                 | Default   | Description                                    |
|--------------------------|-----------|------------------------------------------------|
| `ALLOWED_URL_DOMAINS`    | _(empty)_ | Comma-separated allow-list for document URLs   |
| `WEBHOOK_SECRET`         | _(empty)_ | HMAC-SHA256 key for signing webhook payloads   |
| `DOC_DOWNLOAD_TIMEOUT`   | 30        | Document download timeout (seconds)            |
| `DOC_DOWNLOAD_MAX_BYTES` | 50000000  | Max document size (bytes)                      |

> **Supported document formats:** `document_url` must point to a **plain-text**
> or **Markdown** resource. Binary formats (PDF, DOCX, etc.) are **not**
> supported — extract text before submitting to the API.

### Batch

| Variable           | Default | Description                    |
|--------------------|---------|--------------------------------|
| `BATCH_CONCURRENCY`| 4       | Max parallel batch extractions |

> **Multi-provider:** every request can override the model via `"provider": "gemini-2.5-flash"`.
> Supports cloud APIs (OpenAI, Gemini, Anthropic, Groq, Mistral, Azure) and
> local/self-hosted models (Ollama, vLLM, llama.cpp). See [Supported Models](#supported-models).
> OpenAI models automatically get `fence_output=True` and `use_schema_constraints=False`.

---

## Multi-Pass, Early Stopping & Consensus Mode

### Multi-Pass with Confidence Scoring

Set `passes > 1` to run multiple extraction passes over the same document.
Results are merged and each entity receives a **`confidence_score`** (0.0–1.0)
indicating the fraction of passes that found it. Entities appearing in every
pass score 1.0; those found in only one pass score `1/passes`.

**Early stopping** is automatic — if two consecutive passes produce identical
merged results the pipeline stops early, saving LLM calls without sacrificing
accuracy.

### Consensus Mode

Consensus mode runs the same extraction through **multiple LLM providers** and
keeps only the entities they agree on (Jaccard word-level similarity). This
greatly reduces hallucinations and improves determinism.

Enable it by setting `consensus_providers` in `extraction_config`:

```json
{
  "raw_text": "AGREEMENT between Acme Corp and Beta LLC ...",
  "passes": 2,
  "extraction_config": {
    "consensus_providers": ["gpt-4o", "gemini-2.5-pro"],
    "consensus_threshold": 0.7
  }
}
```

- **`consensus_providers`** — list of ≥ 2 model IDs. Each provider runs the
  full extraction independently; the results are then merged by agreement.
- **`consensus_threshold`** — minimum Jaccard similarity (0.0–1.0, default
  0.6) for two extractions to be considered "in agreement".
- The response `metadata.provider` shows `"consensus(gpt-4o, gemini-2.5-pro)"`.
- Consensus mode composes with multi-pass: set `passes: 2` **and**
  `consensus_providers` to get both cross-pass confidence scoring and
  cross-provider agreement filtering.

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

| `extraction_config` key  | Type         | Description                                                            |
|--------------------------|--------------|------------------------------------------------------------------------|
| `prompt_description`     | `string`     | Custom extraction prompt                                               |
| `examples`               | `list[dict]` | Few-shot examples                                                      |
| `max_workers`            | `int`        | Parallel worker count                                                  |
| `max_char_buffer`        | `int`        | Character buffer size                                                  |
| `additional_context`     | `string`     | Extra context for the LLM                                              |
| `temperature`            | `float`      | LLM temperature (0.0–2.0)                                             |
| `context_window_chars`   | `int`        | Context window size in characters                                      |
| `consensus_providers`    | `list[str]`  | ≥ 2 model IDs for consensus mode (e.g. `["gpt-4o", "gemini-2.5-pro"]`)  |
| `consensus_threshold`    | `float`      | Similarity threshold for consensus agreement (0.0–1.0, default 0.6)   |
| `structured_output`      | `bool\|null` | Enable/disable LLM-level `response_format` (default `null` = auto-detect) |

### Structured Output (response_format)

When the LLM provider supports JSON Schema constraints (OpenAI, Anthropic,
Gemini 2.0+, Groq, etc.), the API auto-generates a schema from the extraction
examples and passes it as `response_format` to `litellm.completion()`.  This
guarantees the LLM output is valid JSON matching the extraction format,
eliminating parse errors.

| `structured_output` value | Behaviour                                                    |
|---------------------------|--------------------------------------------------------------|
| `null` (default)          | Auto-detect — enabled when `litellm.supports_response_schema()` returns `true` |
| `true`                    | Force on (will error if the provider doesn't support it)     |
| `false`                   | Force off — use prompt-only extraction (legacy behaviour)    |

> **Note:** When structured output is active, `fence_output` is automatically
> set to `false` because the LLM returns raw JSON instead of fenced code blocks.

To change the **global** defaults, edit `app/core/defaults.py`.

### Supported Models

The API supports any model accessible through [LiteLLM](https://docs.litellm.ai/docs/providers), including cloud APIs and self-hosted / local inference servers.

#### Cloud Providers

| Provider   | Example models                            | Key variable        | `response_format` |
|------------|-------------------------------------------|---------------------|--------------------|
| OpenAI     | `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo`   | `OPENAI_API_KEY`    | Yes                |
| Google     | `gemini-2.5-pro`, `gemini-2.0-flash`      | `GEMINI_API_KEY`    | Yes (2.0+)         |
| Anthropic  | `claude-3.5-sonnet`, `claude-3-haiku`     | `ANTHROPIC_API_KEY` | Yes                |
| Azure OpenAI | `azure/gpt-4o`                          | `AZURE_API_KEY`     | Yes                |
| Groq       | `groq/llama-3.1-70b`                     | `GROQ_API_KEY`      | Yes                |
| Mistral    | `mistral/mistral-large-latest`            | `MISTRAL_API_KEY`   | Yes                |

#### Local / Self-Hosted

| Provider   | Example models                            | Configuration                 | `response_format` |
|------------|-------------------------------------------|-------------------------------|--------------------|
| Ollama     | `ollama/llama3.1`, `ollama/qwen2.5`       | `OLLAMA_API_BASE` (default `http://localhost:11434`) | Yes (Ollama ≥ 0.5) |
| vLLM       | `hosted_vllm/meta-llama/Llama-3.1-8B`    | `api_base` via provider kwargs | Yes                |
| llama.cpp server | `openai/<model>` with custom `api_base` | OpenAI-compatible endpoint  | Yes                |

> **Note:** Any LiteLLM-supported model can be used by passing the appropriate model ID
> as the `provider` field in the request. LiteLLM translates `response_format` to each
> backend's native format automatically. See the
> [LiteLLM provider list](https://docs.litellm.ai/docs/providers) for the full catalogue.

---

## Security

- **SSRF protection** — private IP / localhost blocking, subdomain matching, URL length limit (2 048 chars), DNS resolution timeout (5 s), redirect-hop re-validation
- **Domain allow-list** — set `ALLOWED_URL_DOMAINS` to restrict accepted document URLs
- **Webhook HMAC signing** — set `WEBHOOK_SECRET` to sign outbound webhooks (`X-Webhook-Signature` / `X-Webhook-Timestamp` headers, HMAC-SHA256)
- **Provider validation** — model IDs are validated against a strict regex pattern

See [docs/security.md](docs/security.md) for full details.

---

## Multi-Tier Caching

The API implements a two-tier caching strategy for extraction cost-control and fast re-runs:

### Tier 1 — LLM Response Cache (litellm + Redis)

Enabled automatically via `ProviderManager.ensure_cache()`. Every `litellm.completion()` call is cached in Redis, keyed by the full request parameters (prompt, model, temperature). Identical LLM prompts hit the cache directly — no API cost.

> **Multi-pass bypass:** When `passes > 1`, only the first pass (pass 0) is
> served from the LiteLLM cache. Passes ≥ 2 automatically include
> `cache={"no-cache": True}` so each subsequent pass gets a fresh LLM response.
> This is handled transparently by the `langextract-litellm` provider via the
> `pass_num` kwarg that LangExtract threads through the annotation loop.

### Tier 2 — Extraction-Result Cache

An **extraction-result-level** cache that sits above the LLM layer. When a document is extracted with the same text, prompt, examples, model, temperature, and passes, the complete result (entities + metadata) is returned from cache in < 500 ms with zero API cost.

**Cache key:** SHA-256 of `text ∥ prompt_description ∥ examples ∥ model_id ∥ temperature ∥ passes ∥ consensus_providers`. Any schema or prompt change automatically invalidates the cache.

**Backends:**

| Backend | Env value | Use case |
|---------|-----------|----------|
| `redis` | `EXTRACTION_CACHE_BACKEND=redis` | Default. Cross-worker, cross-job. |
| `disk`  | `EXTRACTION_CACHE_BACKEND=disk`  | Local dev / offline (uses `diskcache`). |
| `none`  | `EXTRACTION_CACHE_BACKEND=none`  | Completely disabled. |

**Cache-hit response metadata:**

When a result is served from the extraction cache, the response metadata includes `"cache_hit": true` and reflects the actual wall-clock time (typically < 100 ms):

```json
{
  "metadata": {
    "provider": "gpt-4o",
    "tokens_used": 0,
    "processing_time_ms": 42,
    "cache_hit": true
  }
}
```

**Prometheus metrics:**

| Metric | Type | Description |
|--------|------|-------------|
| `langextract_cache_hits` | counter | Total extraction-cache hits |
| `langextract_cache_misses` | counter | Total extraction-cache misses |

---

## Project Structure

```
langextract-api/
├── app/
│   ├── main.py                    # App factory, middleware, lifespan
│   ├── core/
│   │   ├── config.py              # Settings (pydantic-settings)
│   │   ├── constants.py           # Shared literals (retries, timeouts, …)
│   │   ├── defaults.py            # Default prompt & few-shot examples
│   │   ├── logging.py             # Structured logging setup (structlog)
│   │   ├── metrics.py             # Prometheus counters & histograms
│   │   ├── redis.py               # Redis connection pool helper
│   │   └── security.py            # SSRF protection, URL validation
│   ├── services/
│   │   ├── consensus_model.py     # Cross-provider consensus wrapper
│   │   ├── converters.py          # Input normalisation helpers
│   │   ├── downloader.py          # URL fetch with SSRF / content guards
│   │   ├── extraction_cache.py    # Multi-tier extraction-result cache
│   │   ├── extractor.py           # LangExtract extraction business logic
│   │   ├── provider_manager.py    # Singleton model cache & LiteLLM Redis setup
│   │   ├── providers.py           # LLM provider factory
│   │   ├── structured_output.py   # JSON Schema builder for response_format
│   │   └── webhook.py             # Result persistence & webhook delivery
│   ├── workers/
│   │   ├── celery_app.py          # Celery app configuration
│   │   ├── tasks.py               # Celery task entry-points (route to below)
│   │   ├── extract_task.py        # Single-document extraction task
│   │   └── batch_task.py          # Batch extraction task
│   ├── api/
│   │   ├── deps.py                # FastAPI dependencies (Redis, auth, …)
│   │   └── routes/
│   │       ├── extract.py         # POST /extract
│   │       ├── batch.py           # POST /extract/batch
│   │       ├── health.py          # GET /health, GET /metrics
│   │       └── tasks.py           # GET/DELETE /tasks/{task_id}
│   └── schemas/
│       ├── enums.py               # TaskStatus and other enumerations
│       ├── health.py              # HealthResponse model
│       ├── requests.py            # ExtractionRequest, BatchRequest
│       ├── responses.py           # SubmitResponse, TaskResponse
│       └── results.py             # Entity, ExtractionResult
├── tests/                         # pytest suite (219 tests)
├── docs/                          # security.md, deployment.md, recipes.md
├── examples/
│   ├── curl/                      # Bash / curl scripts
│   │   ├── extract_text.sh        #   raw-text submit + poll
│   │   ├── extract_url.sh         #   URL submit
│   │   ├── extract_batch.sh       #   batch submit + poll
│   │   └── poll_status.sh         #   generic poller
│   ├── python/
│   │   └── client.py              #   requests-based example (no deps)
│   ├── typescript/
│   │   └── client.ts              #   fetch-based example (Node 18+, no deps)
│   ├── go/
│   │   └── client.go              #   stdlib-only example
│   └── configs/                   # JSON extraction-config samples
│       ├── invoice_config.json
│       └── cv_config.json
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
