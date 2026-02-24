# LangCore API

> HTTP service for [LangCore](https://github.com/ignatg/langcore) — production-ready structured document extraction powered by FastAPI, Celery, and Redis.

[![PyPI version](https://img.shields.io/pypi/v/langcore-api)](https://pypi.org/project/langcore-api/)
[![Python](https://img.shields.io/pypi/pyversions/langcore-api)](https://pypi.org/project/langcore-api/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

---

## Overview

**langcore-api** wraps the entire LangCore plugin ecosystem into a deployable HTTP service. Submit a document URL or raw text via REST, and get back structured entities — asynchronously via polling or webhooks. It integrates all LangCore plugins (LiteLLM, audit, guardrails, hybrid, DSPy, RAG) and adds production concerns: task queuing, caching, observability, security, and idempotency.

---

## Features

- **Async task queue** — FastAPI receives requests, Redis brokers tasks, Celery workers execute extractions
- **Single and batch extraction** — `POST /extract` for one document, `POST /extract/batch` for many
- **Multi-pass extraction with confidence scoring** — run multiple passes and score entities by consistency (0.0–1.0)
- **Consensus mode** — run the same extraction through multiple LLM providers and keep only agreed-upon entities
- **Multi-tier caching** — LLM response cache (LiteLLM + Redis) and extraction-result cache (SHA-256 keyed) for near-instant re-runs
- **Webhook delivery** — receive results via HMAC-signed webhook callbacks with custom headers
- **Idempotency** — prevent duplicate tasks with `idempotency_key`
- **SSRF protection** — private IP blocking, domain allow-lists, DNS timeout, redirect validation
- **Prometheus metrics** — task counters, cache hit/miss rates, processing histograms
- **Structured logging** — JSON-formatted logs via `structlog`
- **Full plugin integration** — audit logging, output guardrails, hybrid rules, DSPy optimization, and RAG query parsing — all configurable via environment variables
- **Docker-ready** — multi-stage Dockerfile with web, worker, and Flower profiles
- **100+ LLM support** — any model accessible through LiteLLM (OpenAI, Gemini, Anthropic, Azure, Groq, Mistral, Ollama, vLLM, etc.)

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
3. A Celery worker downloads the document text and runs the LangCore pipeline
4. Results are stored in Redis (TTL via `RESULT_EXPIRES`)
5. Client **polls** `GET /api/v1/tasks/{task_id}` or receives a **webhook** callback

---

## Quick Start

### Docker (Recommended)

```bash
cp .env.example .env          # Add your GEMINI_API_KEY or OPENAI_API_KEY
docker compose up --build      # API on :8000, Flower on :5555
```

### Local Development

```bash
uv sync                                        # Install dependencies
docker run -d -p 6379:6379 redis:8-alpine      # Start Redis

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

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/extract` | Submit single extraction |
| `POST` | `/api/v1/extract/batch` | Submit batch of extractions |
| `GET` | `/api/v1/tasks/{task_id}` | Poll task status / result |
| `DELETE` | `/api/v1/tasks/{task_id}` | Revoke a running task |
| `POST` | `/api/v1/dspy/optimize` | Optimize extraction prompts with DSPy |
| `POST` | `/api/v1/rag/parse` | Parse a query for hybrid RAG retrieval |
| `GET` | `/api/v1/health` | Liveness probe |
| `GET` | `/api/v1/health/celery` | Worker readiness probe |
| `GET` | `/api/v1/metrics` | Task counters (submitted / completed / failed) |

Interactive docs at **<http://localhost:8000/api/v1/docs>** (Swagger UI).

### Submit Extraction (URL)

```bash
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d '{
    "document_url": "https://example.com/contract.txt",
    "callback_url": "https://my-app.com/webhooks/done",
    "callback_headers": {"Authorization": "Bearer eyJhbGciOi..."},
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
    "raw_text": "AGREEMENT between Acme Corp and Beta LLC ...",
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

### Webhook Delivery

Receive results via HMAC-signed webhook with custom headers:

```json
{
  "raw_text": "...",
  "callback_url": "https://my-app.com/webhooks/done",
  "callback_headers": {"Authorization": "Bearer <token>"}
}
```

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
        "attributes": {"role": "Seller", "jurisdiction": "Delaware"},
        "char_start": 52,
        "char_end": 68,
        "confidence_score": 1.0
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

---

## Integration with LangCore Ecosystem

langcore-api consumes the full suite of LangCore plugins, all configurable via environment variables:

### LiteLLM (100+ LLM Providers)

Every extraction routes through **langcore-litellm**. Override the model per-request via the `provider` field:

```json
{"raw_text": "...", "provider": "gemini-2.5-flash"}
```

Supports OpenAI, Gemini, Anthropic, Azure, Groq, Mistral, Ollama, vLLM, and any LiteLLM-compatible backend.

### Audit Logging (langcore-audit)

Structured audit trails for every extraction call with configurable sinks:

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIT_ENABLED` | `true` | Enable structured audit logging |
| `AUDIT_SINK` | `logging` | Sink type: `logging`, `jsonfile`, `otel` |
| `AUDIT_LOG_PATH` | `audit.jsonl` | NDJSON file path (when sink=`jsonfile`) |
| `AUDIT_SAMPLE_LENGTH` | _(unset)_ | Truncate prompt/response samples (chars) |

### Guardrails (langcore-guardrails)

Output validation with automatic retry:

| Variable | Default | Description |
|----------|---------|-------------|
| `GUARDRAILS_ENABLED` | `true` | Enable output validation with retry |
| `GUARDRAILS_MAX_RETRIES` | `3` | Max retry attempts on validation failure |
| `GUARDRAILS_INCLUDE_OUTPUT_IN_CORRECTION` | `true` | Include invalid output in correction prompt |

### DSPy Prompt Optimization (langcore-dspy)

Optimize extraction prompts automatically:

| Variable | Default | Description |
|----------|---------|-------------|
| `DSPY_ENABLED` | `false` | Enable the `/dspy/optimize` endpoint |
| `DSPY_MODEL_ID` | `gemini/gemini-2.5-flash` | LLM for optimization |
| `DSPY_OPTIMIZER` | `miprov2` | Strategy: `miprov2` or `gepa` |

### RAG Query Parsing (langcore-rag)

Parse natural-language queries for hybrid retrieval:

| Variable | Default | Description |
|----------|---------|-------------|
| `RAG_ENABLED` | `false` | Enable the `/rag/parse` endpoint |
| `RAG_MODEL_ID` | `gpt-4o` | LLM for query decomposition |
| `RAG_TEMPERATURE` | `0.0` | Sampling temperature |

---

## Multi-Pass, Early Stopping & Consensus Mode

### Multi-Pass with Confidence Scoring

Set `passes > 1` to run multiple extraction passes. Each entity receives a **confidence_score** (0.0–1.0) indicating the fraction of passes that found it. Early stopping kicks in automatically when two consecutive passes produce identical results.

### Consensus Mode

Run the same extraction through multiple LLM providers and keep only entities they agree on:

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

---

## Customising Extraction

Override the default prompt and examples per-request:

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
            "attributes": {"dosage": "81 mg", "frequency": "daily"}
          }
        ]
      }
    ],
    "temperature": 0.2
  }
}
```

| `extraction_config` key | Type | Description |
|-------------------------|------|-------------|
| `prompt_description` | `string` | Custom extraction prompt |
| `examples` | `list[dict]` | Few-shot examples |
| `temperature` | `float` | LLM temperature (0.0–2.0) |
| `consensus_providers` | `list[str]` | ≥ 2 model IDs for consensus mode |
| `consensus_threshold` | `float` | Similarity threshold (0.0–1.0, default 0.6) |
| `structured_output` | `bool\|null` | Enable/disable LLM-level `response_format` |
| `guardrails` | `object` | Output validation config |
| `audit` | `object` | Audit logging config |

---

## Multi-Tier Caching

### Tier 1 — LLM Response Cache

Every `litellm.completion()` call is cached in Redis. Identical prompts hit the cache directly with zero API cost. Multi-pass bypass ensures fresh responses on repeat passes.

### Tier 2 — Extraction-Result Cache

Complete extraction results are cached (keyed by SHA-256 of text + prompt + model + settings). Cache hits return in < 500 ms with zero API cost.

| Backend | Env Value | Use Case |
|---------|-----------|----------|
| `redis` | `EXTRACTION_CACHE_BACKEND=redis` | Default. Cross-worker, cross-job. |
| `disk` | `EXTRACTION_CACHE_BACKEND=disk` | Local dev / offline. |
| `none` | `EXTRACTION_CACHE_BACKEND=none` | Completely disabled. |

---

## Security

- **SSRF protection** — private IP / localhost blocking, subdomain matching, URL length limits, DNS timeout, redirect-hop re-validation
- **Domain allow-list** — `ALLOWED_URL_DOMAINS` restricts accepted document URLs
- **Webhook HMAC signing** — `WEBHOOK_SECRET` signs outbound webhooks (HMAC-SHA256)
- **Provider validation** — model IDs validated against strict regex

See [docs/security.md](docs/security.md) for details.

---

## Configuration

All settings are driven by environment variables (`.env` file supported):

### General

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_NAME` | LangCore API | Display name |
| `API_V1_STR` | `/api/v1` | API version prefix |
| `DEBUG` | `false` | Enable debug mode |
| `LOG_LEVEL` | `info` | Logging level |
| `CORS_ORIGINS` | `["*"]` | Allowed CORS origins |

