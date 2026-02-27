# LangCore API — Extraction Flow

End-to-end walkthrough of what happens when a document is submitted for extraction.

---

## Architecture Overview

```
Client (frontend / curl)
  │
  │  POST /api/v1/extract
  ▼
┌───────────────────┐   task enqueued   ┌───────────┐   task dispatched   ┌───────────────┐
│  FastAPI (api)    │ ───────────────▶  │   Redis   │ ──────────────────▶ │ Celery Worker │
│  POST /extract    │                   │  (broker + │                    │  extract_task  │
└───────────────────┘                   │   result   │                    └───────┬───────┘
  │ returns task_id                     │   backend) │                           │
  │ immediately                         └─────┬─────┘                            │
  ▼                                           │                                  ▼
                                              │                          ┌───────────────┐
Client polls                                  │                          │  LangCore     │
GET /tasks/{task_id}  ◀───── reads result ────┘                          │  (extraction) │
                                              ▲                          └───────┬───────┘
                                              │                                  │
                                              │ stores result                    ▼
                                              │                          ┌───────────────┐
                                              └───────────── Celery ◀─── │  LLM Provider │
                                                             Worker      │  (via LiteLLM)│
                                                               │         └───────────────┘
                                                               │
                                                               │  (optional)
                                                               ▼
                                                       ┌───────────────┐
                                                       │  Callback URL │
                                                       │  (webhook)    │
                                                       └───────────────┘
```

---

## Step 1 — HTTP Request (`POST /api/v1/extract`)

**Route:** `app/api/routes/extract.py → submit_extraction()`

The client sends a JSON body matching the `ExtractionRequest` schema:

### Request Body

| Field                | Type                  | Required | Default      | Description                                                       |
| -------------------- | --------------------- | -------- | ------------ | ----------------------------------------------------------------- |
| `document_url`       | `string (URL)`        | *        | —            | Signed URL to a `.txt` or `.md` document (e.g. Supabase storage). |
| `raw_text`           | `string`              | *        | —            | Plain-text blob to extract from directly.                         |
| `provider`           | `string`              | No       | `"gpt-4o"`   | LLM model ID (e.g. `mistral/mistral-large-latest`, `gemini-2.5-flash`). |
| `passes`             | `int (1–5)`           | No       | `1`          | Number of extraction passes (multi-pass boosts accuracy).         |
| `callback_url`       | `string (URL)`        | No       | `null`       | Webhook URL — worker POSTs result here on completion.             |
| `callback_headers`   | `dict[str, str]`      | No       | `null`       | Extra HTTP headers for the webhook (e.g. `Authorization`).        |
| `idempotency_key`    | `string`              | No       | `null`       | Prevents duplicate task creation for the same request.            |
| `extraction_config`  | `ExtractionConfig`    | No       | `{}`         | Pipeline overrides (see below).                                   |

> \* At least one of `document_url` or `raw_text` must be provided.

### ExtractionConfig (nested)

| Field                  | Type                | Description                                                              |
| ---------------------- | ------------------- | ------------------------------------------------------------------------ |
| `prompt_description`   | `string`            | Custom extraction prompt (replaces the default).                         |
| `examples`             | `list[dict]`        | Few-shot examples (`{text, extractions}`).                               |
| `max_workers`          | `int (1–100)`       | Max parallel extraction workers.                                         |
| `max_char_buffer`      | `int (≥100)`        | Character buffer size for document chunking.                             |
| `additional_context`   | `string`            | Extra context appended to the prompt.                                    |
| `temperature`          | `float (0.0–2.0)`   | LLM sampling temperature.                                                |
| `context_window_chars` | `int (≥1000)`       | Context window in characters (for cross-chunk coreference).              |
| `structured_output`    | `bool`              | Enable JSON Schema–constrained LLM output (`response_format`).          |
| `consensus_providers`  | `list[str] (≥2)`    | Multiple model IDs for consensus extraction (highest agreement wins).    |
| `consensus_threshold`  | `float (0.0–1.0)`   | Jaccard similarity threshold for consensus agreement (default `0.6`).    |
| `guardrails`           | `GuardrailsConfig`  | Output validation/retry via `langcore-guardrails`.                       |
| `audit`                | `AuditConfig`       | Structured audit logging via `langcore-audit`.                           |
| `hybrid_rules`         | `list[dict]`        | Deterministic regex/callable rules for `langcore-hybrid`.                |

