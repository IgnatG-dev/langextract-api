# Plugin Integration Guide

LangCore API integrates four plugin packages that extend the core
extraction pipeline.  Each plugin can be enabled/disabled independently
and configured via environment variables and per-request overrides.

| Plugin | Package | Endpoint | Purpose |
|--------|---------|----------|---------|
| [Audit](#audit-logging) | `langcore-audit` | _(wraps extraction)_ | Structured audit logging of every LLM call |
| [Guardrails](#guardrails--output-validation) | `langcore-guardrails` | _(wraps extraction)_ | Output validation with retry & corrective prompting |
| [DSPy](#dspy-prompt-optimization) | `langcore-dspy` | `POST /api/v1/dspy/optimize` | Automatic prompt optimization |
| [RAG](#rag-query-parsing) | `langcore-rag` | `POST /api/v1/rag/parse` | Query decomposition for hybrid RAG retrieval |

---

## Audit Logging

Wraps every LLM inference call with structured audit logging.  The
audit trail records prompt/response pairs, token usage, latency, and
model metadata.

### How it works

The `AuditLanguageModel` is the **outermost** wrapper around the base
model.  Every call to `infer()` / `async_infer()` is logged to the
configured sink(s) **after** guardrails validation.

```
base model → guardrails → audit
```

### Configuration

#### Environment variables (global defaults)

| Variable             | Default     | Description |
|----------------------|-------------|-------------|
| `AUDIT_ENABLED`      | `true`      | Enable audit logging globally |
| `AUDIT_SINK`         | `logging`   | Sink type: `logging` (stdlib), `jsonfile` (NDJSON), `otel` (OpenTelemetry) |
| `AUDIT_LOG_PATH`     | `audit.jsonl` | File path when `AUDIT_SINK=jsonfile` |
| `AUDIT_SAMPLE_LENGTH`| _(unset)_   | Truncate prompt/response in records (chars). Unset = full text. |

#### Per-request override

Pass `audit` inside `extraction_config` to override the global setting:

```json
{
  "raw_text": "...",
  "extraction_config": {
    "audit": {
      "enabled": true,
      "sample_length": 200
    }
  }
}
```

| Field           | Type        | Description |
|-----------------|-------------|-------------|
| `enabled`       | `bool|null` | Override global `AUDIT_ENABLED`. `null` = use global. |
| `sample_length` | `int|null`  | Override global `AUDIT_SAMPLE_LENGTH`. |

### Sink types

| Sink | Value | Output | Use case |
|------|-------|--------|----------|
| **LoggingSink** | `logging` | Python stdlib logger at INFO level | Development, debugging |
| **JsonFileSink** | `jsonfile` | Append-only NDJSON file | Compliance, offline analysis |
| **OtelSpanSink** | `otel` | OpenTelemetry spans | Production observability (Jaeger, Datadog, etc.) |

### Example: NDJSON audit file

```bash
AUDIT_ENABLED=true
AUDIT_SINK=jsonfile
AUDIT_LOG_PATH=/var/log/langcore/audit.jsonl
```

Each line is a JSON object:

```json
{
  "timestamp": "2026-02-23T12:00:00Z",
  "model_id": "audit/gpt-4o",
  "prompt_sample": "Extract entities from...",
  "response_sample": "[{\"extraction_class\": \"party\"...",
  "tokens": {"prompt": 450, "completion": 120},
  "latency_ms": 1200
}
```

---

## Guardrails / Output Validation

Validates LLM output with automatic retry and corrective prompting.
When output fails validation, the model is re-prompted with the error
details and the invalid output.

### How it works

The `GuardrailLanguageModel` sits between the base model and the
audit wrapper.  On each `infer()` call:

1. The base model generates output
2. Validators check the output
3. If validation fails and retries remain, a correction prompt is sent
4. Steps 1–3 repeat up to `max_retries` times
5. The final (valid or best-effort) output is passed to the audit wrapper

### Configuration

#### Environment variables (global defaults)

| Variable | Default | Description |
|----------|---------|-------------|
| `GUARDRAILS_ENABLED` | `true` | Enable output validation globally |
| `GUARDRAILS_MAX_RETRIES` | `3` | Max retry attempts on validation failure |
| `GUARDRAILS_INCLUDE_OUTPUT_IN_CORRECTION` | `true` | Include invalid output in correction prompt |
| `GUARDRAILS_MAX_CORRECTION_PROMPT_LENGTH` | _(unset)_ | Truncate original prompt in corrections (chars) |
| `GUARDRAILS_MAX_CORRECTION_OUTPUT_LENGTH` | _(unset)_ | Truncate invalid output in corrections (chars) |

#### Per-request override

Pass `guardrails` inside `extraction_config`:

```json
{
  "raw_text": "...",
  "extraction_config": {
    "guardrails": {
      "enabled": true,
      "json_schema": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "extraction_class": {"type": "string"},
            "extraction_text": {"type": "string"}
          },
          "required": ["extraction_class", "extraction_text"]
        }
      },
      "max_retries": 5
    }
  }
}
```

### Available validators

Validators are created automatically based on which fields you set in the
`guardrails` config object:

| Config field | Validator | Trigger | Description |
|--------------|-----------|---------|-------------|
| `json_schema` | `JsonSchemaValidator` | When set to a JSON Schema dict | Validates output against a strict JSON Schema |
| `regex_pattern` | `RegexValidator` | When set to a regex string | Output must match the pattern |
| `confidence_threshold` | `ConfidenceThresholdValidator` | When set to a float (0.0–1.0) | Rejects outputs with confidence below threshold |
| `required_fields` | `FieldCompletenessValidator` | When set to a list of field names | All named fields must be present in output |
| _(none set)_ | `JsonSchemaValidator(schema=None)` | Default fallback | Syntax-only JSON validity check |

When **multiple validators** are configured, they are automatically combined
into a `ValidatorChain` that runs all validators in sequence.

### Full `guardrails` config reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool\|null` | `null` (→ global) | Enable/disable for this request |
| `json_schema` | `object\|null` | `null` | JSON Schema for `JsonSchemaValidator` |
| `json_schema_strict` | `bool` | `true` | Reject additional properties not in schema |
| `regex_pattern` | `string\|null` | `null` | Regex for `RegexValidator` |
| `regex_description` | `string\|null` | `null` | Human-readable regex description (for error messages) |
| `confidence_threshold` | `float\|null` | `null` | Min confidence (0.0–1.0) for `ConfidenceThresholdValidator` |
| `confidence_score_key` | `string\|null` | `"confidence_score"` | Field name containing the confidence score |
| `required_fields` | `list[str]\|null` | `null` | Required field names for `FieldCompletenessValidator` |
| `on_fail` | `string\|null` | `"reask"` | Action on failure: `exception`, `reask`, `filter`, `noop` |
| `max_retries` | `int\|null` | `null` (→ global) | Override `GUARDRAILS_MAX_RETRIES` |
| `include_output_in_correction` | `bool\|null` | `null` (→ global) | Include bad output in correction prompt |

### `on_fail` actions

| Action | Behaviour |
|--------|-----------|
| `reask` | Retry with a corrective prompt that includes the validation error (default) |
| `exception` | Raise immediately — the extraction fails with a validation error |
| `filter` | Return `null` / empty output — silently skip the invalid response |
| `noop` | Log the error but accept the output as-is |

### Example: Strict schema + field completeness

```json
{
  "raw_text": "Agreement between Acme and Beta for $50,000...",
  "extraction_config": {
    "guardrails": {
      "json_schema": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "extraction_class": {"type": "string", "enum": ["party", "amount", "date"]},
            "extraction_text": {"type": "string"},
            "attributes": {"type": "object"}
          },
          "required": ["extraction_class", "extraction_text"]
        }
      },
      "required_fields": ["extraction_class", "extraction_text"],
      "max_retries": 5,
      "on_fail": "reask"
    }
  }
}
```

### Example: Confidence threshold filtering

Filter out low-confidence extractions automatically:

```json
{
  "raw_text": "...",
  "passes": 3,
  "extraction_config": {
    "guardrails": {
      "confidence_threshold": 0.7,
      "confidence_score_key": "confidence_score"
    }
  }
}
```

---

## DSPy Prompt Optimization

Automatically improve extraction prompts using DSPy's MIPROv2 and GEPA
optimizers.  Provide training documents with expected results, and the
optimizer will find a better prompt description and curated few-shot
example set.

### Prerequisites

1. Set `DSPY_ENABLED=true` in your `.env`
2. Ensure the DSPy optimization model has a valid API key configured

### Endpoint

```
POST /api/v1/dspy/optimize
```

### Request body

```json
{
  "prompt_description": "Extract all parties, dates, and monetary amounts from legal agreements.",
  "examples": [
    {
      "text": "Agreement between X Corp and Y Inc dated Dec 15 2024 for $100,000.",
      "extractions": [
        {"extraction_class": "party", "extraction_text": "X Corp"},
        {"extraction_class": "party", "extraction_text": "Y Inc"},
        {"extraction_class": "date", "extraction_text": "Dec 15 2024"},
        {"extraction_class": "monetary_amount", "extraction_text": "$100,000"}
      ]
    }
  ],
  "train_texts": [
    "Contract between Alpha LLC and Beta Corp dated Jan 1 2025 for $50,000.",
    "Service agreement between Gamma Inc and Delta Partners effective March 15 2025."
  ],
  "expected_results": [
    [
      {"extraction_class": "party", "extraction_text": "Alpha LLC"},
      {"extraction_class": "party", "extraction_text": "Beta Corp"},
      {"extraction_class": "date", "extraction_text": "Jan 1 2025"},
      {"extraction_class": "monetary_amount", "extraction_text": "$50,000"}
    ],
    [
      {"extraction_class": "party", "extraction_text": "Gamma Inc"},
      {"extraction_class": "party", "extraction_text": "Delta Partners"},
      {"extraction_class": "date", "extraction_text": "March 15 2025"}
    ]
  ],
  "model_id": "gemini/gemini-2.5-flash",
  "optimizer": "miprov2",
  "num_candidates": 7
}
```

### Request fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `prompt_description` | `string` | Yes | — | Initial prompt to optimize (min 10 chars) |
| `examples` | `list[object]` | Yes | — | Seed few-shot examples (`text` + `extractions`) |
| `train_texts` | `list[string]` | Yes | — | Training document texts |
| `expected_results` | `list[list[object]]` | Yes | — | Expected extractions per training document (parallel with `train_texts`) |
| `model_id` | `string\|null` | No | `DSPY_MODEL_ID` | LLM for optimization |
| `optimizer` | `string\|null` | No | `DSPY_OPTIMIZER` | `miprov2` or `gepa` |
| `num_candidates` | `int\|null` | No | `DSPY_NUM_CANDIDATES` | Candidates to explore (MIPROv2 only, 1–20) |
| `max_bootstrapped_demos` | `int\|null` | No | `DSPY_MAX_BOOTSTRAPPED_DEMOS` | Max bootstrapped demos (0–10) |
| `max_labeled_demos` | `int\|null` | No | `DSPY_MAX_LABELED_DEMOS` | Max labelled demos (0–10) |
| `num_threads` | `int\|null` | No | `DSPY_NUM_THREADS` | Parallel evaluation threads (1–16) |

### Response

```json
{
  "prompt_description": "Extract all contracting parties (company names), effective dates, and monetary amounts (with currency) from legal agreements and contracts.",
  "examples": [
    {
      "text": "Agreement between X Corp and Y Inc dated Dec 15 2024 for $100,000.",
      "extractions": [
        {"extraction_class": "party", "extraction_text": "X Corp"},
        {"extraction_class": "party", "extraction_text": "Y Inc"},
        {"extraction_class": "date", "extraction_text": "Dec 15 2024"},
        {"extraction_class": "monetary_amount", "extraction_text": "$100,000"}
      ]
    }
  ],
  "metadata": {
    "optimizer": "miprov2",
    "model_id": "gemini/gemini-2.5-flash",
    "num_train_documents": 2,
    "num_seed_examples": 1,
    "num_optimized_examples": 1,
    "num_candidates": 7,
    "elapsed_seconds": 45.2
  }
}
```

### Using the optimized config

The response `prompt_description` and `examples` can be passed directly
to the extraction endpoint:

```bash
# 1. Optimize
OPTIMIZED=$(curl -s -X POST http://localhost:8000/api/v1/dspy/optimize \
  -H "Content-Type: application/json" \
  -d '{ ... }')

# 2. Extract with optimized config
curl -X POST http://localhost:8000/api/v1/extract \
  -H "Content-Type: application/json" \
  -d "{
    \"raw_text\": \"New contract text...\",
    \"extraction_config\": {
      \"prompt_description\": $(echo $OPTIMIZED | jq .prompt_description),
      \"examples\": $(echo $OPTIMIZED | jq .examples)
    }
  }"
```

### Optimizer strategies

| Strategy | Best for | Speed | Description |
|----------|----------|-------|-------------|
| **MIPROv2** | General use | Fast (30s–2min) | Explores candidate prompts with Bayesian optimization. Good balance of quality and speed. |
| **GEPA** | Complex prompts | Slower (1–5min) | Reflective, feedback-driven optimization. Better for nuanced extraction tasks. |

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DSPY_ENABLED` | `false` | Enable the endpoint |
| `DSPY_MODEL_ID` | `gemini/gemini-2.5-flash` | Default LLM for optimization |
| `DSPY_OPTIMIZER` | `miprov2` | Default strategy |
| `DSPY_NUM_CANDIDATES` | `7` | Default candidate count |
| `DSPY_MAX_BOOTSTRAPPED_DEMOS` | `3` | Default bootstrapped demos |
| `DSPY_MAX_LABELED_DEMOS` | `4` | Default labelled demos |
| `DSPY_NUM_THREADS` | `4` | Default thread count |

> **Note:** DSPy optimization is compute-intensive.  Expect response times
> of 30 seconds to 5 minutes depending on training set size and strategy.
> The endpoint returns `503 Service Unavailable` when `DSPY_ENABLED=false`.

---

## RAG Query Parsing

Decomposes natural-language queries into semantic search terms and
structured metadata filters using an LLM.  Useful for building hybrid
vector + metadata retrieval pipelines.

### Prerequisites

1. Set `RAG_ENABLED=true` in your `.env`
2. Ensure the RAG parsing model has a valid API key configured

### Endpoint

```
POST /api/v1/rag/parse
```

### Request body

```json
{
  "query": "invoices over $5000 from Acme Corp due in March 2025",
  "schema_fields": {
    "amount": {
      "type": "float",
      "description": "Invoice total amount in USD"
    },
    "vendor": {
      "type": "str",
      "description": "Vendor or supplier company name"
    },
    "due_date": {
      "type": "date",
      "description": "Payment due date (ISO-8601)"
    },
    "status": {
      "type": "str",
      "description": "Invoice status: draft, sent, paid, overdue"
    }
  }
}
```

### Request fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | `string` | Yes | — | Natural-language query to decompose |
| `schema_fields` | `object` | Yes | — | Field definitions (see below) |
| `model_id` | `string\|null` | No | `RAG_MODEL_ID` | LLM for parsing |
| `temperature` | `float\|null` | No | `RAG_TEMPERATURE` | Sampling temperature (0.0–2.0) |
| `max_tokens` | `int\|null` | No | `RAG_MAX_TOKENS` | Max tokens for LLM response |

#### `schema_fields` format

Each key is a field name.  The value is an object with:

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| `type` | `string` | Yes | Python type: `str`, `int`, `float`, `bool`, `date`, `datetime` |
| `description` | `string` | No | Human-readable field description (helps the LLM) |

### Response

```json
{
  "semantic_terms": ["invoices", "Acme Corp"],
  "structured_filters": {
    "amount": {"$gte": 5000},
    "vendor": {"$eq": "Acme Corp"},
    "due_date": {"$gte": "2025-03-01", "$lte": "2025-03-31"}
  },
  "confidence": 0.92,
  "explanation": "Extracted monetary threshold as amount filter, company name as vendor filter, and March 2025 as date range filter."
}
```

### Response fields

| Field | Type | Description |
|-------|------|-------------|
| `semantic_terms` | `list[string]` | Free-text keywords for vector similarity search |
| `structured_filters` | `object` | Metadata filters with MongoDB-style operators |
| `confidence` | `float` | Parser confidence (0.0–1.0) |
| `explanation` | `string` | Human-readable rationale |

### Supported filter operators

The parser generates MongoDB-style operators:

| Operator | Meaning | Example |
|----------|---------|---------|
| `$eq` | Equal | `{"vendor": {"$eq": "Acme"}}` |
| `$ne` | Not equal | `{"status": {"$ne": "draft"}}` |
| `$gt` | Greater than | `{"amount": {"$gt": 1000}}` |
| `$gte` | Greater or equal | `{"amount": {"$gte": 5000}}` |
| `$lt` | Less than | `{"amount": {"$lt": 10000}}` |
| `$lte` | Less or equal | `{"due_date": {"$lte": "2025-03-31"}}` |
| `$in` | In set | `{"status": {"$in": ["sent", "overdue"]}}` |
| `$nin` | Not in set | `{"status": {"$nin": ["draft"]}}` |

### Integration example

Use the parsed query with a vector database:

```python
import httpx

# 1. Parse the user's query
response = httpx.post("http://localhost:8000/api/v1/rag/parse", json={
    "query": "contracts worth over $1M signed in 2024",
    "schema_fields": {
        "amount": {"type": "float", "description": "Contract value"},
        "signed_date": {"type": "date", "description": "Date signed"},
    },
})
parsed = response.json()

# 2. Use semantic_terms for vector search
vector_results = vector_db.search(
    query=" ".join(parsed["semantic_terms"]),
    top_k=20,
)

# 3. Apply structured_filters for metadata filtering
filtered = [
    doc for doc in vector_results
    if apply_filters(doc.metadata, parsed["structured_filters"])
]
```

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `RAG_ENABLED` | `false` | Enable the endpoint |
| `RAG_MODEL_ID` | `gpt-4o` | Default LLM for parsing |
| `RAG_TEMPERATURE` | `0.0` | Lower = more deterministic |
| `RAG_MAX_TOKENS` | `1024` | Max response tokens |
| `RAG_MAX_RETRIES` | `2` | Retries on malformed JSON |

> **Note:** The endpoint returns `503 Service Unavailable` when
> `RAG_ENABLED=false`.

---

## Provider Key Resolution

The API automatically resolves API keys based on the model name.  Keys
are matched by substring against the model ID:

| Model pattern | Key variable | Example models |
|---------------|--------------|----------------|
| `gpt`, `openai`, `o1-`, `o3-`, `o4-` | `OPENAI_API_KEY` | `gpt-4o`, `o3-mini` |
| `claude`, `anthropic` | `ANTHROPIC_API_KEY` | `claude-3.5-sonnet` |
| `mistral`, `mixtral`, `codestral`, `pixtral` | `MISTRAL_API_KEY` | `mistral-large` |
| `gemini`, `gemma` | `GEMINI_API_KEY` | `gemini-2.5-flash` |
| _(no match)_ | `LANGCORE_API_KEY` | Any other model |

This applies to extraction, DSPy optimization, and RAG parsing.  You
only need to set the key for the providers you use.

---

## Disabling Plugins

Each plugin can be individually disabled:

```bash
# Disable everything except extraction
AUDIT_ENABLED=false
GUARDRAILS_ENABLED=false
DSPY_ENABLED=false
RAG_ENABLED=false
```

Or override per-request:

```json
{
  "raw_text": "...",
  "extraction_config": {
    "audit": {"enabled": false},
    "guardrails": {"enabled": false}
  }
}
```

DSPy and RAG are endpoint-level features (not wrapper-level), so they
are only active when explicitly called via their respective endpoints.