### Redis / Celery

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_HOST` | `redis` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `RESULT_EXPIRES` | `86400` | Result TTL in seconds |
| `TASK_TIME_LIMIT` | `3600` | Hard task timeout (seconds) |

### LLM / Extraction

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_PROVIDER` | `gpt-4o` | Default model (overridable per-request) |
| `OPENAI_API_KEY` | _(empty)_ | OpenAI key |
| `GEMINI_API_KEY` | _(empty)_ | Google Gemini key |
| `ANTHROPIC_API_KEY` | _(empty)_ | Anthropic key |
| `EXTRACTION_CACHE_ENABLED` | `true` | Enable result caching |
| `EXTRACTION_CACHE_TTL` | `86400` | Cache TTL (seconds) |
| `EXTRACTION_CACHE_BACKEND` | `redis` | `redis`, `disk`, or `none` |

### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `ALLOWED_URL_DOMAINS` | _(empty)_ | Comma-separated domain allow-list |
| `WEBHOOK_SECRET` | _(empty)_ | HMAC-SHA256 signing key |
| `DOC_DOWNLOAD_TIMEOUT` | `30` | Download timeout (seconds) |
| `DOC_DOWNLOAD_MAX_BYTES` | `50000000` | Max document size |

---

## Supported Models

Any model accessible through [LiteLLM](https://docs.litellm.ai/docs/providers):

| Provider | Example Models | Key Variable |
|----------|---------------|--------------|
| OpenAI | `gpt-4o`, `gpt-4o-mini` | `OPENAI_API_KEY` |
| Google | `gemini-2.5-pro`, `gemini-2.0-flash` | `GEMINI_API_KEY` |
| Anthropic | `claude-3.5-sonnet`, `claude-3-haiku` | `ANTHROPIC_API_KEY` |
| Azure OpenAI | `azure/gpt-4o` | `AZURE_API_KEY` |
| Groq | `groq/llama-3.1-70b` | `GROQ_API_KEY` |
| Mistral | `mistral/mistral-large-latest` | `MISTRAL_API_KEY` |
| Ollama | `ollama/llama3.1` | `OLLAMA_API_BASE` |
| vLLM | `hosted_vllm/meta-llama/Llama-3.1-8B` | Custom `api_base` |

---

## Project Structure

```
langcore-api/
├── app/
│   ├── main.py                    # App factory, middleware, lifespan
│   ├── core/                      # Config, logging, metrics, security, Redis
│   ├── services/                  # Extraction, caching, webhooks, providers
│   ├── workers/                   # Celery app, tasks (single + batch)
│   ├── api/routes/                # FastAPI route handlers
│   └── schemas/                   # Pydantic request/response models
├── tests/                         # pytest suite (219 tests)
├── docs/                          # security.md, deployment.md, recipes.md
├── examples/                      # curl, Python, TypeScript, Go clients
├── docker/                        # Multi-stage Dockerfile + entrypoint
├── docker-compose.yml
├── pyproject.toml
└── Makefile
```

---

## Development

```bash
make install   # uv sync
make lint      # ruff check + format check
make format    # Auto-format
make test      # pytest -v
make test-cov  # pytest with coverage
make dev       # docker compose up --build
make clean     # docker compose down -v
```

### Running Tests

```bash
uv run pytest -v                           # All tests
uv run pytest --cov=app --cov-report=term  # With coverage
uv run pytest tests/test_tasks.py -v       # Single file
```

---

## Further Reading

- [docs/plugins.md](docs/plugins.md) — Plugin integration guide (DSPy, RAG, guardrails, audit)
- [docs/security.md](docs/security.md) — SSRF protection, HMAC webhooks, domain allow-lists
- [docs/deployment.md](docs/deployment.md) — Production deployment guide
- [docs/recipes.md](docs/recipes.md) — Common usage patterns and examples

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.