### What happens in the route handler

1. **URL validation** — `document_url` and `callback_url` are checked against SSRF rules.
2. **Idempotency check** — if `idempotency_key` is set, Redis is checked for a duplicate.
3. **Task submission** — `extract_document.delay(...)` enqueues a Celery task.
4. **Immediate response** — returns `TaskSubmitResponse` with a `task_id`.

### Response (immediate)

```json
{
  "task_id": "0c07ce3a-9a27-4f03-9614-027c832e6148",
  "status": "submitted",
  "message": "Extraction submitted for https://..."
}
```

---

## Step 2 — Celery Task (`extract_document`)

**Location:** `app/workers/extract_task.py`

The Celery worker picks up the task from Redis and calls `async_run_extraction()`.

**Parameters received:**

| Parameter            | Source                            |
| -------------------- | --------------------------------- |
| `document_url`       | From request body                 |
| `raw_text`           | From request body                 |
| `provider`           | From request body (default `gpt-4o`) |
| `passes`             | From request body (default `1`)   |
| `callback_url`       | From request body                 |
| `extraction_config`  | From request body (flattened dict) |
| `callback_headers`   | From request body                 |

**Task configuration:**

- `max_retries = 3`
- `default_retry_delay = 60s`
- Retries on any unhandled exception

---

## Step 3 — Extraction Orchestrator (`async_run_extraction`)

**Location:** `app/services/extractor.py`

This is where the real work happens. The pipeline is:

### 3.1 — Download Document

If `document_url` is provided:

- Re-validates URL against SSRF rules (defense-in-depth)
- Rejects binary extensions (`.pdf`, `.docx`, etc.)
- Downloads via `httpx` with timeout + size limits
- Validates `Content-Type` (only `text/*` and `application/json`)
- Byte-sniffs for binary content
- Returns decoded text

If `raw_text` is provided, it's used directly.

### 3.2 — Build Prompt & Examples

- Uses `prompt_description` from config or falls back to `DEFAULT_PROMPT_DESCRIPTION`
- Converts raw example dicts into `lx.data.ExampleData` objects
- Each example has `text` + `extractions` (list of `{extraction_class, extraction_text, attributes}`)

### 3.3 — Check Extraction Cache

- Builds a cache key from: text hash, prompt, examples, model ID, temperature, passes, consensus config
- If cache **HIT** → returns cached result immediately (no LLM call)
- If cache **MISS** → continues to LLM

### 3.4 — Build LLM Model

Uses `ProviderManager` (singleton) to get or create a cached model instance:

1. **Provider resolution** — maps model ID (e.g. `litellm/mistral/mistral-large-latest`) to a `BaseLanguageModel` via `langcore.providers.registry`
2. **API key resolution** — reads from env vars (`MISTRAL_API_KEY`, `OPENAI_API_KEY`, etc.)
3. **Structured output** — if the provider supports it, builds a `response_format` JSON Schema from examples
4. **LiteLLM Redis cache** — enabled for single-pass jobs (prompt-level dedup)

### 3.5 — Apply Model Wrappers

The base model is wrapped with optional middleware layers:

```
BaseLanguageModel (LiteLLM)
  └─ GuardrailsWrapper (validates output, retries on failure)
      └─ AuditWrapper (logs prompt/response hashes, latency, tokens)
```

- **Guardrails** (`langcore-guardrails`): JSON Schema validation, regex matching, field completeness, confidence thresholds
- **Audit** (`langcore-audit`): Structured logging with prompt/response hashes, latency, token usage
- **Hybrid** (`langcore-hybrid`): Deterministic regex rules tried before LLM

### 3.6 — Run LangCore Extraction

Calls `lx.async_extract()` with assembled kwargs:

```python
lx.async_extract(
    text_or_documents=text_input,
    prompt_description=prompt_description,
    examples=examples,
    model=wrapped_model,
    extraction_passes=passes,
    max_workers=...,
    max_char_buffer=...,
    show_progress=False,
    # optional: additional_context, temperature, context_window_chars
)
```

**Inside LangCore, the following happens:**

1. **Chunking** — splits document into chunks based on `max_char_buffer`
2. **Batching** — groups chunks into batches
3. **Prompt building** — for each chunk, builds a prompt with description + few-shot examples + chunk text
4. **Async inference** — sends all batch prompts to the LLM via `litellm.acompletion()` concurrently
5. **Resolver** — parses LLM JSON output into `Extraction` objects
6. **Alignment** — maps extracted entities back to character/token spans in the source text (exact > fuzzy > lesser matching)
7. **Multi-pass** (if `passes > 1`) — repeats and computes cross-pass confidence scores
8. **Returns** `AnnotatedDocument` with extractions, character spans, and token usage

### 3.7 — Convert to Response Format

`convert_extractions()` maps `AnnotatedDocument.extractions` → list of entity dicts:

```json
{
  "extraction_class": "party",
  "extraction_text": "TechCorp Inc.",
  "attributes": { "role": "provider" },
  "char_start": 118,
  "char_end": 131,
  "confidence_score": 0.95
}
```

### 3.8 — Cache Result

If extraction cache is enabled, stores the result under the computed cache key.

---

## Step 4 — Result Storage & Webhook

**Back in `extract_task.py`:**

1. **Redis persistence** — result stored under `langcore:task_result:{task_id}` with TTL from `RESULT_EXPIRES`
2. **Webhook delivery** (if `callback_url` set):
   - POSTs `{task_id, status, source, data}` to the callback URL
   - HMAC-SHA256 signed (`X-Webhook-Signature` header)
   - Retries up to 4 times with exponential backoff (1s → 10s)
   - Includes any `callback_headers` provided in the request
3. **Metrics** — records task completion (success/failure, duration)

---

## Step 5 — Client Polls for Result

**Route:** `GET /api/v1/tasks/{task_id}`

### Response (when complete)

```json
{
  "task_id": "0c07ce3a-9a27-4f03-9614-027c832e6148",
  "state": "SUCCESS",
  "progress": null,
  "result": {
    "status": "completed",
    "source": "https://...anonymised.txt?token=...",
    "data": {
      "entities": [
        {
          "extraction_class": "party",
          "extraction_text": "TechCorp Inc.",
          "attributes": { "role": "provider" },
          "char_start": 118,
          "char_end": 131,
          "confidence_score": 0.95
        }
      ],
      "metadata": {
        "provider": "litellm/mistral/mistral-large-latest",
        "tokens_used": 4128,
        "processing_time_ms": 53251
      }
    }
  },
  "error": null
}
```

### Task States

| State       | Meaning                                       |
| ----------- | --------------------------------------------- |
| `PENDING`   | Task is queued but not yet picked up           |
| `PROGRESS`  | Worker is actively processing (step metadata)  |
| `SUCCESS`   | Extraction completed                           |
| `FAILURE`   | All retries exhausted                          |
| `RETRY`     | Retrying after a transient error               |
| `REVOKED`   | Task was cancelled                             |

### Failure Webhook

If all retries are exhausted and `callback_url` was set:

```json
{
  "task_id": "0c07ce3a-...",
  "status": "failed",
  "error": "Each item in the sequence must be a mapping."
}
```

---

## Error Handling & Retry Strategy

| Layer           | Error Type               | Behaviour                                              |
| --------------- | ------------------------ | ------------------------------------------------------ |
| **API route**   | Validation error         | 422 immediately (Pydantic)                             |
| **API route**   | SSRF / bad URL           | 400 immediately                                        |
| **Downloader**  | Binary content           | Exception → Celery retry (60s)                         |
| **Downloader**  | Timeout / HTTP 5xx       | Exception → Celery retry (60s)                         |
| **LLM call**    | Auth / Rate limit / 4xx  | Warning logged, no retry at LLM level                  |
| **LLM call**    | Malformed JSON output    | `ValueError` → tenacity retry (up to 2 immediate)      |
| **Resolver**    | Bad chunk parse          | Warning logged, chunk skipped, batch continues          |
| **Guardrails**  | Output validation fail   | Corrective re-ask (up to `max_retries`)                |
| **Celery task** | Any unhandled exception  | Retry in 60s, up to 3 retries total                    |
| **Webhook**     | HTTP error / timeout     | Retry up to 4x with exponential backoff (1s–10s)       |

---

## Batch Extraction (`POST /api/v1/batch/extract`)

Submits multiple documents in one request. Each document becomes an independent Celery task. The batch orchestrator waits for all sub-tasks and aggregates results.

Request body uses `BatchExtractionRequest`:

- `batch_id` — client-supplied batch identifier
- `documents` — list of `ExtractionRequest` objects (same schema as single extract)
- `callback_url` / `callback_headers` — optional batch-level webhook (overrides per-doc)
